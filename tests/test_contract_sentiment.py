from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse

import pytest

from core.message_protocol import ToolCall
from core.tool_executor import ToolExecutor
from core.tool_protocol import ToolContext
from tools import market_data
from tools.registry import get_tool_registry


@pytest.mark.parametrize("failed", [set(), {"contract_stats"}, {"contract_stats", "funding_rate", "ETH_USDT"}])
def test_contract_sentiment_returns_available_facts_through_executor(monkeypatch, failed):
    requested = []

    def fetch(url, **kwargs):
        parsed = urlparse(url)
        endpoint = parsed.path.rsplit("/", 1)[-1]
        requested.append((parsed, parse_qs(parsed.query)))
        if endpoint in failed:
            raise TimeoutError("upstream timed out")
        if endpoint == "ETH_USDT":
            return {"name": "ETH_USDT", "funding_rate": "-0.000038", "funding_interval": 28800}
        if endpoint == "contract_stats":
            return [{"time": 200, "open_interest": 120, "lsr_account": 1.5, "long_liq_usd": 0},
                    {"time": 100, "open_interest": 100, "lsr_account": 1.4}]
        return [{"t": 200, "r": "-0.000038"}, {"t": 100, "r": "0.00001"}]

    monkeypatch.setattr(market_data, "_http_get_json", fetch)
    registry = get_tool_registry()
    message = asyncio.run(ToolExecutor(registry).execute(
        ToolCall(id="sentiment", name="get_contract_sentiment", arguments={"symbol": "ETHUSDT", "limit": 1000}),
        context=ToolContext(session_id="test", request_id="test"), allowed_names={"get_contract_sentiment"},
    ))
    result = json.loads(message.content)
    assert result["symbol"] == "ETH_USDT"
    assert result["status"] == ("success" if not failed else "error" if len(failed) == 3 else "partial")
    assert len(requested) == 3
    assert all(parsed.hostname == "api.gateio.ws" and parsed.path.startswith("/api/v4/futures/usdt/") for parsed, _ in requested)
    assert requested[1][1] == {"contract": ["ETH_USDT"], "interval": ["1h"], "limit": ["48"]}
    if not failed:
        assert [row["open_interest"] for row in result["statistics"]] == [100, 120]
        assert result["statistics"][0]["long_liq_usd"] is None
        assert result["statistics"][1]["long_liq_usd"] == 0
    else:
        assert "statistics" in result["errors"]
        assert "statistics" not in result
    if len(failed) < 3:
        assert result["contract"]["funding_rate"] == "-0.000038"
        assert result["funding_history"] == [{"time": 100, "funding_rate": "0.00001"}, {"time": 200, "funding_rate": "-0.000038"}]


def test_invalid_contract_or_interval_does_not_call_gate(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid request reached Gate")

    monkeypatch.setattr(market_data, "_http_get_json", unexpected)
    assert market_data.get_contract_sentiment("AU9999")["status"] == "error"
    assert market_data.get_contract_sentiment("ETHUSDT", interval="1w")["status"] == "error"
