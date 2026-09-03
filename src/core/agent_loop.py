from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from utils.logging_utils import get_logger
from utils.runtime_paths import get_debug_dir
from .message_protocol import Message, ToolCall
from .state import AgentState
from .supervisor import supervisor_node
from .tool_protocol import ToolContext


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

        for step in range(self.max_steps):
            _debug_event(state, "reason_start", step=step, active_tools=len(allowed_names))
            response = await self.llm.complete(
                messages=state["messages"],
                tools=self.registry.schemas(allowed_names),
            )
            _record_usage(state, response.usage)
            state["messages"].append(response.message)
            calls = list(response.message.tool_calls)

            if not calls:
                _debug_event(state, "final_answer_ready", step=step)
                return _finalize(state)

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

            context = ToolContext(
                session_id=str(state.get("session_id") or "default"),
                request_id=str(state.get("request_id") or ""),
            )
            for call in calls:
                event = "tool_call" if call.name in allowed_names else "tool_call_rejected"
                _debug_event(state, event, step=step, tool_name=call.name, tool_call_id=call.id)
                result = await self.executor.execute(
                    call,
                    context=context,
                    allowed_names=allowed_names,
                )
                state["messages"].append(result)
                _debug_event(state, "tool_result", step=step, tool_name=call.name, tool_call_id=call.id)

        message = f"Agent 已达到最大步骤限制（{self.max_steps}），请缩小问题范围后重试。"
        state["messages"].append(Message(role="assistant", content=message))
        state["error"] = "agent_loop_limit"
        _debug_event(state, "loop_limit", step=self.max_steps)
        return _finalize(state)


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
