"""模拟交易工具 — 对接正式 paper trading 三表

工具列表:
- prepare_simulated_order: 解析并校验直接开单草稿，不写库
- simulate_open_position: 创建模拟跟踪单
- reconcile_paper_orders: 显式同步活跃模拟单状态
- get_journal_status: 查询当前模拟单状态
"""

from __future__ import annotations

from typing import Any, Dict

from langchain_core.tools import tool


def _normalize_direction(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"long", "buy"} or any(token in raw for token in ("做多", "开多", "多单", "看多")):
        return "long"
    if raw in {"short", "sell"} or any(token in raw for token in ("做空", "开空", "空单", "看空")):
        return "short"
    return raw


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _catalog_candidate_view(symbol: str, row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    return {
        "symbol": str(row.get("symbol") or symbol).strip().upper(),
        "name": str(row.get("name") or "").strip(),
        "market": str(row.get("market") or "").strip().upper(),
    }


def _context_candidate_view(candidate: dict[str, Any], row: dict[str, Any] | None) -> dict[str, Any]:
    view = _catalog_candidate_view(str(candidate.get("symbol") or ""), row)
    view["source"] = str(candidate.get("source") or "recent_context").strip()
    if candidate.get("interval"):
        view["interval"] = str(candidate.get("interval") or "").strip()
    if candidate.get("timestamp"):
        view["timestamp"] = str(candidate.get("timestamp") or "").strip()
    return view


def _merge_symbol_candidates(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            symbol = str(item.get("symbol") or "").strip().upper()
            symbol_key = symbol.replace("_", "").replace("-", "")
            if not symbol or not symbol_key or symbol_key in seen:
                continue
            seen.add(symbol_key)
            out.append(item)
    return out


def _resolve_formal_order_symbol(asset_text: str, *, session_id: str) -> dict[str, Any]:
    """只返回 market_config + 最近分析上下文里的正式 symbol 候选；只有正式 symbol 才允许直接写库。"""
    from core.asset_catalog import get_asset_catalog
    from tools.context_memory import load_recent_formal_symbol_candidates

    raw = str(asset_text or "").strip()
    if not raw:
        return {"status": "clarify", "message": "缺少标的，请补充要跟踪的交易品种。"}

    catalog = get_asset_catalog()
    recent_candidates = load_recent_formal_symbol_candidates(session_id=session_id, limit=5)

    direct = catalog.get(raw.upper())
    if direct:
        symbol = str(direct.get("symbol") or raw).strip().upper()
        return {
            "status": "exact_match",
            "asset_text": raw,
            "symbol": symbol,
            "market": str(direct.get("market") or "").strip().upper(),
            "candidate": _catalog_candidate_view(symbol, direct),
            "source": "market_config",
        }

    recent_exact = next(
        (
            candidate
            for candidate in recent_candidates
            if str(candidate.get("symbol") or "").strip().upper() == raw.upper()
        ),
        None,
    )
    if recent_exact:
        symbol = str(recent_exact.get("symbol") or raw).strip().upper()
        return {
            "status": "exact_match",
            "asset_text": raw,
            "symbol": symbol,
            "market": "",
            "candidate": _context_candidate_view(recent_exact, catalog.get(symbol)),
            "source": str(recent_exact.get("source") or "recent_context").strip(),
        }

    hits = catalog.resolve_symbols_from_text(raw, min_score=80)
    catalog_candidates = [_catalog_candidate_view(symbol, catalog.get(symbol)) for symbol in hits[:5]]
    recent_context_candidates = [_context_candidate_view(item, catalog.get(str(item.get("symbol") or "").strip().upper())) for item in recent_candidates]
    candidates = _merge_symbol_candidates(catalog_candidates, recent_context_candidates)

    if candidates:
        only_one = len(candidates) == 1
        return {
            "status": "confirm_required",
            "asset_text": raw,
            "symbol": candidates[0]["symbol"] if only_one else "",
            "market": str(candidates[0].get("market") or "").strip().upper() if only_one else "",
            "candidates": candidates[:5],
            "message": (
                f"已找到候选正式代码 {candidates[0]['symbol']}，请先让用户明确确认这个代码后再入库。"
                if only_one
                else "标的存在多个候选，请先让用户明确确认正式代码后再入库。"
            ),
        }
    return {
        "status": "blocked",
        "asset_text": raw,
        "message": f"未在 market_config 或最近分析上下文中找到“{raw}”的正式代码，视为此前未分析/未收录，当前不能入库此单。",
    }


def _build_prepared_order(
    *,
    asset_text: str,
    direction: str,
    entry_price: Any,
    stop_loss: Any,
    take_profit: Any,
    session_id: str,
    interval: str,
    request_id: str,
    source_snapshot_id: str,
    order_type: str,
    valid_until: str,
    strategy_reason: str,
) -> dict[str, Any]:
    resolution = _resolve_formal_order_symbol(asset_text, session_id=session_id)
    if resolution.get("status") != "exact_match":
        return {
            "status": str(resolution.get("status") or "confirm_required"),
            "asset_text": asset_text,
            "message": resolution.get("message") or "标的无法解析为正式代码",
            "candidates": resolution.get("candidates", []),
            "symbol": resolution.get("symbol"),
        }

    normalized_direction = _normalize_direction(direction)
    entry = _to_float(entry_price)
    stop = _to_float(stop_loss)
    target = _to_float(take_profit)
    missing = []
    if normalized_direction not in {"long", "short"}:
        missing.append("direction")
    if entry is None:
        missing.append("entry_price")
    if stop is None:
        missing.append("stop_loss")
    if target is None:
        missing.append("take_profit")
    if missing:
        return {
            "status": "clarify",
            "asset_text": asset_text,
            "symbol": resolution.get("symbol"),
            "missing_fields": missing,
            "message": f"开单参数不完整，请补充：{', '.join(missing)}",
        }

    if normalized_direction == "long" and not (stop < entry < target):
        return {
            "status": "invalid",
            "asset_text": asset_text,
            "symbol": resolution.get("symbol"),
            "direction": normalized_direction,
            "message": "多单价格关系应满足 stop_loss < entry_price < take_profit。",
        }
    if normalized_direction == "short" and not (target < entry < stop):
        return {
            "status": "invalid",
            "asset_text": asset_text,
            "symbol": resolution.get("symbol"),
            "direction": normalized_direction,
            "message": "空单价格关系应满足 take_profit < entry_price < stop_loss。",
        }

    interval_value = str(interval or "manual").strip() or "manual"
    simulate_args = {
        "session_id": session_id,
        "symbol": resolution["symbol"],
        "direction": normalized_direction,
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit": target,
        "interval": interval_value,
        "request_id": request_id,
        "source_snapshot_id": source_snapshot_id,
        "order_type": order_type,
        "valid_until": valid_until,
        "strategy_reason": strategy_reason,
    }
    return {
        "status": "ready",
        "asset_text": asset_text,
        "symbol": resolution["symbol"],
        "market": resolution.get("market"),
        "direction": normalized_direction,
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit": target,
        "interval": interval_value,
        "resolution": resolution,
        "simulate_args": simulate_args,
        "message": f"已解析为 {resolution['symbol']}，参数校验通过，可创建模拟跟踪单。",
    }


@tool
def prepare_simulated_order(
    asset_text: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    session_id: str = "default",
    interval: str = "manual",
    request_id: str = "",
    source_snapshot_id: str = "",
    order_type: str = "breakout_stop",
    valid_until: str = "",
    strategy_reason: str = "",
) -> Dict[str, Any]:
    """解析并校验直接开单草稿，不写库。

    本工具只返回 market_config + 最近分析上下文里的正式 symbol 候选。
    只有 asset_text 已经是正式 symbol 时，才会返回 ready。
    """
    return _build_prepared_order(
        asset_text=asset_text,
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        session_id=session_id,
        interval=interval,
        request_id=request_id,
        source_snapshot_id=source_snapshot_id,
        order_type=order_type,
        valid_until=valid_until,
        strategy_reason=strategy_reason,
    )


@tool
def simulate_open_position(
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    session_id: str = "default",
    interval: str = "manual",
    request_id: str = "",
    source_snapshot_id: str = "",
    order_type: str = "breakout_stop",
    valid_until: str = "",
    strategy_reason: str = "",
) -> Dict[str, Any]:
    """创建模拟跟踪单（正式写入 journal_ideas / paper_orders / journal_events）

    Args:
        symbol: 标的代码
        direction: 方向 (long/short)
        entry_price: 入场价格
        stop_loss: 止损价格
        take_profit: 第一目标/默认止盈价格
        session_id: 会话标识

    Returns:
        包含 idea_id / order_id 和确认信息的字典
    """
    try:
        prepared = _build_prepared_order(
            asset_text=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            session_id=session_id,
            interval=interval,
            request_id=request_id,
            source_snapshot_id=source_snapshot_id,
            order_type=order_type,
            valid_until=valid_until,
            strategy_reason=strategy_reason,
        )
        if prepared.get("status") != "ready":
            return {
                **prepared,
                "idea_id": None,
                "order_id": None,
                "created": False,
            }

        from infrastructure.persistence.paper_trading_repository import (
            CreateTrackedOrderCommand,
            PaperTradingRepository,
        )

        simulate_args = prepared["simulate_args"]
        repo = PaperTradingRepository()
        bundle = repo.create_tracked_order(
            CreateTrackedOrderCommand(
                session_id=simulate_args["session_id"],
                symbol=simulate_args["symbol"],
                direction=simulate_args["direction"],
                interval=simulate_args["interval"],
                request_id=simulate_args["request_id"],
                source_snapshot_id=simulate_args["source_snapshot_id"],
                order_type=simulate_args["order_type"],
                strategy_reason=simulate_args["strategy_reason"],
                valid_until=simulate_args["valid_until"],
                entry_price=simulate_args["entry_price"],
                stop_loss=simulate_args["stop_loss"],
                take_profit=simulate_args["take_profit"],
                market=str(prepared.get("market") or ""),
                meta={
                    "asset_text": prepared.get("asset_text"),
                    "symbol_resolution": prepared.get("resolution"),
                },
            )
        )
        repo.close()
        return {
            "status": "success",
            "idea_id": bundle.idea.idea_id,
            "order_id": bundle.order.order_id,
            "order_status": bundle.order.status,
            "idea_state": bundle.idea.state,
            "created": bundle.created,
            "symbol": bundle.order.symbol,
            "direction": bundle.order.side,
            "entry_price": simulate_args["entry_price"],
            "stop_loss": bundle.order.stop_loss,
            "take_profit": bundle.order.tp1,
            "message": (
                f"已创建模拟跟踪单 {bundle.order.symbol} {bundle.order.side}，"
                f"状态 {bundle.order.status}，Order ID: {bundle.order.order_id}"
            ),
        }
    except Exception as e:
        # 即使数据库不可用也不让工具崩溃，返回 error dict
        return {
            "status": "error",
            "idea_id": None,
            "order_id": None,
            "symbol": symbol,
            "direction": direction,
            "message": f"创建模拟跟踪单失败（数据库不可用）: {e}",
        }


@tool
def reconcile_paper_orders(
    session_id: str = "default",
    symbol: str = "",
    interval: str = "",
) -> Dict[str, Any]:
    """显式同步活跃模拟单状态。

    第一阶段只处理 pending_trigger / filled 两类活跃单。
    """
    try:
        from domain.trading.paper_trading_service import PaperTradingService

        service = PaperTradingService()
        result = service.reconcile_orders(
            session_id=session_id,
            symbol=symbol or None,
            interval=interval or None,
        )
        service.close()
        return result
    except Exception as e:
        return {
            "status": "error",
            "session_id": session_id,
            "changed": 0,
            "unchanged": 0,
            "items": [],
            "message": f"同步模拟单状态失败（数据库或行情不可用）: {e}",
        }


@tool
def get_journal_status(
    session_id: str = "default",
    symbol: str = "",
    interval: str = "",
) -> Dict[str, Any]:
    """查询当前模拟单状态（从正式三表读取）

    Args:
        session_id: 会话标识

    Returns:
        当前挂单、持仓、最近关闭和事件摘要
    """
    try:
        from infrastructure.persistence.paper_trading_repository import PaperTradingRepository

        repo = PaperTradingRepository()
        orders = repo.list_recent_orders(session_id=session_id, limit=50)
        events = repo.list_recent_events(session_id=session_id, limit=20)
        repo.close()

        pending_orders = []
        open_positions = []
        recent_closed = []
        symbol_filter = symbol.strip().upper()
        interval_filter = interval.strip()
        for bundle in orders:
            if symbol_filter and bundle.order.symbol_key != symbol_filter.replace("_", "").replace("-", ""):
                continue
            if interval_filter and bundle.order.interval != interval_filter:
                continue
            item = {
                "idea_id": bundle.idea.idea_id,
                "order_id": bundle.order.order_id,
                "symbol": bundle.order.symbol,
                "interval": bundle.order.interval,
                "direction": bundle.order.side,
                "idea_state": bundle.idea.state,
                "order_status": bundle.order.status,
                "entry_price": bundle.order.trigger_price or bundle.order.limit_price or bundle.order.entry_zone_high,
                "stop_loss": bundle.order.stop_loss,
                "take_profit": bundle.order.tp1,
                "created_at": bundle.order.created_at.isoformat() if bundle.order.created_at else None,
                "updated_at": bundle.order.updated_at.isoformat() if bundle.order.updated_at else None,
            }
            if bundle.order.status == "pending_trigger":
                pending_orders.append(item)
            elif bundle.order.status == "filled":
                open_positions.append(item)
            else:
                item["closed_at"] = bundle.order.closed_at.isoformat() if bundle.order.closed_at else None
                item["close_reason"] = bundle.order.close_reason
                recent_closed.append(item)

        recent_events = [
            {
                "event_id": e.event_id,
                "event_type": e.event_type,
                "idea_id": e.idea_id,
                "order_id": e.order_id,
                "event_time": e.event_time.isoformat() if e.event_time else None,
                "event_price": e.event_price,
            }
            for e in events
        ]
        return {
            "status": "success",
            "session_id": session_id,
            "symbol": symbol,
            "interval": interval,
            "pending_orders": pending_orders,
            "open_positions": open_positions,
            "recent_closed": recent_closed[:10],
            "recent_events": recent_events,
            "total_pending": len(pending_orders),
            "total_open": len(open_positions),
            "total_closed": len(recent_closed),
            "total_records": len(orders),
            "message": (
                f"当前有 {len(pending_orders)} 条待触发单，"
                f"{len(open_positions)} 条持仓单，"
                f"{len(recent_closed)} 条已关闭记录"
            ),
        }
    except Exception as e:
        return {
            "status": "error",
            "session_id": session_id,
            "symbol": symbol,
            "interval": interval,
            "pending_orders": [],
            "open_positions": [],
            "recent_closed": [],
            "recent_events": [],
            "total_open": 0,
            "total_pending": 0,
            "total_closed": 0,
            "total_records": 0,
            "message": f"查询模拟单状态失败（数据库不可用）: {e}",
        }
