from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import core.agent as agent_module


def test_agent_invoke_does_not_auto_write_journal(monkeypatch):
    """交易记录只能由显式动作触发，不能从回答文案副产物自动落库。"""
    captured: dict[str, object] = {}

    class DummyGraph:
        async def ainvoke(self, initial_state, config=None):
            captured["initial_state"] = initial_state
            captured["config"] = config
            return {
                "messages": [],
                "recommendation": {
                    "text": "若价格回踩 62000 支撑位，可考虑轻仓试多。entry: 62000 stop: 60500 tp: 65000"
                },
                "journal_id": None,
            }

    def fake_build_graph(llm, *, checkpointer=None, store=None):
        return DummyGraph()

    monkeypatch.setattr(agent_module, "build_graph", fake_build_graph)

    agent = agent_module.MarketReActAgent(llm=MagicMock())
    with patch("infrastructure.persistence.journal_repository.JournalRepository.create") as mock_create:
        result = asyncio.run(agent.invoke("给我一个开单建议", session_id="test_session"))

    mock_create.assert_not_called()
    assert result.get("journal_id") is None
