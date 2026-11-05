"""真实安全工具适配层：Nmap 7.40。

这一段是「接入一个真实安全工具」的全部内容。职责边界固定为四条：

1. 授权边界在这里硬校验，LLM 无法通过 prompt 越过；
2. 命令行模板固定，只接受两个白名单参数（target / ports），杜绝任意 flag 注入；
3. 把 Nmap 的 XML 输出解析成结构化 evidence，供上层做结果核验；
4. 超时、输出截断、原始 XML 留档，保证每次观测可复查。

只依赖 Python 标准库；不读数据库、不写业务结论。
"""
from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Any

# 固定命令模板。注意 --version-light / --host-timeout 是防挂死的关键，
# 详见 docs 里的「失败记录 2」。
FIXED_FLAGS = ["-sT", "-sV", "--version-light", "--host-timeout", "15s", "-Pn", "--open"]
PORT_RE = re.compile(r"^[0-9,\-]{1,120}$")
MAX_PORTS = 64
TIMEOUT_S = 40.0
MAX_XML_CHARS = 200_000

LOCAL_TARGETS = {"127.0.0.1", "localhost", "::1"}


class ToolError(Exception):
    """工具执行失败的统一基类。"""


class TargetNotAllowed(ToolError):
    """目标不在授权范围内。注意：抛错时 Nmap 尚未执行。"""


class BadPortSpec(ToolError):
    """端口参数不合法。"""


def nmap_exe() -> str:
    """解析 nmap 可执行文件位置。"""
    return (
        os.environ.get("SECAGENT_NMAP_EXE")
        or shutil.which("nmap")
        or r"D:\Nmap\nmap.exe"
    )


def check_target(target: str) -> str:
    """授权闸门：默认只允许本机回环；外部靶场必须显式声明网段。

    SECAGENT_LAB_CIDR 举例：10.10.10.0/24。没设置时，任何公网/内网地址一律拒绝。
    """
    t = (target or "").strip().lower()
    if t in LOCAL_TARGETS:
        return "127.0.0.1"

    cidr = os.environ.get("SECAGENT_LAB_CIDR", "").strip()
    if cidr:
        try:
            addr = ipaddress.ip_address(t)
            if addr in ipaddress.ip_network(cidr, strict=False):
                return str(addr)
        except ValueError:
            pass

    raise TargetNotAllowed(
        f"目标 {target!r} 不在授权范围：默认仅允许 127.0.0.1/localhost/::1；"
        "如为自建靶场，请显式设置 SECAGENT_LAB_CIDR 后再试"
    )


def check_ports(spec: str) -> str:
    """端口白名单：只允许 数字/逗号/连字符，展开后不超过 MAX_PORTS 个。

    顺带挡掉 `-p-`（全端口）这类可能把演示变成误扫的写法。
    """
    s = (spec or "").strip()
    if not PORT_RE.fullmatch(s):
        raise BadPortSpec(f"端口参数只接受数字、逗号、连字符，收到 {spec!r}")

    ports: list[int] = []
    for part in s.split(","):
        if not part:
            raise BadPortSpec("端口段为空")
        if "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise BadPortSpec(f"端口范围不合法：{part!r}")
            lo, hi = int(a), int(b)
            if lo > hi or hi - lo + 1 > MAX_PORTS:
                raise BadPortSpec(f"端口范围过大或倒置：{part!r}")
            ports.extend(range(lo, hi + 1))
        else:
            if not part.isdigit():
                raise BadPortSpec(f"端口不合法：{part!r}")
            ports.append(int(part))

    if not ports or len(ports) > MAX_PORTS:
        raise BadPortSpec(f"端口数量须在 1..{MAX_PORTS} 之间")
    if any(not (1 <= p <= 65535) for p in ports):
        raise BadPortSpec("端口须在 1..65535 之间")
    return s


