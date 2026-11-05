"""provider 名 → 客户端实例。

mock              本地假服务（链路自检）
openai_compatible 本机 ollama / DeepSeek / 任意兼容网关（已实现，非流式）
workbuddy         浏览器 SDK 通道（阶段0 不实现，见 providers/remote.py）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import LLMClient, LLMNotImplemented
from .providers.mock import MockClient
from .providers.openai_compat import OpenAICompatClient
from .providers.remote import RemoteClient

if TYPE_CHECKING:  # 避免 config -> llm -> registry 的循环导入
    from ..config import LLMProfile

_REGISTRY = {
    "mock": MockClient,
    "openai_compatible": OpenAICompatClient,
    "workbuddy": RemoteClient,
}


def create_client(profile: "LLMProfile") -> LLMClient:
    try:
        factory = _REGISTRY[profile.provider]
    except KeyError as e:
        raise LLMNotImplemented(f"provider '{profile.provider}' 未注册") from e
    return factory(profile)
