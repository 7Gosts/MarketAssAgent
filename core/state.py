"""MarketAssAgent — LangGraph ReAct Agent 状态定义。"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages

MAX_ITERATIONS = 6


class MarketAgentState(TypedDict, total=False):
    """Agent 主状态，贯穿整个 ReAct 循环。"""

    # ── LangGraph 消息协议 ──
    messages: Annotated[list, add_messages]

    # ── 当前分析上下文 ──
    current_symbol: str            # e.g. "BTC_USDT"
    current_interval: str          # e.g. "4h", "1d"
    current_provider: str          # e.g. "gateio", "tickflow", "goldapi"

    # ── 结构化快照 ──
    last_snapshot: dict[str, Any]  # 轻量摘要（趋势/Fib/123/entry/stop）
    output_refs: dict[str, str]    # 产物路径

    # ── 会话追踪 ──
    session_id: str
    channel: str                   # "feishu" | "http" | "cli"
    iteration_count: int           # ReAct 循环计数，防无限

    # ── 输出 ──
    final_reply: str
    has_disclaimer: bool