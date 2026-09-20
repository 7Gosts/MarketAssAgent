from __future__ import annotations

import hashlib
from typing import Any, Optional

from tools.registry import get_tool_registry
from .agent_loop import NativeAgentLoop
from .llm_client import create_llm_client_from_config
from .message_protocol import build_messages
from .message_protocol import Message
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
            raise TypeError("checkpointer/store 已移除，请通过 ConversationService 注入 LocalSessionEventStore")
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
        self.prompt_version = hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()[:16]

    async def invoke(
        self,
        user_input: str,
        session_id: str = "default",
        request_id: str = "",
        history: list[dict[str, str]] | None = None,
        allowed_tools: list[str] | None = None,
        context_messages: list[Message] | None = None,
        event_journal: Any | None = None,
        turn_user_event_id: str = "",
        checkpoint_event_id: str | None = None,
        evidence_index: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """主入口

        Args:
            user_input: 用户输入文本
            session_id: 会话标识
            history: 可选的对话历史 [{"role": "user"/"assistant", "text": "..."}, ...]
        """
        messages = list(context_messages or build_messages(
            system_prompt=self.prompt,
            history=history or [],
            user_input=user_input,
        ))

        initial_state = {
            "messages": messages,
            "session_id": session_id,
            "request_id": str(request_id or "").strip(),
            "metadata": {},
            "error": None,
            "allowed_tools": allowed_tools,
            "event_journal": event_journal,
            "turn_user_event_id": str(turn_user_event_id or ""),
            "prompt_version": self.prompt_version,
            "checkpoint_event_id": checkpoint_event_id,
            "evidence_index": dict(evidence_index or {}),
            "retrieval_receipts": [],
        }
        return await self.loop.run(initial_state)
