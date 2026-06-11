"""API 路由定义"""

from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Any

from memory.session_manager import MarketSessionManager
from services.conversation_service import ConversationService

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
    agent = get_agent(request)

    # 初始化服务（生产环境建议从 app.state 注入）
    session_mgr = MarketSessionManager(repo_root=Path(__file__).resolve().parents[2])
    conv_service = ConversationService(agent=agent, session_manager=session_mgr)

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
