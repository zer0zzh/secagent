"""ReAct 提示词与 JSON 决策解析。7B 模型对格式很敏感，这里把约束压到最短。"""
from __future__ import annotations

import json
import re
from typing import Any

from .tools.registry import tool_manifest

SYSTEM_PROMPT = """你是安全智能体，在授权靶场内做资产指纹识别。

每回合只输出一个 JSON 对象，单行，不要解释、前缀、Markdown，thought 不超过 30 字。

可用工具：
{tools}

两种合法输出：
1) 调用工具：{{"thought": "为什么做这一步", "action": "http_probe", "args": {{"url": "http://127.0.0.1:8080/"}}}}
2) 结束任务：{{"thought": "依据哪些证据", "action": "FINAL", "answer": "中文结论"}}

规则：
- 工具返回 error 不代表结束，换参数或换工具再试一次。
- 不编造未观测到的信息；证据不足就在 answer 里写明不足。
- 给出 FINAL 之前，至少完成 2 次工具调用，并且覆盖这三项里的至少两项：端口开放状态、HTTP 响应头（Server/X-Powered-By/Set-Cookie）、页面内容特征（标题/表单/路径）。
- 最多 {max_steps} 步，尽快收敛。
- answer 必须包含：目标、观测证据、结论。"""


def build_system_prompt(max_steps: int) -> str:
    return SYSTEM_PROMPT.format(tools=tool_manifest(), max_steps=max_steps)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _balanced_object(text: str) -> str | None:
    """从任意文本里抠出第一个括号平衡的 JSON 对象。"""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


def parse_decision(text: str) -> tuple[dict[str, Any] | None, str]:
    """返回 (decision, 错误说明)。decision 为 None 表示解析失败。"""
    raw = (text or "").strip()
    if not raw:
        return None, "输出为空"

    candidates: list[str] = []
    m = _FENCE.search(raw)
    if m:
        candidates.append(m.group(1).strip())
    candidates.append(raw)
    balanced = _balanced_object(raw)
    if balanced:
        candidates.append(balanced)

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "action" in obj:
            obj.setdefault("thought", "")
            if not isinstance(obj.get("args"), dict):
                obj["args"] = {}
            return obj, ""
        if isinstance(obj, dict) and "tool" in obj:  # 容错：{"tool": "...", "args": {...}}
            return {"thought": obj.get("thought", ""), "action": obj["tool"], "args": obj.get("args") or {}}, ""
    return None, f"不是合法 JSON 或缺少 action 字段：{raw[:200]}"


def observation_message(result_text: str, step: int, max_steps: int) -> str:
    return f"[工具返回 · 第{step}/{max_steps}步]\n{result_text}\n\n继续：只输出一个 JSON 对象。"
