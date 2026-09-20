from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from utils.logging_utils import get_logger
from utils.runtime_paths import get_debug_dir
from .historical_claim_guard import HistoricalClaimGuard
from .message_protocol import Message, ToolCall
from .state import AgentState
from .supervisor import supervisor_node
from .tool_protocol import ToolContext
from tools.session_history import query_session_history
from utils.crash_injection import crash_if_requested


logger = get_logger(__name__)


def select_tool_names(requested: list[str] | None, all_names: set[str]) -> set[str]:
    if not requested:
        return set(all_names)
    return {name for name in requested if name in all_names}


class NativeAgentLoop:
    def __init__(self, *, llm: Any, registry: Any, executor: Any, max_steps: int = 8) -> None:
        self.llm = llm
        self.registry = registry
        self.executor = executor
        self.max_steps = max(1, int(max_steps))

    async def run(self, state: AgentState) -> dict[str, Any]:
        all_names = {spec.name for spec in self.registry.all()}
        allowed_names = select_tool_names(state.get("allowed_tools"), all_names)
        seen_signatures = _extract_tool_signatures(state.get("messages") or [])
        event_journal = state.get("event_journal")
        turn_user_event_id = str(state.get("turn_user_event_id") or "")
        last_model_request_event_id = ""

        for step in range(self.max_steps):
            _debug_event(state, "reason_start", step=step, active_tools=len(allowed_names))
            tool_schemas = self.registry.schemas(allowed_names)
            if event_journal is not None:
                if not turn_user_event_id:
                    raise ValueError("turn_user_event_id is required when event_journal is enabled")
                request_payload = _build_request_payload(
                    self.llm,
                    messages=state["messages"],
                    tools=tool_schemas,
                )
                model_request_event = event_journal.append_model_request(
                    request_payload=request_payload,
                    prompt_version=str(state.get("prompt_version") or "unknown"),
                    checkpoint_event_id=state.get("checkpoint_event_id"),
                )
                last_model_request_event_id = model_request_event.event_id
            try:
                response = await self.llm.complete(
                    messages=state["messages"],
                    tools=tool_schemas,
                )
            except Exception as exc:
                if event_journal is not None:
                    event_journal.append_assistant_attempt(
                        model_request_event_id=last_model_request_event_id,
                        attempt_ordinal=1,
                        status="failed",
                        error_type=type(exc).__name__,
                    )
                raise
            _record_usage(state, response.usage)
            calls = list(response.message.tool_calls)

            if not calls:
                (
                    final_message,
                    provider_content,
                    final_request_event_id,
                    final_response,
                    persist_as_local,
                ) = await self._guard_final_answer(
                    state=state,
                    message=response.message,
                    response=response,
                    model_request_event_id=last_model_request_event_id,
                )
                state["messages"].append(final_message)
                if event_journal is not None:
                    if persist_as_local:
                        assistant_event = event_journal.append_local_assistant_message(
                            turn_user_event_id=turn_user_event_id,
                            reason="historical_claim_repair_failed",
                            content=final_message.content,
                        )
                    else:
                        assistant_event = event_journal.append_assistant_message(
                            model_request_event_id=final_request_event_id,
                            message=final_message,
                            provider_response_id=_provider_response_id(final_response),
                            provider_content=provider_content,
                        )
                    metadata = state.get("metadata") or {}
                    metadata["last_assistant_event_id"] = assistant_event.event_id
                    state["metadata"] = metadata
                _debug_event(state, "final_answer_ready", step=step)
                return _finalize(state)

            state["messages"].append(response.message)
            assistant_event = None
            if event_journal is not None:
                assistant_event = event_journal.append_assistant_message(
                    model_request_event_id=last_model_request_event_id,
                    message=response.message,
                    provider_response_id=_provider_response_id(response),
                )
                metadata = state.get("metadata") or {}
                metadata["last_assistant_event_id"] = assistant_event.event_id
                state["metadata"] = metadata

            signatures = [_tool_signature(call) for call in calls]
            duplicates = _count_duplicates(signatures) + sum(sig in seen_signatures for sig in signatures)
            seen_signatures.update(signatures)
            if duplicates:
                logger.warning(
                    "[NativeAgentLoop] duplicate tool call session_id=%s count=%s",
                    state.get("session_id"),
                    duplicates,
                )
            threshold = _tool_call_warn_threshold()
            if len(calls) > threshold:
                logger.warning(
                    "[NativeAgentLoop] tool call count high session_id=%s count=%s threshold=%s",
                    state.get("session_id"),
                    len(calls),
                    threshold,
                )

            for call in calls:
                event = "tool_call" if call.name in allowed_names else "tool_call_rejected"
                _debug_event(state, event, step=step, tool_name=call.name, tool_call_id=call.id)
                spec = self.registry.get(call.name)
                planned = None
                if event_journal is not None:
                    planned = event_journal.append_tool_plan(
                        turn_user_event_id=turn_user_event_id,
                        assistant_event_id=assistant_event.event_id,
                        call=call,
                        tool_version=str(getattr(spec, "version", "unregistered")),
                        effect_class=str(getattr(spec, "effect_class", "opaque_effect")),
                    )
                    crash_if_requested("after_planned_before_started")
                executable = spec is not None and call.name in allowed_names
                if planned is not None and executable:
                    event_journal.append_tool_started(operation_id=planned.operation_id, attempt_no=1)
                    crash_if_requested("after_started_before_tool_execute")
                context = ToolContext(
                    session_id=str(state.get("session_id") or "default"),
                    request_id=str(state.get("request_id") or ""),
                    operation_id=planned.operation_id if planned is not None else "",
                    turn_user_event_id=turn_user_event_id,
                    conversation_scope=getattr(event_journal, "scope", None),
                    event_store=getattr(event_journal, "store", None),
                )
                result = await self.executor.execute(
                    call,
                    context=context,
                    allowed_names=allowed_names,
                )
                crash_if_requested("after_tool_execute_before_result")
                if planned is not None:
                    parsed_result = _decode_tool_message(result)
                    event_journal.append_tool_result(
                        operation_id=planned.operation_id,
                        provider_tool_call_id=call.id,
                        tool_name=call.name,
                        status=_tool_result_status(parsed_result) if executable else "interrupted",
                        result=parsed_result,
                    )
                    _register_retrieval_evidence(state, call.name, parsed_result)
                state["messages"].append(result)
                _debug_event(state, "tool_result", step=step, tool_name=call.name, tool_call_id=call.id)

        message = f"Agent 已达到最大步骤限制（{self.max_steps}），请缩小问题范围后重试。"
        local_message = Message(role="assistant", content=message)
        state["messages"].append(local_message)
        if event_journal is not None:
            event_journal.append_turn_aborted(
                turn_user_event_id=turn_user_event_id,
                abort_nonce=str(state.get("request_id") or "agent_loop_limit"),
                reason="agent_loop_limit",
            )
            assistant_event = event_journal.append_local_assistant_message(
                turn_user_event_id=turn_user_event_id,
                reason="agent_loop_limit",
                content=message,
            )
            metadata = state.get("metadata") or {}
            metadata["last_assistant_event_id"] = assistant_event.event_id
            state["metadata"] = metadata
        state["error"] = "agent_loop_limit"
        _debug_event(state, "loop_limit", step=self.max_steps)
        return _finalize(state)

    async def _guard_final_answer(
        self,
        *,
        state: AgentState,
        message: Message,
        response: Any,
        model_request_event_id: str,
    ) -> tuple[Message, str | None, str, Any, bool]:
        event_journal = state.get("event_journal")
        if event_journal is None:
            return message, None, model_request_event_id, response, False

        guard = HistoricalClaimGuard(
            evidence_index=state.get("evidence_index") or {},
            retrieval_receipts=state.get("retrieval_receipts") or [],
        )
        validation = guard.validate(message.content)
        if validation.valid:
            return (
                Message(role="assistant", content=guard.render(message.content)),
                message.content,
                model_request_event_id,
                response,
                False,
            )

        event_journal.append_assistant_attempt(
            model_request_event_id=model_request_event_id,
            attempt_ordinal=1,
            status="rejected",
            error_type="HistoricalClaimValidation",
            provider_response_id=_provider_response_id(response),
            content=message.content,
            details={"reasons": list(validation.reasons)},
        )
        repair_messages = await self._build_history_repair_messages(
            state=state,
            draft=message.content,
            reasons=validation.reasons,
        )
        request_payload = _build_request_payload(self.llm, messages=repair_messages, tools=[])
        repaired_request = event_journal.append_model_request(
            request_payload=request_payload,
            prompt_version=str(state.get("prompt_version") or "unknown"),
            checkpoint_event_id=state.get("checkpoint_event_id"),
        )
        try:
            repaired_response = await self.llm.complete(messages=repair_messages, tools=[])
        except Exception as exc:
            event_journal.append_assistant_attempt(
                model_request_event_id=repaired_request.event_id,
                attempt_ordinal=1,
                status="failed",
                error_type=type(exc).__name__,
            )
            degraded = guard.degrade(validation.text, validation.invalid_spans)
            metadata = state.get("metadata") or {}
            metadata["historical_claim_guard"] = {
                "result": "repair_failed",
                "initial_reasons": list(validation.reasons),
                "error_type": type(exc).__name__,
            }
            state["metadata"] = metadata
            return (
                Message(role="assistant", content=degraded),
                None,
                repaired_request.event_id,
                None,
                True,
            )

        _record_usage(state, repaired_response.usage)
        repaired_message = repaired_response.message
        refreshed_guard = HistoricalClaimGuard(
            evidence_index=state.get("evidence_index") or {},
            retrieval_receipts=state.get("retrieval_receipts") or [],
        )
        repaired_validation = refreshed_guard.validate(repaired_message.content)
        if repaired_message.tool_calls or not repaired_validation.valid:
            content = refreshed_guard.degrade(
                repaired_message.content,
                repaired_validation.invalid_spans,
            )
            outcome = "insufficient"
        else:
            content = refreshed_guard.render(repaired_message.content)
            outcome = "repaired"
        metadata = state.get("metadata") or {}
        metadata["historical_claim_guard"] = {
            "result": outcome,
            "initial_reasons": list(validation.reasons),
        }
        state["metadata"] = metadata
        return (
            Message(role="assistant", content=content),
            repaired_message.content,
            repaired_request.event_id,
            repaired_response,
            False,
        )

    async def _build_history_repair_messages(
        self,
        *,
        state: AgentState,
        draft: str,
        reasons: tuple[str, ...],
    ) -> list[Message]:
        query = _last_user_text(state.get("messages") or [])
        event_journal = state.get("event_journal")
        if event_journal is None:
            evidence_payload: Any = {
                "status": "error",
                "error": "event context is not configured",
                "items": [],
            }
        else:
            evidence_payload = await asyncio.to_thread(
                query_session_history,
                keyword=query,
                days=_history_days(query),
                limit=100,
                event_store=event_journal.store,
                scope=event_journal.scope,
                exclude_user_event_id=str(state.get("turn_user_event_id") or ""),
            )
        _register_retrieval_evidence(state, "search_session_history", evidence_payload)

        repair_instruction = Message(
            role="system",
            content=(
                "上一版回答的历史主张未通过证据校验。请只依据下面的检索结果重写答案；"
                "每个历史陈述句末必须附 [mem:event_id]。无法确认的内容明确写记录不足。"
                f"\n校验原因：{json.dumps(list(reasons), ensure_ascii=False)}"
                f"\n上一版草稿：{draft}"
                f"\n补查结果：{json.dumps(evidence_payload, ensure_ascii=False, default=str)}"
            ),
        )
        return [*(state.get("messages") or []), repair_instruction]


