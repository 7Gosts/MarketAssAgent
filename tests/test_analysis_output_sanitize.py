from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import patch

import domain.market.analysis_service as analysis_service_module
from domain.market.analysis_service import (
    _perform_market_analysis,
    analyze_market,
)
from domain.market.structure import (
    _assess_structure_signals,
    _detect_wyckoff_signals_v2,
    _structure_signal_rank,
)


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
    assert "confidence" not in payload.get("analysis", {})
    assert not re.search(r"置信度\s*\d+\s*%", text)


def test_assess_structure_signals_bullish_aligned():
    signals = _assess_structure_signals(
        "偏多",
        {"MA_short": 110.0, "MA_mid": 105.0, "MA_long": 100.0},
        {"support": [99.0, 98.0], "resistance": [112.0, 115.0]},
    )
    assert signals["ma_alignment"] == "bullish"
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


@patch("tools.market_data.fetch_market_data")
def test_analyze_market_returns_minimal_schema_v1(mock_fetch):
    mock_fetch.invoke.return_value = {"data": _sample_klines()}

    result = analyze_market.invoke({"symbol": "ETHUSDT", "interval": "4h"})
    assert result["status"] == "success"
    assert set(result.keys()) == {"status", "symbol", "interval", "analysis", "message"}
    assert "structure_signals" not in result["analysis"]
    assert "key_levels" not in result["analysis"]
    assert "structure" not in result["analysis"]
    assert "indicators" not in result["analysis"]
    assert "levels_v2" in result["analysis"]
    assert "trigger_conditions" in result["analysis"]
    assert "invalidation_conditions" in result["analysis"]
    assert "risk_flags" in result["analysis"]
    assert "actionability" in result["analysis"]
    assert "market_structure_v2" not in result["analysis"]
    assert "pattern_detection_v2" not in result["analysis"]
    assert "recent_klines_v1" in result["analysis"]
    assert "fib_v1" in result["analysis"]
    assert "level_zones_v1" in result["analysis"]
    levels_v2 = result["analysis"]["levels_v2"]
    assert "level_details" in levels_v2
    assert "support" in levels_v2["level_details"]
    assert "resistance" in levels_v2["level_details"]
    for detail in (levels_v2["level_details"].get("support") or []) + (
        levels_v2["level_details"].get("resistance") or []
    ):
        assert "price" in detail
        assert "primary_source" in detail
        assert "sources" in detail
    zones_v1 = result["analysis"]["level_zones_v1"]
    assert "lookback_bars" in zones_v1
    assert "support_zones" in zones_v1
    assert "resistance_zones" in zones_v1
    assert "bars" not in result["analysis"]["recent_klines_v1"]
    assert isinstance(result["analysis"]["recent_klines_v1"].get("summary"), list)
    assert len(result["analysis"]["recent_klines_v1"].get("summary") or []) <= 3
    fib_v1 = result["analysis"]["fib_v1"]
    assert set((fib_v1.get("levels") or {}).keys()) == {"23.6%", "38.2%", "50.0%", "61.8%"}
    assert fib_v1.get("current_zone") in {
        "above_swing_high",
        "below_swing_low",
        "0% ~ 23.6%",
        "23.6% ~ 38.2%",
        "38.2% ~ 50.0%",
        "50.0% ~ 61.8%",
        "61.8% ~ 100%",
        "unknown",
    }
    assert "compact_summary_v1" not in result
    assert "output_meta_v1" not in result
    assert "snapshot" not in result
    assert "confidence" not in result["analysis"]
    _assert_no_confidence_percent(result)


