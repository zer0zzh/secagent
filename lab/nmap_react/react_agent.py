"""ReAct 单 Agent · 最小教学实现（接入一个真实安全工具：Nmap）。

与 secagent 主包的关系：
- 主包 `agent/run.py` 是工程链路（配置层 / 多 profile / SQLite）；
- 本文件是**可单文件拷走、可现场逐行讲**的最小闭环，只依赖标准库；
- 两者共用同一套设计契约（单行 JSON 决策、evidence 驱动结论、全量 trace）。

闭环：任务下达 → 工具调用 → 反馈调整 → 结果核验。
刻意不做：多智能体、RAG、自动利用、评测体系、前端。

用法：
    python lab/nmap_react/react_agent.py "对 127.0.0.1 的 8080,11434 端口做服务识别并给出结论"
    python lab/nmap_react/react_agent.py --demo-unreliable     # 复现「输出不可靠」并被拦住
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from nmap_tool import BadPortSpec, TargetNotAllowed, scan

MODEL = os.environ.get("SECAGENT_OLLAMA_MODEL", "qwen2.5:7b-instruct")
BASE_URL = os.environ.get("SECAGENT_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
RUNS = Path(__file__).resolve().parents[2] / "runs"

SYSTEM_PROMPT = """你是授权靶场里的安全分析助手。你只有一个真实工具 nmap_scan（Nmap 服务/端口识别）。

每回合只输出一个单行 JSON 对象，不要解释、不要 Markdown、thought 不超过 30 字：
{"thought":"...","action":"nmap_scan","args":{"target":"127.0.0.1","ports":"8080,11434"}}
{"thought":"...","action":"FINAL","answer":"..."}

