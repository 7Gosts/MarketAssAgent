from __future__ import annotations

from typing import Any, Optional
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from .prompt import get_prompt
from tools.registry import get_all_tools
from .graph import build_graph


class MarketReActAgent:
    """MarketReActAgent 主入口，支持注入 LLM"""

    def __init__(self, llm: Optional[Any] = None):
        if llm is None:
            import os
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "未设置 OPENAI_API_KEY 环境变量，且未传入 llm 参数。\n"
                    "请在 .env 中配置 OPENAI_API_KEY，或显式传入 llm=ChatOpenAI(...)。"
                )
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
        self.llm = llm
        self.tools = get_all_tools()
        self.graph = build_graph(self.llm)
        self.prompt = get_prompt()

    async def invoke(self, user_input: str, session_id: str = "default") -> dict[str, Any]:
        """主入口"""
        messages = [HumanMessage(content=user_input)]

        initial_state = {
            "messages": messages,
            "session_id": session_id,
            "current_symbol": None,
            "current_interval": None,
            "last_snapshot": None,
            "analysis_result": None,
            "risk_assessment": None,
            "recommendation": None,
            "intent": None,
            "next": None,
            "metadata": {},
            "error": None,
        }

        result = await self.graph.ainvoke(initial_state)
        return result
