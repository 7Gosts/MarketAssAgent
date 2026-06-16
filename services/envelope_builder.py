from __future__ import annotations

import ast
import json
import os
from datetime import datetime
from typing import Any

from schemas.conversation import ConversationEnvelope, DeliveryHint
from schemas.response_plan import ResponsePlan


DEFAULT_DISCLAIMER = "仅供技术分析与程序化演示，不构成投资建议。"


class EnvelopeBuilder:
    """Markdown-first envelope builder.

    当前阶段 `reply_text` 是唯一主输出，`blocks` 仅保留空数组兼容字段。
    """

    def build(
        self,
        plan: ResponsePlan | None,
        agent_result: dict[str, Any],
        reply_text: str,
        user_text: str = "",
    ) -> dict[str, Any]:
        markdown_text = self._convert_to_markdown(plan, agent_result, reply_text, user_text=user_text)
        market_meta = self._market_meta_from_result(agent_result)
        envelope = {
            "version": "1.2",
            "reply_text": markdown_text,
            "blocks": [],
            "meta": {
                "task_type": plan.task_type if plan else "chat",
                "response_style": plan.response_style if plan else "brief",
                "key_focus": plan.key_focus if plan else None,
                "symbols": market_meta.get("symbols", []),
                "timestamp": market_meta.get("timestamp"),
            },
            "raw": agent_result.get("raw_data") if agent_result.get("debug") else None,
        }
        return {"envelope": envelope, "reply": markdown_text}

    def build_from_result(
        self,
        *,
        result: dict[str, Any],
        reply_text: str,
        session_id: str,
        user_text: str = "",
        plan: ResponsePlan | None = None,
    ) -> ConversationEnvelope:
        payload = self.build(plan, result, reply_text, user_text=user_text)
        envelope_data = payload["envelope"]

        delivery_hint = DeliveryHint(
            mode="text",
            card_style="plain",
            has_rich_content=False,
            block_summary=[],
        )

        meta = dict(envelope_data.get("meta") or {})
        meta["session_id"] = session_id
        meta["request_style"] = _request_style(user_text, plan)
        meta["timestamp"] = meta.get("timestamp") or datetime.now().isoformat()
        meta["has_rich_content"] = delivery_hint.has_rich_content
        meta["block_summary"] = list(delivery_hint.block_summary)
        if plan:
            meta["response_plan"] = plan.model_dump(mode="json")

        raw_payload = dict(envelope_data.get("raw") or {})
        if not _include_raw_payload():
            raw_payload = {}

        return ConversationEnvelope(
            version=str(envelope_data.get("version") or "1.2"),
            reply_text=str(envelope_data.get("reply_text") or reply_text),
            blocks=[],
            meta=meta,
            raw=raw_payload,
            delivery_hint=delivery_hint,
        )

    def build_from_text(
        self,
        text: str,
        *,
        plan: dict[str, Any] | ResponsePlan | None = None,
        session_id: str = "default",
        user_text: str = "",
    ) -> ConversationEnvelope:
        normalized_plan = plan if isinstance(plan, ResponsePlan) else None
        return build_conversation_envelope(
            result={"reply": text},
            reply_text=text,
            session_id=session_id,
            user_text=user_text,
            plan=normalized_plan,
        )

    def _convert_to_markdown(
        self,
        plan: ResponsePlan | None,
        result: dict[str, Any],
        reply_text: str,
        user_text: str = "",
    ) -> str:
        text = str(reply_text or "").strip()
        if not text:
            text = str(result.get("reply") or result.get("output_text") or "").strip()

        task_type = plan.task_type if plan else ("trade_plan" if _is_trade_plan_request(user_text) else "chat")

        if task_type == "trade_plan":
            return "\n\n".join(
                [
                    "**交易计划建议**",
                    text,
                    f"> 风险提示：{DEFAULT_DISCLAIMER}",
                ]
            )

        if task_type == "position_review":
            return "\n\n".join(
                [
                    "**仓位复盘**",
                    text,
                ]
            )

        return text

    def _market_meta_from_result(self, result: dict[str, Any]) -> dict[str, Any]:
        symbols: list[str] = []
        timestamp: str | None = None

        for payload in _collect_structured_payloads(result):
            payload_symbols, payload_ts = _extract_symbols_and_timestamp(payload)
            for symbol in payload_symbols:
                if symbol not in symbols:
                    symbols.append(symbol)
            if not timestamp and payload_ts:
                timestamp = payload_ts

        for symbol in result.get("symbols") or []:
            if isinstance(symbol, str) and symbol and symbol not in symbols:
                symbols.append(symbol)

        return {
            "symbols": symbols,
            "timestamp": timestamp,
        }


def build_conversation_envelope(
    *,
    result: dict[str, Any],
    reply_text: str,
    session_id: str,
    user_text: str = "",
    plan: ResponsePlan | None = None,
) -> ConversationEnvelope:
    """Build the shared response envelope from existing agent output."""
    builder = EnvelopeBuilder()
    return builder.build_from_result(
        result=result,
        reply_text=reply_text,
        session_id=session_id,
        user_text=user_text,
        plan=plan,
    )


def _include_raw_payload() -> bool:
    value = os.environ.get("MARKETASSAGENT_INCLUDE_RAW", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_trade_plan_request(text: str) -> bool:
    return any(
        keyword in text.lower()
        for keyword in ["开单", "交易计划", "入场", "止损", "止盈", "仓位", "做多", "做空"]
    )


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


def _extract_symbols_and_timestamp(payload: dict[str, Any]) -> tuple[list[str], str | None]:
    symbols: list[str] = []
    timestamp: str | None = None

    def _push_symbol(value: Any) -> None:
        if isinstance(value, str):
            sym = value.strip()
            if sym and sym not in symbols:
                symbols.append(sym)

    _push_symbol(payload.get("symbol"))
    for value in payload.get("symbols") or []:
        _push_symbol(value)

    analysis = payload.get("analysis")
    if isinstance(analysis, dict):
        _push_symbol(analysis.get("symbol"))
        if not timestamp and isinstance(analysis.get("timestamp"), str):
            timestamp = str(analysis.get("timestamp"))

    comparison = payload.get("comparison")
    if isinstance(comparison, dict):
        for item in comparison.get("summary") or []:
            if isinstance(item, dict):
                _push_symbol(item.get("symbol"))

    if not timestamp:
        for ts_key in ("timestamp", "updated_at", "ts"):
            ts_value = payload.get(ts_key)
            if isinstance(ts_value, str) and ts_value.strip():
                timestamp = ts_value.strip()
                break

    return symbols, timestamp


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


def _request_style(user_text: str, plan: ResponsePlan | None) -> str:
    if plan:
        return plan.task_type
    return "trade_plan" if _is_trade_plan_request(user_text) else "analysis"
