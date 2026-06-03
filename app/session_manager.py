"""会话持久化：组合 SessionStateStore（业务）与 JsonSessionPersistence（IO）。

SessionManager 只负责 load/save/append/get_recent/compact 辅助；业务状态逻辑仍在 SessionStateStore。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from app.session_state import SessionState, SessionStateStore


@dataclass(frozen=True)
class SessionConfig:
    enabled: bool = True
    storage_dir: Path = Path("sessions")
    auto_migrate_feishu: bool = True
    compact_enabled: bool = True
    history_max_messages: int = 2000
    history_days: int = 30
    compact_threshold: int = 24
    llm_memory_rounds: int = 4
    legacy_feishu_memory_file: Path = Path("output/feishu_memory.jsonl")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_session_config(*, repo_root: Path | None = None) -> SessionConfig:
    from config.runtime_config import get_analysis_config

    root = repo_root or _repo_root()
    cfg = get_analysis_config()
    node = cfg.get("session") if isinstance(cfg.get("session"), dict) else {}
    feishu = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    memory_node = feishu.get("memory") if isinstance(feishu.get("memory"), dict) else {}

    env_dir = os.getenv("SESSION_STORAGE_DIR", "").strip()
    raw_dir = env_dir or str(node.get("storage_dir") or "sessions").strip()
    storage_dir = Path(raw_dir).expanduser()
    if not storage_dir.is_absolute():
        storage_dir = (root / storage_dir).resolve()

    rounds_src = node.get("llm_memory_rounds")
    if rounds_src is None and isinstance(agent, dict) and "router_context_rounds" in agent:
        rounds_src = agent.get("router_context_rounds")
    if rounds_src is None:
        rounds_src = feishu.get("llm_memory_rounds")

    legacy_raw = str(memory_node.get("memory_file") or "output/feishu_memory.jsonl").strip()
    legacy_path = Path(legacy_raw).expanduser()
    if not legacy_path.is_absolute():
        legacy_path = (root / legacy_path).resolve()

    def _bool(v: Any, default: bool) -> bool:
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in {"1", "true", "yes", "on"}

    def _int(v: Any, default: int, *, minimum: int = 0, maximum: int = 100000) -> int:
        try:
            n = int(v)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, n))

    return SessionConfig(
        enabled=_bool(node.get("enabled"), True),
        storage_dir=storage_dir,
        auto_migrate_feishu=_bool(node.get("auto_migrate_feishu"), True),
        compact_enabled=_bool(node.get("compact_enabled"), True),
        history_max_messages=_int(node.get("history_max_messages"), 2000, minimum=100, maximum=20000),
        history_days=_int(node.get("history_days"), 30, minimum=1, maximum=365),
        compact_threshold=_int(node.get("compact_threshold"), 24, minimum=4, maximum=500),
        llm_memory_rounds=_int(rounds_src, 4, minimum=0, maximum=12),
        legacy_feishu_memory_file=legacy_path,
    )


class JsonSessionPersistence:
    """按 session_id 落盘：结构化 JSON + 可选 history JSONL。"""

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir

    def _state_path(self, session_id: str) -> Path:
        safe = _safe_session_filename(session_id)
        return self.storage_dir / f"{safe}.json"

    def _history_path(self, session_id: str) -> Path:
        safe = _safe_session_filename(session_id)
        return self.storage_dir / f"{safe}_history.jsonl"

    def load_state(self, session_id: str) -> SessionState | None:
        path = self._state_path(session_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[SessionPersistence] load_state failed session_id={} path={} err={}", session_id, path, exc)
            return None
        if not isinstance(raw, dict):
            return None
        state_blob = raw.get("state") if isinstance(raw.get("state"), dict) else raw
        if not isinstance(state_blob, dict):
            return None
        st = SessionState.from_dict(state_blob)
        st.open_id = str(session_id or st.open_id or "").strip()
        return st

    def save_state(
        self,
        session_id: str,
        state: SessionState,
        *,
        user_id: str | None = None,
        channel: str | None = None,
    ) -> None:
        path = self._state_path(session_id)
        payload = {
            "session_id": session_id,
            "user_id": user_id,
            "channel": channel,
            "updated_ts": time.time(),
            "state": state.to_dict(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        text: str,
        action: str | None = None,
        symbol: str | None = None,
        interval: str | None = None,
        question: str | None = None,
        raw_data: dict[str, Any] | None = None,
    ) -> None:
        r = str(role or "").strip().lower()
        t = str(text or "").strip()
        if r not in {"user", "assistant"} or not t:
            return
        row: dict[str, Any] = {
            "role": r,
            "text": t,
            "timestamp": time.time(),
            "created_ts": time.time(),
        }
        if action:
            row["action"] = action
        if symbol:
            row["symbol"] = symbol
        if interval:
            row["interval"] = interval
        if question:
            row["question"] = question
        if raw_data:
            row["raw_data"] = raw_data
        path = self._history_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def get_recent_messages(self, session_id: str, *, limit: int) -> list[dict[str, str]]:
        rows = self._read_history(session_id)
        if limit <= 0:
            return []
        out: list[dict[str, str]] = []
        for it in rows[-limit:]:
            role = str(it.get("role") or "").strip().lower()
            text = str(it.get("text") or "").strip()
            if role in {"user", "assistant"} and text:
                out.append({"role": role, "text": text})
        return out

    def get_full_history_for_compact(self, session_id: str) -> list[dict[str, Any]]:
        return self._read_history(session_id)

    def save_compacted_summary(self, session_id: str, summary: str, *, state: SessionState) -> None:
        state.compacted_summary = str(summary or "").strip() or None
        self.save_state(session_id, state)

    def truncate_history_keep_last(self, session_id: str, *, keep: int) -> None:
        if keep <= 0:
            return
        rows = self._read_history(session_id)
        if len(rows) <= keep:
            return
        path = self._history_path(session_id)
        kept = rows[-keep:]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for row in kept:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def history_exists(self, session_id: str) -> bool:
        return self._history_path(session_id).is_file()

    def _read_history(self, session_id: str) -> list[dict[str, Any]]:
        path = self._history_path(session_id)
        if not path.is_file():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[SessionPersistence] read_history failed session_id={} err={}", session_id, exc)
            return []
        out: list[dict[str, Any]] = []
        for line in lines:
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out


def _safe_session_filename(session_id: str) -> str:
    """文件名安全化：保留常见 id 字符，其余替换为 _。"""
    raw = str(session_id or "").strip()
    if not raw:
        return "anonymous"
    out = []
    for ch in raw:
        if ch.isalnum() or ch in "._-":
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)[:240]


class SessionManager:
    """会话管理器：组合 Store + Persistence。"""

    def __init__(
        self,
        *,
        config: SessionConfig | None = None,
        repo_root: Path | None = None,
    ) -> None:
        self._repo_root = repo_root or _repo_root()
        self.config = config or load_session_config(repo_root=self._repo_root)
        self.store = SessionStateStore(persist_path=None)
        self.persistence = JsonSessionPersistence(self.config.storage_dir)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._io_ok = True
        self._legacy_migrated = False
        if self.config.enabled:
            self._probe_storage()

    def _lock_for(self, session_id: str) -> threading.Lock:
        key = str(session_id or "").strip() or "__anonymous__"
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def _probe_storage(self) -> None:
        try:
            self.config.storage_dir.mkdir(parents=True, exist_ok=True)
            probe = self.config.storage_dir / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            self._io_ok = False
            logger.warning(
                "[SessionManager] 会话持久化已禁用（目录不可写） path={} err={}",
                self.config.storage_dir,
                exc,
            )

    def _safe_io(self, fn: Callable[[], None]) -> None:
        if not self.config.enabled or not self._io_ok:
            return
        try:
            fn()
        except OSError as exc:
            self._io_ok = False
            logger.warning("[SessionManager] IO 失败，降级为纯内存模式 err={}", exc)

    def load_session(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
        channel: str | None = None,
    ) -> SessionState:
        sid = str(session_id or "").strip()
        if not sid:
            return SessionState(open_id="")
        with self._lock_for(sid):
            if self.config.enabled and self._io_ok:
                loaded = self.persistence.load_state(sid)
                if loaded is not None:
                    self.store.hydrate(sid, loaded)
            st = self.store.get(sid)
            if not st.open_id:
                st.open_id = sid
            return st

    def save_session(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
        channel: str | None = None,
    ) -> None:
        sid = str(session_id or "").strip()
        if not sid:
            return

        def _write() -> None:
            st = self.store.get(sid)
            self.persistence.save_state(sid, st, user_id=user_id, channel=channel)

        with self._lock_for(sid):
            self._safe_io(_write)

    def append_message(
        self,
        session_id: str,
        role: str,
        text: str,
        *,
        action: str | None = None,
        symbol: str | None = None,
        interval: str | None = None,
        question: str | None = None,
        raw_data: dict[str, Any] | None = None,
    ) -> None:
        sid = str(session_id or "").strip()
        if not sid:
            return

        def _write() -> None:
            self.persistence.append_message(
                sid,
                role=role,
                text=text,
                action=action,
                symbol=symbol,
                interval=interval,
                question=question,
                raw_data=raw_data,
            )

        with self._lock_for(sid):
            self._safe_io(_write)

    def get_recent_messages(self, session_id: str, *, limit: int) -> list[dict[str, str]]:
        sid = str(session_id or "").strip()
        if not sid or limit <= 0 or not self.config.enabled or not self._io_ok:
            return []
        with self._lock_for(sid):
            try:
                return self.persistence.get_recent_messages(sid, limit=limit)
            except OSError as exc:
                logger.warning("[SessionManager] get_recent_messages failed session_id={} err={}", sid, exc)
                return []

    def get_full_history_for_compact(self, session_id: str) -> list[dict[str, Any]]:
        sid = str(session_id or "").strip()
        if not sid or not self.config.enabled or not self._io_ok:
            return []
        with self._lock_for(sid):
            try:
                return self.persistence.get_full_history_for_compact(sid)
            except OSError as exc:
                logger.warning("[SessionManager] get_full_history_for_compact failed session_id={} err={}", sid, exc)
                return []

    def save_compacted_summary(self, session_id: str, summary: str) -> None:
        sid = str(session_id or "").strip()
        if not sid:
            return

        def _write() -> None:
            st = self.store.get(sid)
            st.compacted_summary = str(summary or "").strip() or None
            st.history_version = int(st.history_version or 0) + 1
            self.persistence.save_compacted_summary(sid, summary, state=st)

        with self._lock_for(sid):
            self._safe_io(_write)

    def truncate_history_keep_last(self, session_id: str, *, keep: int) -> None:
        sid = str(session_id or "").strip()
        if not sid:
            return

        def _write() -> None:
            self.persistence.truncate_history_keep_last(sid, keep=keep)

        with self._lock_for(sid):
            self._safe_io(_write)

    def maybe_migrate_legacy_feishu(self, open_id: str) -> None:
        """首次检测到旧 JSONL 时全量导入各 open_id 历史，并 rename 原文件。"""
        if not self.config.enabled or not self.config.auto_migrate_feishu:
            return
        oid = str(open_id or "").strip()
        if not oid:
            return
        with self._lock_for("__feishu_migration__"):
            if self._legacy_migrated:
                return
            legacy = self.config.legacy_feishu_memory_file
            migrated = legacy.with_name(legacy.name + ".migrated")
            source = legacy if legacy.is_file() else migrated
            if not source.is_file():
                self._legacy_migrated = True
                return

            rows = self._read_legacy_jsonl(source)
            if not rows:
                if source == legacy:
                    self._rename_legacy_file(legacy)
                self._legacy_migrated = True
                return

            by_user: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                key = str(row.get("open_id") or "").strip()
                if not key:
                    continue
                by_user.setdefault(key, []).append(row)

            for uid, items in by_user.items():
                if self.persistence.history_exists(uid):
                    continue
                items.sort(key=lambda x: float(x.get("created_ts") or x.get("timestamp") or 0.0))
                for it in items:
                    role = str(it.get("role") or "").strip().lower()
                    text = str(it.get("text") or "").strip()
                    if role not in {"user", "assistant"} or not text:
                        continue
                    self.persistence.append_message(
                        uid,
                        role=role,
                        text=text,
                        action=str(it.get("action") or "") or None,
                        symbol=str(it.get("symbol") or "") or None,
                        interval=str(it.get("interval") or "") or None,
                        question=str(it.get("question") or "") or None,
                    )

            if source == legacy:
                self._rename_legacy_file(legacy)
            self._legacy_migrated = True
            logger.info("[SessionManager] 飞书历史已迁移至 sessions/ legacy={}", source)

    def _rename_legacy_file(self, legacy: Path) -> None:
        target = legacy.with_name(legacy.name + ".migrated")
        try:
            legacy.rename(target)
        except OSError as exc:
            logger.warning("[SessionManager] rename legacy feishu memory failed src={} err={}", legacy, exc)

    def _read_legacy_jsonl(self, path: Path) -> list[dict[str, Any]]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[SessionManager] read legacy feishu memory failed path={} err={}", path, exc)
            return []
        out: list[dict[str, Any]] = []
        for line in lines:
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out


_GLOBAL_SESSION_MANAGER: SessionManager | None = None


def get_global_session_manager(*, force_new: bool = False) -> SessionManager:
    global _GLOBAL_SESSION_MANAGER
    if force_new or _GLOBAL_SESSION_MANAGER is None:
        legacy_env = os.getenv("SESSION_STATE_PERSIST_PATH", "").strip()
        if legacy_env:
            logger.warning(
                "[SessionManager] SESSION_STATE_PERSIST_PATH 已废弃，请改用 config session.storage_dir"
            )
        _GLOBAL_SESSION_MANAGER = SessionManager()
    return _GLOBAL_SESSION_MANAGER


def reset_global_session_manager_for_tests() -> None:
    global _GLOBAL_SESSION_MANAGER
    _GLOBAL_SESSION_MANAGER = None
