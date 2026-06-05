"""MarketSessionManager — 市场分析会话管理器（组合 SessionManager + SnapshotManager）

替代旧的 MarketSessionManager，移除对已删除 app/ 模块的引用。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from config.runtime_config import get_analysis_config
from core.state import AnalysisSnapshot

from .session_state import SessionConfig, SessionState, load_session_config
from .session_store import SessionStateStore
from .json_persistence import JsonSessionPersistence
from .snapshot import SnapshotManager

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
        self._legacy_migrated: set[str] = set()
        self._probe_storage()

    def _probe_storage(self) -> None:
        """验证存储目录可写"""
        try:
            self.config.storage_dir.mkdir(parents=True, exist_ok=True)
            test_file = self.config.storage_dir / ".probe"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            self._io_ok = True
        except Exception as e:
            logger.warning(f"[Session] 存储目录不可写: {e}")
            self._io_ok = False

    def _get_lock(self, session_id: str) -> Any:
        with self._lock_guard:
            if session_id not in self._locks:
                self._locks[session_id] = __import__("threading").Lock()
            return self._locks[session_id]

    # ── 状态管理 ──

    def load_session(self, session_id: str) -> SessionState:
        """加载或创建 SessionState"""
        with self._get_lock(session_id):
            # 先从内存缓存取
            state = self.store.get(session_id)
            # 如果内存无数据，尝试从磁盘加载
            disk_state = self.persistence.load_state(session_id)
            if disk_state is not None:
                self.store.hydrate(session_id, disk_state)
                return disk_state
            return state

    def save_session(self, session_id: str, state: SessionState) -> None:
        """保存 SessionState"""
        with self._get_lock(session_id):
            self.store.update(state)
            if self._io_ok:
                self.persistence.save_state(session_id, state)

    # ── 对话历史 ──

    def append_message(
        self, session_id: str, role: str, text: str, **meta: Any
    ) -> None:
        """追加一条对话消息"""
        if self._io_ok:
            self.persistence.append_message(session_id, role, text, **meta)

    def get_recent_messages(
        self, session_id: str, *, limit: int = 8
    ) -> list[dict[str, str]]:
        """读取最近 N 条对话历史"""
        if not self._io_ok:
            return []
        return self.persistence.get_recent_messages(session_id, limit=limit)

    def get_full_history_for_compact(
        self, session_id: str
    ) -> list[dict[str, Any]]:
        """返回完整对话历史用于压缩"""
        if not self._io_ok:
            return []
        return self.persistence.get_full_history_for_compact(session_id)

    def save_compacted_summary(self, session_id: str, summary: str) -> None:
        """保存压缩后的对话摘要"""
        if self._io_ok:
            self.persistence.save_compacted_summary(session_id, summary)

    def truncate_history_keep_last(
        self, session_id: str, keep: int = 2000
    ) -> None:
        """裁剪历史，保留最近 N 条"""
        if self._io_ok:
            self.persistence.truncate_history_keep_last(session_id, keep=keep)

    def maybe_migrate_legacy_feishu(self, open_id: str) -> None:
        """（预留）迁移旧版飞书 JSONL 到新格式"""
        if open_id in self._legacy_migrated:
            return
        # 当前不做迁移，标记已处理即可
        self._legacy_migrated.add(open_id)


class MarketSessionManager:
    """市场分析会话管理器：组合 SessionManager + SnapshotManager

    对外保持与旧 MarketSessionManager 相同的接口，内部不再引用 app/* 模块。
    """

    def __init__(self, *, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._session_mgr: SessionManager | None = None
        self._snapshot_mgr: SnapshotManager = SnapshotManager()

    def _ensure_init(self) -> None:
        """延迟初始化 SessionManager"""
        if self._session_mgr is not None:
            return
        cfg = get_analysis_config()
        session_cfg = cfg.get("session") if isinstance(cfg.get("session"), dict) else {}
        self._session_mgr = SessionManager(session_cfg, repo_root=self.repo_root)

    # ── 对话历史 ──

    def get_recent_messages(
        self, session_id: str, *, limit: int = 8
    ) -> list[dict[str, str]]:
        """获取最近 N 条对话历史"""
        self._ensure_init()
        if self._session_mgr is None:
            return []
        return self._session_mgr.get_recent_messages(session_id, limit=limit)

    # ── 状态管理 ──

    def load_session(self, session_id: str) -> SessionState:
        """加载或创建 session state"""
        self._ensure_init()
        if self._session_mgr is None:
            return SessionState(open_id=session_id)
        return self._session_mgr.load_session(session_id)

    def save_session(self, session_id: str, state: SessionState) -> None:
        """保存 session state"""
        self._ensure_init()
        if self._session_mgr is None:
            return
        self._session_mgr.save_session(session_id, state)

    # ── Snapshot 管理 ──

    def save_snapshot(
        self,
        session_id: str,
        snapshot: dict[str, Any],
        output_refs: dict[str, str] | None = None,
    ) -> None:
        """将 snapshot 和 output_refs 持久化"""
        # 保存到 SnapshotManager（内存）
        self._snapshot_mgr.save_snapshot(session_id, snapshot_data=snapshot)

        # 同步到 SessionState
        if self._session_mgr is not None:
            state = self._session_mgr.load_session(session_id)
            state.last_facts_bundle = snapshot
            if output_refs:
                symbols = snapshot.get("symbol", [])
                state.last_symbols = (
                    list(symbols) if isinstance(symbols, list) else [symbols]
                )
                state.last_interval = snapshot.get("interval", "")
                state.last_provider = snapshot.get("provider", "")
            self._session_mgr.save_session(session_id, state)

    # ── 消息保存 ──

    def save_reply(self, session_id: str, reply: str) -> None:
        """保存 assistant 回复到对话历史"""
        self._ensure_init()
        if self._session_mgr is None:
            return
        self._session_mgr.append_message(session_id, "assistant", reply)

    def save_user_message(self, session_id: str, text: str) -> None:
        """保存用户消息到对话历史"""
        self._ensure_init()
        if self._session_mgr is None:
            return
        self._session_mgr.append_message(session_id, "user", text)

    # ── 追问解析 ──

    def resolve_followup(self, session_id: str, text: str) -> dict[str, Any]:
        """解析追问目标（返回上次分析的标的/周期等）"""
        self._ensure_init()
        if self._session_mgr is None:
            return {}
        return self._session_mgr.store.resolve_followup_target(session_id, text)