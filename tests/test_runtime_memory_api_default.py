"""测试 MemoryAPI 在 app runtime 下默认启用。"""

from __future__ import annotations

import inspect
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from core.json_fact_store import JsonFactStore
from domain.profile.user_profile import _get_runtime_memory_api, get_user_profile


class _FakeAgent:
    def __init__(self, *args, **kwargs):
        self.llm = object()
        self.tools: list = []


def _factory_patches():
    return (
        patch("app.factory.MarketReActAgent", _FakeAgent),
        patch("app.factory.init_database_if_possible"),
        patch("app.factory.MarketSessionManager", return_value=MagicMock()),
        patch("app.factory.FeishuAdapter"),
    )


def test_factory_source_no_memory_new_api_flag():
    """运行代码 factory 不应再读取 memory_new_api。"""
    from app import factory

    source = inspect.getsource(factory.create_runtime_services)
    assert "memory_new_api" not in source


def test_create_runtime_services_memory_api_not_none(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "utils.runtime_paths.get_output_dir",
        lambda repo_root=None: tmp_path / "output",
    )
    with ExitStack() as stack:
        for p in _factory_patches():
            stack.enter_context(p)
        from app.factory import create_runtime_services

        services = create_runtime_services()

    assert services.memory_api is not None
    assert isinstance(services.memory_api.store, JsonFactStore)
    assert services.conversation_service.memory_api is services.memory_api


def test_user_profile_available_after_create_runtime_services(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "utils.runtime_paths.get_output_dir",
        lambda repo_root=None: tmp_path / "output",
    )
    with ExitStack() as stack:
        for p in _factory_patches():
            stack.enter_context(p)
        from app.factory import create_runtime_services

        services = create_runtime_services()

    assert _get_runtime_memory_api() is services.memory_api
    read = get_user_profile.invoke({"storage_key": "runtime_test_user"})
    assert read.get("error") != "MemoryAPI not configured"
    assert "MemoryAPI not configured" not in read.get("error", "")
