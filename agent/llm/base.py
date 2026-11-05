"""LLM 客户端公共数据结构与抽象。阶段0：只有形状，没有厂商逻辑。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


class LLMError(Exception):
    """所有 LLM 层异常的基类。"""


class LLMConfigError(LLMError):
    """配置缺失/非法。"""


class LLMNotImplemented(LLMError):
    """阶段0 的刻意断点：真实服务接入逻辑尚未实现。"""


class LLMTransportError(LLMError):
    """网络层失败（连接、超时、非 2xx）。"""


@dataclass(slots=True)
class ChatMessage:
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


@dataclass(slots=True)
class ChatRequest:
    """归一化请求：与厂商无关，provider 负责把它翻译成自家格式。"""

    model: str
    messages: list[ChatMessage]
    temperature: float = 0.2
    top_p: float = 0.95
    max_tokens: int = 2048
    stop: list[str] = field(default_factory=list)
    stream: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)

    def to_openai_payload(self) -> dict[str, Any]:
        """OpenAI 兼容请求体。厂商差异走 extra_body。"""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in self.messages],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "stream": self.stream,
        }
        if self.stop:
            payload["stop"] = self.stop
        if self.extra_body:
            payload.update(self.extra_body)
        return payload


@dataclass(slots=True)
class ChatUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(slots=True)
class ChatResponse:
    text: str
    model: str
    provider: str
    latency_ms: int
    usage: ChatUsage = field(default_factory=ChatUsage)
    finish_reason: str | None = None
    raw: dict[str, Any] | None = None
    started_at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return bool(self.text)


@runtime_checkable
class LLMClient(Protocol):
    """provider 必须实现的唯一方法。"""

    name: str

    def chat(self, req: ChatRequest) -> ChatResponse: ...

    def describe(self) -> dict[str, Any]: ...
