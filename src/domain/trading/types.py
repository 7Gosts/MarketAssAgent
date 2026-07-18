from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class OrderBar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class OrderTransition:
    idea_id: str
    order_id: str
    event_type: str
    old_idea_state: str | None
    new_idea_state: str | None
    old_order_status: str | None
    new_order_status: str | None
    event_time: datetime
    event_price: float | None = None
    request_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    opened_at: datetime | None = None
    opened_price: float | None = None
    filled_at: datetime | None = None
    filled_price: float | None = None
    closed_at: datetime | None = None
    closed_price: float | None = None
    close_reason: str | None = None
    realized_pnl_pct: float | None = None


@dataclass(frozen=True)
class ReconcileAction:
    changed: bool
    reason: str
    transition: OrderTransition | None = None
    matched_bar: OrderBar | None = None
