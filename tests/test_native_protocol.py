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
        effect_class="opaque_effect",
        requires_context=True,
    )

    schema = spec.openai_schema()
    properties = schema["function"]["parameters"]["properties"]
    assert "session_id" not in properties
    assert "request_id" not in properties
    assert ToolContext(session_id="s1", request_id="r1").session_id == "s1"


def test_market_tool_message_presents_known_internal_enums() -> None:
    result = tool_message(
        tool_call_id="call_market",
        name="analyze_market",
        result={
            "status": "success",
            "items": [
                {
                    "status": "success",
                    "request_key": "ETHUSDT@15m",
                    "symbol": "ETHUSDT",
                    "interval": "15m",
                    "analysis": {
                        "ma_regime": "mixed",
                        "ma_alignment": "bullish",
                        "swing_structure": "lower_highs_lower_lows",
                        "recent_candles": [{"event": "break_down"}],
                    },
                }
            ],
        },
    )

    payload = json.loads(result.content)
    analysis = payload["items"][0]["analysis"]
    assert analysis["ma_regime"] != "mixed"
    assert analysis["ma_alignment"] != "bullish"
    assert analysis["swing_structure"] != "lower_highs_lower_lows"
    assert analysis["recent_candles"][0]["event"] != "break_down"


def test_evaluate_structure_tool_message_presents_known_internal_enums() -> None:
    result = tool_message(
        tool_call_id="call_structure",
        name="evaluate_structure",
        result={
            "symbol": "ETHUSDT",
            "ma_regime": "mixed",
            "swing_structure": "higher_highs_higher_lows",
        },
    )

    payload = json.loads(result.content)
    assert payload["ma_regime"] != "mixed"
    assert payload["swing_structure"] != "higher_highs_higher_lows"


def test_journal_status_tool_message_serializes_business_event_fields() -> None:
    result = tool_message(
        tool_call_id="call_journal",
        name="get_journal_status",
        result={
            "status": "success",
            "recent_events": [
                {
                    "symbol": "SOL_USDT",
                    "event_type": "order_closed_tp",
                    "order_id": "ord_03d0c396739cfc2e6fcf73ba",
                    "event_time": "2026-10-27T18:00:00+08:00",
                    "event_price": 99.2,
                }
            ],
        },
    )

    payload = json.loads(result.content)
    event = payload["recent_events"][0]
    assert event["symbol"] == "SOL_USDT"
    assert event["order_id"] == "ord_03d0c396739cfc2e6fcf73ba"
    assert event["event_type"] == "order_closed_tp"


def test_previous_snapshot_tool_message_presents_ma_regime() -> None:
    result = tool_message(
        tool_call_id="call_previous",
        name="get_previous_analysis_snapshot",
        result={
            "status": "success",
            "snapshot": {"symbol": "ETHUSDT", "interval": "4h", "ma_regime": "bullish"},
        },
    )

    payload = json.loads(result.content)
    assert payload["snapshot"]["ma_regime"] != "bullish"
