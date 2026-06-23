"""测试 user_profile 工具的 runtime memory_api 注入行为。"""

from __future__ import annotations

import pytest

from core.memory_api import create_default_memory_api
from domain.profile.user_profile import (
    get_user_profile,
    set_user_profile_memory_api,
    update_user_profile,
)


def test_create_default_memory_api_sqlite_removed():
    with pytest.raises(ValueError, match="SQLite memory backend has been removed"):
        create_default_memory_api(backend="sqlite")


def test_create_default_memory_api_unsupported_backend():
    with pytest.raises(ValueError, match="Unsupported memory backend"):
        create_default_memory_api(backend="bad")


def test_user_profile_tool_without_injection_returns_error():
    """未注入 memory_api 时，工具返回明确错误，不创建任何 store。"""
    set_user_profile_memory_api(None)

    read = get_user_profile.invoke({"storage_key": "no_injection_user"})
    assert read["exists"] is False
    assert "MemoryAPI not configured" in read.get("error", "")

    update = update_user_profile.invoke(
        {"storage_key": "no_injection_user", "updates": {"risk_profile": "high"}, "reason": "test"}
    )
    assert update["updated"] is False
    assert "MemoryAPI not configured" in update.get("error", "")


def test_user_profile_roundtrip_with_json_backend(tmp_path):
    api = create_default_memory_api(repo_root=tmp_path, backend="json")
    set_user_profile_memory_api(api)

    result = update_user_profile.invoke(
        {
            "storage_key": "json_user_001",
            "updates": {"preferred_style": "swing"},
            "reason": "test json backend",
        }
    )
    assert result["updated"] is True

    read = get_user_profile.invoke({"storage_key": "json_user_001"})
    assert read["exists"] is True
    assert read["profile"]["preferred_style"] == "swing"

    set_user_profile_memory_api(None)
