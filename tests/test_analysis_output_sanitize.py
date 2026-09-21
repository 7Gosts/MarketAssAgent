from __future__ import annotations

import json
import re
import asyncio
from typing import Any
from unittest.mock import patch

from core.message_protocol import ToolCall
from core.tool_executor import ToolExecutor
from core.tool_protocol import ToolContext
import domain.market.analysis_service as analysis_service_module
import domain.market.indicators as indicators_module
from domain.market.analysis_service import (
    _perform_market_analysis,
    analyze_market,
)
from domain.market.indicators import _get_ma_config
from domain.market.structure import (
    _assess_structure_signals,
    _detect_wyckoff_signals_v2,
    _structure_signal_rank,
)
from tools.registry import get_tool_registry


def _sample_klines(count: int = 80) -> list[dict]:
    base = 100.0
    rows: list[dict] = []
    for i in range(count):
        close = base + i * 0.5
        rows.append(
            {
                "open": close - 0.2,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1000 + i * 10,
            }
        )
    return rows


def _sample_klines_with_spring_signal() -> list[dict]:
    rows: list[dict] = []
    # earlier bars: relatively stable range
    for i in range(45):
        close = 100.0 + (i % 3) * 0.2
        rows.append(
            {
                "open": close - 0.2,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1400 - i * 2,
            }
        )
    # recent bars: fake break to lower low then recovery (spring-like)
    for i in range(15):
        if i == 8:
            low = 92.0
            close = 94.0
            high = 95.0
        else:
            close = 95.5 + i * 0.05
            high = close + 0.8
            low = close - 0.8
        rows.append(
            {
                "open": close - 0.2,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1100 - i * 8,
            }
        )
    return rows


def _sample_klines_with_upthrust_signal() -> list[dict]:
    rows: list[dict] = []
    # prior bars: stable horizontal range
    for i in range(45):
        close = 100.0 + (i % 2) * 0.2
        rows.append(
            {
                "open": close - 0.2,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1300 - i * 2,
            }
        )
    # recent bars: fake breakout higher then fall back
    for i in range(15):
        if i == 6:
            high = 107.0
            low = 101.0
            close = 102.0
        else:
            close = 98.2 - i * 0.03
            high = close + 0.9
            low = close - 0.9
        rows.append(
            {
                "open": close + 0.1,
                "high": high,
                "low": low,
                "close": close,
                "volume": 980 - i * 7,
            }
        )
    return rows


def _assert_no_confidence_percent(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    for analysis in _analysis_payloads(payload):
        assert "confidence" not in analysis
    assert not re.search(r"置信度\s*\d+\s*%", text)


def _analysis_payloads(payload: dict) -> list[dict[str, Any]]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    return [
        item["analysis"]
        for item in items
        if isinstance(item, dict) and isinstance(item.get("analysis"), dict)
    ]


def _first_analysis(payload: dict) -> dict[str, Any]:
    payloads = _analysis_payloads(payload)
    assert payloads
    return payloads[0]


def _collect_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(map(_collect_keys, value.values())))
    if isinstance(value, list):
        return set().union(*(map(_collect_keys, value)))
    return set()


def test_assess_structure_signals_bullish_aligned():
    signals = _assess_structure_signals(
        "偏多",
        {"MA_short": 110.0, "MA_mid": 105.0, "MA_long": 100.0},
        {"support": [99.0, 98.0], "resistance": [112.0, 115.0]},
    )
    assert signals["trend_ma_match"] is True
    assert signals["trend_clarity"] == "directional"


def test_structure_signal_rank_prefers_aligned_directional():
    aligned = _assess_structure_signals(
        "偏多",
        {"MA_short": 110.0, "MA_mid": 105.0, "MA_long": 100.0},
        {"support": [99.0], "resistance": [112.0]},
    )
    mixed = _assess_structure_signals(
        "震荡",
        {"MA_short": 105.0, "MA_mid": 104.0, "MA_long": 100.0},
        {"support": [99.0], "resistance": [112.0]},
    )
    assert _structure_signal_rank(aligned) > _structure_signal_rank(mixed)


def test_ma_config_uses_crypto_for_crypto_and_equity_for_everything_else(monkeypatch) -> None:
    monkeypatch.setattr(indicators_module, "get_ma_system", lambda: {
        "crypto": {"short": 8, "mid": 21, "long": 55},
        "equity": {"short": 13, "mid": 34, "long": 89},
    })
    assert _get_ma_config("ETH_USDT", market="crypto") == {"short": 8, "mid": 21, "long": 55}
    assert _get_ma_config("NVDA", market="us_equity") == {"short": 13, "mid": 34, "long": 89}
    assert _get_ma_config("00168.HK", market="hk_equity") == {"short": 13, "mid": 34, "long": 89}
    assert _get_ma_config("AU0", market="gold") == {"short": 13, "mid": 34, "long": 89}
    assert _get_ma_config("UNKNOWN", market="unknown") == {"short": 13, "mid": 34, "long": 89}


