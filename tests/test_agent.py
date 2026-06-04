import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

# 确保可以直接运行测试
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent import MarketReActAgent


def make_dummy_llm():
    """创建一个简单的 dummy LLM 用于测试 graph 流程"""
    llm = MagicMock()
    llm.invoke.return_value.content = "行情分析完成。当前趋势偏多。"
    return llm


async def test_agent_invoke_with_dummy_llm():
    """使用 dummy LLM 测试基本调用流程（无需真实 API Key）"""
    dummy_llm = make_dummy_llm()
    agent = MarketReActAgent(llm=dummy_llm)
    
    result = await agent.invoke("BTC_USDT 4h 行情分析", session_id="test_dummy")
    
    assert "messages" in result
    assert result.get("session_id") == "test_dummy"
    print("✅ Dummy LLM test passed")


if __name__ == "__main__":
    asyncio.run(test_agent_invoke_with_dummy_llm())
