from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from infrastructure.persistence.paper_trading_repository import PaperTradingRepository
from tools.market_data import fetch_market_data

from .reconciliation import decide_reconcile_action, normalize_bars
from .types import OrderTransition


class PaperTradingService:
    def __init__(self, repository: PaperTradingRepository | None = None):
        self.repository = repository or PaperTradingRepository()

    def close(self) -> None:
        self.repository.close()

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
        allow_historical_bars: bool = False,
        bars: list[dict[str, Any]] | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        bundles = self.repository.list_active_orders(session_id=session_id, symbol=symbol, interval=interval, limit=100)
        if not bundles:
            return {
                "status": "success",
                "session_id": session_id,
                "changed": 0,
                "unchanged": 0,
                "items": [],
                "message": "当前没有需要同步的活跃模拟单",
            }

        grouped_bars: dict[tuple[str, str], list[dict[str, Any]]] = {}
        items: list[dict[str, Any]] = []
        changed = 0
        unchanged = 0

        for bundle in bundles:
            key = (bundle.order.symbol, bundle.order.interval)
            if bars is not None:
                rows = bars
            else:
                if key not in grouped_bars:
                    payload = fetch_market_data.invoke({"symbol": bundle.order.symbol, "interval": bundle.order.interval})
                    if payload.get("status") != "success":
                        items.append(
                            {
                                "idea_id": bundle.idea.idea_id,
                                "order_id": bundle.order.order_id,
                                "symbol": bundle.order.symbol,
                                "interval": bundle.order.interval,
                                "status": "error",
                                "message": str(payload.get("error") or payload.get("message") or "行情获取失败"),
                            }
                        )
                        unchanged += 1
                        continue
                    grouped_bars[key] = list(payload.get("data") or [])
                rows = grouped_bars.get(key) or []

            action = decide_reconcile_action(
                bundle.idea,
                bundle.order,
                normalize_bars(list(rows or [])),
                allow_historical_bars=allow_historical_bars,
            )
            if action.changed and action.transition is not None:
                updated = self.repository.apply_transition(action.transition, request_id=request_id)
                changed += 1
                items.append(
                    {
                        "idea_id": updated.idea.idea_id,
                        "order_id": updated.order.order_id,
                        "symbol": updated.order.symbol,
                        "interval": updated.order.interval,
                        "status": "changed",
                        "event_type": action.transition.event_type,
                        "idea_state": updated.idea.state,
                        "order_status": updated.order.status,
                        "matched_bar_time": action.matched_bar.time.isoformat() if action.matched_bar else None,
                    }
                )
            else:
                unchanged += 1
                items.append(
                    {
                        "idea_id": bundle.idea.idea_id,
                        "order_id": bundle.order.order_id,
                        "symbol": bundle.order.symbol,
                        "interval": bundle.order.interval,
                        "status": "unchanged",
                        "reason": action.reason,
                        "idea_state": bundle.idea.state,
                        "order_status": bundle.order.status,
                    }
                )

        return {
            "status": "success",
            "session_id": session_id,
            "changed": changed,
            "unchanged": unchanged,
            "items": items,
            "message": f"本次同步 {changed} 条变化，{unchanged} 条无变化",
        }