@patch("tools.market_data.fetch_market_data")
def test_analyze_market_returns_objective_market_facts(mock_fetch, monkeypatch):
    monkeypatch.setattr(indicators_module, "get_ma_system", lambda: {
        "crypto": {"short": 8, "mid": 21, "long": 55},
        "default": {"short": 20, "mid": 60, "long": 120},
    })
    mock_fetch.return_value = {"data": _sample_klines(), "market": "crypto"}

    result = analyze_market(**{"symbol": "ETHUSDT", "interval": "4h"})
    assert result["status"] == "success"
    assert set(result.keys()) == {"status", "items", "message"}
    assert "analysis" not in result
    assert "analyses" not in result
    assert "symbols" not in result
    assert "comparison" not in result
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["status"] == "success"
    assert item["symbol"] == "ETHUSDT"
    assert item["interval"] == "4h"
    analysis = _first_analysis(result)
    assert "structure_signals" not in analysis
    assert "key_levels" not in analysis
    assert "structure" not in analysis
    assert "indicators" not in analysis
    assert analysis["ma_slopes_pct"]["short"] > 0
    assert "swing_structure" in analysis
    assert isinstance(analysis["support_levels"], list)
    assert isinstance(analysis["resistance_levels"], list)
    assert "to_support_pct" in analysis["distance_to_levels_pct"]
    assert isinstance(analysis["recent_candles"], list)
    assert len(analysis["recent_candles"]) <= 3
    assert all(isinstance(candle, dict) for candle in analysis["recent_candles"])
    assert 0 <= analysis["range_position"] <= 1
    forbidden = {
        "trend",
        "bias",
        "can_trade_now",
        "why",
        "wait_condition",
        "triggered",
        "actionability",
        "trigger_conditions",
        "invalidation_conditions",
        "risk_flags",
    }
    assert not (_collect_keys(analysis) & forbidden)
    assert "market_structure_v2" not in analysis
    assert "pattern_detection_v2" not in analysis
    assert "recent_klines_v1" not in analysis
    assert "fib_v1" in analysis
    assert "level_zones_v1" in analysis
    assert analysis["ma_periods"] == {"short": 8, "mid": 21, "long": 55}
    assert analysis["ma_values"] == {"short": 137.75, "mid": 134.5, "long": 126.0}
    assert "support" in analysis["level_details"]
    assert "resistance" in analysis["level_details"]
    for detail in (analysis["level_details"].get("support") or []) + (
        analysis["level_details"].get("resistance") or []
    ):
        assert "price" in detail
        assert "primary_source" in detail
        assert "sources" in detail
    zones_v1 = analysis["level_zones_v1"]
    assert "lookback_bars" in zones_v1
    assert "support_zones" in zones_v1
    assert "resistance_zones" in zones_v1
    fib_v1 = analysis["fib_v1"]
    assert set((fib_v1.get("levels") or {}).keys()) == {"23.6%", "38.2%", "50.0%", "61.8%"}
    assert "compact_summary_v1" not in result
    assert "output_meta_v1" not in result
    assert "snapshot" not in result
    assert "confidence" not in analysis
    _assert_no_confidence_percent(result)


@patch("tools.market_data.fetch_market_data")
def test_analyze_market_persists_snapshot_from_tool_context(mock_fetch, monkeypatch):
    mock_fetch.return_value = {"data": _sample_klines()}
    captured: dict[str, Any] = {}
    monkeypatch.setattr(analysis_service_module, "get_postgres_dsn", lambda: "postgresql://test", raising=False)

    class _RepoStub:
        def create_if_missing(
            self,
            *,
            session_id: str,
            request_id: str,
            snapshot_payload: dict[str, Any],
            raw_snapshot: dict[str, Any] | None = None,
            snapshot_id: str | None = None,
        ) -> Any:
            captured.update(
                {
                    "session_id": session_id,
                    "request_id": request_id,
                    "snapshot_payload": snapshot_payload,
                    "raw_snapshot": raw_snapshot,
                }
            )
            return type("Row", (), {"snapshot_id": "snap_injected_01"})(), True

        @staticmethod
        def get_snapshot_ref(row: Any) -> str:
            return str(getattr(row, "snapshot_id", "") or "").strip()

        def close(self) -> None:
            return None

    monkeypatch.setattr(analysis_service_module, "AnalysisSnapshotRepository", _RepoStub, raising=False)
    registry = get_tool_registry()
    result = asyncio.run(ToolExecutor(registry).execute(
        ToolCall(
            id="tc_injected_01",
            name="analyze_market",
            arguments={"symbol": "ETHUSDT", "interval": "4h"},
        ),
        context=ToolContext(
            session_id="feishu_injected_state",
            request_id="req_injected_state",
        ),
        allowed_names={"analyze_market"},
    ))

    assert result.name == "analyze_market"
    assert captured["session_id"] == "feishu_injected_state"
    assert captured["request_id"] == "req_injected_state"
    assert captured["snapshot_payload"]["symbol"] == "ETHUSDT"
    assert captured["snapshot_payload"]["interval"] == "4h"
    assert isinstance(captured["raw_snapshot"], dict)
    properties = registry.get("analyze_market").parameters["properties"]
    assert "session_id" not in properties
    assert "request_id" not in properties


