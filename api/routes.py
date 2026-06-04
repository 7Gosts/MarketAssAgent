"""API 路由定义"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional, Any
from core.agent import MarketReActAgent

router = APIRouter()
agent = MarketReActAgent()  # 延迟初始化 LLM（需在启动时注入）


class AgentRunRequest(BaseModel):
    text: str = Field(..., description="用户输入")
    session_id: str = Field(default="default", description="会话ID")


class AgentRunResponse(BaseModel):
    session_id: str
    reply: str
    recommendation: Optional[dict[str, Any]] = None


@router.post("/agent/run", response_model=AgentRunResponse)
async def run_agent(req: AgentRunRequest):
    """统一 Agent 入口"""
    result = await agent.invoke(req.text, session_id=req.session_id)
    rec = result.get("recommendation") or {}
    return AgentRunResponse(
        session_id=req.session_id,
        reply=rec.get("text", "分析完成"),
        recommendation=rec
    )
