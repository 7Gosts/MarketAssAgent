"""ConversationService — 唯一会话记忆编排层

职责：
- 保存用户消息
- 读取最近历史
- 调用 agent.invoke(..., history=...)
- 提取回复文本
- 保存 assistant 回复
- 返回统一结果

禁止在 adapter / route 层重复实现上述流程。
"""

from __future__ import annotations

from typing import Any

from core.agent import MarketReActAgent
from memory.session_manager import MarketSessionManager


class ConversationService:
    """会话服务：统一编排记忆读写 + Agent 调用"""

    def __init__(
        self,
        agent: MarketReActAgent,
        session_manager: MarketSessionManager,
    ) -> None:
        self.agent = agent
        self.session_manager = session_manager

    async def run(
        self,
        *,
        text: str,
        session_id: str,
        history_limit: int = 8,
        extra_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        执行一次带记忆的 Agent 调用。

        Returns:
            {
                "result": agent 返回的完整结果,
                "reply_text": 提取后的回复文本,
                "session_id": session_id,
            }
        """
        # 1. 保存用户消息
        self.session_manager.save_user_message(session_id, text)

        # 2. 读取最近历史
        history = self.session_manager.get_recent_messages(
            session_id, limit=history_limit
        )

        # 3. 调用 Agent
        result = await self.agent.invoke(
            text, session_id=session_id, history=history
        )

        # 4. 提取回复文本（统一处理多种可能字段）
        reply_text = self._extract_reply_text(result)

        # 5. 保存 assistant 回复（只有成功提取后才保存）
        if reply_text:
            self.session_manager.save_reply(session_id, reply_text)

        return {
            "result": result,
            "reply_text": reply_text,
            "session_id": session_id,
        }

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