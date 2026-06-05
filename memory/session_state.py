"""SessionState — 结构化会话状态（替代旧 app/session_state.py）"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass
class SessionState:
    """结构化会话状态 — 仅保存上下文元信息，不保存完整事实正文"""

    # 用户标识
    open_id: str = ""
    chat: str = ""

    # 最近动作 / 任务类型
    last_action: str = "chat"           # analyze / chat / quote / compare / research / followup
    last_task_type: str = "chat"        # analysis / quote / compare / research / followup / chat

    # 标的 / 周期 / 数据源
    last_symbol: str | None = None      # 单标的兼容字段
    last_symbols: list[str] = field(default_factory=list)  # 标的列表（统一协议）
    last_interval: str | None = None
    last_provider: str | None = None
    last_question: str | None = None

    # 分析输出引用
    last_output_refs: dict[str, str] = field(default_factory=dict)
    last_facts_bundle: dict[str, Any] = field(default_factory=dict)
    last_display_preferences: dict[str, Any] = field(default_factory=dict)
    last_sim_account_scope: dict[str, Any] = field(default_factory=dict)

    # 历史压缩
    history_version: int = 0
    compacted_summary: str | None = None

    # 时间戳
    updated_ts: float = field(default_factory=time.time)

    # 意图路由追踪
    pending_intent: dict[str, Any] | None = None
    recent_analyses: list[dict[str, Any]] = field(default_factory=list)
    route_attempts: int = 0
    last_error_code: str | None = None
    repair_history: list[dict[str, Any]] = field(default_factory=list)
    termination_reason: str | None = None

    # ── 序列化 ──

    @classmethod
    def _serialize_fields(cls) -> list[str]:
        """返回可序列化的字段名列表"""
        return [
            "open_id", "chat", "last_action", "last_task_type", "last_symbol",
            "last_symbols", "last_interval", "last_provider", "last_question",
            "last_output_refs", "last_facts_bundle", "last_display_preferences",
            "last_sim_account_scope", "history_version", "compacted_summary",
            "updated_ts", "pending_intent", "recent_analyses", "route_attempts",
            "last_error_code", "repair_history", "termination_reason",
        ]

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON-safe dict"""
        return {f: getattr(self, f) for f in self._serialize_fields()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionState:
        """从 dict 反序列化（忽略未知字段）"""
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class SessionConfig:
    """会话配置 — 从 analysis_defaults.yaml 加载"""

    enabled: bool = True
    storage_dir: Path = field(default_factory=lambda: Path("sessions"))
    auto_migrate_feishu: bool = False
    compact_enabled: bool = True
    history_max_messages: int = 2000
    history_days: int = 30
    compact_threshold: int = 24
    llm_memory_rounds: int = 4


def load_session_config(repo_root: Path, session_cfg: dict[str, Any] | None = None) -> SessionConfig:
    """从 YAML 配置或显式参数构建 SessionConfig"""
    cfg = session_cfg or {}
    storage_dir = cfg.get("storage_dir")
    return SessionConfig(
        enabled=bool(cfg.get("enabled", True)),
        storage_dir=Path(storage_dir) if storage_dir else repo_root / "sessions",
        auto_migrate_feishu=bool(cfg.get("auto_migrate_feishu", False)),
        compact_enabled=bool(cfg.get("compact_enabled", True)),
        history_max_messages=int(cfg.get("history_max_messages", 2000)),
        history_days=int(cfg.get("history_days", 30)),
        compact_threshold=int(cfg.get("compact_threshold", 24)),
        llm_memory_rounds=int(cfg.get("llm_memory_rounds", 4)),
    )