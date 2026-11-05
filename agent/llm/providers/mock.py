"""mock provider：真实走 HTTP，但目标是本地假服务，用来验证链路而不是模型能力。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from typing import TYPE_CHECKING

from ..base import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    LLMClient,
    LLMTransportError,
)

if TYPE_CHECKING:
    from ...config import LLMProfile


class MockClient(LLMClient):
    name = "mock"

    def __init__(self, profile: "LLMProfile") -> None:
        self.profile = profile

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.profile.model,
            "base_url": self.profile.base_url,
            "mode": "local-loopback",
        }

    def chat(self, req: ChatRequest) -> ChatResponse:
        url = self.profile.base_url.rstrip("/") + "/v1/chat/completions"
        body = json.dumps(req.to_openai_payload(), ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", **req.extra_headers}
        if self.profile.api_key:
            headers["Authorization"] = f"Bearer {self.profile.api_key}"

        started = time.perf_counter()
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.profile.params.timeout_s) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise LLMTransportError(f"HTTP {e.code} from {url}: {detail}") from e
        except urllib.error.URLError as e:
            raise LLMTransportError(
                f"连不上 {url}（{e.reason}）。先启动本地假服务：python tools/mock_llm_server.py"
            ) from e
        except TimeoutError as e:
            raise LLMTransportError(f"{url} 超时（{self.profile.params.timeout_s}s）") from e

        latency_ms = int((time.perf_counter() - started) * 1000)
        choice = (raw.get("choices") or [{}])[0]
        usage = raw.get("usage") or {}
        return ChatResponse(
            text=(choice.get("message") or {}).get("content", ""),
            model=raw.get("model") or req.model,
            provider=self.name,
            latency_ms=latency_ms,
            usage=ChatUsage(
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            ),
            finish_reason=choice.get("finish_reason"),
            raw=raw,
        )
