"""测试 memory backend 选择逻辑。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from core.json_fact_store import JsonFactStore
from core.memory_api import DefaultMemoryAPI, create_default_memory_api, _get_memory_backend_from_config


def test_create_default_memory_api_json_explicit():
    with tempfile.TemporaryDirectory() as tmp:
        api = create_default_memory_api(repo_root=Path(tmp), backend="json")
        assert isinstance(api, DefaultMemoryAPI)
        assert isinstance(api.store, JsonFactStore)


def test_create_default_memory_api_defaults_to_json():
    with tempfile.TemporaryDirectory() as tmp:
        with patch("core.memory_api._get_memory_backend_from_config", return_value="json"):
            api = create_default_memory_api(repo_root=Path(tmp), backend=None)
            assert isinstance(api.store, JsonFactStore)


def test_create_default_memory_api_sqlite_removed():
    with pytest.raises(ValueError, match="SQLite memory backend has been removed"):
        create_default_memory_api(backend="sqlite")


def test_create_default_memory_api_unsupported_backend():
    with pytest.raises(ValueError, match="Unsupported memory backend"):
        create_default_memory_api(backend="bad")


def test_default_backend_from_config_is_json():
    with patch("config.runtime_config.get_memory_config", return_value={}):
        assert _get_memory_backend_from_config() == "json"


def test_config_sqlite_raises():
    with patch("config.runtime_config.get_memory_config", return_value={"backend": "sqlite"}):
        with pytest.raises(ValueError, match="SQLite memory backend has been removed"):
            _get_memory_backend_from_config()


def test_postgres_backend_only_when_explicitly_configured():
    with patch("config.runtime_config.get_memory_config", return_value={"backend": "postgres"}):
        assert _get_memory_backend_from_config() == "postgres"


def test_create_default_memory_api_postgres_without_dsn():
    with patch("core.memory_api.PostgresFactStore", side_effect=RuntimeError("未配置 database.postgres.dsn")):
        with pytest.raises(RuntimeError, match="dsn"):
            create_default_memory_api(backend="postgres")


def test_default_memory_api_accepts_any_factstore():
    class _MockStore:
        def write_fact(self, f): return "id1"
        def get_latest_fact(self, *a, **k): return None
        def recall(self, *a, **k): return []
        def set_checkpoint(self, *a, **k): pass
        def get_checkpoint(self, *a, **k): return None

    api = DefaultMemoryAPI(store=_MockStore())
    assert api.write_fact("t", type("F", (), {"thread_id": "t", "id": "1", "source": "", "timestamp": "", "type": "", "payload": {}, "provenance": {}, "tags": []})()) == "id1"


def test_user_profile_tool_still_uses_injected_api_only():
    from domain.profile.user_profile import get_user_profile, set_user_profile_memory_api
    set_user_profile_memory_api(None)
    res = get_user_profile.invoke({"storage_key": "x"})
    assert res["exists"] is False
    assert "not configured" in res.get("error", "")
