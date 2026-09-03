from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from core.llm_client import LLMClientError, OpenAICompatibleLLMClient
from core.message_protocol import Message


def test_llm_client_sends_openai_payload_and_parses_tool_calls() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "get_journal_status",
                                "arguments": '{"symbol":"ETHUSDT"}',
                            },
                        }],
                    },
                }],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                    "completion_tokens_details": {"reasoning_tokens": 2},
                    "prompt_tokens_details": {"cached_tokens": 3},
                },
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenAICompatibleLLMClient(
                model="deepseek-chat",
                base_url="https://example.test/v1",
                api_key="fake-key",
                temperature=0.1,
                http_client=http_client,
                max_retries=0,
            )
            return await client.complete(
                messages=[Message(role="user", content="status")],
                tools=[{"type": "function", "function": {"name": "get_journal_status"}}],
            )

    response = asyncio.run(run())
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer fake-key"
    assert captured["payload"]["tool_choice"] == "auto"
    assert response.message.tool_calls[0].arguments == {"symbol": "ETHUSDT"}
    assert response.usage.total_tokens == 14
    assert response.usage.reasoning_tokens == 2
    assert response.usage.cached_prompt_tokens == 3


def test_llm_client_rejects_invalid_provider_payload() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenAICompatibleLLMClient(
                model="test",
                base_url="https://example.test/v1/chat/completions",
                api_key="fake",
                http_client=http_client,
                max_retries=0,
            )
            await client.complete(messages=[Message(role="user", content="hello")], tools=[])

    with pytest.raises(LLMClientError, match="choices"):
        asyncio.run(run())


def test_llm_client_marks_retryable_http_errors() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "busy"}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenAICompatibleLLMClient(
                model="test",
                base_url="https://example.test/v1",
                api_key="fake",
                http_client=http_client,
                max_retries=0,
            )
            await client.complete(messages=[Message(role="user", content="hello")], tools=[])

    with pytest.raises(LLMClientError) as exc_info:
        asyncio.run(run())
    assert exc_info.value.status_code == 503
    assert exc_info.value.retryable is True
