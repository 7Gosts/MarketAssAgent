from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.conversation_scope import ConversationScope
from core.agent_loop import NativeAgentLoop, select_tool_names
from core.llm_client import LLMClientError, LLMResponse, TokenUsage
from core.message_protocol import Message, ToolCall
from core.state import AgentState
from core.tool_executor import ToolExecutor
from core.tool_protocol import ToolContext, ToolSpec
from infrastructure.memory.event_store import LocalSessionEventStore
from infrastructure.memory.context_compactor import ContextBudget, ContextOverflow, SessionCompactor
from infrastructure.memory.session_journal import SessionEventJournal
from tools.registry import ToolRegistry


class FakeLLM:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.requests: list[dict] = []

    async def complete(self, *, messages, tools, max_tokens=None):
        self.requests.append({"messages": list(messages), "tools": list(tools), "max_tokens": max_tokens})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _state(*, allowed_tools=None) -> AgentState:
    return {
        "messages": [Message(role="system", content="system"), Message(role="user", content="status")],
        "session_id": "session_1",
        "request_id": "request_1",
        "metadata": {},
        "error": None,
        "allowed_tools": allowed_tools,
    }


def test_native_loop_executes_tool_and_returns_final_answer() -> None:
    captured: dict[str, str] = {}

    def status(*, symbol: str = "", context: ToolContext):
        captured["session_id"] = context.session_id
        captured["request_id"] = context.request_id
        return {"status": "success", "symbol": symbol}

    spec = ToolSpec(
        name="get_journal_status",
        description="status",
        parameters={
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "additionalProperties": False,
        },
        execute=status,
        requires_context=True,
    )
    registry = ToolRegistry([spec])
    llm = FakeLLM([
        LLMResponse(
            message=Message(
                role="assistant",
                tool_calls=(ToolCall(id="call_1", name="get_journal_status", arguments={"symbol": "ETHUSDT"}),),
            ),
            usage=TokenUsage(total_tokens=8),
        ),
        LLMResponse(message=Message(role="assistant", content="订单仍在等待触发。")),
    ])
    loop = NativeAgentLoop(llm=llm, registry=registry, executor=ToolExecutor(registry), max_steps=4)

    result = asyncio.run(loop.run(_state(allowed_tools=[])))

    assert len(llm.requests) == 2
    assert llm.requests[0]["tools"][0]["function"]["name"] == "get_journal_status"
    assert llm.requests[1]["messages"][-1].tool_call_id == "call_1"
    assert captured == {"session_id": "session_1", "request_id": "request_1"}
    assert result["recommendation"]["text"] == "订单仍在等待触发。"
    assert result["metadata"]["token_usage"]["total_tokens"] == 8


def test_native_loop_returns_rejection_to_model_without_executing_tool() -> None:
    called = False

    def write_tool():
        nonlocal called
        called = True

    spec = ToolSpec(
        name="write_tool",
        description="write",
        parameters={"type": "object", "properties": {}},
        execute=write_tool,
        effect_class="opaque_effect",
    )
    registry = ToolRegistry([spec])
    llm = FakeLLM([
        LLMResponse(message=Message(
            role="assistant",
            tool_calls=(ToolCall(id="bad_1", name="not_registered", arguments={}),),
        )),
        LLMResponse(message=Message(role="assistant", content="无法执行该工具。")),
    ])
    loop = NativeAgentLoop(llm=llm, registry=registry, executor=ToolExecutor(registry), max_steps=3)

    result = asyncio.run(loop.run(_state(allowed_tools=["write_tool"])))

    assert called is False
    assert llm.requests[1]["messages"][-1].tool_call_id == "bad_1"
    assert result["recommendation"]["text"] == "无法执行该工具。"


def test_native_loop_stops_at_max_steps() -> None:
    spec = ToolSpec(
        name="read_tool",
        description="read",
        parameters={"type": "object", "properties": {}},
        execute=lambda: {"ok": True},
    )
    registry = ToolRegistry([spec])
    llm = FakeLLM([
        LLMResponse(message=Message(
            role="assistant",
            tool_calls=(ToolCall(id=f"call_{idx}", name="read_tool", arguments={}),),
        ))
        for idx in range(2)
    ])
    loop = NativeAgentLoop(llm=llm, registry=registry, executor=ToolExecutor(registry), max_steps=2)

    result = asyncio.run(loop.run(_state()))

    assert result["error"] == "agent_loop_limit"
    assert "最大步骤" in result["recommendation"]["text"]


