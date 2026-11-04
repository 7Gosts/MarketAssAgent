from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from core.conversation_scope import ConversationScope
from infrastructure.persistence.analysis_snapshot_repository import normalize_snapshot_symbol
from infrastructure.persistence.db import get_session
from infrastructure.persistence.models import JournalEvent, JournalIdea
from tools.market_data import fetch_market_data

logger = logging.getLogger(__name__)
POLL_SECONDS = 15 * 60
DEFAULT_INTERVAL = "15m"
INTERVAL_DURATIONS = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
}
MAX_VALID_HOURS = 72


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _monitor_data(idea: JournalIdea) -> dict[str, Any]:
    return dict((idea.meta_json or {}).get("plan_monitor") or {})


def _destination(scope: ConversationScope | None) -> dict[str, str] | None:
    if scope is None or scope.transport != "feishu":
        return None
    if scope.visibility_scope == "group":
        receive_id, receive_id_type = scope.visibility_scope_id, "chat_id"
    else:
        receive_id, receive_id_type = scope.actor_id, "open_id"
    if not receive_id:
        return None
    return {"receive_id": receive_id, "receive_id_type": receive_id_type}


def start_plan_monitor(
    *,
    session_id: str,
    request_id: str,
    scope: ConversationScope | None,
    symbol: str,
    direction: str,
    entry_zone_low: float,
    entry_zone_high: float,
    interval: str = DEFAULT_INTERVAL,
    valid_hours: int = 8,
) -> dict[str, Any]:
    """Persist an entry alert. It never creates a paper order."""
    destination = _destination(scope)
    if destination is None:
        return {"status": "error", "message": "计划提醒目前只支持飞书私聊或群聊。"}

    clean_symbol = str(symbol or "").strip().upper().replace("-", "_")
    clean_direction = str(direction or "").strip().lower()
    clean_interval = str(interval or DEFAULT_INTERVAL).strip().lower()
    try:
        low, high, hours = float(entry_zone_low), float(entry_zone_high), int(valid_hours)
    except (TypeError, ValueError):
        return {"status": "error", "message": "入场区间和有效小时数必须是有效数字。"}
    if (
        not clean_symbol
        or clean_direction not in {"long", "short"}
        or not math.isfinite(low)
        or not math.isfinite(high)
        or low <= 0
        or low >= high
    ):
        return {"status": "error", "message": "标的、方向或入场区间无效。"}
    if clean_interval not in INTERVAL_DURATIONS:
        return {"status": "error", "message": "监控周期支持 15m、1h 或 4h。"}
    if not 1 <= hours <= MAX_VALID_HOURS:
        return {"status": "error", "message": "有效期支持 1 到 72 小时。"}
    clean_session = str(session_id or "").strip()
    if not clean_session:
        return {"status": "error", "message": "会话范围缺失，无法创建计划提醒。"}

    now = _now_utc()
    session = get_session()
    try:
        clean_request = str(request_id or "").strip()
        if clean_request:
            existing = (
                session.query(JournalIdea)
                .filter(
                    JournalIdea.session_id == clean_session,
                    JournalIdea.source_request_id == clean_request,
                )
                .order_by(JournalIdea.id.desc())
                .first()
            )
            if existing is not None and _monitor_data(existing):
                return _plan_view(existing)

        plan_id = f"plan_{uuid.uuid4().hex[:24]}"
        idea = JournalIdea(
            idea_id=plan_id,
            session_id=clean_session,
            source_request_id=clean_request,
            current_order_id=None,
            symbol=clean_symbol,
            symbol_key=normalize_snapshot_symbol(clean_symbol),
            interval=clean_interval,
            side=clean_direction,
            setup_type="plan_monitor",
            state="watch",
            entry_zone_low=low,
            entry_zone_high=high,
            valid_until=now + timedelta(hours=hours),
            strategy_reason="后台入场条件提醒；不是模拟订单或开仓记录。",
            meta_json={"plan_monitor": {
                "status": "active",
                "condition": "zone_reclaim",
                "zone_touched": False,
                "last_entry_bar": "",
                "destination": destination,
            "conversation_scope": scope.to_meta(),
            }},
            created_at=now,
            updated_at=now,
        )
        session.add(idea)
        session.add(JournalEvent(
            idea_id=plan_id,
            order_id=None,
            session_id=clean_session,
            event_type="plan_monitor_created",
            new_idea_state="watch",
            event_time=now,
            request_id=clean_request,
            payload_json={"symbol": clean_symbol, "interval": clean_interval, "direction": clean_direction},
            created_at=now,
        ))
        session.commit()
        return _plan_view(idea)
    except Exception as exc:
        session.rollback()
        logger.exception("创建计划提醒失败")
        return {"status": "error", "message": "创建计划提醒失败，检查数据库连接和配置后重试。"}
    finally:
        session.close()


