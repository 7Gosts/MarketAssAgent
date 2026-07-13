from __future__ import annotations

from typing import Any, Optional
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

from config.runtime_config import get_llm_runtime_settings, require_llm_model, resolve_llm_temperature
from tools.registry import get_all_tools
from utils.logging_utils import get_logger
from .graph import build_graph
from .prompt import get_prompt


logger = get_logger(__name__)


def _create_llm_from_config() -> Any:
    """根据 runtime_config 创建 LLM 实例"""
    cfg = get_llm_runtime_settings()
    model = require_llm_model(cfg, context="Agent")
    base_url = cfg.get("base_url")
    api_key = cfg.get("api_key")
    temperature = resolve_llm_temperature(cfg, fallback=0.2)

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

    def __init__(
        self,
        llm: Optional[Any] = None,
        *,
        checkpointer: Any | None = None,
        store: Any | None = None,
    ):
        if llm is None:
            llm = _create_llm_from_config()
        self.llm = llm
        self.tools = get_all_tools()
        self.graph = build_graph(self.llm, checkpointer=checkpointer, store=store)
        self.prompt = get_prompt()

    async def invoke(
        self,
        user_input: str,
        session_id: str = "default",
        history: list[dict[str, str]] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        """主入口

        Args:
            user_input: 用户输入文本
            session_id: 会话标识
            history: 可选的对话历史 [{"role": "user"/"assistant", "text": "..."}, ...]
        """
        messages: list[Any] = []

        # 将历史消息转换为 LangChain Message 对象
        if history:
            for msg in history:
                if msg.get("role") == "user":
                    messages.append(HumanMessage(content=msg.get("text", "")))
                else:
                    messages.append(AIMessage(content=msg.get("text", "")))

        messages.append(HumanMessage(content=user_input))

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
            "allowed_tools": allowed_tools or [],
        }

        result = await self.graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": session_id}},
        )

        return result