@patch("tools.market_data.fetch_market_data")
def test_analyze_market_persists_snapshot_to_db_from_tool_runtime(mock_fetch, monkeypatch):
    mock_fetch.invoke.return_value = {"data": _sample_klines()}
    captured: dict[str, Any] = {}
    monkeypatch.setattr(analysis_service_module, "get_postgres_dsn", lambda: "postgresql://test")

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
            return type("Row", (), {"snapshot_id": "snap_tool_db_01"})(), True

        @staticmethod
        def get_snapshot_ref(row: Any) -> str:
            return str(getattr(row, "snapshot_id", "") or "").strip()

        def close(self) -> None:
            return None

    monkeypatch.setattr(analysis_service_module, "AnalysisSnapshotRepository", _RepoStub)

    result = analyze_market.invoke(
        {"symbol": "ETHUSDT", "interval": "4h"},
        config={"configurable": {"thread_id": "s_tool_db_01", "request_id": "req_tool_01"}},
    )

    assert result["status"] == "success"
    assert captured["session_id"] == "s_tool_db_01"
    assert captured["request_id"] == "req_tool_01"
    assert captured["snapshot_payload"]["symbol"] == "ETHUSDT"
    assert captured["snapshot_payload"]["interval"] == "4h"
    assert isinstance(captured["raw_snapshot"], dict)
    assert captured["raw_snapshot"]["symbol"] == "ETHUSDT"


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
def test_analyze_market_multi_symbol_mode_ranks_by_v2_structure(mock_perform):
    mock_perform.side_effect = [
        {
            "status": "success",
            "symbol": "ETHUSDT",
            "interval": "4h",
            "analysis": {
                "symbol": "ETHUSDT",
                "interval": "4h",
                "trend": "偏多",
                "market_structure_v2": {
                    "structure_label": "channel_up",
                    "wyckoff_phase": "markup",
                    "confidence": 0.78,
                },
                "pattern_detection_v2": {"primary_pattern": "channel_up", "confidence": 0.78},
                "actionability": {"can_trade_now": True},
            },
            "snapshot": {"symbol": "ETHUSDT", "trend": "偏多"},
            "message": "ETHUSDT 4h 技术分析完成: 偏多，均线多头，与趋势一致",
        },
        {
            "status": "success",
            "symbol": "SOLUSDT",
            "interval": "4h",
            "analysis": {
                "symbol": "SOLUSDT",
                "interval": "4h",
                "trend": "震荡",
                "market_structure_v2": {
                    "structure_label": "rectangle",
                    "wyckoff_phase": "accumulation",
                    "confidence": 0.56,
                },
                "pattern_detection_v2": {"primary_pattern": "rectangle", "confidence": 0.56},
                "actionability": {"can_trade_now": False},
            },
            "snapshot": {"symbol": "SOLUSDT", "trend": "震荡"},
            "message": "SOLUSDT 4h 技术分析完成: 震荡，均线交叉，震荡结构",
        },
    ]

    result = analyze_market.invoke(
        {
            "requests": [
                {"symbol": "ETHUSDT", "interval": "4h"},
                {"symbol": "SOLUSDT", "interval": "4h"},
            ]
        }
    )
    assert result["status"] == "success"
    assert result["comparison"]["strongest"]["symbol"] == "ETHUSDT"
    assert result["comparison"]["weakest"]["symbol"] == "SOLUSDT"
    assert "comparison_brief_v1" not in result
    assert "output_meta_v1" not in result


@patch("domain.market.analysis_service._perform_market_analysis")
def test_analyze_market_multi_requests_keeps_same_symbol_multi_interval(mock_perform):
    mock_perform.side_effect = [
        {
            "status": "success",
            "symbol": "SOLUSDT",
            "interval": "1h",
            "analysis": {
                "symbol": "SOLUSDT",
                "interval": "1h",
                "trend": "偏多",
                "market_structure_v2": {"structure_label": "channel_up", "confidence": 0.61},
                "pattern_detection_v2": {"primary_pattern": "channel_up", "confidence": 0.61},
                "actionability": {"can_trade_now": True},
            },
            "message": "SOLUSDT 1h 技术分析完成",
        },
        {
            "status": "success",
            "symbol": "SOLUSDT",
            "interval": "4h",
            "analysis": {
                "symbol": "SOLUSDT",
                "interval": "4h",
                "trend": "偏空",
                "market_structure_v2": {"structure_label": "channel_down", "confidence": 0.57},
                "pattern_detection_v2": {"primary_pattern": "channel_down", "confidence": 0.57},
                "actionability": {"can_trade_now": False},
            },
            "message": "SOLUSDT 4h 技术分析完成",
        },
    ]

    result = analyze_market.invoke(
        {
            "requests": [
                {"symbol": "SOLUSDT", "interval": "1h"},
                {"symbol": "SOLUSDT", "interval": "4h"},
            ]
        }
    )

    assert result["status"] == "success"
    assert result["symbols"] == ["SOLUSDT"]
    assert len(result["requests"]) == 2
    assert set(result["analyses"].keys()) == {"SOLUSDT@1h", "SOLUSDT@4h"}
    assert result["analyses"]["SOLUSDT@1h"]["analysis"]["interval"] == "1h"
    assert result["analyses"]["SOLUSDT@4h"]["analysis"]["interval"] == "4h"
