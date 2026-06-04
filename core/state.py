from typing import TypedDict, Annotated, Optional
from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage


class AnalysisSnapshot(TypedDict):
    """行情分析快照 - 解决追问时上下文丢失问题"""
    symbol: str
    interval: str
    trend: str                    # 偏多 / 偏空 / 震荡
    key_levels: dict              # 支撑位、阻力位等
    structure: str                # 均线排列、量价关系、123法则、Fib 等
    confidence: int               # 0-100
    timestamp: str
    raw_insights: Optional[str]   # 原始分析语料


class AgentState(TypedDict):
    """LangGraph 主状态"""
    
    # 对话历史（LangGraph 官方推荐写法）
    messages: Annotated[list[BaseMessage], add_messages]
    
    # 会话基础信息
    session_id: str
    current_symbol: Optional[str]
    current_interval: Optional[str]
    
    # 核心业务状态
    last_snapshot: Optional[AnalysisSnapshot]
    analysis_result: Optional[dict]
    risk_assessment: Optional[dict]
    recommendation: Optional[dict]
    
    # 流程控制
    intent: Optional[str]
    next: Optional[str]                    # LangGraph 控制下一步节点
    
    # 交易记录（Journal 保存集成）
    journal_id: Optional[int]
    
    # 辅助字段
    metadata: Optional[dict]
    error: Optional[str]
