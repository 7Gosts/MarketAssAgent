from __future__ import annotations

import json

from core.message_protocol import Message, ToolCall, build_messages, tool_message
from core.tool_protocol import ToolContext, ToolSpec


def test_message_serializes_openai_tool_call_and_result() -> None:
    assistant = Message(
        role="assistant",
        tool_calls=(
            ToolCall(id="call_1", name="get_journal_status", arguments={"symbol": "ETHUSDT"}),
        ),
    )

    payload = assistant.to_openai_dict()
    assert payload["tool_calls"][0]["function"]["name"] == "get_journal_status"
    assert json.loads(payload["tool_calls"][0]["function"]["arguments"]) == {"symbol": "ETHUSDT"}

    result = tool_message(
        tool_call_id="call_1",
        name="get_journal_status",
        result={"status": "success"},
    )
    assert result.to_openai_dict() == {
        "role": "tool",
        "content": '{"status":"success"}',
        "tool_call_id": "call_1",
        "name": "get_journal_status",
    }


def test_build_messages_preserves_history_and_adds_system_and_user() -> None:
    messages = build_messages(
        system_prompt="system",
        history=[
            {"role": "user", "text": "first"},
            {"role": "assistant", "content": "second"},
        ],
        user_input="third",
    )

    assert [(message.role, message.content) for message in messages] == [
        ("system", "system"),
        ("user", "first"),
        ("assistant", "second"),
        ("user", "third"),
    ]


def test_tool_schema_never_exposes_runtime_context() -> None:
    spec = ToolSpec(
        name="cancel_paper_order",
        description="cancel",
        parameters={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        execute=lambda **_: None,
        side_effect="write",
        requires_context=True,
    )

    schema = spec.openai_schema()
    properties = schema["function"]["parameters"]["properties"]
    assert "session_id" not in properties
    assert "request_id" not in properties
    assert ToolContext(session_id="s1", request_id="r1").session_id == "s1"
