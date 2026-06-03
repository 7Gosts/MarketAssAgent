"""MarketAssAgent — LangGraph 节点函数（统一入口）。

所有图节点集中在此模块：
- restore_session_node: 从 session 恢复上一轮分析上下文
- init_context_node: 注入系统 prompt + 上下文
- reason_node: LLM 推理
- observe_node: 从工具结果提取 snapshot
- should_continue: 条件边
- supervisor_node: 输出守卫
- persist_snapshot_node: 将 snapshot 持久化到 session

core/agent.py 保留为 re-export 入口，保持向后兼容。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from core.prompt import SYSTEM_PROMPT, build_context_message, build_force_final_message
from core.state import MAX_ITERATIONS, MarketAgentState
from core.supervisor import supervisor_node as _supervisor_node
from memory.snapshot import extract_snapshot, snapshot_to_context_str

logger = logging.getLogger(__name__)


# ── Session 恢复节点 ──────────────────────────────────────────────


def restore_session_node(
    state: MarketAgentState,
    *,
    session_mgr: Any | None = None,
) -> dict[str, Any]:
    """从 session 恢复上一轮分析上下文。

    当 session_mgr 为 None 时，此节点为 no-op。
    恢复的字段：current_symbol, current_interval, current_provider, last_snapshot, output_refs。
    """
    if session_mgr is None:
        return {}

    session_id = state.get("session_id", "")
    if not session_id:
        return {}

    try:
        session_state = session_mgr.load_session(session_id)
    except Exception:
        logger.warning("restore_session_node: 加载 session 失败", exc_info=True)
        return {}

    updates: dict[str, Any] = {}

    # 只在 state 中对应字段为空时恢复（不覆盖当前轮已设置的值）
    if not state.get("current_symbol") and session_state.last_symbols:
        updates["current_symbol"] = session_state.last_symbols[0]
    if not state.get("current_interval") and session_state.last_interval:
        updates["current_interval"] = session_state.last_interval
    if not state.get("current_provider") and session_state.last_provider:
        updates["current_provider"] = session_state.last_provider
    if not state.get("last_snapshot") and session_state.last_facts_bundle:
        updates["last_snapshot"] = session_state.last_facts_bundle
    if not state.get("output_refs") and session_state.last_output_refs:
        updates["output_refs"] = session_state.last_output_refs

    return updates


# ── 上下文注入节点 ──────────────────────────────────────────────


def init_context_node(state: MarketAgentState) -> dict[str, Any]:
    """初始化上下文：注入系统 prompt + 上下文消息 + 强制注入上一轮 snapshot。"""
    messages: list = []

    # 系统 prompt
    messages.append(SystemMessage(content=SYSTEM_PROMPT))

    # 上下文注入（current_symbol, current_interval, last_snapshot 等）
    ctx_msg = build_context_message(state)
    if ctx_msg.content:
        messages.append(ctx_msg)

    # 强制注入上一轮分析摘要（确保追问时 LLM 一定能"看到"上一轮结果）
    snapshot = state.get("last_snapshot")
    if snapshot and isinstance(snapshot, dict) and snapshot.get("symbol"):
        snapshot_str = snapshot_to_context_str(snapshot)
        if snapshot_str:
            messages.append(SystemMessage(
                content=f"[系统注入] 上一轮分析上下文（必须基于此回答追问）：\n{snapshot_str}"
            ))

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


# ── LLM 推理节点 ────────────────────────────────────────────────


def reason_node(state: MarketAgentState, *, llm: Any) -> dict[str, Any]:
    """LLM 推理节点：决定调工具还是直接回答。"""
    messages = state.get("messages", [])
    response = llm.invoke(messages)
    return {"messages": [response]}


# ── 观察节点 ────────────────────────────────────────────────────


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


# ── 条件边 ──────────────────────────────────────────────────────


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


# ── 输出守卫节点（代理到 core.supervisor）──────────────────────────


def supervisor_node(state: MarketAgentState) -> dict[str, Any]:
    """输出守卫节点：禁止口径检查 + 条件语气 + 免责声明。"""
    return _supervisor_node(state)


# ── Session 持久化节点 ──────────────────────────────────────────


def persist_snapshot_node(
    state: MarketAgentState,
    *,
    session_mgr: Any | None = None,
) -> dict[str, Any]:
    """将 last_snapshot/output_refs 持久化到 session。

    当 session_mgr 为 None 时，此节点为 no-op。
    """
    if session_mgr is None:
        return {}

    session_id = state.get("session_id", "")
    if not session_id:
        return {}

    snapshot = state.get("last_snapshot")
    output_refs = state.get("output_refs")

    # 只在有 snapshot 时才持久化
    if not snapshot or not isinstance(snapshot, dict):
        return {}

    try:
        session_mgr.save_snapshot(session_id, snapshot, output_refs)
    except Exception:
        logger.warning("persist_snapshot_node: 持久化 snapshot 失败", exc_info=True)

    # 保存回复
    reply = state.get("final_reply", "")
    if reply:
        try:
            session_mgr.save_reply(session_id, reply)
        except Exception:
            logger.warning("persist_snapshot_node: 保存回复失败", exc_info=True)

    return {}