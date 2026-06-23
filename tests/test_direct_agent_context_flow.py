from __future__ import annotations

import asyncio
from typing import Any

from core.fact_store import Fact
from core.profile import UserProfile
from core.agent_context import build_direct_agent_input
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
        return {"reply": "direct-ok", "messages": []}


class _MemoryStub:
    def __init__(self) -> None:
        self.facts: list[Fact] = []
        self.checkpoints: dict[tuple[str, str], Any] = {
            ("feishu_u123", "last_snapshot"): {"symbol": "ETHUSDT", "interval": "1h", "trend": "震荡"}
        }
        self.update_called = 0
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

    async def get_user_profile(self, user_id: str) -> UserProfile:
        return UserProfile(
            user_id=user_id,
            preferred_style="right_side",
            risk_profile="balanced",
            favorite_symbols=["ETHUSDT"],
            preferred_timeframes=["1h"],
        )

    async def update_user_profile(self, *args: Any, **kwargs: Any) -> UserProfile:
        self.update_called += 1
        return UserProfile(user_id="u123")


class _MemoryProfileErrorStub(_MemoryStub):
    async def get_user_profile(self, user_id: str) -> UserProfile:
        raise RuntimeError("profile backend unavailable")


def test_direct_context_flow_builds_context_and_invokes_agent():
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

    assert memory.update_called == 0
    assert agent.calls == 1
    assert agent.last_allowed_tools == []
    assert "【用户当前消息】" in agent.last_user_input
    assert "刚才那个点位还能用吗？" in agent.last_user_input
    assert "storage_key: u123" in agent.last_user_input
    assert "analyze_market" in agent.last_user_input
    assert "ETHUSDT" in agent.last_user_input
    assert "【最近对话结论】" in agent.last_user_input
    assert "上一轮助手结论" in agent.last_user_input
    assert envelope.reply_text == "direct-ok"

    recent_message_facts = [f for f in memory.facts if f.thread_id == "feishu_u123" and f.type == "recent_message"]
    roles = [str((f.payload or {}).get("role")) for f in recent_message_facts]
    assert "user" in roles
    assert "assistant" in roles


def test_direct_context_mode_profile_load_failure_fallback():
    agent = _AgentStub()
    memory = _MemoryProfileErrorStub()
    session = _SessionManagerStub(history=[])

    service = ConversationService(
        agent=agent,  # type: ignore[arg-type]
        session_manager=session,  # type: ignore[arg-type]
        memory_api=memory,  # type: ignore[arg-type]
    )

    envelope = asyncio.run(
        service.run(
            text="这次给我轻仓计划",
            session_id="feishu_u123",
            history_limit=8,
        )
    )

    assert envelope.reply_text == "direct-ok"
    assert "【用户画像】\n无" in agent.last_user_input
    assert "【上一轮市场快照】" in agent.last_user_input


def test_direct_context_mode_recent_sources_compact_and_limited():
    agent = _AgentStub()
    memory = _MemoryStub()
    memory.facts = []
    memory.facts.extend(
        [
            Fact(
                thread_id="feishu_u123",
                timestamp="2026-06-18T10:01:00Z",
                source="analyze_market",
                type="tool_observation",
                payload={"tool": "analyze_market", "summary": "s1"},
                provenance={"tool_call_id": "tc1"},
            ),
            Fact(
                thread_id="feishu_u123",
                timestamp="2026-06-18T10:02:00Z",
                source="analyze_market",
                type="tool_observation",
                payload={"tool": "analyze_market", "summary": "s2"},
                provenance={"tool_call_id": "tc2"},
            ),
            Fact(
                thread_id="feishu_u123",
                timestamp="2026-06-18T10:03:00Z",
                source="analyze_market",
                type="tool_observation",
                payload={"tool": "analyze_market", "summary": "s3"},
                provenance={"tool_call_id": "tc3"},
            ),
            Fact(
                thread_id="feishu_u123",
                timestamp="2026-06-18T10:04:00Z",
                source="analyze_market",
                type="tool_observation",
                payload={"tool": "analyze_market", "summary": "s4"},
                provenance={"tool_call_id": "tc4"},
            ),
        ]
    )
    session = _SessionManagerStub(history=[])

    service = ConversationService(
        agent=agent,  # type: ignore[arg-type]
        session_manager=session,  # type: ignore[arg-type]
        memory_api=memory,  # type: ignore[arg-type]
    )

    envelope = asyncio.run(
        service.run(
            text="继续说",
            session_id="feishu_u123",
            history_limit=8,
        )
    )

    assert envelope.reply_text == "direct-ok"
    assert "- 2026-06-18T10:04:00Z analyze_market: s4 (tool_call_id=tc4)" in agent.last_user_input
    assert "- 2026-06-18T10:03:00Z analyze_market: s3 (tool_call_id=tc3)" in agent.last_user_input
    assert "- 2026-06-18T10:02:00Z analyze_market: s2 (tool_call_id=tc2)" in agent.last_user_input
    assert "s1" not in agent.last_user_input


def test_direct_context_budget_keeps_snapshot_and_user_message():
    direct_input = build_direct_agent_input(
        user_text="eth 现在还能开多吗",
        session_id="feishu_u123",
        storage_key="u123",
        user_profile={
            "preferred_style": "right_side",
            "notes": "n" * 4000,
            "observations": ["o" * 600, "o" * 600, "o" * 600],
        },
        last_snapshot={
            "symbol": "ETHUSDT",
            "interval": "1h",
            "trend": "偏空",
            "raw_insights": "x" * 5000,
        },
        recent_sources=[
            {
                "timestamp": "2026-06-18T10:00:00Z",
                "tool": "analyze_market",
                "summary": "y" * 5000,
                "tool_call_id": "tc_1",
            }
        ],
        recent_conclusion={"last_assistant_conclusion": "z" * 5000},
        max_chars=1200,
        max_recent_sources=1,
        max_conclusion_chars=240,
    )
    assert "【用户当前消息】" in direct_input
    assert "eth 现在还能开多吗" in direct_input
    assert "ETHUSDT" in direct_input
    assert len(direct_input) <= 1200
