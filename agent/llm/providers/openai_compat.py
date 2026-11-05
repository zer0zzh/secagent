"""通用 OpenAI 兼容客户端（非流式）。

阶段0 只用它对接本机 ollama 做真实调用验证；阶段1 起 DeepSeek / vLLM / one-api
等同一协议的服务直接复用，不需要再写一份。
workbuddy 通道不在此列（浏览器 SDK 限定，见 providers/remote.py）。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from ..base import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    LLMClient,
    LLMConfigError,
    LLMTransportError,
)

if TYPE_CHECKING:
    from ...config import LLMProfile


class OpenAICompatClient(LLMClient):
    name = "openai_compatible"

    def __init__(self, profile: "LLMProfile") -> None:
        self.profile = profile
        if not profile.base_url:
            raise LLMConfigError(f"profile '{profile.name}' 缺少 base_url")

    # ---- 可单独复用的两个纯函数 ----
    def build_payload(self, req: ChatRequest) -> dict[str, Any]:
        return req.to_openai_payload()

    def build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.profile.api_key and self.profile.api_key not in {"ollama", "<empty>"}:
            headers["Authorization"] = f"Bearer {self.profile.api_key}"
        return headers

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.profile.model,
            "base_url": self.profile.base_url,
            "stream": self.profile.params.stream,
            "implemented": True,
        }

    @property
    def url(self) -> str:
        return self.profile.base_url.rstrip("/") + "/chat/completions"

    def chat(self, req: ChatRequest) -> ChatResponse:
        payload = self.build_payload(req)
        headers = {**self.build_headers(), **req.extra_headers}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        params = self.profile.params
        last_err: Exception | None = None
        for attempt in range(params.max_retries + 1):
            started = time.perf_counter()
            try:
                data = self._post(body, headers, params.timeout_s)
                return self._parse(data, req, int((time.perf_counter() - started) * 1000))
            except LLMTransportError as e:
                last_err = e
                if attempt >= params.max_retries or not getattr(e, "retryable", False):
                    raise
                time.sleep(params.retry_backoff_s ** (attempt + 1))
        raise last_err or LLMTransportError("unknown transport failure")

    def _post(self, body: bytes, headers: dict[str, str], timeout: float) -> dict[str, Any]:
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:600]
            err = LLMTransportError(f"HTTP {e.code} · {self.url}\n{detail}")
            err.retryable = e.code >= 500 or e.code == 429  # type: ignore[attr-defined]
            if e.code in (401, 403):
                raise LLMConfigError(
                    f"鉴权失败（HTTP {e.code}）：检查 {self.profile.name} 档案的 api_key\n{detail}"
                ) from e
            raise err from e
        except urllib.error.URLError as e:
            err = LLMTransportError(
                f"连不上 {self.url}（{e.reason}）。本机 ollama 请确认已启动：E:\\Ollama\\ollama.exe serve"
            )
            err.retryable = True  # type: ignore[attr-defined]
            raise err from e
        except TimeoutError as e:
            err = LLMTransportError(f"{self.url} 超时（{timeout}s）")
            err.retryable = True  # type: ignore[attr-defined]
            raise err from e

    def _parse(self, data: dict[str, Any], req: ChatRequest, latency_ms: int) -> ChatResponse:
        choices = data.get("choices") or []
        if not choices:
            raise LLMTransportError(f"响应里没有 choices：{json.dumps(data, ensure_ascii=False)[:300]}")
        msg = choices[0].get("message") or {}
        usage = data.get("usage") or {}
        return ChatResponse(
            text=msg.get("content") or "",
            model=data.get("model") or req.model,
            provider=self.profile.provider,
            latency_ms=latency_ms,
            usage=ChatUsage(
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            ),
            finish_reason=choices[0].get("finish_reason"),
            raw=data,
        )
