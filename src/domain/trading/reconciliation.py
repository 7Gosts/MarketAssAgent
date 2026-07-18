from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from infrastructure.persistence.models import JournalIdea, PaperOrder

from .types import OrderBar, OrderTransition, ReconcileAction


def _parse_bar_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_bars(rows: list[dict[str, Any]]) -> list[OrderBar]:
    bars: list[OrderBar] = []
    for row in rows:
        dt = _parse_bar_time(row.get("time"))
        if dt is None:
            continue
        try:
            bars.append(
                OrderBar(
                    time=dt,
                    open=float(row.get("open")),
                    high=float(row.get("high")),
                    low=float(row.get("low")),
                    close=float(row.get("close")),
                    volume=float(row.get("volume", 0.0) or 0.0),
                )
            )
        except (TypeError, ValueError):
            continue
    bars.sort(key=lambda item: item.time)
    return bars


def _filter_candidate_bars(order: PaperOrder, bars: list[OrderBar], *, allow_historical_bars: bool) -> list[OrderBar]:
    if allow_historical_bars:
        return bars
    baseline = getattr(order, "updated_at", None) or getattr(order, "created_at", None)
    if baseline is None:
        return bars
    if baseline.tzinfo is None:
        baseline = baseline.replace(tzinfo=timezone.utc)
    else:
        baseline = baseline.astimezone(timezone.utc)
    return [bar for bar in bars if bar.time > baseline]


def _event_payload(base: dict[str, Any], bar: OrderBar) -> dict[str, Any]:
    return {
        **base,
        "matched_bar": {
            "time": bar.time.isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        },
    }


def _build_fill_transition(idea: JournalIdea, order: PaperOrder, bar: OrderBar, *, price: float) -> OrderTransition:
    return OrderTransition(
        idea_id=idea.idea_id,
        order_id=order.order_id,
        event_type="order_filled",
        old_idea_state=idea.state,
        new_idea_state="open",
        old_order_status=order.status,
        new_order_status="filled",
        event_time=bar.time,
        event_price=price,
        payload=_event_payload({"reason": "trigger_hit"}, bar),
        opened_at=bar.time,
        opened_price=price,
        filled_at=bar.time,
        filled_price=price,
    )


def _calculate_pnl_pct(order: PaperOrder, close_price: float) -> float | None:
    entry = getattr(order, "filled_price", None) or getattr(order, "trigger_price", None) or getattr(order, "limit_price", None)
    if entry in (None, 0):
        return None
    entry_value = float(entry)
    if str(order.side or "").lower() == "long":
        return round((close_price - entry_value) / entry_value * 100.0, 6)
    return round((entry_value - close_price) / entry_value * 100.0, 6)


def _build_close_transition(
    idea: JournalIdea,
    order: PaperOrder,
    bar: OrderBar,
    *,
    close_reason: str,
    price: float,
) -> OrderTransition:
    event_type = {
        "tp": "order_closed_tp",
        "sl": "order_closed_sl",
        "invalidation": "order_closed_invalidation",
        "timeout": "order_closed_timeout",
    }.get(close_reason, "order_closed_manual")
    return OrderTransition(
        idea_id=idea.idea_id,
        order_id=order.order_id,
        event_type=event_type,
        old_idea_state=idea.state,
        new_idea_state="closed",
        old_order_status=order.status,
        new_order_status="closed",
        event_time=bar.time,
        event_price=price,
        payload=_event_payload({"reason": close_reason}, bar),
        closed_at=bar.time,
        closed_price=price,
        close_reason=close_reason,
        realized_pnl_pct=_calculate_pnl_pct(order, price),
    )


def _build_expired_transition(idea: JournalIdea, order: PaperOrder, when: datetime) -> OrderTransition:
    return OrderTransition(
        idea_id=idea.idea_id,
        order_id=order.order_id,
        event_type="order_expired",
        old_idea_state=idea.state,
        new_idea_state="expired",
        old_order_status=order.status,
        new_order_status="expired",
        event_time=when,
        payload={"reason": "valid_until_reached"},
    )


