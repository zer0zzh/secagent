"""T1 单元测试：python -m unittest discover -s tests -v"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent import store
from agent.config import PROJECT_ROOT

TRACE = PROJECT_ROOT / "runs" / "run-20261105-090111" / "trace.jsonl"


def synthetic_trace(path: Path) -> None:
    """不依赖真实运行记录的最小 trace。"""
    events = [
        {"event": "run_start", "ts": "2026-11-05T09:00:00", "elapsed_s": 0.0,
         "task": "test", "profile": "mock", "model": "m", "started_at": "2026-11-05 09:00:00"},
        {"event": "system_prompt", "content": "sys"},
        {"event": "llm_request", "step": 1, "messages": [{"role": "system", "content": "sys"}], "params": {}},
        {"event": "llm_response", "step": 1, "latency_ms": 10, "completion_tokens": 5,
         "prompt_tokens": 10, "finish_reason": "stop", "raw_text": '{"action":"http_probe"}'},
        {"event": "decision", "step": 1, "thought": "probe", "action": "http_probe", "args": {"url": "http://127.0.0.1:8080/"}},
        {"event": "tool_result", "step": 1, "ok": True, "tool": "http_probe", "duration_ms": 3,
         "data": {"status": 200}, "error": None},
        {"event": "llm_request", "step": 2, "messages": [], "params": {}},
        {"event": "llm_response", "step": 2, "latency_ms": 11, "completion_tokens": 6,
         "prompt_tokens": 12, "finish_reason": "stop", "raw_text": '{"action":"FINAL"}'},
        {"event": "decision", "step": 2, "thought": "done", "action": "FINAL", "args": {}},
        {"event": "final", "step": 2, "answer": "ok"},
        {"event": "run_end", "ts": "2026-11-05T09:00:01", "elapsed_s": 1.0,
         "status": "converged", "steps": 2, "tool_calls": 1, "final_answer": "ok"},
    ]
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.conn = store.connect(self.db)
        self.tables = store.init_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_01_schema_has_all_tables(self):
        expected = {"runs", "steps", "tool_calls", "evidence", "verifications",
                    "verdicts", "eval_cases", "eval_results"}
        self.assertTrue(expected.issubset(set(self.tables)), f"缺表：{expected - set(self.tables)}")

    def test_02_import_synthetic_trace(self):
        trace = Path(self.tmp.name) / "run-test" / "trace.jsonl"
        trace.parent.mkdir(parents=True)
        synthetic_trace(trace)
        r = store.import_trace(trace, self.conn)
        self.assertTrue(r["imported"])
        self.assertEqual(r["steps"], 2)
        self.assertEqual(r["tool_calls"], 1)
        self.assertEqual(r["status"], "converged")
        row = self.conn.execute("SELECT status, steps, tool_calls, final_answer FROM runs").fetchone()
        self.assertEqual(tuple(row), ("converged", 2, 1, "ok"))

    def test_03_raw_text_preserved(self):
        trace = Path(self.tmp.name) / "run-test" / "trace.jsonl"
        trace.parent.mkdir(parents=True)
        synthetic_trace(trace)
        store.import_trace(trace, self.conn)
        texts = [r[0] for r in self.conn.execute("SELECT raw_text FROM steps ORDER BY idx")]
        self.assertEqual(texts, ['{"action":"http_probe"}', '{"action":"FINAL"}'])
        tc = self.conn.execute("SELECT tool, ok, result_json FROM tool_calls").fetchone()
        self.assertEqual(tc["tool"], "http_probe")
        self.assertEqual(json.loads(tc["result_json"])["status"], 200)

    def test_04_import_is_idempotent(self):
        trace = Path(self.tmp.name) / "run-test" / "trace.jsonl"
        trace.parent.mkdir(parents=True)
        synthetic_trace(trace)
        store.import_trace(trace, self.conn)
        r2 = store.import_trace(trace, self.conn)
        self.assertFalse(r2["imported"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM runs").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM steps").fetchone()[0], 2)

    def test_05_verdict_rejects_empty_evidence(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO verdicts (run_id, claim, verdict, evidence_ids, created_at)
                   VALUES (0, '存在 SQL 注入', 'CONFIRMED', '[]', '2026-11-05 09:00:00')"""
            )

    def test_06_db_path_env_override(self):
        old = os.environ.get("SECAGENT_DB")
        os.environ["SECAGENT_DB"] = str(self.db)
        try:
            self.assertEqual(store.db_path(), self.db)
        finally:
            if old is None:
                os.environ.pop("SECAGENT_DB", None)
            else:
                os.environ["SECAGENT_DB"] = old

    @unittest.skipUnless(TRACE.exists(), "真实 trace 不存在时跳过")
    def test_07_import_real_phase0_trace(self):
        r = store.import_trace(TRACE, self.conn)
        self.assertTrue(r["imported"])
        self.assertGreaterEqual(r["steps"], 3, "阶段0 验收 trace 应至少 3 步")
        self.assertGreaterEqual(r["tool_calls"], 2)
        self.assertEqual(r["status"], "converged")
        s = store.summary(self.conn)
        self.assertEqual(s["runs"], 1)
        self.assertGreater(s["steps"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
