"""MarketAssAgent — Session 管理（包装 app.session_manager）。

过渡层说明：
- 本模块包装 app/session_manager 和 app/session_state，对外提供统一 API
- # TODO(legacy): 迁移 SessionState 到 memory/ 后消除对 app/ 的依赖
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

# # TODO(legacy): 迁移 SessionState 到 memory/ 后改为本地导入
from app.session_manager import SessionManager
from app.session_state import SessionState, SessionStateStore  # noqa: F401

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
        """将 snapshot 和 output_refs 持久化到 session state。

        修复：无论 output_refs 是否存在，都保存 snapshot 的 symbol/interval/provider。
        """
        self._ensure_init()
        if self._store is None:
            return

        state = self._store.load_or_create(session_id)
        state.last_facts_bundle = snapshot

        # 无论 output_refs 是否存在都保存 snapshot 基本字段
        sym = snapshot.get("symbol")
        if sym:
            # 正确处理：字符串包裹为列表，列表直接赋值
            state.last_symbols = [sym] if isinstance(sym, str) else list(sym)
        if snapshot.get("interval"):
            state.last_interval = snapshot["interval"]
        if snapshot.get("provider"):
            state.last_provider = snapshot["provider"]

        # output_refs 独立保存（SessionState 有 last_output_refs 字段）
        if output_refs:
            state.last_output_refs = output_refs

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