def test_detect_wyckoff_signals_v2_reports_spring_and_upthrust_fields():
    klines = _sample_klines_with_spring_signal()
    highs = [float(x["high"]) for x in klines]
    lows = [float(x["low"]) for x in klines]
    closes = [float(x["close"]) for x in klines]
    volumes = [float(x["volume"]) for x in klines]

    result = _detect_wyckoff_signals_v2(
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
    )
    assert "signals" in result
    assert "phase" in result
    assert "phase_transition" in result
    assert isinstance(result.get("confidence"), float)


@patch("domain.market.analysis_service._perform_market_analysis")
def test_analyze_market_multi_symbol_mode_summarizes_objective_facts(mock_perform):
    mock_perform.side_effect = [
        {
            "status": "success",
            "request_key": "ETHUSDT@4h",
            "symbol": "ETHUSDT",
            "interval": "4h",
            "analysis": {
                "symbol": "ETHUSDT",
                "interval": "4h",
                "ma_regime": "bullish",
                "ma_alignment": "bullish",
                "swing_structure": "higher_highs_higher_lows",
                "current_price": 2593.0,
                "range_position": 0.78,
            },
            "message": "ETHUSDT 4h 行情事实计算完成",
        },
        {
            "status": "success",
            "request_key": "SOLUSDT@4h",
            "symbol": "SOLUSDT",
            "interval": "4h",
            "analysis": {
                "symbol": "SOLUSDT",
                "interval": "4h",
                "ma_regime": "mixed",
                "ma_alignment": "mixed",
                "swing_structure": "mixed",
                "current_price": 180.0,
                "range_position": 0.45,
            },
            "message": "SOLUSDT 4h 行情事实计算完成",
        },
    ]

    result = analyze_market(**
        {
            "requests": [
                {"symbol": "ETHUSDT", "interval": "4h"},
                {"symbol": "SOLUSDT", "interval": "4h"},
            ]
        }
    )
    assert result["status"] == "success"
    assert "comparison" not in result
    assert "analyses" not in result
    assert "symbols" not in result
    assert [item["request_key"] for item in result["items"]] == ["ETHUSDT@4h", "SOLUSDT@4h"]
    assert "comparison_brief_v1" not in result
    assert "output_meta_v1" not in result


@patch("domain.market.analysis_service._perform_market_analysis")
def test_analyze_market_multi_requests_keeps_same_symbol_multi_interval(mock_perform):
    mock_perform.side_effect = [
        {
            "status": "success",
            "request_key": "SOLUSDT@1h",
            "symbol": "SOLUSDT",
            "interval": "1h",
            "analysis": {
                "symbol": "SOLUSDT",
                "interval": "1h",
            },
            "message": "SOLUSDT 1h 技术分析完成",
        },
        {
            "status": "success",
            "request_key": "SOLUSDT@4h",
            "symbol": "SOLUSDT",
            "interval": "4h",
            "analysis": {
                "symbol": "SOLUSDT",
                "interval": "4h",
            },
            "message": "SOLUSDT 4h 技术分析完成",
        },
    ]

    result = analyze_market(**
        {
            "requests": [
                {"symbol": "SOLUSDT", "interval": "1h"},
                {"symbol": "SOLUSDT", "interval": "4h"},
            ]
        }
    )

    assert result["status"] == "success"
    assert "symbols" not in result
    assert "requests" not in result
    assert "analyses" not in result
    assert [item["request_key"] for item in result["items"]] == ["SOLUSDT@1h", "SOLUSDT@4h"]
    assert result["items"][0]["analysis"]["interval"] == "1h"
    assert result["items"][1]["analysis"]["interval"] == "4h"
