from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import uuid
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from infrastructure.persistence.analysis_snapshot_repository import normalize_snapshot_symbol
from domain.trading.types import OrderTransition

from .db import get_session
from .models import JournalEvent, JournalIdea, PaperOrder


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


@dataclass(frozen=True)
class CreateTrackedOrderCommand:
    session_id: str
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size: float | None = None
    request_id: str = ""
    source_snapshot_id: str = ""
    interval: str = "manual"
    order_type: str = "breakout_stop"
    position_state: str = "pending"
    setup_type: str = "manual"
    market: str = ""
    provider: str = "marketassagent"
    strategy_reason: str = ""
    valid_until: str = ""
    trigger_price: float | None = None
    limit_price: float | None = None
    entry_zone_low: float | None = None
    entry_zone_high: float | None = None
    tp2: float | None = None
    final_target: float | None = None
    timeout_bars: int | None = None
    meta: dict[str, Any] | None = None
    simulation_rule: dict[str, Any] | None = None


@dataclass(frozen=True)
class TrackedOrderBundle:
    idea: JournalIdea
    order: PaperOrder
    created: bool


class PaperTradingRepository:
    def __init__(self, session: Optional[Session] = None):
        self.session = session or get_session()
        self._owns_session = session is None

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    @staticmethod
    def build_ids(command: CreateTrackedOrderCommand) -> tuple[str, str, str]:
        clean_session = _clean_text(command.session_id) or "default"
        clean_symbol = normalize_snapshot_symbol(command.symbol)
        clean_request = _clean_text(command.request_id)
        if clean_request:
            idea_id = _stable_id("idea", clean_session, clean_request, clean_symbol, _clean_text(command.interval))
            order_id = _stable_id("ord", clean_session, clean_request, clean_symbol, _clean_text(command.interval))
            event_id = _stable_id("evt", clean_session, clean_request, clean_symbol, "create")
            return idea_id, order_id, event_id
        suffix = uuid.uuid4().hex[:24]
        return f"idea_{suffix}", f"ord_{suffix}", f"evt_{suffix}"

    def get_bundle_by_idea_id(self, idea_id: str) -> TrackedOrderBundle | None:
        clean_idea_id = _clean_text(idea_id)
        if not clean_idea_id:
            return None
        idea = self.session.query(JournalIdea).filter(JournalIdea.idea_id == clean_idea_id).first()
        if idea is None:
            return None
        order = (
            self.session.query(PaperOrder)
            .filter(PaperOrder.idea_id == clean_idea_id)
            .order_by(PaperOrder.created_at.desc(), PaperOrder.id.desc())
            .first()
        )
        if order is None:
            return None
        return TrackedOrderBundle(idea=idea, order=order, created=False)

    def get_order_bundle(self, *, order_id: str) -> TrackedOrderBundle | None:
        clean_order_id = _clean_text(order_id)
        if not clean_order_id:
            return None
        order = self.session.query(PaperOrder).filter(PaperOrder.order_id == clean_order_id).first()
        if order is None:
            return None
        idea = self.session.query(JournalIdea).filter(JournalIdea.idea_id == order.idea_id).first()
        if idea is None:
            return None
        return TrackedOrderBundle(idea=idea, order=order, created=False)

    def create_tracked_order(self, command: CreateTrackedOrderCommand) -> TrackedOrderBundle:
        clean_session = _clean_text(command.session_id) or "default"
        clean_symbol = _clean_text(command.symbol).upper()
        symbol_key = normalize_snapshot_symbol(clean_symbol)
        clean_direction = _clean_text(command.direction).lower()
        clean_interval = _clean_text(command.interval) or "manual"
        clean_order_type = _clean_text(command.order_type) or "breakout_stop"
        clean_position_state = _clean_text(command.position_state).lower() or "pending"
        if clean_direction not in {"long", "short"}:
            raise ValueError("direction 仅支持 long / short")
        if clean_order_type not in {"breakout_stop", "pullback_limit", "zone_reclaim_close"}:
            raise ValueError("order_type 非法")
        if clean_position_state not in {"pending", "open"}:
            raise ValueError("position_state 仅支持 pending / open")

        entry_price = _clean_float(command.entry_price)
        stop_loss = _clean_float(command.stop_loss)
        take_profit = _clean_float(command.take_profit)
        position_size = _clean_float(command.position_size)
        if entry_price is None or stop_loss is None or take_profit is None:
            raise ValueError("entry_price / stop_loss / take_profit 必须是有效数字")
        if position_size is not None and position_size <= 0:
            raise ValueError("position_size 必须大于 0")

        idea_id, order_id, event_id = self.build_ids(command)
        now = _utc_now()
        valid_until = _parse_datetime(command.valid_until)
        trigger_price = _clean_float(command.trigger_price)
        limit_price = _clean_float(command.limit_price)
        entry_zone_low = _clean_float(command.entry_zone_low)
        entry_zone_high = _clean_float(command.entry_zone_high)
        if entry_zone_low is None:
            entry_zone_low = entry_price
        if entry_zone_high is None:
            entry_zone_high = entry_price
        if trigger_price is None and clean_order_type == "breakout_stop":
            trigger_price = entry_price
        if limit_price is None and clean_order_type == "pullback_limit":
            limit_price = entry_price
        final_target = _clean_float(command.final_target)
        tp2 = _clean_float(command.tp2)
        idea_state = "open" if clean_position_state == "open" else "watch"
        order_status = "filled" if clean_position_state == "open" else "pending_trigger"
        opened_at = now if clean_position_state == "open" else None
        filled_at = now if clean_position_state == "open" else None
        filled_price = entry_price if clean_position_state == "open" else None

        idea = JournalIdea(
            idea_id=idea_id,
            session_id=clean_session,
            source_request_id=_clean_text(command.request_id),
            source_snapshot_id=_clean_text(command.source_snapshot_id) or None,
            current_order_id=order_id,
            symbol=clean_symbol,
            symbol_key=symbol_key,
            market=_clean_text(command.market) or None,
            provider=_clean_text(command.provider) or "marketassagent",
            interval=clean_interval,
            side=clean_direction,
            setup_type=_clean_text(command.setup_type) or "manual",
            state=idea_state,
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            stop_loss=stop_loss,
            tp1=take_profit,
            tp2=tp2,
            final_target=final_target if final_target is not None else take_profit,
            valid_until=valid_until,
            opened_at=opened_at,
            opened_price=filled_price,
            strategy_reason=_clean_text(command.strategy_reason) or None,
            meta_json=dict(command.meta or {}),
            created_at=now,
            updated_at=now,
        )
        order = PaperOrder(
            order_id=order_id,
            idea_id=idea_id,
            symbol=clean_symbol,
            symbol_key=symbol_key,
            market=_clean_text(command.market) or None,
            provider=_clean_text(command.provider) or "marketassagent",
            interval=clean_interval,
            side=clean_direction,
            order_type=clean_order_type,
            status=order_status,
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            position_size=position_size,
            trigger_price=trigger_price,
            limit_price=limit_price,
            stop_loss=stop_loss,
            tp1=take_profit,
            tp2=tp2,
            final_target=final_target if final_target is not None else take_profit,
            valid_until=valid_until,
            timeout_bars=command.timeout_bars,
            filled_at=filled_at,
            filled_price=filled_price,
            simulation_rule_json=dict(command.simulation_rule or {}),
            created_at=now,
            updated_at=now,
        )
        event = JournalEvent(
            event_id=event_id,
            idea_id=idea_id,
            order_id=order_id,
            session_id=clean_session,
            event_type="position_opened" if clean_position_state == "open" else "order_created",
            new_idea_state=idea_state,
            new_order_status=order_status,
            event_time=now,
            event_price=entry_price,
            request_id=_clean_text(command.request_id),
            payload_json={
                "symbol": clean_symbol,
                "interval": clean_interval,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "position_size": position_size,
                "position_state": clean_position_state,
            },
            created_at=now,
        )

        try:
            self.session.add(idea)
            self.session.flush()
            self.session.add(order)
            self.session.flush()
            self.session.add(event)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.get_bundle_by_idea_id(idea_id)
            if existing is None:
                raise
            return existing

        self.session.refresh(idea)
        self.session.refresh(order)
        return TrackedOrderBundle(idea=idea, order=order, created=True)

    def apply_transition(self, transition: OrderTransition, *, request_id: str = "") -> TrackedOrderBundle:
        bundle = self.get_order_bundle(order_id=transition.order_id)
        if bundle is None:
            raise ValueError(f"order_id 不存在: {transition.order_id}")

        idea = bundle.idea
        order = bundle.order
        current_idea_state = _clean_text(idea.state)
        current_order_status = _clean_text(order.status)

        expected_idea_state = _clean_text(transition.old_idea_state)
        expected_order_status = _clean_text(transition.old_order_status)
        if expected_idea_state and current_idea_state != expected_idea_state:
            raise ValueError(
                f"idea 状态不匹配: current={current_idea_state} expected={expected_idea_state}"
            )
        if expected_order_status and current_order_status != expected_order_status:
            raise ValueError(
                f"order 状态不匹配: current={current_order_status} expected={expected_order_status}"
            )

        event_id = _stable_id(
            "evt",
            transition.order_id,
            transition.event_type,
            transition.event_time.isoformat(),
        )
        event = JournalEvent(
            event_id=event_id,
            idea_id=transition.idea_id,
            order_id=transition.order_id,
            session_id=idea.session_id,
            event_type=transition.event_type,
            old_idea_state=current_idea_state or None,
            new_idea_state=_clean_text(transition.new_idea_state) or None,
            old_order_status=current_order_status or None,
            new_order_status=_clean_text(transition.new_order_status) or None,
            event_time=transition.event_time,
            event_price=transition.event_price,
            request_id=_clean_text(request_id) or _clean_text(transition.request_id),
            payload_json=dict(transition.payload or {}),
            created_at=_utc_now(),
        )

        if transition.new_idea_state:
            idea.state = transition.new_idea_state
        if transition.new_order_status:
            order.status = transition.new_order_status

        if transition.opened_at is not None:
            idea.opened_at = transition.opened_at
        if transition.opened_price is not None:
            idea.opened_price = transition.opened_price
        if transition.filled_at is not None:
            order.filled_at = transition.filled_at
        if transition.filled_price is not None:
            order.filled_price = transition.filled_price
        if transition.closed_at is not None:
            idea.closed_at = transition.closed_at
            order.closed_at = transition.closed_at
        if transition.closed_price is not None:
            idea.closed_price = transition.closed_price
            order.closed_price = transition.closed_price
        if transition.close_reason is not None:
            idea.close_reason = transition.close_reason
            order.close_reason = transition.close_reason
        if transition.realized_pnl_pct is not None:
            idea.pnl_pct = transition.realized_pnl_pct
            order.realized_pnl_pct = transition.realized_pnl_pct

        now = _utc_now()
        idea.updated_at = now
        order.updated_at = now

        try:
            self.session.add(event)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
        refreshed = self.get_order_bundle(order_id=transition.order_id)
        if refreshed is None:
            raise ValueError(f"状态流转后无法读取 order_id: {transition.order_id}")
        return refreshed

    def list_active_orders(
        self,
        *,
        session_id: str,
        symbol: str | None = None,
        interval: str | None = None,
        limit: int = 50,
    ) -> list[TrackedOrderBundle]:
        clean_session = _clean_text(session_id)
        if not clean_session:
            return []
        query = self.session.query(JournalIdea, PaperOrder).join(PaperOrder, PaperOrder.idea_id == JournalIdea.idea_id).filter(
            JournalIdea.session_id == clean_session,
            PaperOrder.status.in_(("pending_trigger", "filled")),
        )
        clean_symbol = _clean_text(symbol)
        if clean_symbol:
            query = query.filter(JournalIdea.symbol_key == normalize_snapshot_symbol(clean_symbol))
        clean_interval = _clean_text(interval)
        if clean_interval:
            query = query.filter(JournalIdea.interval == clean_interval)

        rows = (
            query.order_by(JournalIdea.updated_at.desc(), JournalIdea.id.desc())
            .limit(max(1, int(limit)))
            .all()
        )
        return [TrackedOrderBundle(idea=idea, order=order, created=False) for idea, order in rows]

    def list_recent_events(self, *, session_id: str, limit: int = 20) -> list[JournalEvent]:
        clean_session = _clean_text(session_id)
        if not clean_session:
            return []
        return (
            self.session.query(JournalEvent)
            .filter(JournalEvent.session_id == clean_session)
            .order_by(JournalEvent.event_time.desc(), JournalEvent.id.desc())
            .limit(max(1, int(limit)))
            .all()
        )

    def list_recent_orders(self, *, session_id: str, limit: int = 50) -> list[TrackedOrderBundle]:
        clean_session = _clean_text(session_id)
        if not clean_session:
            return []
        rows = (
            self.session.query(JournalIdea, PaperOrder)
            .join(PaperOrder, PaperOrder.idea_id == JournalIdea.idea_id)
            .filter(JournalIdea.session_id == clean_session)
            .order_by(JournalIdea.created_at.desc(), JournalIdea.id.desc())
            .limit(max(1, int(limit)))
            .all()
        )
        return [TrackedOrderBundle(idea=idea, order=order, created=False) for idea, order in rows]
