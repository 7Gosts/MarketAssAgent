"""用户画像工具 — LLM 主动读写用户画像"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from langchain_core.tools import tool

from core.memory_api import MemoryAPI


_RUNTIME_MEMORY_API: MemoryAPI | None = None


def set_user_profile_memory_api(memory_api: MemoryAPI | None) -> None:
    """在运行时注入统一 MemoryAPI（由 runtime/app/factory.py 调用）。"""
    global _RUNTIME_MEMORY_API
    _RUNTIME_MEMORY_API = memory_api


def _get_runtime_memory_api() -> MemoryAPI | None:
    return _RUNTIME_MEMORY_API


def _run_async(coro: Any) -> Any:
    """Run MemoryAPI async methods from sync LangChain tools."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except Exception as e:
            result["error"] = e

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _merge_list_values(current: list[Any], incoming: list[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for item in [*current, *incoming]:
        key = repr(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


@tool
def get_user_profile(storage_key: str) -> dict[str, Any]:
    """
    获取指定 storage_key 的用户画像。

    LLM 应在以下场景主动调用：
    - 准备给出交易计划、仓位建议、风控判断前
    - 需要了解用户风险偏好、持仓风格、关注品种时

    Args:
        storage_key: 用户唯一标识（推荐使用 "feishu_{open_id}" 或 "web_{user_id}"）

    Returns:
        用户画像字典
    """
    api = _get_runtime_memory_api()
    if api is None:
        return {"storage_key": storage_key, "exists": False, "error": "MemoryAPI not configured"}

    try:
        profile = _run_async(api.get_user_profile(storage_key))
        return {"storage_key": storage_key, "exists": True, "profile": profile.model_dump(mode="json")}
    except Exception as e:
        return {"storage_key": storage_key, "exists": False, "error": str(e)}


@tool
def update_user_profile(
    storage_key: str,
    updates: dict[str, Any],
    reason: str = "",
    confidence: float | None = None,
) -> dict[str, Any]:
    """
    更新用户画像（支持部分更新）。

    LLM 应在以下场景主动调用：
    - 用户明确表达交易偏好、风险态度时
    - 完成交易后更新持仓和历史表现
    - 观察到用户风格发生明显变化（支持风格反转）

    Args:
        storage_key: 用户唯一标识
        updates: 要更新的字段（支持部分更新）
        reason: 更新原因（必须提供）
        confidence: 更新置信度（0~1）

    Returns:
        更新后的用户画像
    """
    api = _get_runtime_memory_api()
    if api is None:
        return {"storage_key": storage_key, "updated": False, "error": "MemoryAPI not configured"}

    try:
        # 先读取现有画像
        current = _run_async(api.get_user_profile(storage_key))

        # 合并更新
        for key, value in updates.items():
            if hasattr(current, key):
                current_attr = getattr(current, key)
                if isinstance(current_attr, list) and isinstance(value, list):
                    # list 字段去重合并
                    setattr(current, key, _merge_list_values(current_attr, value))
                else:
                    setattr(current, key, value)

        # 写入
        updated = _run_async(
            api.update_user_profile(
                current,
                source="llm_inference",
                reason=reason,
                confidence=confidence or 0.6,
            )
        )

        after = updated.model_dump(mode="json")
        return {
            "storage_key": storage_key,
            "updated": True,
            "reason": reason,
            "confidence": confidence,
            "changed_fields": list(updates.keys()),
            "profile": after,
        }
    except Exception as e:
        return {"storage_key": storage_key, "updated": False, "error": str(e)}