def _finalize(state: AgentState) -> dict[str, Any]:
    finalized = dict(state)
    finalized.update(supervisor_node(state))
    finalized["metadata"] = state.get("metadata") or {}
    finalized["error"] = state.get("error")
    return finalized


def _record_usage(state: AgentState, usage: Any) -> None:
    values = {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        "reasoning_tokens": int(getattr(usage, "reasoning_tokens", 0) or 0),
        "cached_prompt_tokens": int(getattr(usage, "cached_prompt_tokens", 0) or 0),
    }
    if not any(values.values()):
        return
    metadata = state.setdefault("metadata", {}) or {}
    state["metadata"] = metadata
    totals = metadata.setdefault("token_usage", {})
    for key, value in values.items():
        totals[key] = int(totals.get(key) or 0) + value
    if os.getenv("MARKETASSAGENT_DEBUG_TOKEN_USAGE", "0").strip().lower() in {"1", "true", "yes", "on"}:
        _append_debug_jsonl("llm_token_usage.jsonl", {
            "ts": time.time(),
            "session_id": state.get("session_id"),
            "request_id": state.get("request_id"),
            **values,
        })


def _tool_signature(call: ToolCall) -> str:
    return f"{call.name.strip().lower()}:{json.dumps(call.arguments, ensure_ascii=False, sort_keys=True, default=str)}"