规则：
- 必须先用 nmap_scan 观测，再给 FINAL；最多 5 步，尽快收敛。
- answer 只能引用观测到的端口与服务，不得编造版本号、厂商或端口。
- answer 必须写出具体端口号，并说明依据来自 Nmap。
- 观测不到就如实说观测不到，不要补全。
"""

DECISION_HINT = (
    "上一条不是合法单行 JSON（或输出被截断）。只输出一个 JSON 对象，"
    "thought 不超过 30 字，不要解释、不要换行。"
)


# ---------------------------------------------------------------------------
# 1) LLM：直连本机 ollama，只做非流式 chat completions
# ---------------------------------------------------------------------------
def llm_chat(messages: list[dict[str, str]], max_tokens: int = 320) -> dict[str, Any]:
    body = json.dumps(
        {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    # 关键：绕开系统代理。阶段0 实测过 urllib 会把 127.0.0.1 也交给代理，
    # 导致拿到 502 并被模型当成「目标不可达」写进结论。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer ollama"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with opener.open(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LLM HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM 连接失败：{e.reason}") from e

    ch = (data.get("choices") or [{}])[0]
    return {
        "text": (ch.get("message") or {}).get("content") or "",
        "finish_reason": ch.get("finish_reason"),
        "usage": data.get("usage") or {},
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def parse_decision(text: str) -> tuple[dict[str, Any] | None, str]:
    s = (text or "").strip().replace("```json", "").replace("```", "").strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None, "没有找到 JSON 对象"
    try:
        d = json.loads(s[i : j + 1])
    except json.JSONDecodeError as e:
        return None, f"JSON 解析失败：{e.msg}"
    if not isinstance(d, dict) or not d.get("action"):
        return None, "缺少 action 字段"
    return d, ""


# ---------------------------------------------------------------------------
# 2) 结果核验闸门：结论只认 evidence，不认模型自评
# ---------------------------------------------------------------------------
PORT_IN_TEXT = re.compile(r"(?<![\d.])(\d{2,5})(?![\d.])")
NEGATIVE = re.compile(r"(未发现|没有|无|不存在|未看到)[^，。；]{0,14}(开放|端口|服务)|全部关闭|未开放|不可达")
EXCLUSIVE = re.compile(r"仅开放|只开放|只有.{0,6}(一个|1\s*个)|唯一.{0,4}端口|未发现其他")


def verify(answer: str, evidence: dict[str, Any]) -> tuple[bool, str]:
    """三条断言：无编造端口 / 无证据矛盾 / 必须引用证据。

    这是「输出可核验」的最小实现，不是完整防御；刻意保持三条，
    现场能逐条讲清楚，也不冒充解决了提示注入。
    """
    if not evidence.get("ok"):
        return False, "没有成功的 Nmap 观测，不允许下结论"

    open_ports = {str(p) for p in evidence.get("open_ports", [])}
    mentioned = {p for p in PORT_IN_TEXT.findall(answer) if 1 <= int(p) <= 65535}

    hallucinated = sorted(mentioned - open_ports)
    if hallucinated:
        return False, f"结论提到 Nmap 未观测到的端口 {hallucinated}"

    if not open_ports:
        if not NEGATIVE.search(answer):
            return False, "Nmap 未发现开放端口，结论必须如实说明这一点"
        return True, "结论与 Nmap 证据一致（无开放端口）"

    if NEGATIVE.search(answer):
        return False, "结论称无开放端口/不可达，但 Nmap 观测到 " + ",".join(sorted(open_ports))

    if not (mentioned & open_ports):
        return False, "结论未引用任何 Nmap 观测到的开放端口，属于无证据结论"

    if EXCLUSIVE.search(answer) and len(open_ports) > len(mentioned & open_ports):
        return False, "结论使用排他表述，但漏掉了观测到的端口 " + ",".join(
            sorted(open_ports - (mentioned & open_ports))
        )
    return True, "结论与 Nmap 证据一致"


# ---------------------------------------------------------------------------
# 3) Trace：每一步的完整输入输出落盘
# ---------------------------------------------------------------------------
class Tracer:
    def __init__(self, directory: Path):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.f = (directory / "trace.jsonl").open("a", encoding="utf-8")
        self.t0 = time.perf_counter()

    def emit(self, event: str, **kw: Any) -> None:
        row = {
            "event": event,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "elapsed_s": round(time.perf_counter() - self.t0, 2),
            **kw,
        }
        self.f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.f.flush()

    def close(self) -> None:
        self.f.close()


# ---------------------------------------------------------------------------
# 4) ReAct 循环
# ---------------------------------------------------------------------------
def observation(ev: dict[str, Any]) -> str:
    slim = {k: v for k, v in ev.items() if k not in ("raw_xml", "command", "stderr")}
    return "Nmap 观测结果（唯一事实来源）：" + json.dumps(slim, ensure_ascii=False)


def run(task: str, max_steps: int, run_dir: Path) -> int:
    tr = Tracer(run_dir)
    tr.emit("run_start", task=task, model=MODEL, max_steps=max_steps)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    evidence: dict[str, Any] = {}
    tool_calls = 0
    final: str | None = None

    for step in range(1, max_steps + 1):
        tr.emit("llm_request", step=step, messages=messages)
        try:
            resp = llm_chat(messages)
        except RuntimeError as e:
            tr.emit("llm_error", step=step, error=str(e))
            print(f"第 {step} 步 · LLM 失败：{e}")
            tr.emit("run_end", status="llm_error", steps=step - 1)
            tr.close()
            return 2
        tr.emit(
            "llm_response", step=step, raw_text=resp["text"],
            finish_reason=resp["finish_reason"], latency_ms=resp["latency_ms"],
            usage=resp["usage"],
        )
        decision, err = parse_decision(resp["text"])
        if decision is None:
            if resp["finish_reason"] == "length":
                err = "输出被 max_tokens 截断（finish_reason=length）"
            print(f"第 {step}/{max_steps} 步 · 解析失败：{err}")
            messages.extend([
                {"role": "assistant", "content": resp["text"]},
                {"role": "user", "content": DECISION_HINT},
            ])
            tr.emit("parse_error", step=step, reason=err)
            continue

        action = str(decision.get("action", "")).upper()
        tr.emit("decision", step=step, decision=decision)
        print(f"第 {step}/{max_steps} 步 · {action} {decision.get('args') or ''}")

        if action == "NMAP_SCAN":
            args = decision.get("args") or {}
            tr.emit("tool_call", step=step, tool="nmap_scan", args=args)
            try:
                ev = scan(str(args.get("target", "")), str(args.get("ports", "")))
            except (TargetNotAllowed, BadPortSpec) as e:
                ev = {"tool": "nmap_scan", "ok": False, "error": str(e),
                      "open_ports": [], "hosts": [], "raw_xml": ""}
            tool_calls += 1
            if ev.get("raw_xml"):
                (run_dir / f"nmap-step{step}.xml").write_text(ev["raw_xml"], encoding="utf-8")
            evidence = ev if ev.get("ok") else evidence
            slim = {k: v for k, v in ev.items() if k != "raw_xml"}
            tr.emit("tool_result", step=step, result=slim,
                    raw_xml_file=f"nmap-step{step}.xml" if ev.get("raw_xml") else None)
            print(f"        → ok={ev.get('ok')} {ev.get('summary') or ev.get('error')} "
                  f"({ev.get('elapsed_ms')}ms)")
            messages.extend([
                {"role": "assistant", "content": resp["text"]},
                {"role": "user", "content": observation(ev) if ev.get("ok")
                 else f"工具被拒绝或失败：{ev.get('error')}。请换合法参数或如实说明无法观测。"},
            ])
            continue

        if action == "FINAL":
            answer = str(decision.get("answer") or "").strip()
            if tool_calls == 0:
                ok, reason = False, "尚未调用 nmap_scan，不允许直接下结论"
            else:
                ok, reason = verify(answer, evidence)
            tr.emit("verification", step=step, passed=ok, answer=answer, reason=reason)
            if ok:
                final = answer
                tr.emit("final", step=step, answer=answer)
                print(f"\n结论：{answer}")
                break
            print(f"        → 核验驳回：{reason}")
            messages.extend([
                {"role": "assistant", "content": resp["text"]},
                {"role": "user", "content": f"结论被证据校验驳回：{reason}。"
                 "请依据上一条 Nmap 观测重写 answer，只输出 JSON。"},
            ])
            continue

        messages.extend([
            {"role": "assistant", "content": resp["text"]},
            {"role": "user", "content": f"未知 action {action!r}，只能用 nmap_scan 或 FINAL。"},
        ])

    tr.emit("run_end", status="converged" if final else "step_limit",
            steps=max_steps if not final else step, tool_calls=tool_calls, final=final)
    tr.close()
    print(f"\n步数 {step} ｜ 工具调用 {tool_calls} ｜ trace: {run_dir / 'trace.jsonl'}")
    return 0 if final else 1


# ---------------------------------------------------------------------------
# 5) B2 素材：复现「Agent 输出不可靠」并被证据校验拦住
# ---------------------------------------------------------------------------
UNRELIABLE_ANSWERS = [
    "目标 8080 与 9999 端口开放，其中 9999 运行 Apache 2.4.49，建议立即升级。",
    "扫描完成，未发现任何开放端口，目标主机不可达。",
    "已完成服务识别，Web 服务为 nginx 1.24，风险等级低。",
    "8080 端口是唯一开放的端口，运行 Python SimpleHTTPServer 0.6。",
]


def demo_unreliable(target: str, ports: str, run_dir: Path) -> int:
    tr = Tracer(run_dir)
    ev = scan(target, ports)
    tr.emit("tool_result", step=0, result={k: v for k, v in ev.items() if k != "raw_xml"})
    print(f"Nmap 真实观测：ok={ev['ok']}  {ev['summary']}  ({ev['elapsed_ms']}ms)")
    print(f"开放端口：{ev['open_ports']}\n")
    print("以下为构造的反例结论（模拟模型幻觉 / 阶段0 已出现的同类错误），逐条送入证据校验：\n")
    blocked = 0
    for i, ans in enumerate(UNRELIABLE_ANSWERS, 1):
        ok, reason = verify(ans, ev)
        tr.emit("verification", case=i, passed=ok, answer=ans, reason=reason)
        print(f"  [{i}] {'放行' if ok else '拦截'}  {ans}")
        print(f"      理由：{reason}")
        blocked += (not ok)
    tr.emit("run_end", mode="demo_unreliable", blocked=blocked,
            total=len(UNRELIABLE_ANSWERS))
    tr.close()
    print(f"\n拦截 {blocked}/{len(UNRELIABLE_ANSWERS)} 条；trace: {run_dir / 'trace.jsonl'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ReAct 单 Agent + Nmap（最小教学实现）")
    ap.add_argument("task", nargs="?", default="对 127.0.0.1 的 8080,11434 端口做服务识别并给出结论")
    ap.add_argument("--steps", type=int, default=5)
    ap.add_argument("--demo-unreliable", action="store_true",
                    help="不调 LLM，用真实 Nmap 证据复现「输出不可靠」被拦截")
    ap.add_argument("--target", default="127.0.0.1")
    ap.add_argument("--ports", default="8080,11434")
    args = ap.parse_args(argv)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = "unreliable" if args.demo_unreliable else "react"
    run_dir = RUNS / f"nmap-{tag}-{stamp}"
    if args.demo_unreliable:
        return demo_unreliable(args.target, args.ports, run_dir)
    return run(args.task, args.steps, run_dir)


if __name__ == "__main__":
    sys.exit(main())
