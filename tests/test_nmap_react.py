"""lab/nmap_react 的契约测试。

覆盖三层：授权闸门 / 参数白名单 / XML 解析 / 决策解析 / 证据核验。
全部离线，不启动 Nmap、不调用 LLM，跑一次不到 0.1 秒。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lab" / "nmap_react"))

import nmap_tool  # noqa: E402
import react_agent  # noqa: E402

MINIMAL_XML = """<?xml version="1.0"?>
<nmaprun scanner="nmap" version="7.40">
  <host>
    <status state="up"/>
    <address addr="127.0.0.1" addrtype="ipv4"/>
    <ports>
      <port protocol="tcp" portid="8080">
        <state state="open"/>
        <service name="http" product="SimpleHTTPServer" version="0.6" conf="10">
          <cpe>cpe:/a:python:simplehttpserver:0.6</cpe>
        </service>
      </port>
      <port protocol="tcp" portid="11434">
        <state state="open"/>
        <service name="unknown" conf="3"/>
      </port>
    </ports>
  </host>
</nmaprun>
"""


def evidence(open_ports=(8080, 11434), ok=True):
    return {"ok": ok, "open_ports": list(open_ports),
            "summary": "8080/http SimpleHTTPServer 0.6; 11434/unknown"}


class TestAuthorization(unittest.TestCase):
    def test_loopback_allowed(self):
        for t in ("127.0.0.1", "localhost", "::1"):
            self.assertEqual(nmap_tool.check_target(t), "127.0.0.1")

    def test_public_target_denied(self):
        for t in ("8.8.8.8", "scanme.nmap.org", "192.168.1.10",
                  "127.0.0.1; whoami", ""):
            with self.assertRaises(nmap_tool.TargetNotAllowed):
                nmap_tool.check_target(t)

    def test_explicit_lab_cidr(self):
        with patch.dict("os.environ", {"SECAGENT_LAB_CIDR": "10.10.10.0/24"}):
            self.assertEqual(nmap_tool.check_target("10.10.10.5"), "10.10.10.5")
            with self.assertRaises(nmap_tool.TargetNotAllowed):
                nmap_tool.check_target("10.10.11.5")

    def test_port_whitelist(self):
        for p in ("8080", "8080,11434", "80-90"):
            self.assertEqual(nmap_tool.check_ports(p), p)

    def test_port_injection_denied(self):
        for p in ("-p-", "1-65535", "8080;id", "80,abc", "8080 --script vuln", ""):
            with self.assertRaises(nmap_tool.BadPortSpec):
                nmap_tool.check_ports(p)


class TestXmlParsing(unittest.TestCase):
    def test_parse_fields(self):
        hosts = nmap_tool.parse_xml(MINIMAL_XML)
        self.assertEqual(hosts[0]["address"], "127.0.0.1")
        self.assertEqual(hosts[0]["state"], "up")
        p = hosts[0]["ports"][0]
        self.assertEqual((p["port"], p["state"], p["service"]), (8080, "open", "http"))
        self.assertEqual((p["product"], p["version"]), ("SimpleHTTPServer", "0.6"))
        self.assertIn("cpe:/a:python:simplehttpserver:0.6", p["cpe"])
        self.assertEqual(hosts[0]["ports"][1]["service"], "unknown")


class TestDecisionParsing(unittest.TestCase):
    def test_plain_json(self):
        d, err = react_agent.parse_decision('{"thought":"探端口","action":"FINAL","answer":"8080 开放"}')
        self.assertEqual(err, "")
        self.assertEqual(d["action"], "FINAL")

    def test_fenced_json(self):
        d, _ = react_agent.parse_decision('```json\n{"action":"nmap_scan","args":{}}\n```')
        self.assertEqual(d["action"], "nmap_scan")

    def test_broken_json(self):
        d, err = react_agent.parse_decision("我觉得应该先扫描一下")
        self.assertIsNone(d)
        self.assertIn("JSON", err)


class TestVerifier(unittest.TestCase):
    def test_valid_answer_passes(self):
        ok, _ = react_agent.verify(
            "依据 Nmap，8080 端口开放 HTTP 服务（SimpleHTTPServer 0.6），11434 端口服务未识别。",
            evidence())
        self.assertTrue(ok)

    def test_hallucinated_port_blocked(self):
        ok, reason = react_agent.verify("8080 与 9999 端口开放，9999 运行 Apache 2.4.49", evidence())
        self.assertFalse(ok)
        self.assertIn("9999", reason)

    def test_negation_blocked(self):
        ok, reason = react_agent.verify("扫描完成，未发现任何开放端口，目标不可达。", evidence())
        self.assertFalse(ok)
        self.assertIn("观测到", reason)

    def test_no_evidence_reference_blocked(self):
        ok, _ = react_agent.verify("已完成服务识别，Web 服务为 nginx，风险等级低。", evidence())
        self.assertFalse(ok)

    def test_exclusive_claim_blocked(self):
        ok, reason = react_agent.verify("8080 端口是唯一开放的端口。", evidence())
        self.assertFalse(ok)
        self.assertIn("11434", reason)

    def test_no_observation_blocked(self):
        ok, _ = react_agent.verify("8080 端口开放。", evidence(ok=False))
        self.assertFalse(ok)

    def test_empty_scan_allows_negative_conclusion(self):
        ok, _ = react_agent.verify("Nmap 未发现任何开放端口。", evidence(open_ports=()))
        self.assertTrue(ok)

    def test_empty_scan_rejects_fabrication(self):
        ok, _ = react_agent.verify("8080 端口开放 HTTP 服务。", evidence(open_ports=()))
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
