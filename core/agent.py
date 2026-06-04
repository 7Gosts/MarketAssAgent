from __future__ import annotations

from typing import Any, Optional
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from config.runtime_config import get_llm_runtime_settings
from tools.registry import get_all_tools
from .graph import build_graph
from .prompt import get_prompt


def _create_llm_from_config() -> Any:
    """根据 runtime_config 创建 LLM 实例"""
    cfg = get_llm_runtime_settings()

    provider = cfg.get("provider", "openai").lower()
    model = cfg.get("model") or "gpt-4o-mini"
    base_url = cfg.get("base_url")
    api_key = cfg.get("api_key")
    temperature = cfg.get("temperature") or 0.2

    # 目前统一使用 ChatOpenAI（支持 OpenAI-compatible）
    kwargs = {
        "model": model,
        "temperature": float(temperature),
    }
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key

    return ChatOpenAI(**kwargs)


class MarketReActAgent:
    """MarketReActAgent 主入口，支持通过配置切换 LLM 提供商"""

    def __init__(self, llm: Optional[Any] = None):
        if llm is None:
            llm = _create_llm_from_config()
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
