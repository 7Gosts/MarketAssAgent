from __future__ import annotations

import ast
import json
import os
from datetime import datetime
from typing import Any

from schemas.conversation import ConversationBlock, ConversationEnvelope, DeliveryHint
from schemas.response_plan import ResponsePlan


DEFAULT_DISCLAIMER = "仅供技术分析与程序化演示，不构成投资建议。"


def build_conversation_envelope(
    *,
    result: dict[str, Any],
    reply_text: str,
    session_id: str,
    user_text: str = "",
    plan: ResponsePlan | None = None,
) -> ConversationEnvelope:
    """Build the shared response envelope from existing agent output."""
    payloads = _collect_structured_payloads(result)
    recommendation = result.get("recommendation") if isinstance(result, dict) else {}
    recommendation = recommendation if isinstance(recommendation, dict) else {}

    blocks: list[ConversationBlock] = []
    raw: dict[str, Any] = {}

    market_block = _build_market_block(result, payloads)
    if market_block:
        if _is_trade_plan_request(user_text):
            market_block = _as_trade_plan_market_block(market_block)
        blocks.append(market_block)
        raw["market_analysis"] = market_block.data

    research_block = _build_research_block(payloads)
    if research_block:
        blocks.append(research_block)
        raw["research_summary"] = research_block.data

    planned_block = _build_planned_text_block(plan, reply_text)
    if planned_block:
        blocks.append(planned_block)

    if not blocks:
        blocks.append(
            ConversationBlock(
                type="text_fallback",
                title="回复",
                data={"text": reply_text},
            )
        )

    disclaimer = str(recommendation.get("disclaimer") or DEFAULT_DISCLAIMER).strip()
    blocks.append(
        ConversationBlock(
            type="risk_warning",
            title="风险提示",
            data={"text": disclaimer},
        )
    )

    raw["recommendation"] = _compact_recommendation(recommendation)
    if result.get("journal_id") is not None:
        raw["journal_id"] = result.get("journal_id")
    if result.get("error"):
        raw["error"] = result.get("error")

    delivery_hint = _build_delivery_hint(blocks)
    meta = _build_meta(
        session_id=session_id,
        blocks=blocks,
        delivery_hint=delivery_hint,
        user_text=user_text,
        plan=plan,
    )

    return ConversationEnvelope(
        reply_text=reply_text,
        blocks=blocks,
        meta=meta,
        raw=raw if _include_raw_payload() else {},
        delivery_hint=delivery_hint,
    )


