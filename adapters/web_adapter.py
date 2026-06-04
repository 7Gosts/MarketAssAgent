"""Web / API Adapter（占位）。

HTTP 入口调用 core MarketReActAgent。
"""

from __future__ import annotations

from core.agent import MarketReActAgent


_agent = MarketReActAgent()


def run_agent(text: str, session_id: str = "web") -> dict:
    """返回完整 state，供 FastAPI 返回。"""
    return _agent.invoke(text, session_id=session_id)
