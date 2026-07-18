"""上下文记忆工具：为 light loop 提供按需补证能力。"""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from config.runtime_config import get_postgres_dsn
from core.memory_api import MemoryAPI
from infrastructure.persistence.analysis_snapshot_repository import AnalysisSnapshotRepository
from utils.logging_utils import get_logger


_RUNTIME_MEMORY_API: MemoryAPI | None = None


_DEFAULT_SUMMARY_BUDGET = 8000
_MAX_SUMMARY_BUDGET = 10000
logger = get_logger(__name__)


def set_context_memory_api(memory_api: MemoryAPI | None) -> None:
    """在运行时注入统一 MemoryAPI（由 runtime/app/factory.py 调用）。"""
    global _RUNTIME_MEMORY_API
    _RUNTIME_MEMORY_API = memory_api


def _get_runtime_memory_api() -> MemoryAPI | None:
    return _RUNTIME_MEMORY_API


def _truncate_text(text: str, max_len: int) -> str:
    raw = str(text or "")
    if len(raw) <= max_len:
        return raw
    if max_len <= 3:
        return raw[:max_len]
    return raw[: max_len - 3] + "..."


def _safe_limit(value: int, *, default: int, minimum: int, maximum: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    if num < minimum:
        return minimum
    if num > maximum:
        return maximum
    return num


def _compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    levels_v2 = snapshot.get("levels_v2") if isinstance(snapshot.get("levels_v2"), dict) else {}
    key_levels = snapshot.get("key_levels") if isinstance(snapshot.get("key_levels"), dict) else {}
    actionability = snapshot.get("actionability") if isinstance(snapshot.get("actionability"), dict) else {}
    invalidation = (
        snapshot.get("invalidation_conditions")
        if isinstance(snapshot.get("invalidation_conditions"), dict)
        else {}
    )

    support = key_levels.get("support") if isinstance(key_levels.get("support"), list) else []
    resistance = key_levels.get("resistance") if isinstance(key_levels.get("resistance"), list) else []

    nearest_support = levels_v2.get("nearest_support")
    nearest_resistance = levels_v2.get("nearest_resistance")
    if nearest_support not in (None, "") and nearest_support not in support:
        support = [nearest_support, *support][:2]
    if nearest_resistance not in (None, "") and nearest_resistance not in resistance:
        resistance = [nearest_resistance, *resistance][:2]

    payload = {
        "symbol": snapshot.get("symbol"),
        "interval": snapshot.get("interval"),
        "timestamp": snapshot.get("timestamp"),
        "current_price": snapshot.get("current_price"),
        "trend": snapshot.get("trend"),
        "key_levels": {
            "support": support[:2],
            "resistance": resistance[:2],
        },
        "bias": actionability.get("bias"),
        "can_trade_now": actionability.get("can_trade_now"),
        "wait_condition": actionability.get("wait_condition"),
        "invalidation": invalidation.get("time_stop_rule") or invalidation.get("stop"),
        "structure_hint": _truncate_text(str(snapshot.get("raw_insights") or ""), 160),
    }
    return {
        k: v
        for k, v in payload.items()
        if v not in (None, "", [], {})
    }


@tool
def get_last_snapshot(session_id: str) -> dict[str, Any]:
    """
    读取指定会话的最近市场快照（结构化精简版）。

    适用场景：追问“刚才点位还有效吗/还能拿吗/为什么这么判断”。
    """
    api = _get_runtime_memory_api()
    if api is None:
        return {"status": "error", "session_id": session_id, "snapshot": {}, "error": "MemoryAPI not configured"}

    snapshot = api.snapshot(session_id)
    if not isinstance(snapshot, dict) or not snapshot:
        return {"status": "not_found", "session_id": session_id, "snapshot": {}}

    compact = _compact_snapshot(snapshot)
    return {"status": "success", "session_id": session_id, "snapshot": compact}


@tool
def get_recent_tool_observations(session_id: str, limit: int = 3) -> dict[str, Any]:
    """
    读取最近工具观察摘要。

    适用场景：来源追问、想快速核对上一轮关键事实。
    """
    api = _get_runtime_memory_api()
    if api is None:
        return {"status": "error", "session_id": session_id, "items": [], "error": "MemoryAPI not configured"}

    read_limit = _safe_limit(limit, default=3, minimum=1, maximum=10)
    try:
        facts = api.recall(session_id, {"type": "tool_observation"}, limit=read_limit)
    except Exception as e:
        return {"status": "error", "session_id": session_id, "items": [], "error": str(e)}

    items: list[dict[str, Any]] = []
    for fact in facts:
        payload = fact.payload if isinstance(fact.payload, dict) else {}
        provenance = fact.provenance if isinstance(fact.provenance, dict) else {}
        content = str(payload.get("content") or "").strip()
        items.append(
            {
                "timestamp": str(fact.timestamp or "").strip(),
                "tool": str(payload.get("tool") or fact.source or "").strip(),
                "summary": _truncate_text(str(payload.get("summary") or "").strip(), 180),
                "content": _truncate_text(content, 360),
                "tool_call_id": str(provenance.get("tool_call_id") or "").strip(),
            }
        )
    return {"status": "success", "session_id": session_id, "items": items}


def _normalize_symbol_for_match(symbol: Any) -> str:
    return str(symbol or "").strip().upper().replace("_", "").replace("-", "")


def _append_recent_symbol_candidate(
    items: list[dict[str, Any]],
    seen: set[str],
    *,
    symbol: Any,
    source: str,
    timestamp: str = "",
    interval: str = "",
) -> None:
    clean_symbol = str(symbol or "").strip().upper()
    symbol_key = _normalize_symbol_for_match(clean_symbol)
    if not clean_symbol or not symbol_key or symbol_key in seen:
        return
    seen.add(symbol_key)
    row = {
        "symbol": clean_symbol,
        "source": source,
    }
    if timestamp:
        row["timestamp"] = timestamp
    if interval:
        row["interval"] = interval
    items.append(row)


def load_recent_formal_symbol_candidates(session_id: str, *, limit: int = 6) -> list[dict[str, Any]]:
    """聚合最近分析上下文里的正式 symbol 候选，供开单确认链路复用。"""
    clean_session = str(session_id or "").strip()
    if not clean_session:
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    read_limit = _safe_limit(limit, default=6, minimum=1, maximum=12)

    api = _get_runtime_memory_api()
    if api is not None:
        try:
            snapshot = api.snapshot(clean_session)
        except Exception:
            snapshot = {}
        if isinstance(snapshot, dict):
            _append_recent_symbol_candidate(
                out,
                seen,
                symbol=snapshot.get("symbol"),
                source="last_snapshot",
                timestamp=str(snapshot.get("timestamp") or "").strip(),
                interval=str(snapshot.get("interval") or "").strip(),
            )

        try:
            turn_summaries = api.recall(clean_session, {"type": "turn_summary"}, limit=read_limit)
        except Exception:
            turn_summaries = []
        for fact in turn_summaries:
            payload = fact.payload if isinstance(fact.payload, dict) else {}
            symbols = payload.get("symbols") if isinstance(payload.get("symbols"), list) else []
            intervals = payload.get("intervals") if isinstance(payload.get("intervals"), list) else []
            for idx, symbol in enumerate(symbols):
                _append_recent_symbol_candidate(
                    out,
                    seen,
                    symbol=symbol,
                    source="turn_summary",
                    timestamp=str(fact.timestamp or "").strip(),
                    interval=str(intervals[idx] if idx < len(intervals) else (intervals[0] if intervals else "")).strip(),
                )
                if len(out) >= read_limit:
                    return out[:read_limit]

        try:
            observations = api.recall(clean_session, {"type": "tool_observation"}, limit=read_limit)
        except Exception:
            observations = []
        for fact in observations:
            payload = fact.payload if isinstance(fact.payload, dict) else {}
            content = str(payload.get("content") or "").strip()
            parsed = None
            try:
                parsed = json.loads(content) if content else None
            except Exception:
                parsed = None
            symbol = ""
            interval = ""
            if isinstance(parsed, dict):
                compact = parsed.get("compact_summary_v1") if isinstance(parsed.get("compact_summary_v1"), dict) else {}
                symbol = str(compact.get("symbol") or parsed.get("symbol") or "").strip()
                interval = str(compact.get("interval") or parsed.get("interval") or "").strip()
            _append_recent_symbol_candidate(
                out,
                seen,
                symbol=symbol,
                source="tool_observation",
                timestamp=str(fact.timestamp or "").strip(),
                interval=interval,
            )
            if len(out) >= read_limit:
                return out[:read_limit]

    if len(out) >= read_limit or not get_postgres_dsn():
        return out[:read_limit]

    repo: AnalysisSnapshotRepository | None = None
    try:
        repo = AnalysisSnapshotRepository()
        rows = repo.list_recent_by_session(session_id=clean_session, limit=max(read_limit * 2, 8))
        for row in rows:
            _append_recent_symbol_candidate(
                out,
                seen,
                symbol=getattr(row, "symbol", ""),
                source="analysis_snapshot",
                timestamp=getattr(row, "snapshot_time", None).isoformat() if getattr(row, "snapshot_time", None) else "",
                interval=str(getattr(row, "interval", "") or "").strip(),
            )
            if len(out) >= read_limit:
                break
    except Exception as e:
        logger.warning("[analysis-snapshot] list recent symbols failed session_id=%s error=%s", clean_session, e)
    finally:
        if repo is not None:
            repo.close()

    return out[:read_limit]


def _load_previous_analysis_snapshot_from_db(
    *,
    session_id: str,
    symbol: str,
    interval: str,
    exclude_request_id: str,
    limit: int,
) -> dict[str, Any] | None:
    if not get_postgres_dsn():
        return None
    repo: AnalysisSnapshotRepository | None = None
    try:
        repo = AnalysisSnapshotRepository()
        row = repo.get_previous_by_context(
            session_id=session_id,
            symbol=symbol,
            interval=interval,
            exclude_request_id=exclude_request_id,
            limit=limit,
        )
        if row is None:
            return None
        return repo.to_compact_payload(row)
    except Exception as e:
        logger.warning(
            "[analysis-snapshot] read db failed session_id=%s symbol=%s interval=%s exclude_request_id=%s error=%s",
            session_id,
            symbol,
            interval,
            exclude_request_id or "-",
            e,
        )
        return None
    finally:
        if repo is not None:
            repo.close()


@tool
def get_previous_analysis_snapshot(
    session_id: str,
    symbol: str,
    interval: str,
    exclude_request_id: str = "",
    limit: int = 50,
    request_id: Annotated[str, InjectedState("request_id")] = "",
) -> dict[str, Any]:
    """
    读取同会话、同标的、同周期的最近一条行情分析轻量快照。

    适用场景：需要回答“相比上次同标的分析有什么变化”。
    """
    symbol_key = _normalize_symbol_for_match(symbol)
    interval_key = str(interval or "").strip()
    effective_exclude_request_id = str(exclude_request_id or request_id).strip()
    if not symbol_key or not interval_key:
        return {
            "status": "error",
            "session_id": session_id,
            "snapshot": {},
            "error": "symbol and interval are required",
        }

    if not get_postgres_dsn():
        logger.info(
            "[analysis-snapshot] read stop source=db session_id=%s symbol=%s interval=%s reason=no_postgres_dsn",
            session_id,
            symbol,
            interval_key,
        )
        return {
            "status": "error",
            "session_id": session_id,
            "snapshot": {},
            "error": "PostgreSQL not configured",
        }

    read_limit = _safe_limit(limit, default=50, minimum=1, maximum=200)
    logger.info(
        "[analysis-snapshot] read start session_id=%s symbol=%s interval=%s exclude_request_id=%s db_enabled=%s",
        session_id,
        symbol,
        interval_key,
        effective_exclude_request_id or "-",
        "yes",
    )
    db_snapshot = _load_previous_analysis_snapshot_from_db(
        session_id=session_id,
        symbol=symbol,
        interval=interval_key,
        exclude_request_id=effective_exclude_request_id,
        limit=read_limit,
    )
    if db_snapshot:
        logger.info(
            "[analysis-snapshot] read hit source=db session_id=%s symbol=%s interval=%s timestamp=%s",
            session_id,
            symbol,
            interval_key,
            str(db_snapshot.get("timestamp") or "-"),
        )
        return {"status": "success", "session_id": session_id, "snapshot": db_snapshot}
    logger.info(
        "[analysis-snapshot] read miss source=db session_id=%s symbol=%s interval=%s",
        session_id,
        symbol,
        interval_key,
    )
    return {
        "status": "not_found",
        "session_id": session_id,
        "snapshot": {},
    }


def _compact_turn_summary(payload: dict[str, Any], *, timestamp: str) -> dict[str, Any]:
    key_levels = payload.get("key_levels") if isinstance(payload.get("key_levels"), dict) else {}
    support = key_levels.get("support") if isinstance(key_levels.get("support"), list) else []
    resistance = key_levels.get("resistance") if isinstance(key_levels.get("resistance"), list) else []
    out = {
        "timestamp": timestamp,
        "symbols": payload.get("symbols") if isinstance(payload.get("symbols"), list) else [],
        "intervals": payload.get("intervals") if isinstance(payload.get("intervals"), list) else [],
        "current_price": payload.get("current_price"),
        "trend": payload.get("trend"),
        "key_levels": {
            "support": support[:2],
            "resistance": resistance[:2],
        },
        "stance": payload.get("stance"),
        "next_trigger": _truncate_text(str(payload.get("next_trigger") or "").strip(), 120),
        "invalidation": _truncate_text(str(payload.get("invalidation") or "").strip(), 120),
        "user_question": _truncate_text(str(payload.get("user_question") or "").strip(), 120),
        "assistant_conclusion": _truncate_text(str(payload.get("assistant_conclusion") or "").strip(), 160),
    }
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


@tool
def search_conversation_summaries(
    session_id: str,
    limit: int = 12,
    max_chars: int = _DEFAULT_SUMMARY_BUDGET,
) -> dict[str, Any]:
    """
    读取最近多轮 turn_summary 摘要集合（非原始对话）。

    默认中文摘要预算约 8000 字，硬上限 10000。
    """
    api = _get_runtime_memory_api()
    if api is None:
        return {"status": "error", "session_id": session_id, "items": [], "error": "MemoryAPI not configured"}

    read_limit = _safe_limit(limit, default=12, minimum=1, maximum=20)
    budget = _safe_limit(max_chars, default=_DEFAULT_SUMMARY_BUDGET, minimum=800, maximum=_MAX_SUMMARY_BUDGET)

    try:
        facts = api.recall(session_id, {"type": "turn_summary"}, limit=read_limit)
    except Exception as e:
        return {"status": "error", "session_id": session_id, "items": [], "error": str(e)}

    # recall 默认新到旧，这里改成旧到新，便于 LLM 顺序承接。
    ordered = list(reversed(facts))
    items: list[dict[str, Any]] = []
    used_chars = 0

    for fact in ordered:
        payload = fact.payload if isinstance(fact.payload, dict) else {}
        if not payload:
            continue
        row = _compact_turn_summary(payload, timestamp=str(fact.timestamp or "").strip())
        if not row:
            continue
        row_chars = len(json.dumps(row, ensure_ascii=False, default=str))
        if items and used_chars + row_chars > budget:
            break
        items.append(row)
        used_chars += row_chars
        if used_chars >= budget:
            break

    return {
        "status": "success",
        "session_id": session_id,
        "items": items,
        "total_items": len(items),
        "total_chars": used_chars,
        "char_budget": budget,
    }