def _build_request_payload(llm: Any, *, messages: list[Message], tools: list[dict[str, Any]]) -> dict[str, Any]:
    builder = getattr(llm, "build_request_payload", None)
    if callable(builder):
        return dict(builder(messages=messages, tools=tools))
    payload: dict[str, Any] = {
        "model": str(getattr(llm, "model", "unknown")),
        "messages": [message.to_openai_dict() for message in messages],
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    return payload


def _provider_response_id(response: Any) -> str:
    raw = getattr(response, "raw", None)
    return str(raw.get("id") or "") if isinstance(raw, dict) else ""


def _decode_tool_message(message: Message) -> Any:
    try:
        return json.loads(message.content)
    except (TypeError, json.JSONDecodeError):
        return message.content


def _tool_result_status(result: Any) -> str:
    if isinstance(result, dict) and str(result.get("status") or "").lower() in {"error", "failed"}:
        return "failed"
    return "succeeded"


def _register_retrieval_evidence(state: AgentState, tool_name: str, result: Any) -> None:
    if tool_name != "search_session_history" or not isinstance(result, dict):
        return
    receipt = result.get("retrieval_receipt")
    if not isinstance(receipt, dict):
        return
    receipts = state.setdefault("retrieval_receipts", [])
    receipts.append(dict(receipt))
    evidence = state.setdefault("evidence_index", {})
    for event_id in receipt.get("hit_event_ids") or receipt.get("event_ids") or []:
        if str(event_id):
            evidence[str(event_id)] = "retrieved"


def _last_user_text(messages: list[Message]) -> str:
    for message in reversed(messages):
        if message.role == "user" and message.content.strip():
            return message.content.strip()
    return "最近对话"


def _history_days(text: str) -> int:
    match = re.search(r"最近\s*(\d+)\s*天", text)
    if match:
        return min(max(int(match.group(1)), 1), 90)
    if "一周" in text or "本周" in text or "这周" in text:
        return 7
    if "一个月" in text or "本月" in text:
        return 30
    return 7


def _extract_tool_signatures(messages: list[Message]) -> set[str]:
    return {
        _tool_signature(call)
        for message in messages
        for call in getattr(message, "tool_calls", ())
    }


def _count_duplicates(values: list[str]) -> int:
    seen: set[str] = set()
    duplicates = 0
    for value in values:
        if value in seen:
            duplicates += 1
        else:
            seen.add(value)
    return duplicates


def _tool_call_warn_threshold(default: int = 6) -> int:
    try:
        value = int(os.getenv("MARKETASSAGENT_TOOL_CALL_WARN_THRESHOLD", "") or default)
    except ValueError:
        return default
    return value if value >= 1 else default


def _debug_event(state: AgentState, event_type: str, **payload: Any) -> None:
    if os.getenv("MARKETASSAGENT_DEBUG_AGENT_LOOP", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    _append_debug_jsonl("agent_loop_trace.jsonl", {
        "ts": time.time(),
        "session_id": state.get("session_id"),
        "request_id": state.get("request_id"),
        "event_type": event_type,
        "payload": payload,
    })


def _append_debug_jsonl(filename: str, payload: dict[str, Any]) -> None:
    try:
        debug_dir: Path = get_debug_dir()
        debug_dir.mkdir(parents=True, exist_ok=True)
        with (debug_dir / filename).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.warning("native agent debug dump failed: %s", exc)
