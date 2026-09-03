from __future__ import annotations

from core.agent_loop import (
    _count_duplicates,
    _extract_tool_signatures,
    _tool_call_warn_threshold,
    _tool_signature,
    select_tool_names,
)
from core.message_protocol import Message, ToolCall


def test_tool_signature_is_stable_for_same_args_order() -> None:
    a = _tool_signature(ToolCall("a", "analyze_market", {"symbol": "ETHUSDT", "interval": "1h"}))
    b = _tool_signature(ToolCall("b", "analyze_market", {"interval": "1h", "symbol": "ETHUSDT"}))
    assert a == b


def test_extract_tool_signatures_from_messages() -> None:
    messages = [
        Message(
            role="assistant",
            tool_calls=(
                ToolCall("tc_1", "get_last_snapshot", {}),
                ToolCall("tc_2", "analyze_market", {"symbol": "ETHUSDT"}),
            ),
        ),
        Message(role="assistant", content="no tools"),
    ]
    signatures = _extract_tool_signatures(messages)
    assert len(signatures) == 2
    assert any(sig.startswith("get_last_snapshot:") for sig in signatures)
    assert any(sig.startswith("analyze_market:") for sig in signatures)


def test_count_duplicates_only_counts_repeated_items() -> None:
    assert _count_duplicates(["a", "b", "a", "c", "b", "d"]) == 2


def test_tool_call_warn_threshold_from_env(monkeypatch) -> None:
    monkeypatch.setenv("MARKETASSAGENT_TOOL_CALL_WARN_THRESHOLD", "8")
    assert _tool_call_warn_threshold(default=6) == 8

    monkeypatch.setenv("MARKETASSAGENT_TOOL_CALL_WARN_THRESHOLD", "0")
    assert _tool_call_warn_threshold(default=6) == 6

    monkeypatch.setenv("MARKETASSAGENT_TOOL_CALL_WARN_THRESHOLD", "bad")
    assert _tool_call_warn_threshold(default=6) == 6


def test_tool_selection_filters_unknown_names_and_keeps_empty_compatibility() -> None:
    all_names = {"analyze_market", "get_journal_status"}
    assert select_tool_names([], all_names) == all_names
    assert select_tool_names(["get_journal_status", "unknown"], all_names) == {"get_journal_status"}
