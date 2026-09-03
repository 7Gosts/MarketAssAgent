from __future__ import annotations

from typing import Any, Optional

from tools.registry import get_tool_registry
from .agent_loop import NativeAgentLoop
from .llm_client import create_llm_client_from_config
from .message_protocol import build_messages
from .prompt import get_system_prompt
from .tool_executor import ToolExecutor


def _create_llm_from_config() -> Any:
    """根据统一运行时配置创建 OpenAI-compatible 客户端。"""
    return create_llm_client_from_config()


class MarketReActAgent:
    """MarketReActAgent 主入口，支持通过配置切换 LLM 提供商"""

    def __init__(
        self,
        llm: Optional[Any] = None,
        *,
        max_steps: int = 8,
        checkpointer: Any | None = None,
        store: Any | None = None,
    ):
        if checkpointer is not None or store is not None:
            raise TypeError("checkpointer/store 已移除，请改用 MemoryAPI 或 session manager")
        if llm is None:
            llm = _create_llm_from_config()
        self.llm = llm
        self.registry = get_tool_registry()
        self.tools = self.registry.all()
        self.executor = ToolExecutor(self.registry)
        self.loop = NativeAgentLoop(
            llm=self.llm,
            registry=self.registry,
            executor=self.executor,
            max_steps=max_steps,
        )
        self.prompt = get_system_prompt()

    async def invoke(
        self,
        user_input: str,
        session_id: str = "default",
        request_id: str = "",
        history: list[dict[str, str]] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        """主入口

        Args:
            user_input: 用户输入文本
            session_id: 会话标识
            history: 可选的对话历史 [{"role": "user"/"assistant", "text": "..."}, ...]
        """
        messages = build_messages(
            system_prompt=self.prompt,
            history=history or [],
            user_input=user_input,
        )

        initial_state = {
            "messages": messages,
            "session_id": session_id,
            "request_id": str(request_id or "").strip(),
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
            "allowed_tools": allowed_tools,
        }
        return await self.loop.run(initial_state)
