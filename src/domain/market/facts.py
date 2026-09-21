"""Typed market facts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class MarketFacts(BaseModel):
    """Flat top-level contract for objective facts produced by analyze_market."""

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