def _include_raw_payload() -> bool:
    value = os.environ.get("MARKETASSAGENT_INCLUDE_RAW", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_trade_plan_request(text: str) -> bool:
    return any(
        keyword in text.lower()
        for keyword in ["开单", "交易计划", "入场", "止损", "止盈", "仓位", "做多", "做空"]
    )


def _as_trade_plan_market_block(block: ConversationBlock) -> ConversationBlock:
    data = dict(block.data)
    data["request_style"] = "trade_plan"
    title = block.title.replace("技术分析", "开单计划") if block.title else "开单计划"
    return block.model_copy(update={"title": title, "data": data})


def _collect_structured_payloads(result: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []

    for key in ("analysis_result", "last_snapshot"):
        value = result.get(key)
        if isinstance(value, dict) and value:
            payloads.append(value)

    messages = result.get("messages") or []
    for message in messages:
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        payload = _coerce_payload(content)
        if isinstance(payload, dict):
            payloads.append(payload)
        elif isinstance(payload, list):
            payloads.extend(item for item in payload if isinstance(item, dict))

    return payloads


def _coerce_payload(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(raw)
        except Exception:
            continue
    return None


def _build_market_block(
    result: dict[str, Any],
    payloads: list[dict[str, Any]],
) -> ConversationBlock | None:
    explicit_multi = next((p for p in payloads if _is_multi_market_payload(p)), None)
    if explicit_multi:
        return _multi_market_block(explicit_multi)

    analyses: list[dict[str, Any]] = []
    for payload in payloads:
        analysis = _extract_single_analysis(payload)
        if analysis:
            analyses.append(analysis)

    for key in ("analysis_result", "last_snapshot"):
        value = result.get(key)
        if isinstance(value, dict) and value:
            analysis = _extract_single_analysis(value)
            if analysis:
                analyses.append(analysis)

    analyses = _dedupe_analyses(analyses)
    if len(analyses) > 1:
        symbols = {str(a.get("symbol") or "").strip() for a in analyses if a.get("symbol")}
        if len(symbols) == 1:
            analysis = _choose_primary_analysis(analyses)
            return _single_market_block(analysis)

        return _multi_market_block(
            {
                "symbols": [a.get("symbol") for a in analyses if a.get("symbol")],
                "interval": analyses[0].get("interval"),
                "analyses": {
                    str(a.get("symbol") or idx): {"status": "success", "analysis": a}
                    for idx, a in enumerate(analyses)
                },
                "comparison": _summarize_analyses(analyses),
            }
        )

    if len(analyses) == 1:
        return _single_market_block(analyses[0])

    return None


def _choose_primary_analysis(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    for analysis in reversed(analyses):
        if analysis.get("interval"):
            return analysis
    return analyses[-1]


def _single_market_block(analysis: dict[str, Any]) -> ConversationBlock:
    symbol = str(analysis.get("symbol") or "").strip()
    interval = str(analysis.get("interval") or "").strip()
    title_parts = [p for p in [symbol, interval, "技术分析"] if p]
    return ConversationBlock(
        type="market_analysis",
        title=" ".join(title_parts) or "市场分析",
        data={
            "is_multi": False,
            "symbol": symbol,
            "interval": interval,
            "current_price": analysis.get("current_price"),
            "trend": analysis.get("trend"),
            "confidence": analysis.get("confidence"),
            "key_levels": analysis.get("key_levels") or {},
            "structure": analysis.get("structure") or "",
            "indicators": analysis.get("indicators") or {},
            "timestamp": analysis.get("timestamp"),
        },
    )


def _is_multi_market_payload(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("analyses"), dict) and isinstance(payload.get("comparison"), dict)


def _extract_single_analysis(payload: dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(payload.get("analysis"), dict):
        return payload["analysis"]
    if _looks_like_analysis(payload):
        return payload
    return None


def _looks_like_analysis(payload: dict[str, Any]) -> bool:
    return bool(payload.get("symbol")) and (
        "trend" in payload
        or "confidence" in payload
        or "key_levels" in payload
        or "current_price" in payload
    )


def _dedupe_analyses(analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for analysis in analyses:
        key = (
            str(analysis.get("symbol") or ""),
            str(analysis.get("interval") or ""),
            str(analysis.get("timestamp") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(analysis)
    return unique


def _multi_market_block(payload: dict[str, Any]) -> ConversationBlock:
    comparison = payload.get("comparison") if isinstance(payload.get("comparison"), dict) else {}
    summary = comparison.get("summary") if isinstance(comparison.get("summary"), list) else []
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), list) else []
    interval = str(payload.get("interval") or "").strip()

    return ConversationBlock(
        type="market_analysis",
        title=f"{len(symbols) or len(summary)} 个标的对比分析",
        data={
            "is_multi": True,
            "symbols": symbols,
            "interval": interval,
            "summary": summary,
            "strongest": comparison.get("strongest"),
            "weakest": comparison.get("weakest"),
            "trend_distribution": comparison.get("trend_distribution") or {},
        },
    )


def _summarize_analyses(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    summary = [
        {
            "symbol": a.get("symbol"),
            "trend": a.get("trend"),
            "confidence": a.get("confidence") or 0,
            "current_price": a.get("current_price"),
        }
        for a in analyses
    ]
    valid = [item for item in summary if item.get("symbol")]
    strongest = max(valid, key=lambda x: x.get("confidence", 0)) if valid else None
    weakest = min(valid, key=lambda x: x.get("confidence", 0)) if valid else None
    return {
        "summary": summary,
        "strongest": strongest,
        "weakest": weakest,
        "trend_distribution": {
            trend: len([s for s in valid if s.get("trend") == trend])
            for trend in ("偏多", "偏空", "震荡")
        },
    }


def _build_research_block(payloads: list[dict[str, Any]]) -> ConversationBlock | None:
    payload = next((p for p in payloads if "results" in p and "keyword" in p), None)
    if not payload:
        return None
    keyword = str(payload.get("keyword") or "").strip()
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    return ConversationBlock(
        type="research_summary",
        title=f"{keyword} 研报摘要" if keyword else "研报摘要",
        data={
            "keyword": keyword,
            "total": payload.get("total") or len(results),
            "results": results,
        },
    )


def _build_planned_text_block(
    plan: ResponsePlan | None,
    reply_text: str,
) -> ConversationBlock | None:
    if not plan or plan.task_type in {"market_view", "comparison", "research"}:
        return None
    if plan.task_type == "chat":
        return None

    block_type = {
        "trade_plan": "trade_plan",
        "position_advice": "position_advice",
        "rule_explain": "rule_explain",
        "journal_review": "journal_summary",
    }.get(plan.task_type)
    if not block_type:
        return None

    title = {
        "trade_plan": "交易计划",
        "position_advice": "仓位建议",
        "rule_explain": "规则说明",
        "journal_review": "复盘摘要",
    }.get(plan.task_type, "回复")

    return ConversationBlock(
        type=block_type,  # type: ignore[arg-type]
        title=title,
        data={
            "text": reply_text,
            "sections": plan.sections,
            "task_type": plan.task_type,
        },
    )


def _compact_recommendation(recommendation: dict[str, Any]) -> dict[str, Any]:
    return {
        key: recommendation.get(key)
        for key in ("text", "disclaimer", "timestamp")
        if recommendation.get(key)
    }


def _build_delivery_hint(blocks: list[ConversationBlock]) -> DeliveryHint:
    block_types = [block.type for block in blocks]
    has_rich = any(
        block.type in {"market_analysis", "trade_plan", "position_advice", "journal_summary"}
        for block in blocks
    )
    return DeliveryHint(
        mode="rich" if has_rich else "text",
        card_style="assistant_response" if has_rich else "plain",
        has_rich_content=has_rich,
        block_summary=block_types,
    )


def _build_meta(
    *,
    session_id: str,
    blocks: list[ConversationBlock],
    delivery_hint: DeliveryHint,
    user_text: str = "",
    plan: ResponsePlan | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "session_id": session_id,
        "timestamp": datetime.now().isoformat(),
        "block_summary": delivery_hint.block_summary,
        "has_rich_content": delivery_hint.has_rich_content,
        "request_style": _request_style(user_text, plan),
    }

    if plan:
        meta["response_plan"] = plan.model_dump(mode="json")

    market_block = next((b for b in blocks if b.type == "market_analysis"), None)
    if market_block:
        data = market_block.data
        if data.get("is_multi"):
            meta["symbols"] = data.get("symbols") or []
        elif data.get("symbol"):
            meta["symbol"] = data.get("symbol")
        if data.get("interval"):
            meta["interval"] = data.get("interval")

    return meta


def _request_style(user_text: str, plan: ResponsePlan | None) -> str:
    if plan:
        return plan.task_type
    return "trade_plan" if _is_trade_plan_request(user_text) else "analysis"
