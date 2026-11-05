"""真实服务的占位实现：阶段0 只准备请求体，不发请求。

要接真实 LLM 时，只需把 chat() 里那段 raise 换成：
    1) 按 build_payload() 组装请求
    2) 用 urllib/httpx/openai-sdk 发出
    3) 把响应映射成 ChatResponse（流式则逐块累积 content / reasoning_content）
其余代码（配置、CLI、registry、日志）都不用动。
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from ..base import (
    ChatRequest,
    ChatResponse,
    LLMClient,
    LLMNotImplemented,
)

if TYPE_CHECKING:
    from ...config import LLMProfile


class RemoteClient(LLMClient):
    """workbuddy / openai_compatible 共用。阶段0 不实现网络调用。"""

    name = "remote"

    def __init__(self, profile: "LLMProfile") -> None:
        self.profile = profile

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.profile.provider,
            "model": self.profile.model,
            "base_url": self.profile.base_url,
            "runtime": self.profile.runtime,
            "implemented": False,
        }

    def build_payload(self, req: ChatRequest) -> dict[str, Any]:
        """已实现：归一化请求 → OpenAI 兼容请求体。可供单测/接入时直接复用。"""
        return req.to_openai_payload()

    def build_headers(self) -> dict[str, str]:
        """已实现：鉴权头。workbuddy 通道用 publishableKey，其余用 Bearer。"""
        headers = {"Content-Type": "application/json"}
        if self.profile.provider == "workbuddy":
            headers["X-Publishable-Key"] = self.profile.api_key
        elif self.profile.api_key:
            headers["Authorization"] = f"Bearer {self.profile.api_key}"
        return headers

    def chat(self, req: ChatRequest) -> ChatResponse:
        raise LLMNotImplemented(
            f"[阶段0] provider={self.profile.provider} 的接入逻辑尚未实现。\n"
            f"  已就绪：payload={list(self.build_payload(req))}，headers={list(self.build_headers())}\n"
            f"  下一步：实现 agent/llm/providers/remote.py::chat()，或把 active 切回 mock 继续验证链路。"
        )


def echo_request(profile: "LLMProfile", req: ChatRequest) -> dict[str, Any]:
    """调试用：不发请求，只回显将要发出什么（不含密钥明文）。"""
    client = RemoteClient(profile)
    return {
        "provider": profile.provider,
        "url": (profile.base_url or "<unset>").rstrip("/") + "/chat/completions",
        "headers": sorted(client.build_headers()),
        "payload": client.build_payload(req),
        "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
