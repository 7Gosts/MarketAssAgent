"""Trading domain placeholder for trade plan logic."""

from __future__ import annotations

from typing import Any


def build_trade_plan_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Return normalized trade-plan context for future domain migration."""
    return dict(payload or {})

