from __future__ import annotations

import json
import os
import time
from typing import Any, Callable
from pathlib import Path
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langchain_core.messages import AIMessage
from .state import AgentState
from .prompt import get_prompt
from .supervisor import supervisor_node
from tools.registry import get_all_tools
from utils.logging_utils import get_logger
from utils.runtime_paths import get_debug_dir


logger = get_logger(__name__)


def make_call_model(llm: Any) -> Callable[[AgentState], dict[str, Any]]:
    """Factory that returns a call_model bound to a specific LLM instance with tool calling."""
    tools = get_all_tools()
    tools_by_name = {getattr(t, "name", ""): t for t in tools}
    prompt = get_prompt()

    def call_model(state: AgentState) -> dict[str, Any]:
        """思考节点：让 LLM 决定下一步动作（支持真正的 Tool Calling）"""
        messages = state["messages"]
        session_id = str(state.get("session_id") or "default")
        requested = state.get("allowed_tools") or []
        allowed = [t for t in requested if t in tools_by_name]
        active_tools = [tools_by_name[name] for name in allowed] if allowed else tools
        llm_with_tools = llm.bind_tools(active_tools) if active_tools else llm
        chain = prompt | llm_with_tools
        logger.info(
            "[Graph] reason start session_id=%s message_count=%s active_tools=%s last_user_preview=%r",
            session_id,
            len(messages or []),
            len(active_tools),
            _preview_message(_last_human_message(messages)),
        )
        _dump_agent_loop_debug_event(
            session_id=session_id,
            event_type="reason_start",
            payload={
                "message_count": len(messages or []),
                "active_tools": len(active_tools),
                "last_user_preview": _preview_message(_last_human_message(messages)),
            },
        )
        response = chain.invoke({"messages": messages})
        usage = _extract_usage(response)
        if usage:
            logger.info(
                "[Graph] token usage session_id=%s prompt=%s completion=%s total=%s reasoning=%s cached_prompt=%s",
                session_id,
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                usage.get("total_tokens"),
                usage.get("reasoning_tokens"),
                usage.get("cached_prompt_tokens"),
            )
            _dump_token_usage_debug(session_id=session_id, usage=usage)

        # 强约束：即使模型返回了越权工具调用，也在图层过滤。
        allowed_names = {getattr(t, "name", "") for t in active_tools}
        raw_tool_calls = list(getattr(response, "tool_calls", None) or [])
        filtered_tool_calls = [
            tc for tc in raw_tool_calls
            if str(tc.get("name", "")) in allowed_names
        ]
        history_signatures = _extract_tool_signatures_from_messages(messages)
        current_signatures = [_tool_signature(tc) for tc in filtered_tool_calls]
        duplicate_in_batch = _count_duplicates(current_signatures)
        duplicate_from_history = sum(1 for sig in current_signatures if sig in history_signatures)
        tool_call_warn_threshold = _get_tool_call_warn_threshold(default=6)

        # 真正的 Tool Calling 判断
        has_tool_calls = bool(filtered_tool_calls)
        if filtered_tool_calls:
            tool_names: list[str] = []
            for tc in filtered_tool_calls:
                logger.info(
                    "[Graph] tool call session_id=%s name=%s args=%s",
                    session_id,
                    str(tc.get("name", "")).strip() or "unknown_tool",
                    _preview_tool_args(tc.get("args")),
                )
                tool_name = str(tc.get("name", "")).strip() or "unknown_tool"
                tool_names.append(tool_name)
                _dump_agent_loop_debug_event(
                    session_id=session_id,
                    event_type="tool_call",
                    payload={
                        "tool_name": tool_name,
                        "args_preview": _preview_tool_args(tc.get("args")),
                        "next_focus": f"等待 {tool_name} 返回后继续判断是否已有足够证据。",
                    },
                )
            if len(filtered_tool_calls) > tool_call_warn_threshold:
                logger.warning(
                    "[Graph] tool call count high session_id=%s count=%s threshold=%s names=%s",
                    session_id,
                    len(filtered_tool_calls),
                    tool_call_warn_threshold,
                    tool_names,
                )
            if duplicate_in_batch > 0 or duplicate_from_history > 0:
                logger.warning(
                    "[Graph] duplicate tool call detected session_id=%s duplicate_in_batch=%s duplicate_from_history=%s",
                    session_id,
                    duplicate_in_batch,
                    duplicate_from_history,
                )
            _dump_agent_loop_debug_event(
                session_id=session_id,
                event_type="reason_continue",
                payload={
                    "tool_call_count": len(filtered_tool_calls),
                    "tool_call_names": tool_names,
                    "duplicate_tool_call_count": duplicate_in_batch + duplicate_from_history,
                    "next_focus": "等待工具返回后，基于证据判断是否继续补证或直接回答。",
                },
            )
        else:
            logger.info(
                "[Graph] no tool call session_id=%s response_preview=%r",
                session_id,
                _preview_message(getattr(response, "content", "")),
            )
            _dump_agent_loop_debug_event(
                session_id=session_id,
                event_type="final_answer_ready",
                payload={
                    "response_preview": _preview_message(getattr(response, "content", "")),
                    "next_focus": "当前证据已足够，准备输出最终回答。",
                },
            )

        # 确保返回的是 AIMessage
        if not isinstance(response, AIMessage):
            response = AIMessage(
                content=getattr(response, "content", str(response)),
                tool_calls=filtered_tool_calls
            )
        else:
            response = AIMessage(content=response.content, tool_calls=filtered_tool_calls)

        return {
            "messages": [response],
            "next": "continue" if has_tool_calls else "end"
        }

    return call_model


