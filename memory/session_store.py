"""SessionStateStore — 内存 + JSON 文件持久化，线程安全"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from utils.logging_utils import get_logger
from .session_state import SessionState


logger = get_logger(__name__)


class SessionStateStore:
    """会话状态存储：内存缓存 + 可选 JSON 文件持久化，线程安全"""

    _DEFAULT_TTL_SEC: int = 1800  # 30 分钟

    def __init__(
        self,
        *,
        repo_root: Path,
        session_cfg: dict[str, Any] | None = None,
    ) -> None:
        cfg = session_cfg or {}
        self._states: dict[str, SessionState] = {}
        self._lock = threading.Lock()
        self._ttl_sec = int(cfg.get("ttl_sec", self._DEFAULT_TTL_SEC))
        self._persist_dir = repo_root / "sessions"
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._load_from_disk()

    # ── 公开方法 ──

    def get(self, open_id: str) -> SessionState:
        """获取或创建 SessionState"""
        with self._lock:
            if open_id not in self._states:
                self._states[open_id] = SessionState(open_id=open_id)
            return self._states[open_id]

    def update(self, state: SessionState) -> None:
        """更新 SessionState 并持久化"""
        with self._lock:
            state.updated_ts = time.time()
            self._states[state.open_id] = state
            self._save_state_to_disk(state)

    def hydrate(self, session_id: str, state: SessionState) -> None:
        """将外部状态注入到内存缓存"""
        with self._lock:
            self._states[session_id] = state
            self._save_state_to_disk(state)

    def update_from_route(self, open_id: str, **kwargs: Any) -> SessionState:
        """便捷方法：根据路由结果更新状态"""
        state = self.get(open_id)
        for k, v in kwargs.items():
            if hasattr(state, k) and v is not None:
                setattr(state, k, v)
        self.update(state)
        return state

    def record_error(self, open_id: str, **kwargs: Any) -> SessionState:
        """记录路由错误"""
        state = self.get(open_id)
        state.route_attempts += 1
        state.last_error_code = kwargs.get("error_code", "unknown")
        if "repair" in kwargs:
            state.repair_history.append(kwargs["repair"])
        self.update(state)
        return state

    def record_success(self, open_id: str, **kwargs: Any) -> SessionState:
        """记录路由成功"""
        state = self.get(open_id)
        state.route_attempts = 0
        state.last_error_code = None
        if "action" in kwargs:
            state.last_action = kwargs["action"]
        if "task_type" in kwargs:
            state.last_task_type = kwargs["task_type"]
        self.update(state)
        return state

    def record_final_termination(self, open_id: str, **kwargs: Any) -> SessionState:
        """记录终止原因"""
        state = self.get(open_id)
        state.termination_reason = kwargs.get("reason", "max_attempts")
        self.update(state)
        return state

    def reset_route_attempts(self, open_id: str) -> SessionState:
        """重置路由尝试计数"""
        state = self.get(open_id)
        state.route_attempts = 0
        state.last_error_code = None
        self.update(state)
        return state

    def resolve_followup_target(self, open_id: str, text: str) -> dict[str, Any]:
        """解析追问目标（依赖上次分析上下文）"""
        state = self.get(open_id)
        return {
            "symbol": state.last_symbol,
            "symbols": state.last_symbols,
            "interval": state.last_interval,
            "last_action": state.last_action,
        }

    # ── 持久化 ──

    def _save_state_to_disk(self, state: SessionState) -> None:
        """将单个状态保存为 JSON 文件"""
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
        """启动时从磁盘加载所有状态"""
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
                # 过期清理
                if now - state.updated_ts > self._ttl_sec:
                    continue
                self._states[state.open_id] = state
            except Exception:
                continue
