"""API 路由定义"""

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Any

router = APIRouter()


class AgentRunRequest(BaseModel):
    text: str = Field(..., description="用户输入")
    session_id: str = Field(default="default", description="会话ID")


class AgentRunResponse(BaseModel):
    session_id: str
    reply: str
    recommendation: Optional[dict[str, Any]] = None


def get_agent(request: Request):
    """从 app.state 获取已初始化的 Agent"""
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent 未初始化")
    return agent


@router.post("/agent/run", response_model=AgentRunResponse)
async def run_agent(req: AgentRunRequest, request: Request):
    """统一 Agent 入口（通过 ConversationService 编排记忆）"""
    services = getattr(request.app.state, "services", None)
    if services is None or not hasattr(services, "conversation_service"):
        raise HTTPException(status_code=500, detail="ConversationService 未初始化")

    conv_service = services.conversation_service
    result = await conv_service.run(
        text=req.text,
        session_id=req.session_id,
        history_limit=8,
    )

    rec = result["result"].get("recommendation") or {}

    return AgentRunResponse(
        session_id=req.session_id,
        reply=result["reply_text"] or "分析完成",
        recommendation=rec
    )
