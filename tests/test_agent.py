import asyncio
import pytest
from core.agent import MarketReActAgent


@pytest.mark.asyncio
async def test_agent_invoke_basic():
    """基本调用测试（使用默认占位 LLM）"""
    agent = MarketReActAgent()
    result = await agent.invoke("BTC_USDT 4h 行情分析", session_id="test_001")
    assert "messages" in result
    assert result.get("session_id") == "test_001"
