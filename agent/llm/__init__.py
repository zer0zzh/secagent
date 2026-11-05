"""包初始化：导出常用类型，方便 `from agent.llm import ChatRequest`。"""
from .base import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    LLMClient,
    LLMConfigError,
    LLMError,
    LLMNotImplemented,
    LLMTransportError,
)
__all__ = [
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ChatUsage",
    "LLMClient",
    "LLMError",
    "LLMConfigError",
    "LLMNotImplemented",
    "LLMTransportError",
    "create_client",
]


def __getattr__(name: str):
    """惰性导出 create_client，避免 config 与 llm 包的循环导入。"""
    if name == "create_client":
        from .registry import create_client as _f

        return _f
    raise AttributeError(name)