def _last_human_message(messages: list[Any]) -> str:
    for msg in reversed(messages or []):
        msg_type = getattr(msg, "type", None)
        if msg_type == "human":
            return getattr(msg, "content", "") or ""
        if isinstance(msg, dict) and str(msg.get("role") or "").strip() == "user":
            return str(msg.get("content") or msg.get("text") or "")
    return ""


def _preview_message(value: Any, max_len: int = 220) -> str:
    raw = _coerce_text(value)
    raw = " ".join(raw.split())
    if not raw:
        return ""
    if os.getenv("MARKETASSAGENT_LOG_FULL_CONTEXT", "0").strip().lower() in {"1", "true", "yes", "on"}:
        return raw
    if len(raw) <= max_len:
        return raw
    return f"{raw[:max_len]}..."


def _preview_tool_args(args: Any, max_len: int = 260) -> str:
    try:
        raw = json.dumps(args, ensure_ascii=False, sort_keys=True)
    except Exception:
        raw = _coerce_text(args)
    if os.getenv("MARKETASSAGENT_LOG_FULL_TOOL_ARGS", "0").strip().lower() in {"1", "true", "yes", "on"}:
        return raw
    if len(raw) <= max_len:
        return raw
    return f"{raw[:max_len]}..."


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def _extract_usage(response: Any) -> dict[str, int]:
    usage: dict[str, int] = {}

    # LangChain 常见字段：usage_metadata
    usage_meta = getattr(response, "usage_metadata", None)
    if isinstance(usage_meta, dict):
        usage["prompt_tokens"] = int(usage_meta.get("input_tokens") or 0)
        usage["completion_tokens"] = int(usage_meta.get("output_tokens") or 0)
        usage["total_tokens"] = int(usage_meta.get("total_tokens") or 0)
        output_details = usage_meta.get("output_token_details")
        if isinstance(output_details, dict):
            usage["reasoning_tokens"] = int(output_details.get("reasoning") or 0)
        input_details = usage_meta.get("input_token_details")
        if isinstance(input_details, dict):
            usage["cached_prompt_tokens"] = int(input_details.get("cache_read") or 0)

    # OpenAI-compatible 常见字段：response_metadata.token_usage
    resp_meta = getattr(response, "response_metadata", None)
    if isinstance(resp_meta, dict):
        token_usage = resp_meta.get("token_usage")
        if isinstance(token_usage, dict):
            usage.setdefault("prompt_tokens", int(token_usage.get("prompt_tokens") or 0))
            usage.setdefault("completion_tokens", int(token_usage.get("completion_tokens") or 0))
            usage.setdefault("total_tokens", int(token_usage.get("total_tokens") or 0))

            completion_details = token_usage.get("completion_tokens_details")
            if isinstance(completion_details, dict):
                usage.setdefault("reasoning_tokens", int(completion_details.get("reasoning_tokens") or 0))

            prompt_details = token_usage.get("prompt_tokens_details")
            if isinstance(prompt_details, dict):
                usage.setdefault("cached_prompt_tokens", int(prompt_details.get("cached_tokens") or 0))

    has_positive = any(int(v or 0) > 0 for v in usage.values())
    if not has_positive:
        return {}
    usage.setdefault("reasoning_tokens", 0)
    usage.setdefault("cached_prompt_tokens", 0)
    return usage


