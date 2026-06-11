"""Web / API Adapter"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from core.agent import MarketReActAgent
from memory.session_manager import MarketSessionManager


class WebAdapter:
    """Web 入口适配器（统一走 SessionManager 记忆层）"""

    def __init__(self, agent: MarketReActAgent, *, repo_root: Path | None = None):
        self.agent = agent
        root = repo_root or Path(__file__).resolve().parents[2]
        self._session_mgr = MarketSessionManager(repo_root=root)

    async def run(self, text: str, session_id: str = "web") -> Dict[str, Any]:
        """执行一次 Agent 调用，带记忆注入与保存"""
        # 1. 保存用户消息
        self._session_mgr.save_user_message(session_id, text)

        # 2. 读取最近历史
        history = self._session_mgr.get_recent_messages(session_id, limit=8)

        # 3. 调用 Agent（注入 history）
        result = await self.agent.invoke(text, session_id=session_id, history=history)

        # 4. 保存 assistant 回复
        reply_text = ""
        if isinstance(result, dict):
            rec = result.get("recommendation") or {}
            reply_text = rec.get("text", "") or result.get("reply", "")
        if reply_text:
            self._session_mgr.save_reply(session_id, reply_text)

        return result
