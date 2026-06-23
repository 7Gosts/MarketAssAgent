"""Web / API Adapter（薄封装，委托 ConversationService）"""

from __future__ import annotations

from interfaces.renderers.web_renderer import WebRenderer
from application.services.conversation_service import ConversationService
from schemas.conversation import ConversationEnvelope


class WebAdapter:
    """Web 入口适配器（薄封装，统一走 ConversationService）"""

    def __init__(self, conversation_service: ConversationService):
        self._conv_service = conversation_service
        self._renderer = WebRenderer()

    async def run(self, text: str, session_id: str = "web") -> ConversationEnvelope:
        """执行一次 Agent 调用，委托 ConversationService 编排记忆"""
        envelope = await self._conv_service.run(
            text=text,
            session_id=session_id,
            history_limit=8,
        )
        envelope.reply_text = self._renderer.render(envelope.reply_text)
        return envelope
