"""AgentState 定义（LangGraph 核心状态）。

使用 TypedDict + Annotated 实现可累加的 messages 字段，
并引入 AnalysisSnapshot 结构化类型，提升类型安全性和可扩展性。
"""

from __future__ import annotations

from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AnalysisSnapshot(TypedDict):
    """行情分析快照（结构化）"""

    symbol: str
    interval: str
    trend: str  # 偏多 / 偏空 / 震荡
    key_levels: dict[str, Any]  # 支撑位、阻力位等
    structure: str  # 123法则、均线排列等
    confidence: int  # 0-100
    timestamp: str


class AgentState(TypedDict):
    """MarketAssAgent 的核心运行时状态。

    设计原则：
    - messages 使用 add_messages reducer 自动累加对话历史
    - AnalysisSnapshot 结构化，避免过多裸 dict
    - 保留 next / error / metadata 等 LangGraph 常用控制字段
    - 保持灵活性，analysis_result / risk_assessment / recommendation 仍为 dict 缓冲
    """

    # 必须字段
    messages: Annotated[list[BaseMessage], add_messages]

    # 会话信息
    session_id: str
    current_symbol: Optional[str]
    current_interval: Optional[str]

    # 核心业务状态
    last_snapshot: Optional[AnalysisSnapshot]

    # 中间结果（保留 dict 作为缓冲，后续可进一步结构化）
    analysis_result: Optional[dict[str, Any]]
    risk_assessment: Optional[dict[str, Any]]
    recommendation: Optional[dict[str, Any]]

    # 流程控制
    intent: Optional[str]
    next: Optional[str]  # LangGraph 常用控制字段

    # 辅助字段
    metadata: Optional[dict[str, Any]]
    error: Optional[str]
