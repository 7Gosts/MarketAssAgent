from __future__ import annotations

from typing import Any, Optional
from .state import AgentState


def supervisor_node(state: AgentState) -> dict[str, Any]:
    """最终输出守卫 - 生成 recommendation。"""
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

    # 兼容旧响应字段；正式交易记录必须走显式交易写入路径。
    result["journal_id"] = None

    return result
