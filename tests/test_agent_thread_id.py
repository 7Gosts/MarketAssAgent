from __future__ import annotations

import asyncio

from core.agent import MarketReActAgent
from core.llm_client import LLMResponse
from core.message_protocol import Message


class DummyLLM:
    async def complete(self, *, messages, tools):
        return LLMResponse(message=Message(role="assistant", content="ok"))


def test_agent_invoke_preserves_session_and_request_id() -> None:
    agent = MarketReActAgent(llm=DummyLLM())

    result = asyncio.run(agent.invoke("hello", session_id="feishu_abc", request_id="req_123"))

    assert result["session_id"] == "feishu_abc"
    assert result["request_id"] == "req_123"