def _tool_signature(tool_call: dict[str, Any]) -> str:
    name = str(tool_call.get("name", "")).strip().lower()
    args = tool_call.get("args")
    try:
        args_raw = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        args_raw = _coerce_text(args)
    return f"{name}:{args_raw}"


def _extract_tool_signatures_from_messages(messages: list[Any]) -> set[str]:
    signatures: set[str] = set()
    for msg in messages or []:
        tool_calls = getattr(msg, "tool_calls", None)
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            if isinstance(tc, dict):
                signatures.add(_tool_signature(tc))
    return signatures


def _count_duplicates(values: list[str]) -> int:
    seen: set[str] = set()
    duplicates = 0
    for value in values:
        if value in seen:
            duplicates += 1
            continue
        seen.add(value)
    return duplicates


def _get_tool_call_warn_threshold(default: int = 6) -> int:
    raw = os.getenv("MARKETASSAGENT_TOOL_CALL_WARN_THRESHOLD", "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 1 else default


def _dump_token_usage_debug(*, session_id: str, usage: dict[str, int]) -> None:
    if os.getenv("MARKETASSAGENT_DEBUG_TOKEN_USAGE", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    try:
        debug_dir: Path = get_debug_dir()
        debug_dir.mkdir(parents=True, exist_ok=True)
        target = debug_dir / "llm_token_usage.jsonl"
        record = {
            "ts": time.time(),
            "session_id": session_id,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
            "cached_prompt_tokens": int(usage.get("cached_prompt_tokens") or 0),
        }
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("token usage debug dump failed: %s", e)


def _dump_agent_loop_debug_event(*, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
    if os.getenv("MARKETASSAGENT_DEBUG_AGENT_LOOP", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    try:
        debug_dir: Path = get_debug_dir()
        debug_dir.mkdir(parents=True, exist_ok=True)
        target = debug_dir / "agent_loop_trace.jsonl"
        record = {
            "ts": time.time(),
            "session_id": session_id,
            "event_type": event_type,
            "payload": payload,
        }
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("agent loop debug dump failed: %s", e)


def make_logged_tool_node(tool_node: ToolNode) -> Callable[..., dict[str, Any]]:
    def run_tools(
        state: AgentState,
        config: RunnableConfig | None = None,
        runtime: Runtime | None = None,
    ) -> dict[str, Any]:
        session_id = str(state.get("session_id") or "default")
        result = tool_node.invoke(state, config=config, runtime=runtime)
        messages = result.get("messages") if isinstance(result, dict) else []
        for msg in messages or []:
            msg_type = getattr(msg, "type", None)
            if msg_type != "tool":
                continue
            tool_name = str(getattr(msg, "name", "") or "").strip() or "unknown_tool"
            content_preview = _preview_message(getattr(msg, "content", ""))
            logger.info(
                "[Graph] tool result session_id=%s name=%s content=%r",
                session_id,
                tool_name,
                content_preview,
            )
            _dump_agent_loop_debug_event(
                session_id=session_id,
                event_type="tool_result",
                payload={
                    "tool_name": tool_name,
                    "result_preview": content_preview,
                    "next_focus": f"已拿到 {tool_name} 返回，下一步结合结果判断是否继续取证。",
                },
            )
        return result if isinstance(result, dict) else {"messages": []}

    return run_tools


def build_graph(
    llm: Any,
    *,
    checkpointer: Any | None = None,
    store: Any | None = None,
):
    """构建完整的 LangGraph 工作流，支持真正的 Tool Calling"""
    tools = get_all_tools()
    tool_node = ToolNode(tools)
    logged_tool_node = make_logged_tool_node(tool_node)
    call_model = make_call_model(llm)

    workflow = StateGraph(AgentState)

    workflow.add_node("reason", call_model)
    workflow.add_node("act", logged_tool_node)
    workflow.add_node("supervisor", supervisor_node)

    workflow.set_entry_point("reason")

    workflow.add_conditional_edges(
        "reason",
        lambda state: state.get("next", "end"),
        {
            "continue": "act",
            "end": "supervisor"
        }
    )

    workflow.add_edge("act", "reason")
    workflow.add_edge("supervisor", END)

    return workflow.compile(checkpointer=checkpointer, store=store)
