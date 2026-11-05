"""工具层：白名单、参数校验、超时、输出截断。

设计原则：模型只能看到工具名与参数，能不能执行由这里的闸门决定，不由模型决定。
"""
from __future__ import annotations

import ipaddress
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_OUTPUT_CHARS = 4000
DEFAULT_TOOL_TIMEOUT = 8.0

# 危险模式：出现即拒绝，不执行
_FORBIDDEN_SNIPPETS = (
    "file://",
    "gopher://",
    "dict://",
    "ftp://",
    ";",
    "|",
    "&&",
    "$(",
    "`",
    "rm -rf",
    "curl ",
    "wget ",
)


class ToolDenied(Exception):
    """被闸门拦下。"""


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    args_schema: dict[str, Any]
    handler: Callable[..., dict[str, Any]]
    timeout_s: float = DEFAULT_TOOL_TIMEOUT


@dataclass(slots=True)
class ToolResult:
    ok: bool
    name: str
    duration_ms: int
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_model_text(self) -> str:
        if self.ok:
            body = self.data
        else:
            body = {"error": self.error}
        import json

        text = json.dumps(body, ensure_ascii=False, default=str)
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + "…(truncated)"
        return text

    def to_trace(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.name,
            "duration_ms": self.duration_ms,
            "data": self.data if self.ok else None,
            "error": self.error,
        }


# ---------------------------------------------------------------- allowlist
def host_allowed(host: str) -> tuple[bool, str]:
    """只允许本机 / 内网地址。默认拒绝一切公网目标。"""
    host = (host or "").strip().strip("[]").lower()
    if not host:
        return False, "host 为空"
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return True, ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False, f"域名 '{host}' 不在白名单：只允许 IP、localhost、*.local"
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return True, ""
    return False, f"{host} 是公网地址，被 allowlist 拒绝（只允许本机/内网靶场）"


