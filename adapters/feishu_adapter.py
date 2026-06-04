"""Feishu Adapter（占位）。

将飞书消息转发给 core MarketReActAgent 处理。
"""

from __future__ import annotations

from core.agent import MarketReActAgent


_agent = MarketReActAgent()


def handle_feishu_message(text: str, session_id: str) -> str:
    """处理飞书消息，返回最终回复文本。"""
    state = _agent.invoke(text, session_id=session_id)
    rec = state.get("recommendation") or {}
    return rec.get("text") or rec.get("disclaimer") or "分析完成。"
