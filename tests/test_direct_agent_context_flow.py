from __future__ import annotations

import asyncio
from typing import Any

from core.fact_store import Fact
from core.agent_context import build_light_agent_input
from application.services.conversation_service import ConversationService


class _SessionManagerStub:
    def __init__(self, history: list[dict[str, str]] | None = None) -> None:
        self.history = list(history or [])
        self.user_messages: list[tuple[str, str]] = []
        self.replies: list[tuple[str, str]] = []

    def save_user_message(self, session_id: str, text: str) -> None:
        self.user_messages.append((session_id, text))
        self.history.append({"role": "user", "text": text})

    def get_recent_messages(self, session_id: str, limit: int = 8) -> list[dict[str, str]]:
        return self.history[-limit:]

    def save_reply(self, session_id: str, reply: str) -> None:
        self.replies.append((session_id, reply))
        self.history.append({"role": "assistant", "text": reply})


class _AgentStub:
    def __init__(self) -> None:
        self.calls = 0
        self.last_user_input = ""
        self.last_allowed_tools: list[str] | None = None
        self.last_history: list[dict[str, str]] | None = None

    async def invoke(
        self,
        user_input: str,
        session_id: str = "default",
        history: list[dict[str, str]] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        self.last_user_input = user_input
        self.last_allowed_tools = allowed_tools
        self.last_history = list(history or [])
        return {"reply": "direct-ok", "messages": []}


class _MemoryStub:
    def __init__(self) -> None:
        self.facts: list[Fact] = []
        self.checkpoints: dict[tuple[str, str], Any] = {
            ("feishu_u123", "last_snapshot"): {"symbol": "ETHUSDT", "interval": "1h", "trend": "震荡"}
        }
        self.facts.append(
            Fact(
                thread_id="feishu_u123",
                source="analyze_market",
                type="tool_observation",
                payload={"tool": "analyze_market", "summary": "success / ETHUSDT / 1h / 震荡"},
                provenance={"tool_call_id": "tc_123"},
            )
        )

    def recall(self, thread_id: str, query: dict[str, Any], limit: int = 10) -> list[Fact]:
        fact_type = str(query.get("type") or "")
        matched = [f for f in self.facts if f.thread_id == thread_id and (not fact_type or f.type == fact_type)]
        return list(reversed(matched))[:limit]

    def write_fact(self, thread_id: str, fact: Fact) -> str:
        if not fact.thread_id:
            fact.thread_id = thread_id
        self.facts.append(fact)
        return fact.id

    def snapshot(self, thread_id: str) -> dict[str, Any]:
        value = self.checkpoints.get((thread_id, "last_snapshot"))
        return value if isinstance(value, dict) else {}

    def checkpoint(self, thread_id: str, key: str, value: Any) -> None:
        self.checkpoints[(thread_id, key)] = value

    def get_checkpoint(self, thread_id: str, key: str) -> Any:
        return self.checkpoints.get((thread_id, key))


def test_light_context_flow_builds_input_and_invokes_agent():
    agent = _AgentStub()
    memory = _MemoryStub()
    session = _SessionManagerStub(history=[{"role": "assistant", "text": "上轮结论：先观察"}])

    service = ConversationService(
        agent=agent,  # type: ignore[arg-type]
        session_manager=session,  # type: ignore[arg-type]
        memory_api=memory,  # type: ignore[arg-type]
    )

    envelope = asyncio.run(
        service.run(
            text="刚才那个点位还能用吗？",
            session_id="feishu_u123",
            history_limit=8,
        )
    )

    assert agent.calls == 1
    assert agent.last_allowed_tools == []
    assert "【历史对话摘要】" in agent.last_user_input
    assert "【任务目标】" in agent.last_user_input
    assert "storage_key: u123" in agent.last_user_input
    assert "【用户画像】" not in agent.last_user_input
    assert "【最近工具来源】" not in agent.last_user_input
    assert envelope.reply_text == "direct-ok"

    recent_message_facts = [f for f in memory.facts if f.thread_id == "feishu_u123" and f.type == "recent_message"]
    roles = [str((f.payload or {}).get("role")) for f in recent_message_facts]
    assert "user" in roles
    assert "assistant" in roles


def test_light_agent_input_contains_summary_and_task_goal():
    light_input = build_light_agent_input(
        user_text="刚才那个支撑还有效吗？",
        session_id="feishu_u123",
        storage_key="u123",
        conversation_summary={
            "recent_dialogue_summary": "用户刚才关注 ETH 1h 行情；助手认为 2400 附近是关键支撑。",
            "current_carryover_hint": "当前问题大概率在追问 ETH 1h 的支撑是否仍有效。",
            "snapshot_hint": "ETHUSDT, 1h, trend=震荡, price=2420, support=2400",
        },
        max_chars=1200,
        max_summary_chars=1000,
    )
    assert "【历史对话摘要】" in light_input
    assert "【任务目标】" in light_input
    assert "【用户当前消息】" in light_input
    assert "2400" in light_input
    assert "【用户画像】" not in light_input
    assert len(light_input) <= 1200


def test_light_mode_uses_summary_without_passing_history():
    agent = _AgentStub()
    memory = _MemoryStub()
    session = _SessionManagerStub(
        history=[
            {"role": "user", "text": "看看 ETH 1h 行情"},
            {"role": "assistant", "text": "ETH 1h 仍偏震荡，2400 附近是关键支撑。"},
        ]
    )

    service = ConversationService(
        agent=agent,  # type: ignore[arg-type]
        session_manager=session,  # type: ignore[arg-type]
        memory_api=memory,  # type: ignore[arg-type]
    )

    envelope = asyncio.run(
        service.run(
            text="刚才那个支撑还有效吗？",
            session_id="feishu_u123",
            history_limit=8,
        )
    )

    assert envelope.reply_text == "direct-ok"
    assert "【历史对话摘要】" in agent.last_user_input
    assert "2400" in agent.last_user_input
    assert "【用户画像】" not in agent.last_user_input
    assert "【最近工具来源】" not in agent.last_user_input
    assert agent.last_history == []


def test_run_writes_structured_turn_summary_fact():
    agent = _AgentStub()
    memory = _MemoryStub()
    session = _SessionManagerStub(history=[])

    async def _invoke(
        user_input: str,
        session_id: str = "default",
        history: list[dict[str, str]] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "reply": "ETH 1h 仍偏震荡，2400 一带先看支撑，未突破前不追多。",
            "analysis_result": {
                "symbol": "ETHUSDT",
                "interval": "1h",
                "timestamp": "2026-07-10T10:00:00",
                "current_price": 2420.0,
                "trend": "震荡",
                "levels_v2": {"nearest_support": 2400.0, "nearest_resistance": 2480.0},
                "actionability": {"bias": "wait", "wait_condition": "等待价格触及关键位后再确认"},
                "invalidation_conditions": {"time_stop_rule": "若 3 根同周期K线未延续则失效"},
                "trigger_conditions": {"side": "wait", "entry": None, "stop": None},
                "raw_insights": "ETHUSDT 在 1h 周期呈震荡结构。",
            },
            "messages": [],
        }

    agent.invoke = _invoke  # type: ignore[method-assign]

    service = ConversationService(
        agent=agent,  # type: ignore[arg-type]
        session_manager=session,  # type: ignore[arg-type]
        memory_api=memory,  # type: ignore[arg-type]
    )

    envelope = asyncio.run(
        service.run(
            text="看看 ETH 1h",
            session_id="feishu_u123",
            history_limit=8,
        )
    )

    assert envelope.reply_text.startswith("ETH 1h")
    turn_summaries = [f for f in memory.facts if f.thread_id == "feishu_u123" and f.type == "turn_summary"]
    assert len(turn_summaries) >= 1
    payload = turn_summaries[-1].payload
    assert payload["symbols"] == ["ETHUSDT"]
    assert payload["intervals"] == ["1h"]
    assert payload["trend"] == "震荡"
    assert payload["current_price"] == 2420.0
    assert payload["key_levels"]["support"][0] == 2400.0
    assert payload["next_trigger"] == "等待价格触及关键位后再确认"

    snapshots = [f for f in memory.facts if f.thread_id == "feishu_u123" and f.type == "analysis_snapshot"]
    assert len(snapshots) >= 1
    snapshot_payload = snapshots[-1].payload
    assert snapshot_payload == {
        "schema_version": "analysis_snapshot.v1",
        "symbol": "ETHUSDT",
        "interval": "1h",
        "timestamp": "2026-07-10T10:00:00",
        "price": 2420.0,
        "trend": "震荡",
        "stance": "wait",
        "support": [2400.0],
        "resistance": [2480.0],
    }


def test_turn_summary_uses_analyze_market_tool_payload_when_snapshot_missing():
    agent = _AgentStub()
    memory = _MemoryStub()
    session = _SessionManagerStub(history=[])

    async def _invoke(
        user_input: str,
        session_id: str = "default",
        history: list[dict[str, str]] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "reply": "ETH 4h 先看区间。",
            "messages": [
                {
                    "type": "tool",
                    "name": "analyze_market",
                    "tool_call_id": "tc_extract_01",
                    "content": (
                        '{"status":"success","symbol":"ETH_USDT","interval":"4h",'
                        '"analysis":{"symbol":"ETH_USDT","interval":"4h","timestamp":"2026-07-10T11:00:00",'
                        '"current_price":1576.14,'
                        '"trend":"震荡","levels_v2":{"nearest_support":1549.01,"nearest_resistance":1601.2}}}'
                    ),
                }
            ],
        }

    agent.invoke = _invoke  # type: ignore[method-assign]
    service = ConversationService(
        agent=agent,  # type: ignore[arg-type]
        session_manager=session,  # type: ignore[arg-type]
        memory_api=memory,  # type: ignore[arg-type]
    )

    asyncio.run(
        service.run(
            text="看看 ETH 4h",
            session_id="feishu_u_tool",
            history_limit=8,
        )
    )

    turn_summaries = [f for f in memory.facts if f.thread_id == "feishu_u_tool" and f.type == "turn_summary"]
    assert len(turn_summaries) >= 1
    payload = turn_summaries[-1].payload
    assert payload["symbols"] == ["ETH_USDT"]
    assert payload["intervals"] == ["4h"]
    assert payload["trend"] == "震荡"
    assert payload["current_price"] == 1576.14
    assert payload["key_levels"]["support"][0] == 1549.01
    assert payload["key_levels"]["resistance"][0] == 1601.2

    snapshots = [f for f in memory.facts if f.thread_id == "feishu_u_tool" and f.type == "analysis_snapshot"]
    assert len(snapshots) >= 1
    assert snapshots[-1].payload["symbol"] == "ETH_USDT"
    assert snapshots[-1].payload["interval"] == "4h"
    assert snapshots[-1].payload["price"] == 1576.14
    assert snapshots[-1].payload["support"] == [1549.01]
    assert snapshots[-1].payload["resistance"] == [1601.2]


def test_light_mode_prefers_turn_summary_over_raw_history():
    agent = _AgentStub()
    memory = _MemoryStub()
    memory.facts.append(
        Fact(
            thread_id="feishu_u123",
            source="conversation_service",
            type="turn_summary",
            payload={
                "symbols": ["BTCUSDT"],
                "intervals": ["4h"],
                "current_price": 65000.0,
                "trend": "偏多",
                "key_levels": {"support": [64000.0], "resistance": [66500.0]},
                "assistant_conclusion": "BTC 4h 偏多，64000 附近支撑有效，未破位前先看延续。",
                "next_trigger": "若回踩 64000 一带止跌，再看顺势延续。",
            },
        )
    )
    session = _SessionManagerStub(
        history=[
            {"role": "user", "text": "这是一段应该被 turn_summary 覆盖的原始历史"},
            {"role": "assistant", "text": "这段原始历史不应该作为 light input 主摘要来源"},
        ]
    )

    service = ConversationService(
        agent=agent,  # type: ignore[arg-type]
        session_manager=session,  # type: ignore[arg-type]
        memory_api=memory,  # type: ignore[arg-type]
    )

    envelope = asyncio.run(
        service.run(
            text="刚才那个支撑还能看吗？",
            session_id="feishu_u123",
            history_limit=8,
        )
    )

    assert envelope.reply_text == "direct-ok"
    assert "BTCUSDT / 4h / 价=65000.0 / 趋势=偏多" in agent.last_user_input
    assert "支撑=64000.0" in agent.last_user_input
    assert "这是一段应该被 turn_summary 覆盖的原始历史" not in agent.last_user_input
