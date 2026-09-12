from __future__ import annotations

import asyncio
import json

from core.message_protocol import ToolCall
from core.tool_executor import ToolExecutor
from core.tool_protocol import ToolContext, ToolSpec
import tools.sim_account as sim_account_module
import tools.registry as registry_module
from tools.registry import ToolRegistry, get_tool_registry


def _payload(message):
    return json.loads(message.content)


def test_executor_injects_context_and_preserves_tool_call_id() -> None:
    captured: dict[str, object] = {}

    def execute(*, order_id: str, context: ToolContext):
        captured.update(order_id=order_id, session_id=context.session_id, request_id=context.request_id)
        return {"status": "success"}

    spec = ToolSpec(
        name="cancel_paper_order",
        description="cancel",
        parameters={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        execute=execute,
        side_effect="write",
        requires_context=True,
    )
    executor = ToolExecutor(ToolRegistry([spec]))

    message = asyncio.run(executor.execute(
        ToolCall(id="call_1", name="cancel_paper_order", arguments={"order_id": "ord_1"}),
        context=ToolContext(session_id="owner", request_id="req_1"),
        allowed_names={"cancel_paper_order"},
    ))

    assert message.tool_call_id == "call_1"
    assert captured == {"order_id": "ord_1", "session_id": "owner", "request_id": "req_1"}
    assert _payload(message) == {"status": "success"}


def test_executor_rejects_model_supplied_runtime_context() -> None:
    called = False

    def execute(**_kwargs):
        nonlocal called
        called = True

    spec = ToolSpec(
        name="cancel_paper_order",
        description="cancel",
        parameters={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        execute=execute,
        requires_context=True,
    )
    executor = ToolExecutor(ToolRegistry([spec]))
    message = asyncio.run(executor.execute(
        ToolCall(
            id="call_1",
            name="cancel_paper_order",
            arguments={"order_id": "ord_1", "session_id": "attacker"},
        ),
        context=ToolContext(session_id="owner", request_id="req_1"),
        allowed_names={"cancel_paper_order"},
    ))

    assert called is False
    assert _payload(message)["error"] == "invalid_arguments"


def test_executor_returns_structured_errors() -> None:
    def explode():
        raise RuntimeError("boom")

    spec = ToolSpec(
        name="explode",
        description="explode",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        execute=explode,
    )
    executor = ToolExecutor(ToolRegistry([spec]))
    context = ToolContext(session_id="s1", request_id="r1")

    unknown = asyncio.run(executor.execute(
        ToolCall(id="u", name="unknown", arguments={}),
        context=context,
        allowed_names={"unknown"},
    ))
    denied = asyncio.run(executor.execute(
        ToolCall(id="d", name="explode", arguments={}),
        context=context,
        allowed_names=set(),
    ))
    failed = asyncio.run(executor.execute(
        ToolCall(id="f", name="explode", arguments={}),
        context=context,
        allowed_names={"explode"},
    ))

    assert _payload(unknown)["error"] == "unknown_tool"
    assert _payload(denied)["error"] == "tool_not_allowed"
    assert _payload(failed)["error"] == "tool_execution_failed"


def test_real_cancel_spec_hides_and_overrides_runtime_context(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_cancel(*, order_id: str, session_id: str, reason: str = "", request_id: str = ""):
        captured.update(
            order_id=order_id,
            session_id=session_id,
            reason=reason,
            request_id=request_id,
        )
        return {"status": "success"}

    monkeypatch.setattr(sim_account_module, "cancel_paper_order", fake_cancel)
    registry = get_tool_registry()
    spec = registry.get("cancel_paper_order")
    assert spec is not None
    assert "session_id" not in spec.parameters["properties"]
    assert "request_id" not in spec.parameters["properties"]

    message = asyncio.run(ToolExecutor(registry).execute(
        ToolCall(
            id="call_cancel_real",
            name="cancel_paper_order",
            arguments={"order_id": "ord_1", "reason": "误建"},
        ),
        context=ToolContext(session_id="owner_session", request_id="request_1"),
        allowed_names={"cancel_paper_order"},
    ))

    assert _payload(message)["status"] == "success"
    assert captured == {
        "order_id": "ord_1",
        "session_id": "owner_session",
        "reason": "误建",
        "request_id": "request_1",
    }


def test_analyze_market_reconciles_all_session_orders_before_analysis(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_reconcile(*, session_id: str):
        calls.append(("reconcile", {"session_id": session_id}))
        return {"status": "error", "changed": 0, "message": "market unavailable"}

    def fake_analyze(**kwargs):
        calls.append(("analyze", kwargs))
        return {"status": "success", "symbol": kwargs["symbol"]}

    loaded_tools = registry_module._load_tools()
    loaded_tools["reconcile_paper_orders"] = fake_reconcile
    loaded_tools["analyze_market"] = fake_analyze
    monkeypatch.setattr(registry_module, "_load_tools", lambda: loaded_tools)
    registry = get_tool_registry()

    spec = registry.get("analyze_market")
    assert spec is not None
    assert "order_type" not in registry.get("simulate_open_position").parameters["properties"]
    assert registry.get("reconcile_paper_orders").parameters["properties"] == {}
    result = spec.execute(
        context=ToolContext(session_id="owner_session", request_id="request_1"),
        symbol="ETH_USDT",
        interval="1h",
    )

    assert result["status"] == "success"
    assert calls == [
        ("reconcile", {"session_id": "owner_session"}),
        (
            "analyze",
            {
                "symbol": "ETH_USDT",
                "interval": "1h",
                "session_id": "owner_session",
                "request_id": "request_1",
            },
        ),
    ]
