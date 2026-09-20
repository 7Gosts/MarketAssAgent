from typing import Any, Optional, TypedDict

from .message_protocol import Message


class AgentState(TypedDict):
    """原生 Agent Loop 状态。"""
    
    messages: list[Message]
    
    # 会话基础信息
    session_id: str
    request_id: str
    metadata: Optional[dict]
    error: Optional[str]
    allowed_tools: Optional[list[str]]
    event_journal: Any
    turn_user_event_id: str
    prompt_version: str
    checkpoint_event_id: Optional[str]
    evidence_index: dict[str, str]
    retrieval_receipts: list[dict[str, Any]]
