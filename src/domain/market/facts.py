"""Typed market facts and the single LLM presentation boundary."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


_FIELD_LABELS = {
    "ma_regime": {"bullish": "均线偏多", "bearish": "均线偏空", "mixed": "均线方向分化", "unavailable": "数据不足"},
    "ma_alignment": {"bullish": "多头排列", "bearish": "空头排列", "mixed": "均线交错"},
    "position": {"above": "均线上方", "below": "均线下方", "equal": "与均线持平"},
    "swing_structure": {
        "higher_highs_higher_lows": "高点与低点同步抬高",
        "lower_highs_lower_lows": "高点与低点同步下移",
        "higher_lows": "低点逐步抬高",
        "lower_highs": "高点逐步下移",
        "mixed": "摆动结构分化",
        "insufficient": "数据不足",
    },
    "volume_state": {"expanding": "放量", "contracting": "缩量", "stable": "量能平稳", "unavailable": "数据不足"},
    "direction": {"up": "上涨", "down": "下跌", "flat": "平盘"},
    "event": {"break_up": "向上突破", "break_down": "向下跌破", "inside": "内包整理"},
    "volume_tag": {"expanded": "放量", "contracted": "缩量", "normal": "量能正常"},
    "role": {"support": "支撑", "resistance": "阻力"},
    "strength": {"strong": "强", "medium": "中等", "weak": "弱"},
    "source_type": {"fractal_level": "分形关键位", "fib_retracement": "斐波那契回撤位", "level": "关键位"},
    "source": {"pivot_cluster_50": "近50根K线关键位聚类"},
    "source_labels": {"fractal": "分形", "recent_12": "近12根K线"},
    "current_zone": {
        "unknown": "数据不足",
        "above_swing_high": "高于摆动高点",
        "below_swing_low": "低于摆动低点",
        "0% ~ 23.6%": "0% 至 23.6%",
        "23.6% ~ 38.2%": "23.6% 至 38.2%",
        "38.2% ~ 50.0%": "38.2% 至 50.0%",
        "50.0% ~ 61.8%": "50.0% 至 61.8%",
        "61.8% ~ 100%": "61.8% 至 100%",
    },
}


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _localized_value(field: str, value: str) -> str:
    labels = _FIELD_LABELS[field]
    if value in labels:
        return labels[value]
    if value in labels.values() or _contains_chinese(value):
        return value
    raise ValueError(f"未知行情字段值: {field}={value}")


def localize_market_fields(value: Any, field: str = "") -> Any:
    """Return a localized copy while preserving the validated JSON shape."""
    if isinstance(value, dict):
        if field == "ma_regime_distribution":
            return {_localized_value("ma_regime", str(key)): count for key, count in value.items()}
        return {key: localize_market_fields(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [localize_market_fields(item, field) for item in value]
    if isinstance(value, str) and field in _FIELD_LABELS:
        return _localized_value(field, value)
    return value


class MarketFacts(BaseModel):
    """Flat contract for objective facts produced by analyze_market."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "market_facts.v1"
    symbol: str
    interval: str
    timestamp: str
    current_price: float
    ma_regime: str
    ma_alignment: str
    ma_periods: dict[str, int]
    ma_values: dict[str, float | None]
    price_vs_ma: dict[str, dict[str, Any]]
    ma_slopes_pct: dict[str, float]
    swing_structure: str
    swing_points: dict[str, list[float]]
    support_levels: list[float]
    resistance_levels: list[float]
    distance_to_levels_pct: dict[str, float | None]
    level_details: dict[str, list[dict[str, Any]]]
    volume_state: str
    volume_ratio: float | None = None
    recent_candles: list[dict[str, Any]]
    range_position: float | None = None
    fib_v1: dict[str, Any]
    level_zones_v1: dict[str, Any]
    requested_symbol: str | None = None
    resolution: dict[str, Any] | None = None
    request_key: str | None = None
