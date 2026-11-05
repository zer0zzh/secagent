"""命令行入口：配置查看 / 连通性探针 / 一键自检。

python -m agent.cli config show [--profile NAME] [--json] [--reveal]
python -m agent.cli payload [--profile NAME]      # 只打印将要发出的请求体，不发请求
python -m agent.cli probe   [--profile NAME]
python -m agent.cli selftest                      # 起本地假服务 → 探针 → 收尾
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from .config import CONFIG_PATH, PROJECT_ROOT, check_profile, load_config
from .llm import ChatMessage, ChatRequest, LLMError, LLMNotImplemented, create_client
from .llm.providers.remote import echo_request

RUNS_DIR = PROJECT_ROOT / "runs"


def _build_request(profile, prompt: str) -> ChatRequest:
    p = profile.params
    return ChatRequest(
        model=profile.model,
        messages=[
            ChatMessage(role="system", content="You are a precise assistant. Be terse. Answer in the user's language."),
            ChatMessage(role="user", content=prompt),
        ],
        temperature=p.temperature,
        top_p=p.top_p,
        max_tokens=min(p.max_tokens, 512),
        stop=p.stop,
        stream=p.stream,
        extra_headers=p.extra_headers,
        extra_body=p.extra_body,
    )


def cmd_config(args) -> int:
    cfg = load_config()
    if args.json:
        print(json.dumps(cfg.to_dict(reveal=args.reveal), ensure_ascii=False, indent=2))
        return 0
    print(f"配置来源        {cfg.source}")
    print(f"生效 profile    {cfg.active}   (可用: {', '.join(sorted(cfg.profiles))})")
    print()
    hdr = f"{'profile':<18}{'provider':<20}{'model':<24}{'base_url':<38}{'api_key':<18}{'状态'}"
    print(hdr)
    print("-" * len(hdr))
    for name, p in cfg.profiles.items():
        issues = check_profile(p)
        mark = "OK" if not issues else f"待填 {len(issues)} 项"
        key = p.api_key if args.reveal else ("<empty>" if not p.api_key else f"{p.api_key[:3]}***{p.api_key[-4:]}" if len(p.api_key) > 8 else "*" * len(p.api_key))
        print(f"{name:<18}{p.provider:<20}{(p.model or '<unset>'):<24}{(p.base_url or '<unset>'):<38}{key:<18}{mark}")
    print()
    target = cfg.profile(args.profile)
    print(f"[{target.name}] 参数：")
    for k, v in target.params.to_dict().items():
        print(f"  {k:<16}{v}")
    issues = check_profile(target)
    if issues:
        print("\n提示：")
        for i in issues:
            print(f"  - {i}")
    return 0


def cmd_payload(args) -> int:
    cfg = load_config()
    profile = cfg.profile(args.profile)
    req = _build_request(profile, "connectivity probe: reply PONG")
    print(json.dumps(echo_request(profile, req), ensure_ascii=False, indent=2))
    return 0


def cmd_probe(args) -> int:
    cfg = load_config()
    profile = cfg.profile(args.profile)
    issues = check_profile(profile)
    if issues:
        print("配置提示：")
        for i in issues:
            print(f"  - {i}")
        print()

    req = _build_request(profile, args.prompt or "connectivity probe: reply PONG")
    client = create_client(profile)
    print(f"探针 → {profile.name} ({profile.provider}) {profile.base_url or '<unset>'} model={profile.model or '<unset>'}")
    try:
        resp = client.chat(req)
    except LLMNotImplemented as e:
        print(f"\n未实现（阶段0 预期）：\n{e}")
        return 2
    except LLMError as e:
        print(f"\n失败：{e}")
        return 1

    RUNS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / f"probe-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "response.json").write_text(
        json.dumps(
            {
                "profile": profile.to_dict(),
                "request": req.to_openai_payload(),
                "response_text": resp.text,
                "latency_ms": resp.latency_ms,
                "usage": resp.usage.__dict__ if hasattr(resp.usage, "__dict__") else {},
                "raw": resp.raw,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\n状态        ok")
    print(f"耗时        {resp.latency_ms} ms")
    print(f"模型        {resp.model}")
    print(f"tokens      prompt={resp.usage.prompt_tokens} completion={resp.usage.completion_tokens} total={resp.usage.total_tokens}")
    print(f"finish      {resp.finish_reason}")
    print(f"回复        {resp.text[:400]}")
    print(f"落盘        {run_dir / 'response.json'}")
    return 0


def _wait_port(host: str, port: int, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def cmd_selftest(args) -> int:
    cfg = load_config()
    profile = cfg.profile("mock")
    host, port = "127.0.0.1", 8770
    server = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "tools" / "mock_llm_server.py"), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        if not _wait_port(host, port):
            print("本地假服务没起来，看 tools/mock_llm_server.py 的报错")
            return 1
        print("① 本地假服务已监听 127.0.0.1:8770")
        req = _build_request(profile, "connectivity probe: reply PONG")
        resp = create_client(profile).chat(req)
        print(f"② 链路打通：{resp.latency_ms} ms，tokens={resp.usage.total_tokens}")
        print(f"③ 响应片段：{resp.text[:200]}")
        print("\n自检通过：配置解析 → 请求构造 → HTTP 发送 → 响应解析 → 落盘，全线可用。")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("config", help="查看配置")
    c.add_argument("action", choices=["show"])
    c.add_argument("--profile")
    c.add_argument("--json", action="store_true")
    c.add_argument("--reveal", action="store_true", help="显示密钥明文（默认脱敏）")
    c.set_defaults(func=cmd_config)

    p = sub.add_parser("payload", help="打印将要发出的请求体（不发请求）")
    p.add_argument("--profile")
    p.set_defaults(func=cmd_payload)

    pr = sub.add_parser("probe", help="连通性探针")
    pr.add_argument("--profile")
    pr.add_argument("--prompt", help="自定义用户消息，默认是 PONG 探针")
    pr.set_defaults(func=cmd_probe)

    st = sub.add_parser("selftest", help="起假服务并自检")
    st.set_defaults(func=cmd_selftest)

    args = ap.parse_args(argv)
    args.profile = getattr(args, "profile", None)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
