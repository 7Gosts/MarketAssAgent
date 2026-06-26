from __future__ import annotations

from langchain_core.messages import AIMessage

from core.graph import (
    _count_duplicates,
    _extract_tool_signatures_from_messages,
    _get_tool_call_warn_threshold,
    make_logged_tool_node,
    _tool_signature,
)


def test_tool_signature_is_stable_for_same_args_order():
    a = _tool_signature({"name": "analyze_market", "args": {"symbol": "ETHUSDT", "interval": "1h"}})
    b = _tool_signature({"name": "analyze_market", "args": {"interval": "1h", "symbol": "ETHUSDT"}})
    assert a == b


def test_extract_tool_signatures_from_messages():
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "get_last_snapshot", "args": {"session_id": "s1"}, "id": "tc_1", "type": "tool_call"},
                {"name": "analyze_market", "args": {"symbol": "ETHUSDT"}, "id": "tc_2", "type": "tool_call"},
            ],
        ),
        AIMessage(content="no tools", tool_calls=[]),
    ]
    signatures = _extract_tool_signatures_from_messages(messages)
    assert len(signatures) == 2
    assert any(sig.startswith("get_last_snapshot:") for sig in signatures)
    assert any(sig.startswith("analyze_market:") for sig in signatures)


def test_count_duplicates_only_counts_repeated_items():
    values = ["a", "b", "a", "c", "b", "d"]
    assert _count_duplicates(values) == 2


def test_get_tool_call_warn_threshold_from_env(monkeypatch):
    monkeypatch.setenv("MARKETASSAGENT_TOOL_CALL_WARN_THRESHOLD", "8")
    assert _get_tool_call_warn_threshold(default=6) == 8

    monkeypatch.setenv("MARKETASSAGENT_TOOL_CALL_WARN_THRESHOLD", "0")
    assert _get_tool_call_warn_threshold(default=6) == 6

    monkeypatch.setenv("MARKETASSAGENT_TOOL_CALL_WARN_THRESHOLD", "bad")
    assert _get_tool_call_warn_threshold(default=6) == 6


def test_make_logged_tool_node_forwards_config_and_runtime():
    captured: dict[str, object] = {}

    class _FakeToolNode:
        def invoke(self, state, config=None, runtime=None):
            captured["state"] = state
            captured["config"] = config
            captured["runtime"] = runtime
            return {"messages": []}

    run_tools = make_logged_tool_node(_FakeToolNode())  # type: ignore[arg-type]
    state = {"messages": [], "session_id": "s1"}
    config = {"configurable": {"tools": {"foo": "bar"}}}
    runtime = object()
    result = run_tools(state, config=config, runtime=runtime)

    assert result == {"messages": []}
    assert captured["state"] == state
    assert captured["config"] == config
    assert captured["runtime"] is runtime