def test_native_loop_persists_write_before_execute_tool_protocol(tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def status(*, context: ToolContext):
        captured["operation_id"] = context.operation_id
        return {"status": "success", "value": 1}

    spec = ToolSpec(
        name="read_tool",
        description="read",
        parameters={"type": "object", "properties": {}},
        execute=status,
        effect_class="pure_query",
        requires_context=True,
    )
    registry = ToolRegistry([spec])
    llm = FakeLLM([
        LLMResponse(
            message=Message(
                role="assistant",
                tool_calls=(ToolCall(id="call_1", name="read_tool", arguments={}),),
            ),
            raw={"id": "response-1"},
        ),
        LLMResponse(message=Message(role="assistant", content="done"), raw={"id": "response-2"}),
    ])
    scope = ConversationScope(
        transport="feishu",
        tenant_id="tenant-a",
        visibility_scope="private",
        visibility_scope_id="ou_alice",
        actor_id="ou_alice",
    )
    store = LocalSessionEventStore(tmp_path)
    journal = SessionEventJournal(store=store, scope=scope, session_id=scope.session_id)
    journal.ensure_session()
    user = journal.append_user_message(
        text="status",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_agent_loop",
    )
    state = _state()
    state["session_id"] = scope.session_id
    state["event_journal"] = journal
    state["turn_user_event_id"] = user.event_id
    state["prompt_version"] = "test-v1"
    loop = NativeAgentLoop(llm=llm, registry=registry, executor=ToolExecutor(registry), max_steps=4)

    asyncio.run(loop.run(state))

    events = store.read_branch(scope=scope, session_id=scope.session_id)
    assert [event.event_type for event in events] == [
        "session/header",
        "user/message",
        "model/request",
        "assistant/message",
        "tool/call_planned",
        "tool/call_started",
        "tool/result",
        "model/request",
        "assistant/message",
    ]
    planned = next(event for event in events if event.event_type == "tool/call_planned")
    started = next(event for event in events if event.event_type == "tool/call_started")
    assert captured["operation_id"] == planned.payload["operation_id"]
    assert started.seq < next(event.seq for event in events if event.event_type == "tool/result")


def test_select_tool_names_keeps_empty_list_compatibility() -> None:
    all_names = {"a", "b"}
    assert select_tool_names(None, all_names) == all_names
    assert select_tool_names([], all_names) == all_names
    assert select_tool_names(["b", "unknown"], all_names) == {"b"}


@pytest.mark.parametrize("overflow", ["local_budget", "provider", "provider_twice"])
def test_compaction_after_tool_does_not_repeat_execution(tmp_path: Path, monkeypatch, overflow):
    limits = dict(context_window_tokens=4000 if overflow == "local_budget" else 8000,
                  reserve_tokens=1000, recent_fraction=0.3, estimator_margin=0.08)
    monkeypatch.setattr("core.agent_loop.get_agent_context_limits", lambda: limits)
    scope = ConversationScope(transport="feishu", tenant_id="tenant-a", visibility_scope="private",
                              visibility_scope_id="ou_alice", actor_id="ou_alice")
    store = LocalSessionEventStore(tmp_path)
    journal = SessionEventJournal(store=store, scope=scope, session_id=scope.session_id)
    journal.ensure_session()
    old = journal.append_user_message(text="历史" * 500, transport="feishu", tenant_or_app_id="tenant-a", external_message_id="old")
    request = journal.append_model_request(request_payload={"model": "test", "messages": []}, prompt_version="v1")
    journal.append_assistant_message(model_request_event_id=request.event_id, message=Message(role="assistant", content="done"))
    current = journal.append_user_message(text="执行", transport="feishu", tenant_or_app_id="tenant-a", external_message_id="current")
    executions = []

    def write_tool():
        executions.append(True)
        return {"value": "返回" * 800}

    registry = ToolRegistry([ToolSpec(name="write_tool", description="write", parameters={"type": "object", "properties": {}},
                                      execute=write_tool, effect_class="opaque_effect")])
    error = LLMClientError("context too long", status_code=400, context_overflow=True)
    responses = [LLMResponse(message=Message(role="assistant", tool_calls=(ToolCall(id="write_1", name="write_tool", arguments={}),)))]
    if overflow != "local_budget":
        responses.append(error)
    responses.append(LLMResponse(message=Message(role="assistant", content=f"历史约束。[mem:{old.event_id}]"), finish_reason="stop"))
    responses.append(error if overflow == "provider_twice" else LLMResponse(message=Message(role="assistant", content="done")))
    llm = FakeLLM(responses)
    state = _state()
    state.update(session_id=scope.session_id, event_journal=journal, turn_user_event_id=current.event_id,
                 evidence_index={old.event_id: "verbatim", "retrieved_id": "retrieved"})
    loop = NativeAgentLoop(llm=llm, registry=registry, executor=ToolExecutor(registry))
    if overflow == "provider_twice":
        with pytest.raises(ContextOverflow):
            asyncio.run(loop.run(state))
    else:
        assert asyncio.run(loop.run(state))["recommendation"]["text"] == "done"
    assert executions == [True]
    assert not llm.responses
    assert state["evidence_index"][old.event_id] == "checkpoint_cited"
    assert state["evidence_index"]["retrieved_id"] == "retrieved"
    events = store.read_branch(scope=scope, session_id=scope.session_id)
    assert sum(event.event_type == "context/compaction" for event in events) == 1
    assert sum(event.event_type == "tool/result" for event in events) == 1
    for request in llm.requests:
        assert SessionCompactor._request_tokens(request["messages"], request["tools"], ContextBudget(**limits)) <= limits["context_window_tokens"] - limits["reserve_tokens"]
        assert request["max_tokens"] <= limits["reserve_tokens"]
