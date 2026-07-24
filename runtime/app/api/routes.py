"""API 路由定义"""

from fastapi import APIRouter, BackgroundTasks, Request, HTTPException
from pydantic import BaseModel, Field

from application.presenters import WebPresenter

router = APIRouter()


class AgentRunRequest(BaseModel):
    text: str = Field(..., description="用户输入")
    session_id: str = Field(default="default", description="会话ID")


class AgentRunResponse(BaseModel):
    envelope: dict


@router.post("/agent/run", response_model=AgentRunResponse)
async def run_agent(req: AgentRunRequest, request: Request, background_tasks: BackgroundTasks):
    """统一 Agent 入口（通过 ConversationService 编排记忆）"""
    services = getattr(request.app.state, "services", None)
    if services is None or not hasattr(services, "conversation_service"):
        raise HTTPException(status_code=500, detail="ConversationService 未初始化")

    conv_service = services.conversation_service
    envelope = await conv_service.run(
        text=req.text,
        session_id=req.session_id,
        history_limit=8,
    )

    payload = WebPresenter().render(envelope=envelope)
    background_tasks.add_task(conv_service.persist_delivered_turn_summary, envelope)
    return AgentRunResponse(**payload)
