"""SessionState — 结构化会话状态"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.runtime_paths import get_sessions_dir


@dataclass
class SessionState:
    """结构化会话状态 — 仅保存上下文元信息，不保存完整事实正文"""

    open_id: str = ""
    chat: str = ""

    last_action: str = "chat"
    last_task_type: str = "chat"

    last_symbol: str | None = None
    last_symbols: list[str] = field(default_factory=list)
    last_interval: str | None = None
    last_provider: str | None = None
    last_question: str | None = None

    last_output_refs: dict[str, str] = field(default_factory=dict)
    last_facts_bundle: dict[str, Any] = field(default_factory=dict)
    last_display_preferences: dict[str, Any] = field(default_factory=dict)
    last_sim_account_scope: dict[str, Any] = field(default_factory=dict)

    updated_ts: float = field(default_factory=time.time)

    @classmethod
    def _serialize_fields(cls) -> list[str]:
        return [
            "open_id",
            "chat",
            "last_action",
            "last_task_type",
            "last_symbol",
            "last_symbols",
            "last_interval",
            "last_provider",
            "last_question",
            "last_output_refs",
            "last_facts_bundle",
            "last_display_preferences",
            "last_sim_account_scope",
            "updated_ts",
        ]

    def to_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in self._serialize_fields()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionState:
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class SessionConfig:
    """会话配置 — 从 analysis_defaults.yaml 加载"""

    storage_dir: Path = field(default_factory=lambda: Path("sessions"))


def load_session_config(repo_root: Path, session_cfg: dict[str, Any] | None = None) -> SessionConfig:
    cfg = session_cfg or {}
    storage_dir = cfg.get("storage_dir")
    return SessionConfig(
        storage_dir=Path(storage_dir) if storage_dir else get_sessions_dir(repo_root=repo_root),
    )
