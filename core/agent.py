from __future__ import annotations

from typing import Any
from langchain_core.messages import HumanMessage
from .prompt import get_prompt
from tools.registry import get_all_tools


def call_model(state: dict[str, Any]) -> dict[str, Any]:
    """占位模型调用节点（后续接入 LLM + Tool-calling）。"""
    # 简单策略：若无 last_snapshot 则继续调用工具，否则结束
    if not state.get("last_snapshot"):
        return {"next": "continue"}
    return {"next": "end"}


from .graph import build_graph  # 延迟导入避免循环


class MarketReActAgent:
    def __init__(self):
        self.tools = get_all_tools()
        self.graph = build_graph(self.tools)
        self.prompt = get_prompt()

    async def invoke(self, user_input: str, session_id: str = "default"):
        """主入口"""
        messages = [HumanMessage(content=user_input)]
        
        initial_state = {
            "messages": messages,
            "session_id": session_id,
            "current_symbol": None,
            "current_interval": None,
            "last_snapshot": None,
        }
        
        result = await self.graph.ainvoke(initial_state)
        return result
