"""ConversationService — 唯一会话记忆编排层

职责：
- 保存用户消息
- 读取最近历史
- 调用 agent.invoke(..., history=...)
- 提取回复文本
- 保存 assistant 回复
- 返回统一 ConversationEnvelope

禁止在 adapter / route 层重复实现上述流程。
"""

from __future__ import annotations

from typing import Any

from core.agent import MarketReActAgent
from memory.session_manager import MarketSessionManager
from services.assistant_orchestrator import AssistantOrchestrator
from services.envelope_builder import build_conversation_envelope
from core.planner import ResponsePlanner, summarize_history
from schemas.conversation import ConversationEnvelope


class ConversationService:
    """会话服务：统一编排记忆读写 + Agent 调用"""

    def __init__(
        self,
        agent: MarketReActAgent,
        session_manager: MarketSessionManager,
        planner: ResponsePlanner | None = None,
        orchestrator: AssistantOrchestrator | None = None,
    ) -> None:
        self.agent = agent
        self.session_manager = session_manager
        self.planner = planner or ResponsePlanner()
        self.orchestrator = orchestrator or AssistantOrchestrator(agent)

    async def run(
        self,
        *,
        text: str,
        session_id: str,
        history_limit: int = 8,
        invoke_fn: Any | None = None,
        extra_meta: dict[str, Any] | None = None,
    ) -> ConversationEnvelope:
        """
        执行一次带记忆的 Agent 调用。

        Args:
            invoke_fn: 可选的自定义调用函数（用于 chat 路径等）。
                       默认使用 self.agent.invoke。

        Returns:
            ConversationEnvelope: 统一展示协议。
        """
        # 1. 保存用户消息
        self.session_manager.save_user_message(session_id, text)

        # 2. 读取最近历史
        history = self.session_manager.get_recent_messages(
            session_id, limit=history_limit
        )

        # 3. 先规划用户真正要的回答形态，再执行。
        plan = await self.planner.plan(text, session_summary=summarize_history(history))
        result = await self.orchestrator.run(
            text=text,
            plan=plan,
            session_id=session_id,
            history=history,
            invoke_fn=invoke_fn,
        )

        # 4. 提取回复文本（统一处理多种可能字段）
        reply_text = self._extract_reply_text(result)

        # 5. 保存 assistant 回复（只有成功提取后才保存）
        if reply_text:
            self.session_manager.save_reply(session_id, reply_text)

        return build_conversation_envelope(
            result=result,
            reply_text=reply_text,
            session_id=session_id,
            user_text=text,
            plan=plan,
        )

    def _extract_reply_text(self, result: Any) -> str:
        """从 agent 返回结果中提取回复文本（兼容多种字段）"""
        if not isinstance(result, dict):
            return ""

        # 优先级顺序
        candidates = [
            "polished_text",
            "reply",
            "output_text",
            "text",
        ]

        for key in candidates:
            if key in result and result[key]:
                return str(result[key]).strip()

        # 尝试从 recommendation 中提取
        rec = result.get("recommendation") or {}
        if isinstance(rec, dict):
            for key in ["text", "polished_text", "reply"]:
                if key in rec and rec[key]:
                    return str(rec[key]).strip()

        # 兜底：尝试从 messages 中取最后一条 assistant 消息
        messages = result.get("messages") or []
        for msg in reversed(messages):
            if hasattr(msg, "content") and msg.content:
                return str(msg.content).strip()
            if isinstance(msg, dict) and msg.get("content"):
                return str(msg["content"]).strip()

        return ""
