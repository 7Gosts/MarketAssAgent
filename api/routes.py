"""API 路由定义"""

from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Any

from memory.session_manager import MarketSessionManager

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
    """统一 Agent 入口（已接入 MarketSessionManager 记忆层）"""
    agent = get_agent(request)

    # 初始化统一会话管理器
    session_mgr = MarketSessionManager(repo_root=Path(__file__).resolve().parents[2])

    # 1. 保存用户消息
    session_mgr.save_user_message(req.session_id, req.text)

    # 2. 读取最近历史
    history = session_mgr.get_recent_messages(req.session_id, limit=8)

    # 3. 调用 Agent（注入 history）
    result = await agent.invoke(req.text, session_id=req.session_id, history=history)

    # 4. 保存 assistant 回复
    rec = result.get("recommendation") or {}
    reply_text = rec.get("text", "") or result.get("reply", "")
    if reply_text:
        session_mgr.save_reply(req.session_id, reply_text)

    return AgentRunResponse(
        session_id=req.session_id,
        reply=reply_text or "分析完成",
        recommendation=rec
    )
