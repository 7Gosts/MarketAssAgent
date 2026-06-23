from __future__ import annotations

import json
import re
from unittest.mock import patch

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


def _sample_klines_for_markup_transition() -> list[dict]:
    rows: list[dict] = []
    # prior bars: tight range (accumulation-like)
    for i in range(40):
        close = 100.0 + (i % 3) * 0.05
        rows.append(
            {
                "open": close - 0.03,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1200 - i * 2,
            }
        )
    # recent bars: strong markup
    for i in range(20):
        close = 101.0 + i * 0.55
        rows.append(
            {
                "open": close - 0.2,
                "high": close + 0.8,
                "low": close - 0.8,
                "close": close,
                "volume": 1050 + i * 4,
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
def test_analyze_market_prefers_v2_and_hides_legacy_fields(mock_fetch):
    mock_fetch.invoke.return_value = {"data": _sample_klines()}

    result = analyze_market.invoke({"symbol": "ETHUSDT", "interval": "4h"})
    assert result["status"] == "success"
    assert "structure_signals" not in result["analysis"]
    assert "key_levels" not in result["analysis"]
    assert "structure" not in result["analysis"]
    assert "indicators" not in result["analysis"]
    assert "levels_v2" in result["analysis"]
    assert "trigger_conditions" in result["analysis"]
    assert "invalidation_conditions" in result["analysis"]
    assert "risk_flags" in result["analysis"]
    assert "actionability" in result["analysis"]
    assert "market_structure_v2" in result["analysis"]
    assert "pattern_detection_v2" in result["analysis"]
    assert result["analysis"]["market_structure_v2"]["structure_label"] in {
        "accumulation",
        "markup",
        "distribution",
        "markdown",
        "triangle_convergence",
        "rectangle",
        "expanding_triangle",
        "channel_up",
        "channel_down",
        "unknown",
    }
    assert "wyckoff_phase" in result["analysis"]["market_structure_v2"]
    assert "multi_pattern_overlap" in result["analysis"]["market_structure_v2"]
    assert isinstance(result["analysis"]["market_structure_v2"]["multi_pattern_overlap"], list)
    assert "primary_pattern" in result["analysis"]["pattern_detection_v2"]
    assert "wyckoff_phase" in result["analysis"]["pattern_detection_v2"]
    assert "multi_pattern_overlap" in result["analysis"]["pattern_detection_v2"]
    assert "compact_summary_v1" in result
    assert "output_meta_v1" in result
    assert result["compact_summary_v1"]["symbol"] == "ETHUSDT"
    assert "structure_label" in result["compact_summary_v1"]
    assert "pattern_name" in result["compact_summary_v1"]
    assert "omit_candidates" in result["compact_summary_v1"]
    assert result["output_meta_v1"]["analysis_chars"] >= result["output_meta_v1"]["compact_chars"]
    assert "confidence" not in result["analysis"]
    _assert_no_confidence_percent(result)


@patch("tools.market_data.fetch_market_data")
def test_analyze_market_v2_wyckoff_and_overlap_fields(mock_fetch):
    mock_fetch.invoke.return_value = {"data": _sample_klines_with_spring_signal()}

    result = analyze_market.invoke({"symbol": "BTCUSDT", "interval": "1d"})
    assert result["status"] == "success"

    market_structure = result["analysis"]["market_structure_v2"]
    pattern = result["analysis"]["pattern_detection_v2"]
    overlap = market_structure.get("multi_pattern_overlap") or []
    assert isinstance(overlap, list)
    assert len(overlap) >= 1
    for item in overlap:
        assert isinstance(item, dict)
        assert {"pattern", "confidence", "reason"}.issubset(item.keys())
        assert isinstance(item["pattern"], str) and item["pattern"]
        assert isinstance(item["reason"], str) and item["reason"]
        assert 0.0 <= float(item["confidence"]) <= 1.0
    scores = [float(item["confidence"]) for item in overlap]
    assert scores == sorted(scores, reverse=True)
    assert any(
        any(ch.isdigit() for ch in str(ev))
        for ev in (market_structure.get("evidence") or [])
    )
    assert market_structure.get("wyckoff_phase") in {
        "accumulation",
        "markup",
        "distribution",
        "markdown",
        None,
    }
    assert "wyckoff_phase" in pattern
    assert "multi_pattern_overlap" in pattern


@patch("tools.market_data.fetch_market_data")
def test_analyze_market_v2_detects_upthrust_distribution(mock_fetch):
    mock_fetch.invoke.return_value = {"data": _sample_klines_with_upthrust_signal()}

    result = analyze_market.invoke({"symbol": "BTCUSDT", "interval": "1d"})
    assert result["status"] == "success"

    market_structure = result["analysis"]["market_structure_v2"]
    assert market_structure.get("wyckoff_phase") in {"distribution", "markdown", "accumulation", None}
    assert "upthrust" in (market_structure.get("wyckoff_signals") or [])
    assert market_structure.get("spring_upthrust_detected") is True


@patch("tools.market_data.fetch_market_data")
def test_analyze_market_v2_detects_phase_transition(mock_fetch):
    mock_fetch.invoke.return_value = {"data": _sample_klines_for_markup_transition()}

    result = analyze_market.invoke({"symbol": "ETHUSDT", "interval": "4h"})
    assert result["status"] == "success"

    market_structure = result["analysis"]["market_structure_v2"]
    transition = market_structure.get("wyckoff_phase_transition")
    assert transition is None or transition.endswith("_to_markup") or transition.endswith("_to_markup_watch")
    assert result["analysis"]["pattern_detection_v2"].get("wyckoff_phase_transition") == transition


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

    result = analyze_market.invoke({"symbol_interval_map": {"ETHUSDT": "4h", "SOLUSDT": "4h"}})
    assert result["status"] == "success"
    assert result["comparison"]["strongest"]["symbol"] == "ETHUSDT"
    assert result["comparison"]["weakest"]["symbol"] == "SOLUSDT"
    assert "comparison_brief_v1" in result
    assert "output_meta_v1" in result
    assert result["comparison_brief_v1"]["strongest_symbol"] == "ETHUSDT"
