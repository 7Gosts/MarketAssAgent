from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from infrastructure.persistence.paper_trading_repository import PaperTradingRepository
from tools.market_data import (
    fetch_gate_spot_trades,
    fetch_gate_spot_trades_before,
)

from .reconciliation import decide_price_event_action
from .types import OrderPriceEvent, OrderTransition

TRADE_PAGE_SIZE = 1000
MAX_BACKFILL_PAGES_PER_CYCLE = 3
TRACKING_STATE_KEY = "price_tracking"


class PaperTradingService:
    def __init__(self, repository: PaperTradingRepository | None = None):
        self.repository = repository or PaperTradingRepository()

    def close(self) -> None:
        self.repository.close()

    @staticmethod
    def _tracking_state(order: Any) -> dict[str, Any]:
        raw = dict(getattr(order, "simulation_rule_json", None) or {})
        return dict(raw.get(TRACKING_STATE_KEY) or {})

    @staticmethod
    def _save_tracking_state(state: dict[str, Any], order: Any) -> dict[str, Any]:
        rules = dict(getattr(order, "simulation_rule_json", None) or {})
        rules[TRACKING_STATE_KEY] = state
        return rules

    def _persist_tracking_state(self, *, order_id: str, state: dict[str, Any]) -> None:
        bundle = self.repository.get_order_bundle(order_id=order_id)
        if bundle is None:
            raise ValueError(f"order_id 不存在: {order_id}")
        self.repository.commit_tracking_state(
            order_id=order_id,
            state=self._save_tracking_state(state, bundle.order),
        )

    @staticmethod
    def _trade_event(row: dict[str, Any]) -> OrderPriceEvent:
        raw_time = str(row.get("time") or "")
        parsed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return OrderPriceEvent(
            time=parsed.astimezone(timezone.utc),
            trade_id=str(row["trade_id"]),
            price=float(row["price"]),
        )

    def _load_trade_events(
        self,
        bundle: Any,
        state: dict[str, Any],
        *,
        trade_fetcher: Any,
        older_trade_fetcher: Any,
    ) -> tuple[list[OrderPriceEvent], dict[str, Any], str | None, bool]:
        order = bundle.order
        last_id = str(state.get("last_trade_id") or "")
        baseline_time = (
            order.filled_at
            if order.status == "filled" and order.filled_at is not None
            else order.created_at
        )
        baseline_time = baseline_time.astimezone(timezone.utc) if baseline_time.tzinfo else baseline_time.replace(tzinfo=timezone.utc)
        if not last_id and not state.get("backfill_cursor"):
            head = trade_fetcher(order.symbol)
            if head.get("status") != "success":
                return [], state, str(head.get("message") or "Gate 成交读取失败"), False
            rows = list(head.get("events") or [])
            if not rows:
                return [], state, "Gate 暂无可用成交，无法初始化游标", False
            before_baseline = [row for row in rows if self._trade_event(row).time <= baseline_time]
            if before_baseline:
                state["last_trade_id"] = max(
                    before_baseline,
                    key=lambda row: (self._trade_event(row).time, int(row["trade_id"])),
                )["trade_id"]
                last_id = str(state["last_trade_id"])
            else:
                state["backfill_cursor"] = str(rows[0]["trade_id"])

        if not last_id and state.get("backfill_cursor"):
            cursor = str(state["backfill_cursor"])
            for _ in range(MAX_BACKFILL_PAGES_PER_CYCLE):
                older = older_trade_fetcher(order.symbol, last_id=cursor)
                if older.get("status") != "success":
                    return [], state, str(older.get("message") or "Gate 历史成交回补失败"), False
                rows = list(older.get("events") or [])
                if not rows:
                    return [], state, "Gate 历史成交无法覆盖订单跟踪起点", False
                before_baseline = [row for row in rows if self._trade_event(row).time <= baseline_time]
                if before_baseline:
                    state["last_trade_id"] = max(
                        before_baseline,
                        key=lambda row: (self._trade_event(row).time, int(row["trade_id"])),
                    )["trade_id"]
                    state.pop("backfill_cursor", None)
                    last_id = str(state["last_trade_id"])
                    break
                cursor = str(rows[0]["trade_id"])
                state["backfill_cursor"] = cursor
            if not last_id:
                return [], state, None, True

        payload = trade_fetcher(order.symbol, last_id=last_id)
        if payload.get("status") != "success":
            return [], state, str(payload.get("message") or "Gate 成交读取失败"), False
        rows = list(payload.get("events") or [])
        events = [self._trade_event(row) for row in rows]
        events.sort(key=lambda event: (event.time, int(event.trade_id)))
        return events, state, None, len(rows) >= TRADE_PAGE_SIZE

    def track_active_orders(
        self,
        *,
        session_id: str | None = None,
        symbol: str | None = None,
        interval: str | None = None,
        all_sessions: bool = False,
        request_id: str = "",
        trade_fetcher: Any = fetch_gate_spot_trades,
        older_trade_fetcher: Any = fetch_gate_spot_trades_before,
    ) -> dict[str, Any]:
        """Advance all active crypto orders from ordered Gate spot trades."""
        bundles = self.repository.list_active_orders(
            session_id=session_id,
            symbol=symbol,
            interval=interval,
            release_transaction=True,
            all_sessions=all_sessions,
        )
        counts = {"checked": 0, "changed": 0, "unchanged": 0, "failed": 0, "items": []}
        trade_page_cache: dict[tuple[str, str, str], dict[str, Any]] = {}

        def cached_trade_fetcher(symbol: str, *, last_id: str | None = None) -> dict[str, Any]:
            key = (symbol, str(last_id or ""), "forward")
            if key not in trade_page_cache:
                trade_page_cache[key] = trade_fetcher(symbol, last_id=last_id)
            return trade_page_cache[key]

        def cached_older_fetcher(symbol: str, *, last_id: str) -> dict[str, Any]:
            key = (symbol, str(last_id), "backward")
            if key not in trade_page_cache:
                trade_page_cache[key] = older_trade_fetcher(symbol, last_id=last_id)
            return trade_page_cache[key]

        for bundle in bundles:
            order = bundle.order
            counts["checked"] += 1
            state = self._tracking_state(order)
            if str(order.market or "").lower() not in {"crypto", "cryptocurrency"}:
                counts["unchanged"] += 1
                counts["items"].append({"order_id": order.order_id, "status": "unsupported_market"})
                continue
            events, state, error, more_pending = self._load_trade_events(
                bundle,
                state,
                trade_fetcher=cached_trade_fetcher,
                older_trade_fetcher=cached_older_fetcher,
            )
            if error:
                counts["failed"] += 1
                counts["items"].append({
                    "idea_id": bundle.idea.idea_id,
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "interval": order.interval,
                    "status": "error",
                    "message": error,
                })
                continue
            if more_pending and not events:
                self._persist_tracking_state(order_id=order.order_id, state=state)
                counts["unchanged"] += 1
                counts["items"].append({
                    "idea_id": bundle.idea.idea_id,
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "interval": order.interval,
                    "status": "unchanged",
                    "reason": "no_new_trade_page",
                    "idea_state": bundle.idea.state,
                    "order_status": order.status,
                })
                continue
            zone_touched = bool(state.get("zone_touched"))
            changed_types: list[str] = []
            latest_time = state.get("last_trade_time")
            error = None
            try:
                for event in events:
                    action, zone_touched = decide_price_event_action(
                        bundle.idea,
                        order,
                        event,
                        zone_touched=zone_touched,
                    )
                    state["last_trade_id"] = event.trade_id
                    state["last_trade_time"] = event.time.isoformat()
                    state["last_price"] = event.price
                    state["zone_touched"] = zone_touched
                    latest_time = event.time.isoformat()
                    if action.changed and action.transition is not None:
                        tracking_after = dict(state)
                        updated = self.repository.apply_transition(
                            action.transition,
                            request_id=request_id,
                            tracking_state=self._save_tracking_state(tracking_after, order),
                            commit=False,
                        )
                        bundle = updated
                        order = updated.order
                        changed_types.append(action.transition.event_type)
                if events:
                    state["last_trade_id"] = events[-1].trade_id
                if not more_pending and order.status == "pending_trigger" and order.valid_until is not None:
                    valid_until = order.valid_until
                    valid_until = valid_until.astimezone(timezone.utc) if valid_until.tzinfo else valid_until.replace(tzinfo=timezone.utc)
                    if valid_until <= datetime.now(timezone.utc):
                        transition = OrderTransition(
                            idea_id=bundle.idea.idea_id,
                            order_id=order.order_id,
                            event_type="order_expired",
                            old_idea_state=bundle.idea.state,
                            new_idea_state="expired",
                            old_order_status=order.status,
                            new_order_status="expired",
                            event_time=valid_until,
                            payload={"reason": "valid_until_reached"},
                        )
                        updated = self.repository.apply_transition(
                            transition,
                            request_id=request_id,
                            tracking_state=self._save_tracking_state(state, order),
                            commit=False,
                        )
                        bundle, order = updated, updated.order
                        changed_types.append(transition.event_type)
                self.repository.commit_tracking_state(
                    order_id=order.order_id,
                    state=self._save_tracking_state(state, order),
                    commit=False,
                )
                self.repository.commit()
            except Exception as exc:
                self.repository.rollback()
                error = str(exc)
            if error:
                counts["failed"] += 1
                counts["items"].append({"order_id": order.order_id, "status": "error", "reason": error})
            elif changed_types:
                counts["changed"] += 1
                counts["items"].append({
                    "idea_id": bundle.idea.idea_id,
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "interval": order.interval,
                    "status": "changed",
                    "event_type": changed_types[-1],
                    "event_types": changed_types,
                    "idea_state": bundle.idea.state,
                    "order_status": order.status,
                    "matched_trade_time": latest_time,
                })
            else:
                counts["unchanged"] += 1
                counts["items"].append({
                    "idea_id": bundle.idea.idea_id,
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "interval": order.interval,
                    "status": "unchanged",
                    "reason": "no_new_trade",
                    "idea_state": bundle.idea.state,
                    "order_status": order.status,
                })
        return counts

    def cancel_order(
        self,
        *,
        session_id: str,
        order_id: str,
        reason: str = "",
        request_id: str = "",
    ) -> dict[str, Any]:
        """取消仍未触发的模拟订单，保留订单和取消事件。"""
        bundle = self.repository.get_order_bundle(order_id=order_id)
        if bundle is None:
            raise ValueError(f"order_id 不存在: {order_id}")
        if bundle.idea.session_id != session_id:
            raise ValueError("订单不属于当前会话，拒绝取消")

        current_status = str(bundle.order.status or "").strip()
        if current_status == "cancelled":
            return self._cancel_result(bundle, reason=reason, idempotent=True)
        if current_status != "pending_trigger":
            raise ValueError(
                f"仅允许取消 pending_trigger 订单，当前状态为 {current_status or 'unknown'}；"
                "已成交持仓应使用平仓流程"
            )

        clean_reason = str(reason or "用户手动取消").strip() or "用户手动取消"
        transition = OrderTransition(
            idea_id=bundle.idea.idea_id,
            order_id=bundle.order.order_id,
            event_type="order_cancelled",
            old_idea_state=bundle.idea.state,
            new_idea_state="cancelled",
            old_order_status=bundle.order.status,
            new_order_status="cancelled",
            event_time=datetime.now(timezone.utc),
            request_id=request_id,
            payload={"reason": clean_reason, "source": "manual"},
            close_reason="cancelled",
        )
        updated = self.repository.apply_transition(transition, request_id=request_id)
        return self._cancel_result(updated, reason=clean_reason, idempotent=False)

    @staticmethod
    def _cancel_result(bundle: Any, *, reason: str, idempotent: bool) -> dict[str, Any]:
        return {
            "status": "success",
            "idea_id": bundle.idea.idea_id,
            "order_id": bundle.order.order_id,
            "symbol": bundle.order.symbol,
            "direction": bundle.order.side,
            "idea_state": bundle.idea.state,
            "order_status": bundle.order.status,
            "reason": reason or "用户手动取消",
            "idempotent": idempotent,
            "message": f"已取消模拟订单 {bundle.order.order_id}，订单和取消事件已保留。",
        }

    def reconcile_orders(
        self,
        *,
        session_id: str,
        symbol: str | None = None,
        interval: str | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        bundles = self.repository.list_active_orders(session_id=session_id, symbol=symbol, interval=interval)
        if not bundles:
            return {
                "status": "success",
                "session_id": session_id,
                "changed": 0,
                "unchanged": 0,
                "items": [],
                "message": "当前没有需要同步的活跃模拟单",
            }

        tracking = self.track_active_orders(
            session_id=session_id,
            symbol=symbol,
            interval=interval,
            request_id=request_id,
        )
        return {
            "status": "success",
            "session_id": session_id,
            "changed": tracking["changed"],
            "unchanged": tracking["unchanged"],
            "items": tracking["items"],
            "message": (
                f"本次价格事件同步 {tracking['changed']} 条变化，"
                f"{tracking['unchanged']} 条无变化，{tracking['failed']} 条跟踪暂不可验证"
            ),
        }
