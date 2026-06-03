"""MarketAssAgent — Session 管理（包装 app.session_manager）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.session_manager import SessionManager
from app.session_state import SessionState, SessionStateStore

logger = logging.getLogger(__name__)


class MarketSessionManager:
    """包装现有 SessionManager，增加 snapshot 持久化。"""

    def __init__(self, *, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._inner: SessionManager | None = None
        self._store: SessionStateStore | None = None

    def _ensure_init(self) -> None:
        if self._inner is not None:
            return
        from config.runtime_config import get_analysis_config
        cfg = get_analysis_config()
        session_cfg = cfg.get("session") if isinstance(cfg.get("session"), dict) else {}
        self._inner = SessionManager(session_cfg, repo_root=self.repo_root)
        self._store = SessionStateStore(repo_root=self.repo_root, session_cfg=session_cfg)

    def get_recent_messages(self, session_id: str, *, limit: int = 8) -> list[dict[str, str]]:
        """获取最近 N 条对话历史。"""
        self._ensure_init()
        if self._inner is None:
            return []
        return self._inner.get_recent_messages(session_id, limit=limit)

    def load_session(self, session_id: str) -> SessionState:
        """加载或创建 session state。"""
        self._ensure_init()
        if self._store is None:
            return SessionState(open_id=session_id)
        return self._store.load_or_create(session_id)

    def save_snapshot(self, session_id: str, snapshot: dict[str, Any], output_refs: dict[str, str] | None = None) -> None:
        """将 snapshot 和 output_refs 持久化到 session state。"""
        self._ensure_init()
        if self._store is None:
            return
        state = self._store.load_or_create(session_id)
        state.last_facts_bundle = snapshot
        if output_refs:
            # 注意：SessionState 没有 output_refs 字段，但 recent_analyses 可以存
            state.last_symbols = list(snapshot.get("symbol", [])) if isinstance(snapshot.get("symbol"), list) else [snapshot.get("symbol", "")]
            state.last_interval = snapshot.get("interval", "")
            state.last_provider = snapshot.get("provider", "")
        self._store.save(session_id, state)

    def save_reply(self, session_id: str, reply: str) -> None:
        """保存 assistant 回复到对话历史。"""
        self._ensure_init()
        if self._inner is None:
            return
        self._inner.save_message(session_id, role="assistant", text=reply)

    def save_user_message(self, session_id: str, text: str) -> None:
        """保存用户消息到对话历史。"""
        self._ensure_init()
        if self._inner is None:
            return
        self._inner.save_message(session_id, role="user", text=text)