def _pending_fill_price(order: PaperOrder, bar: OrderBar) -> float:
    if order.limit_price is not None:
        return float(order.limit_price)
    if order.trigger_price is not None:
        return float(order.trigger_price)
    if order.confirm_close_above is not None:
        return float(order.confirm_close_above)
    if order.confirm_close_below is not None:
        return float(order.confirm_close_below)
    return float(bar.close)


def decide_reconcile_action(
    idea: JournalIdea,
    order: PaperOrder,
    bars: list[OrderBar],
    *,
    allow_historical_bars: bool = False,
) -> ReconcileAction:
    relevant_bars = _filter_candidate_bars(order, bars, allow_historical_bars=allow_historical_bars)
    if not relevant_bars:
        return ReconcileAction(changed=False, reason="no_new_bar")

    valid_until = getattr(order, "valid_until", None)
    if valid_until is not None:
        latest_bar_time = relevant_bars[-1].time
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        else:
            valid_until = valid_until.astimezone(timezone.utc)
        if str(order.status or "") == "pending_trigger" and latest_bar_time >= valid_until:
            transition = _build_expired_transition(idea, order, valid_until)
            return ReconcileAction(changed=True, reason="expired", transition=transition)

    side = str(order.side or "").lower()
    order_type = str(order.order_type or "")
    status = str(order.status or "")

    if status == "pending_trigger":
        for bar in relevant_bars:
            triggered = False
            if order_type == "breakout_stop":
                if side == "long" and order.trigger_price is not None:
                    triggered = bar.high >= float(order.trigger_price)
                elif side == "short" and order.trigger_price is not None:
                    triggered = bar.low <= float(order.trigger_price)
            elif order_type == "pullback_limit":
                low = float(order.entry_zone_low if order.entry_zone_low is not None else order.limit_price or 0.0)
                high = float(order.entry_zone_high if order.entry_zone_high is not None else order.limit_price or 0.0)
                triggered = bar.low <= high and bar.high >= low
            elif order_type == "zone_reclaim_close":
                low = float(order.entry_zone_low if order.entry_zone_low is not None else 0.0)
                high = float(order.entry_zone_high if order.entry_zone_high is not None else 0.0)
                in_zone = bar.low <= high and bar.high >= low
                if side == "long":
                    triggered = in_zone and order.confirm_close_above is not None and bar.close >= float(order.confirm_close_above)
                else:
                    triggered = in_zone and order.confirm_close_below is not None and bar.close <= float(order.confirm_close_below)
            if triggered:
                transition = _build_fill_transition(idea, order, bar, price=_pending_fill_price(order, bar))
                return ReconcileAction(changed=True, reason="filled", transition=transition, matched_bar=bar)
        return ReconcileAction(changed=False, reason="unchanged")

    if status == "filled":
        target_price = float(order.final_target if order.final_target is not None else order.tp1 or 0.0)
        stop_loss = float(order.stop_loss) if order.stop_loss is not None else None
        for bar in relevant_bars:
            stop_hit = False
            target_hit = False
            if side == "long":
                stop_hit = stop_loss is not None and bar.low <= stop_loss
                target_hit = target_price > 0 and bar.high >= target_price
            else:
                stop_hit = stop_loss is not None and bar.high >= stop_loss
                target_hit = target_price > 0 and bar.low <= target_price

            if stop_hit:
                transition = _build_close_transition(idea, order, bar, close_reason="sl", price=float(stop_loss))
                return ReconcileAction(changed=True, reason="closed_sl", transition=transition, matched_bar=bar)
            if target_hit:
                transition = _build_close_transition(idea, order, bar, close_reason="tp", price=float(target_price))
                return ReconcileAction(changed=True, reason="closed_tp", transition=transition, matched_bar=bar)
        return ReconcileAction(changed=False, reason="unchanged")

    return ReconcileAction(changed=False, reason="terminal_status")
