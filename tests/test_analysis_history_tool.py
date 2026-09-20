from __future__ import annotations

from typing import Any

import tools.analysis_history as analysis_history
from core.message_protocol import ToolCall
from core.tool_executor import ToolExecutor
from core.tool_protocol import ToolContext
from tools.analysis_history import get_previous_analysis_snapshot
from tools.registry import get_tool_registry


def test_previous_analysis_snapshot_reads_business_database(monkeypatch):
    monkeypatch.setattr(analysis_history, "get_postgres_dsn", lambda: "postgresql://test")
    monkeypatch.setattr(
        analysis_history,
        "_load_previous_analysis_snapshot_from_db",
        lambda **kwargs: {
            "schema_version": "analysis_snapshot.v2",
            "symbol": "ETH_USDT",
            "interval": "4h",
            "timestamp": "2026-07-13T10:00:00",
            "price": 1778.0,
            "ma_regime": "bullish",
        },
    )

    previous = get_previous_analysis_snapshot(
        session_id="s_ctx_db_01",
        symbol="ETHUSDT",
        interval="4h",
    )

    assert previous["status"] == "success"
    assert previous["snapshot"]["price"] == 1778.0


def test_previous_snapshot_tool_uses_server_request_id(monkeypatch):
    monkeypatch.setattr(analysis_history, "get_postgres_dsn", lambda: "postgresql://test")
    captured: dict[str, Any] = {}

    def fake_loader(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(analysis_history, "_load_previous_analysis_snapshot_from_db", fake_loader)
    result = __import__("asyncio").run(ToolExecutor(get_tool_registry()).execute(
        ToolCall(
            id="tc_previous_01",
            name="get_previous_analysis_snapshot",
            arguments={"symbol": "ETHUSDT", "interval": "4h"},
        ),
        context=ToolContext(session_id="session_1", request_id="request_1"),
        allowed_names={"get_previous_analysis_snapshot"},
    ))

    assert result.name == "get_previous_analysis_snapshot"
    assert captured["exclude_request_id"] == "request_1"
