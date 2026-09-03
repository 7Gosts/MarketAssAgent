from typing import Optional, TypedDict

from .message_protocol import Message


class AnalysisSnapshot(TypedDict):
    """行情分析快照 - 解决追问时上下文丢失问题"""
    symbol: str
    interval: str
    trend: str                    # 偏多 / 偏空 / 震荡
    key_levels: dict              # 支撑位、阻力位等
    structure: str                # 均线排列、量价关系、123法则、Fib 等
    structure_signals: dict       # ma_alignment / trend_ma_match / trend_clarity / key_levels
    timestamp: str
    raw_insights: Optional[str]   # 原始分析语料


class AgentState(TypedDict):
    """原生 Agent Loop 状态。"""
    
    messages: list[Message]
    
    # 会话基础信息
    session_id: str
    request_id: str
    current_symbol: Optional[str]
    current_interval: Optional[str]
    
    # 核心业务状态
    last_snapshot: Optional[AnalysisSnapshot]
    analysis_result: Optional[dict]
    risk_assessment: Optional[dict]
    recommendation: Optional[dict]
    
    # 流程控制
    intent: Optional[str]
    next: Optional[str]                    # 兼容旧响应的终止状态
    
    # 交易记录（兼容旧字段；正式写入走显式交易工具）
    journal_id: Optional[int]
    
    # 辅助字段
    metadata: Optional[dict]
    error: Optional[str]
    allowed_tools: Optional[list[str]]
