"""SessionStateStore — 内存 + JSON 文件持久化，线程安全"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from utils.logging_utils import get_logger
from utils.runtime_paths import get_sessions_dir
from .session_state import SessionState


logger = get_logger(__name__)


class SessionStateStore:
    """会话状态存储：内存缓存 + 可选 JSON 文件持久化，线程安全"""

    _DEFAULT_TTL_SEC: int = 1800

    def __init__(
        self,
        *,
        repo_root: Path,
        session_cfg: dict[str, Any] | None = None,
    ) -> None:
        self._states: dict[str, SessionState] = {}
        self._lock = threading.Lock()
        self._ttl_sec = int((session_cfg or {}).get("ttl_sec", self._DEFAULT_TTL_SEC))
        self._persist_dir = get_sessions_dir(repo_root=repo_root)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._load_from_disk()

    def get(self, open_id: str) -> SessionState:
        with self._lock:
            if open_id not in self._states:
                self._states[open_id] = SessionState(open_id=open_id)
            return self._states[open_id]

    def update(self, state: SessionState) -> None:
        with self._lock:
            state.updated_ts = time.time()
            self._states[state.open_id] = state
            self._save_state_to_disk(state)

    def hydrate(self, session_id: str, state: SessionState) -> None:
        with self._lock:
            self._states[session_id] = state
            self._save_state_to_disk(state)

    def _save_state_to_disk(self, state: SessionState) -> None:
        sid = state.open_id or "unknown"
        path = self._persist_dir / sid / f"{sid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(
                json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("[SessionStore] 保存失败 %s: %s", sid, e)

    def _load_from_disk(self) -> None:
        if not self._persist_dir.is_dir():
            return
        now = time.time()
        for session_dir in self._persist_dir.iterdir():
            if not session_dir.is_dir():
                continue
            state_file = session_dir / f"{session_dir.name}.json"
            if not state_file.is_file():
                continue
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                state = SessionState.from_dict(data)
                if now - state.updated_ts > self._ttl_sec:
                    continue
                self._states[state.open_id] = state
            except Exception:
                continue
