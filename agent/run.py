"""ReAct 主循环。

python -m agent.run "对 http://127.0.0.1:8080 做一次 HTTP 指纹识别并给出结论"
可选：--max-steps 8 --profile ollama --quiet

产物：runs/<时间戳>/trace.jsonl（每行一个事件，含完整输入输出）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, check_profile, load_config
from .llm import ChatMessage, ChatRequest, LLMError, create_client
from .prompt import build_system_prompt, observation_message, parse_decision
from .tools.registry import execute

RUNS_DIR = PROJECT_ROOT / "runs"


class Trace:
    """一行一个事件的 JSONL 记录器。父目录 runs/<stamp>/。"""

    def __init__(self, task: str, profile: str, model: str) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = RUNS_DIR / f"run-{stamp}"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / "trace.jsonl"
        self.started = time.time()
        self._fh = self.path.open("a", encoding="utf-8")
        self.emit(
            "run_start",
            task=task,
            profile=profile,
            model=model,
            started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def emit(self, event: str, **payload: Any) -> None:
        record = {
            "event": event,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_s": round(time.time() - self.started, 2),
            **payload,
        }
        self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()

    def close(self, **payload: Any) -> None:
        self.emit("run_end", **payload)
        self._fh.close()


def _request(profile, messages: list[ChatMessage]) -> ChatRequest:
    p = profile.params
    return ChatRequest(
        model=profile.model,
        messages=messages,
        temperature=0.0 if p.temperature is None else min(p.temperature, 0.3),
        top_p=p.top_p,
        max_tokens=min(p.max_tokens, 320),
        stop=p.stop,
        stream=False,
        extra_headers=p.extra_headers,
        extra_body=p.extra_body,
    )


def run(task: str, max_steps: int = 8, profile_name: str | None = None, quiet: bool = False, min_tools: int = 2) -> int:
    cfg = load_config()
    profile = cfg.profile(profile_name)
    notes = check_profile(profile)
    if notes:
        for n in notes:
            print(f"[配置提示] {n}")

    client = create_client(profile)
    trace = Trace(task, profile.name, profile.model)

    system = build_system_prompt(max_steps)
    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=task),
    ]
    trace.emit("system_prompt", content=system)

    steps = 0
    tool_calls = 0
    final_answer: str | None = None
    parse_failures = 0

    for step in range(1, max_steps + 1):
        steps = step
        req = _request(profile, messages)
        trace.emit(
            "llm_request",
            step=step,
            model=req.model,
            params={
                "temperature": req.temperature,
                "top_p": req.top_p,
                "max_tokens": req.max_tokens,
            },
            messages=[m.to_dict() for m in messages],
        )
        t0 = time.perf_counter()
        try:
            resp = client.chat(req)
        except LLMError as e:
            trace.emit("llm_error", step=step, error=str(e))
            print(f"LLM 调用失败：{e}")
            trace.close(status="llm_error", steps=step)
            return 1

        trace.emit(
            "llm_response",
            step=step,
            latency_ms=resp.latency_ms,
            completion_tokens=resp.usage.completion_tokens,
            prompt_tokens=resp.usage.prompt_tokens,
            finish_reason=resp.finish_reason,
            raw_text=resp.text,
        )
        if not quiet:
            print(f"\n── 第 {step}/{max_steps} 步 · {resp.latency_ms} ms · {resp.usage.completion_tokens} tok")

        truncated = (resp.finish_reason or "") == "length"
        decision, err = parse_decision(resp.text)
        if decision is None and truncated:
            err = "输出被 max_tokens 截断（finish_reason=length）"
            trace.emit("truncated", step=step, max_tokens=req.max_tokens, raw_text=resp.text)
        if decision is None:
            parse_failures += 1
            trace.emit("parse_error", step=step, error=err, raw_text=resp.text)
            if not quiet:
                print(f"  解析失败：{err[:120]}")
            if parse_failures > 2:
                trace.close(status="parse_failed", steps=step)
                print("连续解析失败，终止。")
                return 2
            messages.append(ChatMessage(role="assistant", content=resp.text))
            messages.append(
                ChatMessage(
                    role="user",
                    content='上一条不是合法 JSON（或被截断）。只输出一个 JSON 对象，不要解释、不要换行，thought 不超过 30 字。'
                    '例如 {"thought":"探根路径","action":"http_probe","args":{"url":"http://127.0.0.1:8080/"}}',
                )
            )
            continue

        parse_failures = 0
        thought = str(decision.get("thought") or "")[:400]
        action = str(decision.get("action") or "")
        args = decision.get("args") or {}
        if not quiet:
            print(f"  thought: {thought}")
            print(f"  action : {action}  args={json.dumps(args, ensure_ascii=False)[:200]}")
        trace.emit("decision", step=step, thought=thought, action=action, args=args, raw_text=resp.text)

        if action.upper() == "FINAL":
            answer = str(decision.get("answer") or "").strip()
            if tool_calls < min_tools:
                # 核验闸门：一次观测就下结论属于"看起来对"，不接受
                trace.emit(
                    "final_rejected",
                    step=step,
                    reason=f"tool_calls={tool_calls} < min_tools={min_tools}",
                    answer=answer,
                )
                if not quiet:
                    print(f"  FINAL 被拒：证据不足（已调用 {tool_calls} 次，至少需要 {min_tools} 次）")
                messages.append(ChatMessage(role="assistant", content=resp.text))
                messages.append(
                    ChatMessage(
                        role="user",
                        content=f"拒绝结束：你只做了 {tool_calls} 次观测，证据不足以支撑指纹结论。"
                        f"再调用至少一次工具（例如 port_scan 确认端口、或对 /robots.txt 等路径再探一次），然后重新给 FINAL。",
                    )
                )
                continue
            final_answer = answer
            trace.emit("final", step=step, answer=final_answer, tool_calls=tool_calls)
            if not quiet:
                print(f"\n结论：{final_answer}")
            break

        result = execute(action, args)
        tool_calls += 1
        trace.emit("tool_result", step=step, **result.to_trace())
        obs = result.to_model_text()
        if not quiet:
            state = "ok" if result.ok else "ERR"
            print(f"  → {action} {state} ({result.duration_ms} ms): {obs[:220]}")

        messages.append(ChatMessage(role="assistant", content=resp.text))
        messages.append(ChatMessage(role="user", content=observation_message(obs, step, max_steps)))

    converged = final_answer is not None
    trace.close(
        status="converged" if converged else "step_limit",
        steps=steps,
        tool_calls=tool_calls,
        final_answer=final_answer,
        trace_path=str(trace.path),
    )

    if not converged:
        print(f"\n达到步数上限（{max_steps}），未收敛。")

    print(f"\n步数 {steps} ｜ trace: {trace.path}")
    return 0 if converged else 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent.run", description="ReAct 安全智能体")
    ap.add_argument("task", help="任务描述")
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--profile", default=None, help="config/llm.yaml 里的档案名")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--min-tools", type=int, default=2, help="FINAL 前至少完成的工具调用次数")
    args = ap.parse_args(argv)
    return run(args.task, args.max_steps, args.profile, args.quiet, args.min_tools)


if __name__ == "__main__":
    raise SystemExit(main())
