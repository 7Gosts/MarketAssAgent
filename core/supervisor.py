"""MarketAssAgent — 输出守卫（免责声明、禁止口径、条件语气）。"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage

from core.guardrails import FORBIDDEN_CLAIMS

DISCLAIMER = "仅供技术分析与程序化演示，不构成投资建议。"

# 绝对表述 → 条件语气替换表
_CONDITIONAL_REPLACEMENTS = {
    "应该买入": "可考虑逢低关注",
    "应该卖出": "若触发失效可考虑离场",
    "建议开多": "若结构触发可考虑小仓试探",
    "建议开空": "若结构触发可考虑小仓试探",
    "必须止损": "建议严格设止损",
    "保证盈利": "无法保证盈利",
}


def _ensure_conditional_language(text: str) -> str:
    """将绝对表述替换为条件语气。"""
    for absolute, conditional in _CONDITIONAL_REPLACEMENTS.items():
        text = text.replace(absolute, conditional)
    return text


def _check_forbidden_claims(text: str) -> str:
    """移除禁止口径。"""
    for kw in FORBIDDEN_CLAIMS:
        if kw in text:
            text = text.replace(kw, f"[已移除不当表述:{kw}]")
    return text


def supervisor_node(state: dict[str, Any]) -> dict[str, Any]:
    """输出守卫节点：纯 Python，不做 LLM 调用。

    1. 检查禁止口径
    2. 条件语气替换
    3. 追加免责声明
    4. 设置 final_reply
    """
    messages = state.get("messages", [])
    reply = ""

    # 取最近一条 AIMessage 的文本内容
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str):
                reply = content.strip()
            elif isinstance(content, list):
                # 多部分内容，取文本部分
                parts = []
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        parts.append(part["text"])
                    elif isinstance(part, str):
                        parts.append(part)
                reply = "\n".join(parts).strip()
            break

    if not reply:
        reply = "我暂时无法回答，请稍后再试。"

    # 1. 禁止口径检查
    reply = _check_forbidden_claims(reply)

    # 2. 条件语气替换
    reply = _ensure_conditional_language(reply)

    # 3. 追加免责声明
    if DISCLAIMER not in reply:
        reply = reply.rstrip() + "\n" + DISCLAIMER

    return {
        "final_reply": reply,
        "has_disclaimer": True,
    }