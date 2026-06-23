from __future__ import annotations

import json
import re
from unittest.mock import patch

from tools.technical_analysis import (
    _assess_structure_signals,
    _perform_market_analysis,
    _structure_signal_rank,
    analyze_market,
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
def test_analyze_market_exposes_structure_signals(mock_fetch):
    mock_fetch.invoke.return_value = {"data": _sample_klines()}

    result = analyze_market.invoke({"symbol": "ETHUSDT", "interval": "4h"})
    assert result["status"] == "success"
    assert "structure_signals" in result["analysis"]
    assert "levels_v2" in result["analysis"]
    assert "trigger_conditions" in result["analysis"]
    assert "invalidation_conditions" in result["analysis"]
    assert "risk_flags" in result["analysis"]
    assert "actionability" in result["analysis"]
    assert "market_structure_v1" in result["analysis"]
    assert "pattern_detection_v1" in result["analysis"]
    assert result["analysis"]["market_structure_v1"]["structure_label"] in {
        "trending",
        "ranging",
        "triangle_convergence",
        "rectangle",
        "expanding",
        "unknown",
    }
    assert "primary_pattern" in result["analysis"]["pattern_detection_v1"]
    assert "compact_summary_v1" in result
    assert "output_meta_v1" in result
    assert result["compact_summary_v1"]["symbol"] == "ETHUSDT"
    assert "structure_label" in result["compact_summary_v1"]
    assert "pattern_name" in result["compact_summary_v1"]
    assert "omit_candidates" in result["compact_summary_v1"]
    assert result["output_meta_v1"]["analysis_chars"] >= result["output_meta_v1"]["compact_chars"]
    assert "confidence" not in result["analysis"]
    _assert_no_confidence_percent(result)


@patch("tools.technical_analysis._perform_market_analysis")
def test_analyze_market_multi_symbol_mode_ranks_by_structure_signals(mock_perform):
    bullish_signals = _assess_structure_signals(
        "偏多",
        {"MA_short": 110.0, "MA_mid": 105.0, "MA_long": 100.0},
        {"support": [99.0], "resistance": [112.0]},
    )
    mixed_signals = _assess_structure_signals(
        "震荡",
        {"MA_short": 105.0, "MA_mid": 104.0, "MA_long": 100.0},
        {"support": [99.0], "resistance": [112.0]},
    )
    mock_perform.side_effect = [
        {
            "status": "success",
            "symbol": "ETHUSDT",
            "interval": "4h",
            "analysis": {
                "symbol": "ETHUSDT",
                "interval": "4h",
                "trend": "偏多",
                "structure_signals": bullish_signals,
            },
            "snapshot": {"symbol": "ETHUSDT", "trend": "偏多", "structure_signals": bullish_signals},
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
                "structure_signals": mixed_signals,
            },
            "snapshot": {"symbol": "SOLUSDT", "trend": "震荡", "structure_signals": mixed_signals},
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
