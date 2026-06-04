from __future__ import annotations

from typing import Any, Optional
from .state import AgentState


def supervisor_node(state: AgentState) -> dict[str, Any]:
    """最终输出守卫 - 生成 recommendation 并尝试保存 Journal"""
    messages = state["messages"]
    last_content = ""
    if messages:
        last = messages[-1]
        last_content = getattr(last, "content", str(last))

    disclaimer = "仅供技术分析与程序化演示，不构成投资建议。"

    recommendation = {
        "text": last_content,
        "disclaimer": disclaimer,
        "timestamp": __import__("datetime").datetime.now().isoformat()
    }

    result: dict[str, Any] = {
        "messages": messages,
        "recommendation": recommendation,
        "next": "end"
    }

    # 如果 recommendation 中包含交易信息，尝试保存 Journal（可选扩展点）
    # 当前先返回 journal_id=None，后续可在 agent.py 中真正保存
    result["journal_id"] = None

    return result
