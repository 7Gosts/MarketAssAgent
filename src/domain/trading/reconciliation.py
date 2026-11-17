from __future__ import annotations

from datetime import timezone

from infrastructure.persistence.models import JournalIdea, PaperOrder

from .types import OrderPriceEvent, OrderTransition, ReconcileAction


def _calculate_pnl_pct(order: PaperOrder, close_price: float) -> float | None:
    entry = getattr(order, "filled_price", None) or getattr(order, "trigger_price", None) or getattr(order, "limit_price", None)
    if entry in (None, 0):
        return None
    entry_value = float(entry)
    if str(order.side or "").lower() == "long":
        return round((close_price - entry_value) / entry_value * 100.0, 6)
    return round((entry_value - close_price) / entry_value * 100.0, 6)


def decide_price_event_action(
    idea: JournalIdea,
    order: PaperOrder,
    event: OrderPriceEvent,
    *,
    zone_touched: bool = False,
) -> tuple[ReconcileAction, bool]:
    """Advance a simulated order using one timestamped market price."""
    status = str(order.status or "")
    side = str(order.side or "").lower()
    price = float(event.price)
    event_time = event.time.astimezone(timezone.utc) if event.time.tzinfo else event.time.replace(tzinfo=timezone.utc)

    valid_until = getattr(order, "valid_until", None)
    if status == "pending_trigger" and valid_until is not None:
        valid_until = valid_until.astimezone(timezone.utc) if valid_until.tzinfo else valid_until.replace(tzinfo=timezone.utc)
        if valid_until <= event_time:
            transition = OrderTransition(
                idea_id=idea.idea_id,
                order_id=order.order_id,
                event_type="order_expired",
                old_idea_state=idea.state,
                new_idea_state="expired",
                old_order_status=order.status,
                new_order_status="expired",
                event_time=valid_until,
                payload={"reason": "valid_until_reached", "source": "gate_spot_trade"},
            )
            return ReconcileAction(changed=True, reason="expired", transition=transition), zone_touched

    if status == "pending_trigger":
        if str(order.order_type or "") == "zone_reclaim_close":
            low = float(order.entry_zone_low or 0.0)
            high = float(order.entry_zone_high or 0.0)
            zone_touched = zone_touched or low <= price <= high
            if side == "long":
                triggered = zone_touched and order.confirm_close_above is not None and price >= float(order.confirm_close_above)
            else:
                triggered = zone_touched and order.confirm_close_below is not None and price <= float(order.confirm_close_below)
        else:
            limit_price = float(order.limit_price or order.trigger_price or order.entry_zone_high or order.entry_zone_low or 0.0)
            triggered = limit_price > 0 and (price <= limit_price if side == "long" else price >= limit_price)

        if triggered:
            fill_price = price
            if str(order.order_type or "") != "zone_reclaim_close":
                fill_price = float(order.limit_price or order.trigger_price or order.entry_zone_high or order.entry_zone_low or price)
            transition = OrderTransition(
                idea_id=idea.idea_id,
                order_id=order.order_id,
                event_type="order_filled",
                old_idea_state=idea.state,
                new_idea_state="open",
                old_order_status=order.status,
                new_order_status="filled",
                event_time=event_time,
                event_price=fill_price,
                payload={"reason": "trigger_hit", "source": "gate_spot_trade", "trade_id": event.trade_id},
                opened_at=event_time,
                opened_price=fill_price,
                filled_at=event_time,
                filled_price=fill_price,
            )
            return ReconcileAction(changed=True, reason="filled", transition=transition), zone_touched
        return ReconcileAction(changed=False, reason="unchanged"), zone_touched

    if status == "filled":
        target_price = float(order.final_target if order.final_target is not None else order.tp1 or 0.0)
        stop_loss = float(order.stop_loss) if order.stop_loss is not None else None
        stop_hit = stop_loss is not None and (price <= stop_loss if side == "long" else price >= stop_loss)
        target_hit = target_price > 0 and (price >= target_price if side == "long" else price <= target_price)
        if stop_hit or target_hit:
            reason = "sl" if stop_hit else "tp"
            close_price = float(stop_loss) if stop_hit else target_price
            transition = OrderTransition(
                idea_id=idea.idea_id,
                order_id=order.order_id,
                event_type="order_closed_sl" if stop_hit else "order_closed_tp",
                old_idea_state=idea.state,
                new_idea_state="closed",
                old_order_status=order.status,
                new_order_status="closed",
                event_time=event_time,
                event_price=close_price,
                payload={"reason": reason, "source": "gate_spot_trade", "trade_id": event.trade_id},
                closed_at=event_time,
                closed_price=close_price,
                close_reason=reason,
                realized_pnl_pct=_calculate_pnl_pct(order, close_price),
            )
            return ReconcileAction(changed=True, reason=f"closed_{reason}", transition=transition), zone_touched

    return ReconcileAction(changed=False, reason="unchanged"), zone_touched
