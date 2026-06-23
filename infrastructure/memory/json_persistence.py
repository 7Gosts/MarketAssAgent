"""JsonSessionPersistence — JSONL 对话历史 + JSON 状态文件持久化"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .session_state import SessionState


class JsonSessionPersistence:
    """JSONL 对话历史 + JSON 状态文件读写"""

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self._ensure_dir()

    # ── 路径工具 ──

    def _state_path(self, session_id: str) -> Path:
        d = self.storage_dir / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{session_id}.json"

    def _history_path(self, session_id: str) -> Path:
        d = self.storage_dir / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d / "_history.jsonl"

    def _ensure_dir(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    # ── 状态持久化 ──

    def load_state(self, session_id: str) -> SessionState | None:
        """从 JSON 文件加载 SessionState，不存在返回 None"""
        path = self._state_path(session_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return SessionState.from_dict(data)
        except Exception:
            return None

    def save_state(self, session_id: str, state: SessionState) -> None:
        """将 SessionState 写入 JSON 文件"""
        path = self._state_path(session_id)
        state.updated_ts = time.time()
        path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 对话历史持久化 ──

    def append_message(
        self,
        session_id: str,
        role: str,
        text: str,
        **meta: Any,
    ) -> None:
        """追加一条消息到 JSONL 文件（每行一条 JSON）"""
        path = self._history_path(session_id)
        record: dict[str, Any] = {
            "role": role,
            "text": text,
            "ts": time.time(),
        }
        if meta:
            record["meta"] = meta
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def get_recent_messages(
        self, session_id: str, *, limit: int = 8
    ) -> list[dict[str, str]]:
        """读取最近 N 条对话历史，返回简化格式 [{"role": ..., "text": ...}]"""
        lines = self._read_history(session_id)
        recent = lines[-limit:] if limit else lines
        return [{"role": r.get("role", ""), "text": r.get("text", "")} for r in recent]

    # ── 内部方法 ──

    def _read_history(self, session_id: str) -> list[dict[str, Any]]:
        """读取全部 JSONL 行，跳过畸形行"""
        path = self._history_path(session_id)
        if not path.is_file():
            return []
        results: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return results