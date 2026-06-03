"""测试 memory/snapshot.py — snapshot 提取与可读化。"""

from __future__ import annotations

from typing import Any


def test_extract_snapshot_non_dict_input():
    """extract_snapshot 对非 dict 输入应返回空 dict。"""
    from memory.snapshot import extract_snapshot
    assert extract_snapshot(None) == {}
    assert extract_snapshot("string") == {}
    assert extract_snapshot(42) == {}
    assert extract_snapshot([]) == {}


def test_extract_snapshot_basic_fields():
    """extract_snapshot 应正确提取 symbol, interval, provider, trend, last_price。"""
    from memory.snapshot import extract_snapshot
    raw = {
        "symbol": "BTC_USDT",
        "interval": "4h",
        "provider": "gateio",
        "trend": "偏多",
        "last_price": 67234.5,
    }
    result = extract_snapshot(raw)
    assert result["symbol"] == "BTC_USDT"
    assert result["interval"] == "4h"
    assert result["provider"] == "gateio"
    assert result["trend"] == "偏多"
    assert result["last_price"] == 67234.5


def test_extract_snapshot_nested_structures():
    """extract_snapshot 应提取嵌套结构（sma_snapshot, wyckoff_123, fib_zone）。"""
    from memory.snapshot import extract_snapshot
    raw = {
        "symbol": "BTC_USDT",
        "ma_snapshot": {"sma8": 66800, "sma20": 66500, "sma60": 64800},
        "wyckoff_123_v1": {
            "preferred_side": "long",
            "selected_setup": {
                "triggered": True,
                "entry": 67000,
                "stop": 65800,
                "tp1": 69500,
                "tp2": 72000,
            },
        },
        "price_vs_fib_zone": "0.618~0.786",
    }
    result = extract_snapshot(raw)
    assert result["sma_snapshot"] == {"sma8": 66800, "sma20": 66500, "sma60": 64800}
    assert result["wyckoff_123"]["side"] == "long"
    assert result["wyckoff_123"]["triggered"] is True
    assert result["wyckoff_123"]["entry"] == 67000
    assert result["fib_zone"] == "0.618~0.786"


def test_extract_snapshot_analysis_result_wrapper():
    """extract_snapshot 应处理 analysis_result 嵌套包装。"""
    from memory.snapshot import extract_snapshot
    raw = {
        "analysis_result": {
            "symbol": "BTC_USDT",
            "trend": "偏多",
            "last_price": 67234.5,
        }
    }
    result = extract_snapshot(raw)
    assert result["symbol"] == "BTC_USDT"
    assert result["trend"] == "偏多"


def test_snapshot_to_context_str_empty():
    """snapshot_to_context_str 对空 snapshot 应返回空字符串。"""
    from memory.snapshot import snapshot_to_context_str
    assert snapshot_to_context_str({}) == ""
    assert snapshot_to_context_str(None) == ""


def test_snapshot_to_context_str_readable_output(sample_snapshot):
    """snapshot_to_context_str 应生成人类可读的上下文字符串。"""
    from memory.snapshot import snapshot_to_context_str
    result = snapshot_to_context_str(sample_snapshot)
    assert "BTC_USDT" in result
    assert "偏多" in result
    assert "67234.5" in result
    assert "0.618~0.786" in result
    assert "sma8" in result
    assert "long" in result
    # 中文版输出"已触发"而非"triggered"
    assert "已触发" in result


def test_snapshot_to_context_str_partial_snapshot():
    """snapshot_to_context_str 应处理部分 snapshot（只有 symbol，没有趋势）。"""
    from memory.snapshot import snapshot_to_context_str
    partial = {"symbol": "ETH_USDT"}
    result = snapshot_to_context_str(partial)
    assert "ETH_USDT" in result
    assert "趋势" not in result