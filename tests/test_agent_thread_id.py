from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import core.agent as agent_module


def test_agent_invoke_passes_thread_id_via_graph_config(monkeypatch):
    captured: dict[str, object] = {}

    class DummyGraph:
        async def ainvoke(self, initial_state, config=None):
            captured["initial_state"] = initial_state
            captured["config"] = config
            return {"messages": [], "recommendation": {"text": "ok"}}

    def fake_build_graph(llm, *, checkpointer=None, store=None):
        captured["checkpointer"] = checkpointer
        captured["store"] = store
        return DummyGraph()

    monkeypatch.setattr(agent_module, "build_graph", fake_build_graph)

    dummy_llm = MagicMock()
    checkpointer = object()
    store = object()
    agent = agent_module.MarketReActAgent(
        llm=dummy_llm,
        checkpointer=checkpointer,
        store=store,
    )

    asyncio.run(agent.invoke("hello", session_id="feishu_abc", request_id="req_123"))

    assert captured["checkpointer"] is checkpointer
    assert captured["store"] is store
    cfg = captured.get("config")
    assert isinstance(cfg, dict)
    assert cfg.get("configurable", {}).get("thread_id") == "feishu_abc"
    assert cfg.get("configurable", {}).get("request_id") == "req_123"
