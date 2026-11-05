"""T1 · SQLite 数据层：建表 + 把 trace.jsonl 导入成可查询记录。

设计见 docs/phase1/02-design.md。本任务只负责 runs / steps / tool_calls，
evidence / verifier / verdict 留给 T4~T6，避免一次改太多。

    python -m agent.store init
    python -m agent.store import runs/run-20261105-090111/trace.jsonl
    python -m agent.store summary
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT

DEFAULT_DB = PROJECT_ROOT / "data" / "secagent.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def db_path() -> Path:
    return Path(os.environ.get("SECAGENT_DB") or DEFAULT_DB)


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> list[str]:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return [
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    ]


def _events(trace_path: Path) -> list[dict[str, Any]]:
    out = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def import_trace(trace_path: Path | str, conn: sqlite3.Connection) -> dict[str, Any]:
    """把一次运行的 JSONL 流水导入 DB。幂等：同一个 run_key 重复导入不产生新行。"""
    trace_path = Path(trace_path)
    events = _events(trace_path)
    if not events:
        raise ValueError(f"trace 为空：{trace_path}")

    start = next((e for e in events if e["event"] == "run_start"), None)
    if start is None:
        raise ValueError("trace 缺少 run_start 事件")
    run_key = trace_path.parent.name
    end = next((e for e in events if e["event"] == "run_end"), {})
    final = next((e for e in events if e["event"] == "final"), {})

    existing = conn.execute("SELECT id FROM runs WHERE run_key = ?", (run_key,)).fetchone()
    if existing:
        return {"run_id": existing[0], "run_key": run_key, "imported": False}

    cur = conn.execute(
        """INSERT INTO runs
           (run_key, started_at, finished_at, task, profile, model, status, steps,
            tool_calls, final_answer, trace_path, total_tokens)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_key,
            start.get("started_at", ""),
            end.get("ts", ""),
            start.get("task", ""),
            start.get("profile", ""),
            start.get("model", ""),
            end.get("status", "unknown"),
            end.get("steps", 0),
            end.get("tool_calls", 0),
            end.get("final_answer") or final.get("answer") or "",
            str(trace_path.resolve()),
            sum(e.get("completion_tokens") or 0 for e in events if e["event"] == "llm_response"),
        ),
    )
    run_id = cur.lastrowid

    # 按 step 合并 llm_request / llm_response / decision 三类事件
    steps: dict[int, dict[str, Any]] = {}
    for e in events:
        idx = e.get("step")
        if idx is None:
            continue
        s = steps.setdefault(int(idx), {"idx": int(idx)})
        if e["event"] == "llm_response":
            s.update(
                raw_text=e.get("raw_text"),
                latency_ms=e.get("latency_ms"),
                prompt_tokens=e.get("prompt_tokens"),
                completion_tokens=e.get("completion_tokens"),
                finish_reason=e.get("finish_reason"),
            )
        elif e["event"] == "decision":
            s.update(
                thought=e.get("thought"),
                action=e.get("action"),
                args_json=json.dumps(e.get("args") or {}, ensure_ascii=False),
            )
        elif e["event"] == "parse_error":
            s.update(action="PARSE_ERROR", raw_text=e.get("raw_text"))
        elif e["event"] == "final_rejected":
            s["action"] = "FINAL_REJECTED"

    step_ids: dict[int, int] = {}
    for idx in sorted(steps):
        s = steps[idx]
        c = conn.execute(
            """INSERT INTO steps
               (run_id, idx, role, thought, action, args_json, raw_text, latency_ms,
                prompt_tokens, completion_tokens, finish_reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, idx, "agent", s.get("thought"), s.get("action"), s.get("args_json"),
                s.get("raw_text"), s.get("latency_ms"), s.get("prompt_tokens"),
                s.get("completion_tokens"), s.get("finish_reason"),
            ),
        )
        step_ids[idx] = c.lastrowid

    tool_rows = 0
    for e in events:
        if e["event"] != "tool_result":
            continue
        args = next(
            (d.get("args") for d in events if d["event"] == "decision" and d.get("step") == e.get("step")),
            {},
        )
        conn.execute(
            """INSERT INTO tool_calls
               (run_id, step_id, tool, args_json, ok, duration_ms, result_json, error, truncated)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                step_ids.get(e.get("step")),
                e.get("tool", ""),
                json.dumps(args or {}, ensure_ascii=False),
                1 if e.get("ok") else 0,
                e.get("duration_ms"),
                json.dumps(e.get("data"), ensure_ascii=False) if e.get("data") is not None else None,
                e.get("error"),
                0,
            ),
        )
        tool_rows += 1

    conn.commit()
    return {
        "run_id": run_id,
        "run_key": run_key,
        "imported": True,
        "steps": len(step_ids),
        "tool_calls": tool_rows,
        "status": end.get("status", "unknown"),
    }


def summary(conn: sqlite3.Connection) -> dict[str, Any]:
    def one(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "db": str(db_path()),
        "tables": one("SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"),
        "runs": one("SELECT count(*) FROM runs"),
        "steps": one("SELECT count(*) FROM steps"),
        "tool_calls": one("SELECT count(*) FROM tool_calls"),
        "evidence": one("SELECT count(*) FROM evidence"),
        "verdicts": one("SELECT count(*) FROM verdicts"),
        "last_run": conn.execute(
            "SELECT run_key, status, steps, tool_calls FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone(),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent.store")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    imp = sub.add_parser("import")
    imp.add_argument("trace", help="trace.jsonl 路径")
    sub.add_parser("summary")
    args = ap.parse_args(argv)

    conn = connect()
    tables = init_schema(conn)
    if args.cmd == "init":
        print(f"已建表 {len(tables)} 张：{', '.join(tables)}")
        print(f"数据库：{db_path()}")
    elif args.cmd == "import":
        print(json.dumps(import_trace(args.trace, conn), ensure_ascii=False, indent=2))
    elif args.cmd == "summary":
        s = summary(conn)
        if s["last_run"]:
            s["last_run"] = tuple(s["last_run"])
        print(json.dumps(s, ensure_ascii=False, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