def cancel_plan_monitor(*, session_id: str, plan_id: str, request_id: str = "") -> dict[str, Any]:
    session = get_session()
    try:
        idea = session.query(JournalIdea).filter(
            JournalIdea.idea_id == str(plan_id or "").strip(),
            JournalIdea.session_id == str(session_id or "").strip(),
        ).first()
        if idea is None or not _monitor_data(idea):
            return {"status": "not_found", "plan_id": plan_id}
        monitor = _monitor_data(idea)
        if monitor.get("status") not in {"active", "notice_pending"}:
            return {"status": str(monitor.get("status") or "unknown"), "plan_id": plan_id}

        now = _now_utc()
        monitor["status"] = "cancelled"
        idea.meta_json = {**dict(idea.meta_json or {}), "plan_monitor": monitor}
        idea.state = "cancelled"
        idea.updated_at = now
        session.add(JournalEvent(
            idea_id=idea.idea_id,
            order_id=None,
            session_id=idea.session_id,
            event_type="plan_monitor_cancelled",
            old_idea_state="watch",
            new_idea_state="cancelled",
            event_time=now,
            request_id=str(request_id or ""),
            created_at=now,
        ))
        session.commit()
        return {"status": "cancelled", "plan_id": plan_id}
    except Exception as exc:
        session.rollback()
        logger.exception("取消计划提醒失败 plan_id=%s", plan_id)
        return {"status": "error", "plan_id": plan_id, "message": "取消计划提醒失败，请稍后重试。"}
    finally:
        session.close()


def get_plan_monitors(*, session_id: str, symbol: str = "") -> dict[str, Any]:
    session = get_session()
    try:
        query = session.query(JournalIdea).filter(JournalIdea.session_id == str(session_id or "").strip())
        if symbol:
            query = query.filter(JournalIdea.symbol_key == normalize_snapshot_symbol(symbol))
        items = [
            _plan_view(idea)
            for idea in query.order_by(JournalIdea.created_at.desc()).limit(30).all()
            if _monitor_data(idea)
        ]
        return {"status": "success", "items": items}
    except Exception as exc:
        logger.exception("读取计划提醒失败")
        return {"status": "error", "items": [], "message": "读取计划提醒失败，请稍后重试。"}
    finally:
        session.close()


def _plan_view(idea: JournalIdea) -> dict[str, Any]:
    monitor = _monitor_data(idea)
    return {
        "status": str(monitor.get("status") or "active"),
        "plan_id": idea.idea_id,
        "symbol": idea.symbol,
        "direction": idea.side,
        "interval": idea.interval,
        "entry_zone": [idea.entry_zone_low, idea.entry_zone_high],
        "valid_until": idea.valid_until.isoformat() if idea.valid_until else None,
    }


