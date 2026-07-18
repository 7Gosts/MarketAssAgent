from __future__ import annotations

from typing import Any

from infrastructure.persistence.paper_trading_repository import PaperTradingRepository
from tools.market_data import fetch_market_data

from .reconciliation import decide_reconcile_action, normalize_bars


class PaperTradingService:
    def __init__(self, repository: PaperTradingRepository | None = None):
        self.repository = repository or PaperTradingRepository()

    def close(self) -> None:
        self.repository.close()

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
