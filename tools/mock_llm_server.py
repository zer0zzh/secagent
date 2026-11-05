"""本地假 LLM 服务：OpenAI 兼容，最小实现，仅用于阶段0 链路验证。

启动：python tools/mock_llm_server.py [--port 8770]
接口：GET /health   GET /v1/models   POST /v1/chat/completions
行为：读出 messages，按要求返回固定 JSON（便于验证解析与日志），不做任何模型推理。
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID = "mock-react-1"


def build_reply(payload: dict) -> dict:
    messages = payload.get("messages") or []
    last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    has_system = any(m.get("role") == "system" for m in messages)
    # 回一段固定结构的 ReAct 草稿，方便后续 agent 解析器对着它写
    content = json.dumps(
        {
            "thought": "mock 回环：确认已收到 system/user 消息，链路可用。",
            "observation": {
                "system_present": has_system,
                "message_count": len(messages),
                "last_user": last_user[:120],
                "model_requested": payload.get("model"),
                "temperature": payload.get("temperature"),
                "max_tokens": payload.get("max_tokens"),
            },
            "action": "FINAL",
            "answer": "PONG · mock-llm 链路正常（这是一个假响应，不代表模型能力）",
        },
        ensure_ascii=False,
    )
    prompt_chars = sum(len(m.get("content") or "") for m in messages)
    return {
        "id": f"mock-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": payload.get("model") or MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": max(1, prompt_chars // 3),
            "completion_tokens": max(1, len(content) // 3),
            "total_tokens": max(2, (prompt_chars + len(content)) // 3),
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "MockLLM/0.1"

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # 安静一点，只留一行
        print(f"[mock] {self.address_string()} {fmt % args}", flush=True)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok", "model": MODEL_ID, "ts": time.time()})
        elif self.path.rstrip("/") == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "local-mock"}]})
        else:
            self._send(404, {"error": {"message": f"unknown path {self.path}"}})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send(404, {"error": {"message": f"unknown path {self.path}"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, {"error": {"message": "invalid json"}})
            return
        if payload.get("stream"):
            self._send(400, {"error": {"message": "mock 不支持 stream，阶段0 只用非流式", "code": "request_stream_required"}})
            return
        if not payload.get("messages"):
            self._send(400, {"error": {"message": "messages is required"}})
            return
        self._send(200, build_reply(payload))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[mock] listening on http://{args.host}:{args.port}  (model={MODEL_ID})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock] bye", flush=True)
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