def parse_xml(xml: str) -> list[dict[str, Any]]:
    """Nmap XML -> 结构化 evidence。只保留结论真正用得到的字段。"""
    # XML 头部带 DOCTYPE / stylesheet PI，ET 默认不加载外部 DTD，可安全解析。
    root = ET.fromstring(xml)
    hosts: list[dict[str, Any]] = []
    for h in root.findall("host"):
        addr_el = h.find("address")
        status_el = h.find("status")
        ports: list[dict[str, Any]] = []
        for po in h.findall("./ports/port"):
            state_el = po.find("state")
            sv = po.find("service")
            ports.append(
                {
                    "port": int(po.get("portid", "0")),
                    "protocol": po.get("protocol", "tcp"),
                    "state": state_el.get("state") if state_el is not None else "unknown",
                    "service": (sv.get("name") if sv is not None else None) or "unknown",
                    "product": sv.get("product") if sv is not None else None,
                    "version": sv.get("version") if sv is not None else None,
                    "conf": sv.get("conf") if sv is not None else None,
                    "cpe": [
                        c.text for c in (sv.findall("cpe") if sv is not None else []) if c.text
                    ],
                }
            )
        hosts.append(
            {
                "address": addr_el.get("addr") if addr_el is not None else None,
                "state": status_el.get("state") if status_el is not None else "unknown",
                "ports": ports,
            }
        )
    return hosts


def scan(target: str, ports: str) -> dict[str, Any]:
    """执行一次授权范围内的 Nmap 扫描，返回结构化证据。

    返回体即「工具契约」，上层 Agent 与核验闸门只认这些字段。
    """
    host = check_target(target)          # 闸门 1：目标必须先过授权
    port_spec = check_ports(ports)       # 闸门 2：参数必须先过白名单

    exe = nmap_exe()
    cmd = [exe, *FIXED_FLAGS, "-p", port_spec, "-oX", "-", host]
    started = time.perf_counter()
    try:
        # shell=False + list 参数：模型无法注入额外 flag 或命令。
        proc = subprocess.run(
            cmd, capture_output=True, timeout=TIMEOUT_S, shell=False, check=False
        )
    except subprocess.TimeoutExpired:
        return {
            "tool": "nmap_scan", "ok": False, "target": host, "ports": port_spec,
            "error": f"nmap 超过 {TIMEOUT_S}s 未返回，已终止",
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "hosts": [], "open_ports": [], "raw_xml": "",
        }
    except OSError as e:
        return {
            "tool": "nmap_scan", "ok": False, "target": host, "ports": port_spec,
            "error": f"无法启动 nmap：{type(e).__name__}: {e}",
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "hosts": [], "open_ports": [], "raw_xml": "",
        }

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    raw = proc.stdout.decode("utf-8", "replace")[:MAX_XML_CHARS]
    stderr = proc.stderr.decode("utf-8", "replace")[:400]

    try:
        hosts = parse_xml(raw)
    except ET.ParseError as e:
        return {
            "tool": "nmap_scan", "ok": False, "target": host, "ports": port_spec,
            "error": f"Nmap XML 解析失败：{e}", "stderr": stderr,
            "elapsed_ms": elapsed_ms, "hosts": [], "open_ports": [], "raw_xml": raw,
        }

    open_ports = sorted(
        {p["port"] for h in hosts for p in h["ports"] if p["state"] == "open"}
    )
    try:
        version = ET.fromstring(raw).get("version")
    except ET.ParseError:
        version = None
    services = [
        f"{p['port']}/{p['service']}"
        + (f" {p['product']}" if p.get("product") else "")
        + (f" {p['version']}" if p.get("version") else "")
        for h in hosts for p in h["ports"] if p["state"] == "open"
    ]
    return {
        "tool": "nmap_scan",
        "ok": True,
        "target": host,
        "ports": port_spec,
        "command": " ".join(cmd),
        "nmap_version": version,
        "elapsed_ms": elapsed_ms,
        "hosts": hosts,
        "open_ports": open_ports,
        "summary": "; ".join(services) if services else "未发现开放端口",
        "stderr": stderr,
        "raw_xml": raw,
        "error": None,
    }


if __name__ == "__main__":
    import json
    import sys

    tgt = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    prt = sys.argv[2] if len(sys.argv) > 2 else "8080,11434"
    r = scan(tgt, prt)
    r.pop("raw_xml", None)
    print(json.dumps(r, ensure_ascii=False, indent=2))