def evaluate_zone_reclaim(
    *, direction: str, zone_low: float, zone_high: float, already_touched: bool,
    previous_close: float | None, candles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate a zone touch followed by a close back outside the zone."""
    touched = already_touched
    for candle in candles:
        try:
            high, low, close = float(candle["high"]), float(candle["low"]), float(candle["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if direction == "short":
            if (
                not touched
                and previous_close is not None
                and previous_close < zone_low
                and high >= zone_low
                and low <= zone_high
            ):
                touched = True
            if touched and close < zone_low:
                return {"triggered": True, "zone_touched": touched, "candle": candle}
        else:
            if (
                not touched
                and previous_close is not None
                and previous_close > zone_high
                and low <= zone_high
                and high >= zone_low
            ):
                touched = True
            if touched and close > zone_high:
                return {"triggered": True, "zone_touched": touched, "candle": candle}
        previous_close = close
    return {"triggered": False, "zone_touched": touched, "candle": None}


def _closed_candles(
    payload: dict[str, Any],
    now: datetime,
    interval: str,
) -> list[dict[str, Any]]:
    duration = INTERVAL_DURATIONS[interval]
    candles = []
    for row in payload.get("data") or []:
        if not isinstance(row, dict):
            continue
        start = _parse_time(row.get("time"))
        if start is not None and start + duration <= now:
            candles.append({**row, "_start": start})
    return sorted(candles, key=lambda row: row["_start"])


def _snapshot_plan(idea: JournalIdea) -> dict[str, Any]:
    return {
        "plan_id": idea.idea_id,
        "session_id": idea.session_id,
        "symbol": idea.symbol,
        "direction": idea.side,
        "interval": idea.interval,
        "zone_low": float(idea.entry_zone_low),
        "zone_high": float(idea.entry_zone_high),
        "created_at": idea.created_at,
        "valid_until": idea.valid_until,
        "monitor": _monitor_data(idea),
    }


def _save_monitor(
    plan: dict[str, Any],
    *,
    status: str | None = None,
    event_type: str = "",
    payload: dict[str, Any] | None = None,
) -> bool:
    session = get_session()
    try:
        idea = session.query(JournalIdea).filter(
            JournalIdea.idea_id == plan["plan_id"],
            JournalIdea.session_id == plan["session_id"],
            JournalIdea.state == "watch",
        ).first()
        if idea is None:
            return False
        monitor = _monitor_data(idea)
        if monitor.get("status") not in {"active", "notice_pending"}:
            return False
        monitor.update(plan["monitor"])
        if status:
            monitor["status"] = status
        idea.meta_json = {**dict(idea.meta_json or {}), "plan_monitor": monitor}
        idea.updated_at = _now_utc()
        if status == "expired":
            idea.state = "expired"
        if event_type:
            session.add(JournalEvent(
                idea_id=idea.idea_id,
                order_id=None,
                session_id=idea.session_id,
                event_type=event_type,
                old_idea_state="watch",
                new_idea_state=idea.state,
                event_time=idea.updated_at,
                event_price=(payload or {}).get("close"),
                payload_json=payload or {},
                created_at=idea.updated_at,
            ))
        session.commit()
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def build_plan_monitor_event(plan: dict[str, Any], candle: dict[str, Any]) -> str:
    interval = str(plan.get("interval") or DEFAULT_INTERVAL)
    direction = "做空" if plan["direction"] == "short" else "做多"
    threshold = plan["zone_low"] if plan["direction"] == "short" else plan["zone_high"]
    comparison = "下方" if plan["direction"] == "short" else "上方"
    candle_time = _parse_time(candle.get("time"))
    close_time = (candle_time + INTERVAL_DURATIONS[interval]).astimezone(ZoneInfo("Asia/Shanghai"))
    bar_time = close_time.strftime("%Y-%m-%d %H:%M CST")
    return (
        "【后台计划监控事件，非用户原话】\n"
        f"计划编号：{plan['plan_id']}\n"
        f"程序已确认：{plan['symbol']} {direction}观察规则触发。"
        f"{interval} K线范围曾触及观察区，随后收盘回到 "
        f"{threshold:g}{comparison}。观察区 {plan['zone_low']:g}–{plan['zone_high']:g}，"
        f"该根最高 {float(candle['high']):g}、最低 {float(candle['low']):g}、"
        f"收盘 {float(candle['close']):g}，K线收盘时间 {bar_time}。"
        f"行情来源：{candle.get('source') or '行情数据接口'}。\n\n"
        "请结合本会话此前的计划和行情语境，判断它现在是否仍值得关注，并向用户解释。"
        "无论结论如何，都要报告程序已检测到的条件和价格事实；不要否认或改写触发事实，"
        "不要声称用户已成交、"
        "不要替用户开单，也不要因这条后台事件自行创建或修改监控计划。"
    )


async def _deliver_pending_notice(
    plan: dict[str, Any],
    *,
    notifier: Callable[..., Any] | None,
    counts: dict[str, int],
) -> None:
    monitor = plan["monitor"]
    try:
        if notifier is None:
            raise RuntimeError("计划提醒没有配置会话通知处理器")
        await notifier(plan, monitor["trigger_candle"])
    except Exception:
        monitor["notification_attempt"] = int(monitor.get("notification_attempt") or 1) + 1
        _save_monitor(plan)
        raise

    monitor["status"] = "triggered"
    if _save_monitor(
        plan,
        status="triggered",
        event_type="plan_monitor_triggered",
        payload=monitor["trigger_candle"],
    ):
        counts["triggered"] += 1


async def run_plan_monitor_cycle(
    *, now: datetime | None = None,
    market_fetcher: Callable[..., dict[str, Any]] = fetch_market_data,
    notifier: Callable[..., Any] | None = None,
) -> dict[str, int]:
    """Check persisted monitors once without holding a DB connection over network I/O."""
    current = (now or _now_utc()).astimezone(timezone.utc)
    session = get_session()
    try:
        plans = [
            _snapshot_plan(idea)
            for idea in session.query(JournalIdea).filter(
                JournalIdea.state == "watch",
                JournalIdea.setup_type == "plan_monitor",
            ).all()
            if _monitor_data(idea).get("status") in {"active", "notice_pending"}
        ]
    finally:
        session.close()

    counts = {"checked": 0, "triggered": 0, "expired": 0, "failed": 0}
    market_cache: dict[tuple[str, str], dict[str, Any]] = {}
    for plan in plans:
        counts["checked"] += 1
        monitor = plan["monitor"]
        try:
            if monitor.get("status") == "notice_pending":
                valid_until = plan["valid_until"]
                if valid_until is not None:
                    valid_until = _as_utc(valid_until)
                    if valid_until <= current:
                        monitor["status"] = "expired"
                        if _save_monitor(plan, status="expired", event_type="plan_monitor_expired"):
                            counts["expired"] += 1
                        continue
                await _deliver_pending_notice(plan, notifier=notifier, counts=counts)
                continue

            valid_until = plan["valid_until"]
            if valid_until is not None:
                valid_until = _as_utc(valid_until)
                if valid_until <= current:
                    monitor["status"] = "expired"
                    if _save_monitor(plan, status="expired", event_type="plan_monitor_expired"):
                        counts["expired"] += 1
                    continue

            interval = plan["interval"]
            cache_key = (plan["symbol"], interval)
            payload = market_cache.get(cache_key)
            if payload is None:
                payload = market_fetcher(symbol=plan["symbol"], interval=interval)
                market_cache[cache_key] = payload
            if payload.get("status") != "success":
                logger.warning("计划行情读取失败 plan_id=%s payload=%s", plan["plan_id"], payload)
                counts["failed"] += 1
                continue

            created_at = plan["created_at"]
            created_at = _as_utc(created_at)
            candles = [c for c in _closed_candles(payload, current, interval) if c["_start"] >= created_at]
            if valid_until is not None:
                duration = INTERVAL_DURATIONS[interval]
                candles = [c for c in candles if c["_start"] + duration <= valid_until]
            previous_bar = str(monitor.get("last_entry_bar") or "")
            fresh = [c for c in candles if c["_start"].isoformat() > previous_bar]
            if not fresh:
                continue

            all_candles = _closed_candles(payload, current, interval)
            first_index = next((i for i, c in enumerate(all_candles) if c["_start"] == fresh[0]["_start"]), 0)
            previous_close = float(all_candles[first_index - 1]["close"]) if first_index else None
            result = evaluate_zone_reclaim(
                direction=plan["direction"],
                zone_low=plan["zone_low"],
                zone_high=plan["zone_high"],
                already_touched=bool(monitor.get("zone_touched")),
                previous_close=previous_close,
                candles=fresh,
            )
            monitor["zone_touched"] = result["zone_touched"]
            monitor["last_entry_bar"] = fresh[-1]["_start"].isoformat()
            if result["triggered"]:
                candle = result["candle"]
                monitor["status"] = "notice_pending"
                monitor["notification_attempt"] = 1
                monitor["trigger_candle"] = {
                    "time": candle["_start"].isoformat(),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "source": "Gate.io现货K线" if payload.get("market") == "crypto" else "AKShare K线",
                }
                if not _save_monitor(plan, status="notice_pending"):
                    continue
                await _deliver_pending_notice(plan, notifier=notifier, counts=counts)
            else:
                _save_monitor(plan)
        except Exception:
            logger.exception("计划跟踪失败 plan_id=%s", plan["plan_id"])
            counts["failed"] += 1
    return counts


async def run_plan_tracker(*, notifier: Callable[..., Any]) -> None:
    logger.info("计划跟踪任务已启动，轮询周期=%s 秒", POLL_SECONDS)
    while True:
        try:
            logger.info(
                "计划跟踪轮询完成: %s",
                await run_plan_monitor_cycle(notifier=notifier),
            )
        except Exception:
            logger.exception("计划跟踪轮询失败")
        await asyncio.sleep(max(1, POLL_SECONDS - int(time.time()) % POLL_SECONDS))
