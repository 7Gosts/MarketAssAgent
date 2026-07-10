from __future__ import annotations

from core.fact_store import Fact
from core.memory_api import create_default_memory_api
from tools.context_memory import (
    get_last_snapshot,
    get_previous_analysis_snapshot,
    get_recent_tool_observations,
    search_conversation_summaries,
    set_context_memory_api,
)
from tools.registry import get_all_tools


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
    api.write_fact(
        "s_ctx_01",
        Fact(
            thread_id="s_ctx_01",
            source="conversation_service",
            type="analysis_snapshot",
            payload={
                "schema_version": "analysis_snapshot.v1",
                "symbol": "ETH_USDT",
                "interval": "4h",
                "timestamp": "2026-07-10T10:00:00",
                "price": 1773.87,
                "trend": "震荡",
                "stance": "wait",
                "support": [1759.2],
                "resistance": [1791.3],
            },
            provenance={"request_id": "old_req"},
            tags=["analysis_snapshot", "symbol:ETH_USDT", "interval:4h"],
        ),
    )
    api.write_fact(
        "s_ctx_01",
        Fact(
            thread_id="s_ctx_01",
            source="conversation_service",
            type="analysis_snapshot",
            payload={
                "schema_version": "analysis_snapshot.v1",
                "symbol": "ETH_USDT",
                "interval": "1h",
                "timestamp": "2026-07-10T11:00:00",
                "price": 1768.95,
                "trend": "震荡",
                "stance": "wait",
                "support": [1758.0],
                "resistance": [1779.6],
            },
            provenance={"request_id": "new_req"},
            tags=["analysis_snapshot", "symbol:ETH_USDT", "interval:1h"],
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

    previous = get_previous_analysis_snapshot.invoke({"session_id": "s_ctx_01", "symbol": "ETHUSDT", "interval": "4h"})
    assert previous["status"] == "success"
    assert previous["snapshot"]["symbol"] == "ETH_USDT"
    assert previous["snapshot"]["interval"] == "4h"
    assert previous["snapshot"]["price"] == 1773.87

    weak_symbol = get_previous_analysis_snapshot.invoke({"session_id": "s_ctx_01", "symbol": "ETH_USDT", "interval": "4h"})
    assert weak_symbol["status"] == "success"
    assert weak_symbol["snapshot"]["price"] == 1773.87

    missing = get_previous_analysis_snapshot.invoke({"session_id": "s_ctx_01", "symbol": "BTCUSDT", "interval": "4h"})
    assert missing["status"] == "not_found"

    set_context_memory_api(None)


def test_context_memory_tools_registered():
    names = {getattr(tool, "name", "") for tool in get_all_tools()}
    assert "get_last_snapshot" in names
    assert "get_previous_analysis_snapshot" in names
    assert "get_recent_tool_observations" in names
    assert "search_conversation_summaries" in names
