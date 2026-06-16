"""MarketSessionManager — 市场分析会话管理器"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from config.runtime_config import get_analysis_config

from .session_state import SessionConfig, SessionState, load_session_config
from .session_store import SessionStateStore
from .json_persistence import JsonSessionPersistence

logger = logging.getLogger(__name__)


class SessionManager:
    """会话管理器：组合状态存储 + 对话历史持久化，线程安全"""

    def __init__(
        self,
        session_cfg: dict[str, Any] | None = None,
        *,
        repo_root: Path,
    ) -> None:
        self.config = load_session_config(repo_root, session_cfg)
        self.store = SessionStateStore(repo_root=repo_root, session_cfg=session_cfg)
        self.persistence = JsonSessionPersistence(self.config.storage_dir)
        self._locks: dict[str, Any] = {}
        self._lock_guard = __import__("threading").Lock()
        self._io_ok: bool = True
        self._probe_storage()

    def _probe_storage(self) -> None:
        try:
            self.config.storage_dir.mkdir(parents=True, exist_ok=True)
            test_file = self.config.storage_dir / ".probe"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            self._io_ok = True
        except Exception as e:
            logger.warning("[Session] 存储目录不可写: %s", e)
            self._io_ok = False

    def _get_lock(self, session_id: str) -> Any:
        with self._lock_guard:
            if session_id not in self._locks:
                self._locks[session_id] = __import__("threading").Lock()
            return self._locks[session_id]

    def load_session(self, session_id: str) -> SessionState:
        with self._get_lock(session_id):
            state = self.store.get(session_id)
            disk_state = self.persistence.load_state(session_id)
            if disk_state is not None:
                self.store.hydrate(session_id, disk_state)
                return disk_state
            return state

    def save_session(self, session_id: str, state: SessionState) -> None:
        with self._get_lock(session_id):
            self.store.update(state)
            if self._io_ok:
                self.persistence.save_state(session_id, state)

    def append_message(self, session_id: str, role: str, text: str, **meta: Any) -> None:
        if self._io_ok:
            self.persistence.append_message(session_id, role, text, **meta)

    def get_recent_messages(self, session_id: str, *, limit: int = 8) -> list[dict[str, str]]:
        if not self._io_ok:
            return []
        return self.persistence.get_recent_messages(session_id, limit=limit)


class MarketSessionManager:
    """市场分析会话管理器：对话历史 + SessionState 持久化。"""

    def __init__(self, *, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._session_mgr: SessionManager | None = None

    def _ensure_init(self) -> None:
        if self._session_mgr is not None:
            return
        cfg = get_analysis_config()
        session_cfg = cfg.get("session") if isinstance(cfg.get("session"), dict) else {}
        self._session_mgr = SessionManager(session_cfg, repo_root=self.repo_root)

    def get_recent_messages(self, session_id: str, *, limit: int = 8) -> list[dict[str, str]]:
        self._ensure_init()
        if self._session_mgr is None:
            return []
        return self._session_mgr.get_recent_messages(session_id, limit=limit)

    def load_session(self, session_id: str) -> SessionState:
        self._ensure_init()
        if self._session_mgr is None:
            return SessionState(open_id=session_id)
        return self._session_mgr.load_session(session_id)

    def save_session(self, session_id: str, state: SessionState) -> None:
        self._ensure_init()
        if self._session_mgr is None:
            return
        self._session_mgr.save_session(session_id, state)

    def save_reply(self, session_id: str, reply: str) -> None:
        self._ensure_init()
        if self._session_mgr is None:
            return
        self._session_mgr.append_message(session_id, "assistant", reply)

    def save_user_message(self, session_id: str, text: str) -> None:
        self._ensure_init()
        if self._session_mgr is None:
            return
        self._session_mgr.append_message(session_id, "user", text)
