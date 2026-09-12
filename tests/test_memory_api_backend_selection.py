"""测试 memory backend 选择逻辑。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from core.json_fact_store import JsonFactStore
from core.memory_api import DefaultMemoryAPI, create_default_memory_api


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


def test_create_default_memory_api_postgres_without_dsn():
    with patch("core.memory_api.PostgresFactStore", side_effect=RuntimeError("未配置 database.postgres.dsn")):
        with pytest.raises(RuntimeError, match="dsn"):
            create_default_memory_api(backend="postgres")