def _url_target(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ToolDenied(f"只允许 http/https，收到 '{parsed.scheme}'")
    host = parsed.hostname or ""
    ok, why = host_allowed(host)
    if not ok:
        raise ToolDenied(why)
    return parsed.scheme, host


def _check_raw(args: dict[str, Any]) -> None:
    blob = " ".join(str(v) for v in args.values()).lower()
    for bad in _FORBIDDEN_SNIPPETS:
        if bad in blob:
            raise ToolDenied(f"参数命中危险模式 '{bad}'，已拒绝执行")


# ---------------------------------------------------------------- 工具实现
def http_probe(url: str, timeout: float = 6.0, max_body: int = 1200) -> dict[str, Any]:
    _url_target(url)
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "secagent/0.1 (authorized-lab-only)"},
    )
    # 关键：绕过系统代理。Git Bash / WorkBuddy 的 http_proxy 会让 127.0.0.1 的请求
    # 走代理，代理返回 502，看起来像"目标挂了"，其实是观测失真。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.perf_counter()
    try:
        with opener.open(req, timeout=min(timeout, DEFAULT_TOOL_TIMEOUT)) as resp:
            body = resp.read(max_body)
            charset = resp.headers.get_content_charset() or "utf-8"
            text = body.decode(charset, "replace")
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return {
                "url": url,
                "status": resp.status,
                "reason": resp.reason,
                "server": headers.get("server", ""),
                "powered_by": headers.get("x-powered-by", ""),
                "content_type": headers.get("content-type", ""),
                "content_length": headers.get("content-length", ""),
                "set_cookie": bool(headers.get("set-cookie")),
                "redirect": headers.get("location", ""),
                "headers": dict(list(headers.items())[:20]),
                "title": _extract_title(text),
                "body_head": text[:600],
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
    except urllib.error.HTTPError as e:
        # 4xx/5xx 也是有效指纹，不当异常抛
        return {
            "url": url,
            "status": e.code,
            "reason": e.reason,
            "server": e.headers.get("server", "") if e.headers else "",
            "headers": dict(list((e.headers or {}).items())[:20]),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "note": "服务端返回错误码，属有效响应",
        }
    except urllib.error.URLError as e:
        # 连不上也是证据：结构化返回，交给模型判断，不抛异常打断循环
        reason = getattr(e, "reason", e)
        reason_text = f"{type(reason).__name__}: {reason}"
        if "CERTIFICATE_VERIFY_FAILED" in reason_text or "SSL" in reason_text.upper():
            # TLS 失败 ≠ 端口关闭：TCP 已经连上了
            return {
                "url": url,
                "reachable": True,
                "tls_error": True,
                "error": reason_text,
                "hint": "TCP 已连通，TLS 握手失败（多半是自签名证书）。该端口是开放的，服务是 HTTPS",
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
        return {
            "url": url,
            "reachable": False,
            "error": reason_text,
            "hint": "TCP 层没连上：端口未开放或服务未启动。可先用 port_scan 确认端口状态",
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }


def _extract_title(html: str) -> str:
    low = html.lower()
    start = low.find("<title")
    if start == -1:
        return ""
    gt = low.find(">", start)
    end = low.find("</title>", gt)
    if gt == -1 or end == -1:
        return ""
    return html[gt + 1 : end].strip()[:120]


def port_scan(host: str, ports: list[int] | str = (80, 443, 8000, 8080, 8443)) -> dict[str, Any]:
    ok, why = host_allowed(host)
    if not ok:
        raise ToolDenied(why)
    if isinstance(ports, str):
        ports = [int(p) for p in ports.replace(" ", "").split(",") if p]
    if len(ports) > 32:
        raise ToolDenied("一次最多扫描 32 个端口")
    results = []
    for port in ports:
        port = int(port)
        if not 1 <= port <= 65535:
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.2)
        t0 = time.perf_counter()
        try:
            sock.connect((host, port))
            banner = ""
            try:
                sock.settimeout(0.8)
                sock.sendall(f"HEAD / HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
                banner = sock.recv(200).decode("latin-1", "replace").split("\r\n")[0]
            except OSError:
                banner = ""
            results.append(
                {"port": port, "state": "open", "banner": banner, "connect_ms": int((time.perf_counter() - t0) * 1000)}
            )
        except OSError:
            results.append({"port": port, "state": "closed"})
        finally:
            sock.close()
    return {"host": host, "scanned": len(results), "ports": results, "open": [r["port"] for r in results if r["state"] == "open"]}


TOOLS: dict[str, ToolSpec] = {
    "http_probe": ToolSpec(
        name="http_probe",
        description="对单个 URL 发 GET，返回状态码、响应头、Server、标题、正文片段",
        args_schema={"url": "string (必填)", "timeout": "number (可选)"},
        handler=http_probe,
    ),
    "port_scan": ToolSpec(
        name="port_scan",
        description="TCP 连接探测端口开放情况，可带回 HTTP 首行横幅",
        args_schema={"host": "string (必填)", "ports": "int[] (可选)"},
        handler=port_scan,
    ),
}


def tool_manifest() -> str:
    lines = []
    for spec in TOOLS.values():
        args = ", ".join(f'"{k}": {v}' for k, v in spec.args_schema.items())
        lines.append(f'- {spec.name}: {{{args}}}  # {spec.description}')
    return "\n".join(lines)


def execute(name: str, args: dict[str, Any]) -> ToolResult:
    t0 = time.perf_counter()
    spec = TOOLS.get(name)
    if spec is None:
        return ToolResult(False, name, 0, error=f"未知工具 '{name}'，可用：{list(TOOLS)}")
    if not isinstance(args, dict):
        return ToolResult(False, name, 0, error="args 必须是 JSON 对象")
    try:
        _check_raw(args)
        data = spec.handler(**args)
        return ToolResult(True, name, int((time.perf_counter() - t0) * 1000), data=data)
    except ToolDenied as e:
        return ToolResult(False, name, int((time.perf_counter() - t0) * 1000), error=f"被闸门拒绝：{e}")
    except TypeError as e:
        return ToolResult(False, name, int((time.perf_counter() - t0) * 1000), error=f"参数不匹配：{e}")
    except Exception as e:  # 工具自身异常不能打断循环
        return ToolResult(False, name, int((time.perf_counter() - t0) * 1000), error=f"{type(e).__name__}: {e}")
