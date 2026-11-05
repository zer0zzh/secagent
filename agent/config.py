"""配置加载：YAML + .env + 环境变量覆盖 + 校验 + 脱敏。

阶段0 只做"把配置读准"，不建立任何网络连接。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .llm.base import LLMConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "llm.yaml"
ENV_PATH = PROJECT_ROOT / ".env"

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_SECRET_HINTS = ("key", "token", "secret", "password")

PROVIDERS = {"mock", "workbuddy", "openai_compatible"}

# 可由环境变量覆盖的字段：SECAGENT_LLM_<PROFILE>_<FIELD>
_OVERRIDABLE = {
    "MODEL": "model",
    "BASE_URL": "base_url",
    "API_KEY": "api_key",
    "TEMPERATURE": "temperature",
    "TOP_P": "top_p",
    "MAX_TOKENS": "max_tokens",
    "TIMEOUT_S": "timeout_s",
    "MAX_RETRIES": "max_retries",
    "STREAM": "stream",
}


def load_dotenv(path: Path = ENV_PATH) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE，支持 # 注释与引号。不覆盖已存在的环境变量。"""
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        loaded[k] = v
        os.environ.setdefault(k, v)
    return loaded


def expand(value: Any, env: dict[str, str] | None = None) -> Any:
    """展开 ${VAR} / ${VAR:-默认}。缺失且无默认时返回空串并记录（不抛错，阶段0 允许占位）。"""
    env = env if env is not None else os.environ
    if not isinstance(value, str):
        return value

    def repl(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        got = env.get(name)
        if got not in (None, ""):
            return got
        return default if default is not None else ""

    return _ENV_PATTERN.sub(repl, value)


def _expand_tree(node: Any) -> Any:
    if isinstance(node, str):
        return expand(node)
    if isinstance(node, dict):
        return {k: _expand_tree(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_expand_tree(v) for v in node]
    return node


def mask(value: str | None) -> str:
    """密钥脱敏：只露末 4 位。"""
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}***{value[-4:]}"


def is_secret(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in _SECRET_HINTS)


@dataclass(slots=True)
class RequestParams:
    """一次请求的可调参数。defaults 与 profile.params 合并后的结果。"""

    temperature: float = 0.2
    top_p: float = 0.95
    max_tokens: int = 2048
    stop: list[str] = field(default_factory=list)
    stream: bool = False
    timeout_s: float = 60
    max_retries: int = 2
    retry_backoff_s: float = 1.5
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RequestParams":
        known = {f for f in cls.__slots__}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        return cls(**clean)

    def to_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in self.__slots__}


@dataclass(slots=True)
class LLMProfile:
    name: str
    provider: str
    model: str
    base_url: str
    api_key: str
    runtime: str
    description: str
    params: RequestParams

    def to_dict(self, reveal: bool = False) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "model": self.model or "<unset>",
            "base_url": self.base_url or "<unset>",
            "api_key": self.api_key if reveal else mask(self.api_key),
            "runtime": self.runtime,
            "description": self.description,
            "params": self.params.to_dict(),
        }


@dataclass(slots=True)
class LLMConfig:
    version: int
    active: str
    defaults: RequestParams
    profiles: dict[str, LLMProfile]
    source: str

    def profile(self, name: str | None = None) -> LLMProfile:
        key = name or self.active
        if key not in self.profiles:
            raise LLMConfigError(
                f"profile '{key}' 不存在；可用：{', '.join(sorted(self.profiles))}"
            )
        return self.profiles[key]

    def to_dict(self, reveal: bool = False) -> dict[str, Any]:
        return {
            "source": self.source,
            "version": self.version,
            "active": self.active,
            "defaults": self.defaults.to_dict(),
            "profiles": {k: v.to_dict(reveal) for k, v in self.profiles.items()},
        }


def _apply_env_overrides(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """SECAGENT_LLM_<PROFILE>_<FIELD> 覆盖单个字段（优先级最高）。"""
    prefix = f"SECAGENT_LLM_{name.upper()}_"
    params = dict(data.get("params") or {})
    for env_key, value in os.environ.items():
        if not env_key.startswith(prefix) or not value:
            continue
        field_name = _OVERRIDABLE.get(env_key[len(prefix):])
        if not field_name:
            continue
        if field_name in {"temperature", "top_p"}:
            value = float(value)  # type: ignore[assignment]
        elif field_name in {"max_tokens", "max_retries"}:
            value = int(value)  # type: ignore[assignment]
        elif field_name == "timeout_s":
            value = float(value)  # type: ignore[assignment]
        elif field_name == "stream":
            value = str(value).lower() in {"1", "true", "yes"}  # type: ignore[assignment]
        if field_name in RequestParams.__slots__ and field_name not in {"model", "base_url", "api_key"}:
            params[field_name] = value
        else:
            data[field_name] = value
    data["params"] = params
    return data


def load_config(path: Path = CONFIG_PATH, *, dotenv: bool = True) -> LLMConfig:
    if dotenv:
        load_dotenv()
    if not path.exists():
        raise LLMConfigError(f"配置文件不存在：{path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = _expand_tree(raw)

    active = os.environ.get("SECAGENT_PROFILE") or raw.get("active") or "mock"
    defaults = RequestParams.from_dict(raw.get("defaults") or {})

    profiles: dict[str, LLMProfile] = {}
    for name, cfg in (raw.get("profiles") or {}).items():
        cfg = _apply_env_overrides(name, dict(cfg or {}))
        provider = (cfg.get("provider") or "").strip()
        if provider not in PROVIDERS:
            raise LLMConfigError(f"profile '{name}' 的 provider '{provider}' 非法；可选：{sorted(PROVIDERS)}")

        merged = defaults.to_dict()
        merged.update(cfg.get("params") or {})
        params = RequestParams.from_dict(merged)

        profiles[name] = LLMProfile(
            name=name,
            provider=provider,
            model=(cfg.get("model") or "").strip(),
            base_url=(cfg.get("base_url") or "").strip(),
            api_key=(cfg.get("api_key") or "").strip(),
            runtime=(cfg.get("runtime") or "native").strip(),
            description=(cfg.get("description") or "").strip(),
            params=params,
        )

    config = LLMConfig(
        version=int(raw.get("version") or 1),
        active=str(active),
        defaults=defaults,
        profiles=profiles,
        source=str(path),
    )
    config.profile()  # 提前暴露 active 不存在的问题
    return config


def check_profile(p: LLMProfile) -> list[str]:
    """返回问题清单（空列表 = 可用）。阶段0 不阻断，只提示。"""
    issues: list[str] = []
    if not p.base_url:
        issues.append("base_url 为空：需要填服务地址或 ${ENV}")
    if p.provider != "mock" and not p.api_key:
        issues.append("api_key 为空：在 .env 里填对应变量")
    if not p.model:
        issues.append("model 为空：需要指定模型名")
    if p.runtime in {"browser-sdk", "miniprogram"}:
        issues.append(f"runtime={p.runtime}：该通道只允许在浏览器/小程序环境调用，CLI 直连会被 Origin 校验拒绝")
    return issues


if __name__ == "__main__":  # 便于 python agent/config.py 直接看结果
    print(json.dumps(load_config().to_dict(), ensure_ascii=False, indent=2))
