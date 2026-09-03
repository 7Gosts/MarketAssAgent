from __future__ import annotations

import asyncio

from core.agent import MarketReActAgent
from core.llm_client import LLMResponse
from core.message_protocol import Message


class DummyLLM:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def complete(self, *, messages, tools):
        self.requests.append({"messages": list(messages), "tools": list(tools)})
        return LLMResponse(message=Message(role="assistant", content="行情分析完成。当前趋势偏多。"))


def test_agent_invoke_with_dummy_llm() -> None:
    llm = DummyLLM()
    agent = MarketReActAgent(llm=llm)

    result = asyncio.run(agent.invoke("分析 ETH", session_id="test_session"))

    assert result["recommendation"]["text"] == "行情分析完成。当前趋势偏多。"
    assert llm.requests[0]["messages"][0].role == "system"
    assert llm.requests[0]["messages"][-1].content == "分析 ETH"
