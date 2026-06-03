"""MarketAssAgent — Agent 节点函数（init_context, reason, observe, 条件边）。"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from core.prompt import SYSTEM_PROMPT, build_context_message, build_force_final_message
from core.state import MAX_ITERATIONS, MarketAgentState
from memory.snapshot import extract_snapshot

logger = logging.getLogger(__name__)


def init_context_node(state: MarketAgentState) -> dict[str, Any]:
    """初始化上下文：从 session 恢复状态，注入系统 prompt + 上下文消息。"""
    messages: list = []

    # 系统 prompt
    messages.append(SystemMessage(content=SYSTEM_PROMPT))

    # 上下文注入（last_snapshot, current_symbol 等）
    ctx_msg = build_context_message(state)
    if ctx_msg.content:
        messages.append(ctx_msg)

    # 如果 iteration_count 达上限，强制给最终回答
    if state.get("iteration_count", 0) >= MAX_ITERATIONS:
        messages.append(build_force_final_message())

    # 保留已有消息（用户消息 + 工具调用历史）
    existing = state.get("messages", [])
    messages.extend(existing)

    updates: dict[str, Any] = {"messages": messages}
    if not state.get("iteration_count"):
        updates["iteration_count"] = 0

    return updates


def reason_node(state: MarketAgentState, *, llm: Any) -> dict[str, Any]:
    """LLM 推理节点：决定调工具还是直接回答。"""
    messages = state.get("messages", [])
    response = llm.invoke(messages)
    return {"messages": [response]}


def observe_node(state: MarketAgentState) -> dict[str, Any]:
    """观察节点：从工具结果提取 snapshot，更新 state 字段。"""
    updates: dict[str, Any] = {
        "iteration_count": state.get("iteration_count", 0) + 1,
    }

    messages = state.get("messages", [])
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            continue

        # 尝试提取 snapshot
        content = msg.content
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue

        if isinstance(content, dict):
            snapshot = extract_snapshot(content)
            if snapshot:
                updates["last_snapshot"] = snapshot

            # 从 snapshot 更新 current_symbol / interval / provider
            if snapshot.get("symbol") and not state.get("current_symbol"):
                updates["current_symbol"] = snapshot["symbol"]
            if snapshot.get("interval") and not state.get("current_interval"):
                updates["current_interval"] = snapshot["interval"]
            if snapshot.get("provider") and not state.get("current_provider"):
                updates["current_provider"] = snapshot["provider"]

            # 提取 output_refs
            meta = content.get("meta")
            if isinstance(meta, dict):
                refs = {}
                if meta.get("ai_overview_path"):
                    refs["ai_overview_path"] = meta["ai_overview_path"]
                if meta.get("full_report_path"):
                    refs["full_report_path"] = meta["full_report_path"]
                if meta.get("session_dir"):
                    refs["session_dir"] = meta["session_dir"]
                if refs:
                    updates["output_refs"] = refs

        # 只处理最近一个 ToolMessage
        break

    return updates


def should_continue(state: MarketAgentState) -> str:
    """条件边：决定走 tools 还是 supervisor。"""
    messages = state.get("messages", [])
    if not messages:
        return "supervisor"

    last_msg = messages[-1]

    # 如果是 AIMessage 且有 tool_calls
    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        # 达到上限，强制走 supervisor
        if state.get("iteration_count", 0) >= MAX_ITERATIONS:
            logger.warning("iteration_count=%d 达上限，强制终止", state.get("iteration_count"))
            return "supervisor"
        return "tools"

    # 其他情况走 supervisor
    return "supervisor"