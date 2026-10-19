from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from core.llm_client import LLMClientError, OpenAICompatibleLLMClient
from core.message_protocol import Message, tool_message


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
                model="gpt-6.1-sol",
                base_url="https://example.test/v1",
                api_key="fake-key",
                temperature=0.1,
                http_client=http_client,
                max_retries=0,
            )
            return await client.complete(
                messages=[Message(role="user", content="status")],
                tools=[{"type": "function", "function": {"name": "get_journal_status"}}],
                max_tokens=1024,
            )

    response = asyncio.run(run())
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer fake-key"
    assert captured["payload"]["tool_choice"] == "auto"
    assert captured["payload"]["model"] == "gpt-6.1-sol"
    assert "max_tokens" not in captured["payload"]
    assert "max_completion_tokens" not in captured["payload"]
    assert response.message.tool_calls[0].arguments == {"symbol": "ETHUSDT"}
    assert response.usage.total_tokens == 14
    assert response.usage.reasoning_tokens == 2
    assert response.usage.cached_prompt_tokens == 3


def test_responses_client_completes_tool_roundtrip() -> None:
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.test/v1/responses"
        captured.append(json.loads(request.content))
        output = [{"type": "function_call", "call_id": "call_1", "name": "echo", "arguments": '{"text":"OK"}'}]
        if len(captured) == 2:
            output = [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]
        return httpx.Response(200, json={
            "id": "resp_1", "status": "completed", "output": output,
            "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14,
                      "output_tokens_details": {"reasoning_tokens": 2}},
        })

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenAICompatibleLLMClient(
                model="gpt-6.1-sol", base_url="https://example.test/v1",
                api_format="responses", http_client=http_client,
            )
            messages = [Message(role="user", content="echo OK")]
            tools = [{"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}}]
            first = await client.complete(messages=messages, tools=tools, max_tokens=1024)
            call = first.message.tool_calls[0]
            assert first.finish_reason == "tool_calls"
            messages.extend([first.message, tool_message(tool_call_id=call.id, name=call.name, result=call.arguments)])
            return await client.complete(messages=messages, tools=tools, max_tokens=1024)

    result = asyncio.run(run())
    assert captured[0]["tools"][0]["name"] == "echo"
    assert captured[1]["input"][1]["arguments"] == '{"text":"OK"}'
    assert captured[1]["input"][2] == {"type": "function_call_output", "call_id": "call_1", "output": '{"text":"OK"}'}
    for payload in captured:
        assert payload["store"] is False
        assert not {"max_tokens", "max_completion_tokens", "max_output_tokens"}.intersection(payload)
    assert result.message.content == "OK"
    assert result.finish_reason == "stop"
    assert result.usage.reasoning_tokens == 2
    assert result.raw["id"] == "resp_1"


def test_responses_client_rejects_incomplete_output() -> None:
    client = OpenAICompatibleLLMClient(model="test", api_format="responses", base_url="https://example.test/v1")
    with pytest.raises(LLMClientError, match="not completed"):
        client._parse_provider_response({"status": "incomplete", "output": [{"type": "message", "content": [{"type": "output_text", "text": "partial"}]}]})


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


@pytest.mark.parametrize("error, expected", [
    ({"code": "context_length_exceeded", "message": "too long"}, True),
    ({"message": "maximum context length exceeded"}, True),
    ({"message": "max_tokens is out of range"}, False),
])
def test_llm_client_distinguishes_context_overflow(error, expected):
    with pytest.raises(LLMClientError) as exc_info:
        OpenAICompatibleLLMClient._raise_for_status(httpx.Response(400, json={"error": error}))
    assert exc_info.value.context_overflow is expected
    assert exc_info.value.retryable is False
