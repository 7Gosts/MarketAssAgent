"""Web / API Adapter"""

from __future__ import annotations

from typing import Any, Dict
from core.agent import MarketReActAgent


class WebAdapter:
    """Web 入口适配器"""

    def __init__(self, agent: MarketReActAgent):
        self.agent = agent

    async def run(self, text: str, session_id: str = "web") -> Dict[str, Any]:
        """执行一次 Agent 调用，返回完整结果"""
        return await self.agent.invoke(text, session_id=session_id)
