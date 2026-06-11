"""FeishuMemory — 飞书对话框内对话记忆（JSONL 后端）

@deprecated: 此模块已废弃。
主路径记忆已统一迁移至 memory/session_manager.py (MarketSessionManager)。
此文件仅保留兼容用途，未来版本将移除。
请勿在新代码中直接使用。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from config.runtime_config import get_analysis_config


@dataclass
class FeishuMemoryConfig:
    """飞书对话记忆配置 — 从 analysis_defaults.yaml 的 feishu.memory 段加载"""

    enabled: bool = True
    backend: Literal["jsonl"] = "jsonl"
    memory_file: Path = field(default_factory=lambda: Path("output/feishu_memory.jsonl"))
    max_messages_per_user: int = 2000
    history_days: int = 30
    long_term_top_k: int = 3

    @classmethod
    def from_yaml(cls) -> FeishuMemoryConfig:
        """从 analysis_defaults.yaml 加载配置"""
        cfg = get_analysis_config()
        feishu = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
        mem = feishu.get("memory") if isinstance(feishu.get("memory"), dict) else {}
        return cls(
            enabled=bool(mem.get("enabled", True)),
            backend=str(mem.get("backend", "jsonl")),
            memory_file=Path(mem.get("memory_file", "output/feishu_memory.jsonl")),
            max_messages_per_user=int(mem.get("max_messages_per_user", 2000)),
            history_days=int(mem.get("history_days", 30)),
            long_term_top_k=int(mem.get("long_term_top_k", 3)),
        )


class FeishuMemory:
    """飞书对话记忆：JSONL 后端，按用户 open_id 分区

    JSONL 每行格式:
    {"open_id": "ou_xxx", "role": "user/assistant/writer", "text": "...", "ts": 1717516800.123, "meta": {...}}
    """

    def __init__(self, config: FeishuMemoryConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._ensure_output_dir()

    # ── 公开接口 ──

    def save_message(
        self, open_id: str, role: str, text: str, **meta: Any
    ) -> None:
        """追加一条消息到 JSONL 文件"""
        if not self._config.enabled:
            return
        record: dict[str, Any] = {
            "open_id": open_id,
            "role": role,
            "text": text,
            "ts": time.time(),
        }
        if meta:
            record["meta"] = meta

        with self._lock:
            with open(self._config.memory_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # 写入后检查是否需要裁剪
        self._maybe_prune(open_id)

    def load_history(
        self, open_id: str, *, limit: int | None = None
    ) -> list[dict[str, str]]:
        """加载用户最近对话历史

        Args:
            open_id: 用户标识
            limit: 最大返回条数，None 则使用 config.max_messages_per_user

        Returns:
            格式为 [{"role": "user"/"assistant", "text": "..."}, ...]
        """
        if not self._config.enabled:
            return []

        max_limit = limit or self._config.max_messages_per_user
        all_messages = self._read_user_messages(open_id)

        # 过滤最近 history_days 天内的消息
        cutoff = time.time() - self._config.history_days * 86400
        recent = [m for m in all_messages if m.get("ts", 0) >= cutoff]

        # 取最近 limit 条
        recent = recent[-max_limit:]

        return [{"role": m["role"], "text": m["text"]} for m in recent]

    def load_history_window(
        self, open_id: str, rounds: int = 4
    ) -> list[dict[str, str]]:
        """加载最近 N 轮对话（1 轮 = user + assistant），供路由器使用

        与 load_history 不同，此方法返回"轮次"对齐的历史，
        传入 router_context_rounds 配置值。
        """
        if not self._config.enabled:
            return []

        all_messages = self._read_user_messages(open_id)

        # 从尾部往前数 rounds 轮（每轮包含一对 user+assistant）
        result: list[dict[str, str]] = []
        rounds_collected = 0
        i = len(all_messages) - 1

        while i >= 0 and rounds_collected < rounds:
            msg = all_messages[i]
            result.insert(0, {"role": msg["role"], "text": msg["text"]})

            if msg["role"] == "user":
                rounds_collected += 1
                # 也收集这条 user 之前的 assistant（如果有的话）
                if i > 0 and all_messages[i - 1]["role"] == "assistant":
                    i -= 1
                    result.insert(
                        0,
                        {
                            "role": all_messages[i]["role"],
                            "text": all_messages[i]["text"],
                        },
                    )
            i -= 1

        return result

    def prune_user(self, open_id: str) -> None:
        """按 history_days 和 max_messages_per_user 裁剪旧消息"""
        if not self._config.enabled:
            return
        self._do_prune(open_id)

    # ── 内部方法 ──

    def _ensure_output_dir(self) -> None:
        """确保输出目录存在"""
        self._config.memory_file.parent.mkdir(parents=True, exist_ok=True)

    def _read_user_messages(self, open_id: str) -> list[dict[str, Any]]:
        """读取指定用户的所有消息记录"""
        if not self._config.memory_file.is_file():
            return []

        messages: list[dict[str, Any]] = []
        with self._lock:
            with open(self._config.memory_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if record.get("open_id") == open_id:
                            messages.append(record)
                    except json.JSONDecodeError:
                        continue
        return messages

    def _maybe_prune(self, open_id: str) -> None:
        """按需裁剪：当用户消息超过限制时触发"""
        user_messages = self._read_user_messages(open_id)
        if len(user_messages) > self._config.max_messages_per_user:
            self._do_prune(open_id)

    def _do_prune(self, open_id: str) -> None:
        """执行裁剪：保留最近 history_days 天 && 最近 max_messages_per_user 条"""
        if not self._config.memory_file.is_file():
            return

        cutoff = time.time() - self._config.history_days * 86400

        with self._lock:
            kept_lines: list[str] = []
            user_count = 0

            with open(self._config.memory_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()

            # 先收集目标用户的消息
            user_messages: list[dict[str, Any]] = []
            other_lines: list[str] = []

            for line in all_lines:
                line_stripped = line.strip()
                if not line_stripped:
                    continue
                try:
                    record = json.loads(line_stripped)
                except json.JSONDecodeError:
                    other_lines.append(line_stripped)
                    continue

                if record.get("open_id") == open_id:
                    user_messages.append(record)
                else:
                    other_lines.append(line_stripped)

            # 过滤 + 限制
            recent = [m for m in user_messages if m.get("ts", 0) >= cutoff]
            recent = recent[-self._config.max_messages_per_user :]

            # 重写文件
            with open(self._config.memory_file, "w", encoding="utf-8") as f:
                for line in other_lines:
                    f.write(line + "\n")
                for msg in recent:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")