from __future__ import annotations

from typing import Any

from core.fact_store import Fact
from core.memory_api import create_default_memory_api
import pytest
import tools.context_memory as context_memory_module
from tools.context_memory import (
    get_last_snapshot,
    get_previous_analysis_snapshot,
    get_recent_tool_observations,
    search_conversation_summaries,
    set_context_memory_api,
)
from tools.registry import get_all_tools


@pytest.fixture(autouse=True)
def _disable_real_snapshot_db(monkeypatch):
    monkeypatch.setattr(context_memory_module, "get_postgres_dsn", lambda: "")


def test_context_memory_tools_without_injection_return_error():
    set_context_memory_api(None)

    snapshot = get_last_snapshot.invoke({"session_id": "s_no_api"})
    assert snapshot["status"] == "error"

    observations = get_recent_tool_observations.invoke({"session_id": "s_no_api"})
    assert observations["status"] == "error"

    previous = get_previous_analysis_snapshot.invoke({"session_id": "s_no_api", "symbol": "ETHUSDT", "interval": "1h"})
    assert previous["status"] == "error"

    summaries = search_conversation_summaries.invoke({"session_id": "s_no_api"})
    assert summaries["status"] == "error"


def test_context_memory_tools_roundtrip_with_json_backend(tmp_path):
    api = create_default_memory_api(repo_root=tmp_path, backend="json")
    set_context_memory_api(api)

    api.checkpoint(
        "s_ctx_01",
        "last_snapshot",
        {
            "symbol": "ETHUSDT",
            "interval": "1h",
            "timestamp": "2026-06-27T10:00:00Z",
            "current_price": 2420.0,
            "trend": "震荡",
            "levels_v2": {"nearest_support": 2400.0, "nearest_resistance": 2480.0},
            "actionability": {"bias": "wait", "can_trade_now": False, "wait_condition": "等突破后确认"},
            "invalidation_conditions": {"time_stop_rule": "3 根同周期K线未延续则失效"},
            "raw_insights": "ETHUSDT 在 1h 周期维持震荡结构。",
        },
    )
    api.write_fact(
        "s_ctx_01",
        Fact(
            thread_id="s_ctx_01",
            source="analyze_market",
            type="tool_observation",
            payload={
                "tool": "analyze_market",
                "summary": "success / ETHUSDT / 1h / 震荡",
                "content": '{"compact_summary_v1":{"symbol":"ETHUSDT"}}',
            },
            provenance={"tool_call_id": "tc_ctx_01"},
        ),
    )
    api.write_fact(
        "s_ctx_01",
        Fact(
            thread_id="s_ctx_01",
            source="conversation_service",
            type="turn_summary",
            payload={
                "symbols": ["ETHUSDT"],
                "intervals": ["1h"],
                "current_price": 2420.0,
                "trend": "震荡",
                "key_levels": {"support": [2400.0], "resistance": [2480.0]},
                "stance": "wait",
                "next_trigger": "若放量站上 2480 再看延续。",
                "assistant_conclusion": "2400 支撑有效前不追空。",
            },
        ),
    )
    snapshot = get_last_snapshot.invoke({"session_id": "s_ctx_01"})
    assert snapshot["status"] == "success"
    assert snapshot["snapshot"]["symbol"] == "ETHUSDT"
    assert snapshot["snapshot"]["trend"] == "震荡"

    observations = get_recent_tool_observations.invoke({"session_id": "s_ctx_01", "limit": 1})
    assert observations["status"] == "success"
    assert len(observations["items"]) == 1
    assert observations["items"][0]["tool"] == "analyze_market"
    assert observations["items"][0]["tool_call_id"] == "tc_ctx_01"

    summaries = search_conversation_summaries.invoke(
        {"session_id": "s_ctx_01", "limit": 10, "max_chars": 8000}
    )
    assert summaries["status"] == "success"
    assert summaries["total_items"] >= 1
    assert summaries["total_chars"] <= 8000
    assert summaries["items"][0]["symbols"] == ["ETHUSDT"]

    set_context_memory_api(None)


def test_previous_analysis_snapshot_reads_from_db_only(monkeypatch):
    monkeypatch.setattr(context_memory_module, "get_postgres_dsn", lambda: "postgresql://test")
    monkeypatch.setattr(
        context_memory_module,
        "_load_previous_analysis_snapshot_from_db",
        lambda **kwargs: {
            "schema_version": "analysis_snapshot.v1",
            "symbol": "ETH_USDT",
            "interval": "4h",
            "timestamp": "2026-07-13T10:00:00",
            "price": 1778.0,
            "trend": "震荡偏强",
        },
    )
    previous = get_previous_analysis_snapshot.invoke(
        {"session_id": "s_ctx_db_01", "symbol": "ETHUSDT", "interval": "4h"}
    )
    assert previous["status"] == "success"
    assert previous["snapshot"]["price"] == 1778.0
    assert previous["snapshot"]["trend"] == "震荡偏强"


def test_previous_analysis_snapshot_can_read_db_without_memory_api(monkeypatch):
    set_context_memory_api(None)
    monkeypatch.setattr(context_memory_module, "get_postgres_dsn", lambda: "postgresql://test")
    monkeypatch.setattr(
        context_memory_module,
        "_load_previous_analysis_snapshot_from_db",
        lambda **kwargs: {
            "schema_version": "analysis_snapshot.v1",
            "symbol": "ETH_USDT",
            "interval": "4h",
            "timestamp": "2026-07-13T12:00:00",
            "price": 1795.3,
            "trend": "震荡",
        },
    )

    previous = get_previous_analysis_snapshot.invoke(
        {"session_id": "s_ctx_db_only", "symbol": "ETHUSDT", "interval": "4h"}
    )
    assert previous["status"] == "success"
    assert previous["snapshot"]["price"] == 1795.3


def test_previous_analysis_snapshot_auto_excludes_current_request_id(monkeypatch):
    monkeypatch.setattr(context_memory_module, "get_postgres_dsn", lambda: "postgresql://test")
    captured: dict[str, Any] = {}

    def _fake_db_loader(**kwargs):
        captured.update(kwargs)
        return {
            "schema_version": "analysis_snapshot.v1",
            "symbol": "ETH_USDT",
            "interval": "4h",
            "timestamp": "2026-07-13T12:00:00",
            "price": 1795.3,
            "trend": "震荡",
        }

    monkeypatch.setattr(context_memory_module, "_load_previous_analysis_snapshot_from_db", _fake_db_loader)

    previous = get_previous_analysis_snapshot.invoke(
        {"session_id": "s_ctx_db_only", "symbol": "ETHUSDT", "interval": "4h"},
        config={"configurable": {"thread_id": "s_ctx_db_only", "request_id": "req_turn_01"}},
    )
    assert previous["status"] == "success"
    assert captured["exclude_request_id"] == "req_turn_01"


def test_context_memory_tools_registered():
    names = {getattr(tool, "name", "") for tool in get_all_tools()}
    assert "get_last_snapshot" in names
    assert "get_previous_analysis_snapshot" in names
    assert "get_recent_tool_observations" in names
    assert "search_conversation_summaries" in names
