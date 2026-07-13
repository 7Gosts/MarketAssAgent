from __future__ import annotations

import asyncio
from typing import Any

from core.fact_store import Fact
from application.services.conversation_service import ConversationService


class _DummySessionManager:
    def __init__(self, history: list[dict[str, str]]):
        self._history = list(history)
        self.user_messages: list[tuple[str, str]] = []
        self.replies: list[tuple[str, str]] = []
        self.recent_calls: list[tuple[str, int]] = []

    def save_user_message(self, session_id: str, text: str) -> None:
        self.user_messages.append((session_id, text))

    def get_recent_messages(self, session_id: str, limit: int = 8) -> list[dict[str, str]]:
        self.recent_calls.append((session_id, limit))
        return self._history[-limit:]

    def save_reply(self, session_id: str, reply: str) -> None:
        self.replies.append((session_id, reply))


class _DummyMemoryAPI:
    def __init__(self, seed_facts: list[Fact] | None = None) -> None:
        self.facts = list(seed_facts or [])
        self.checkpoints: dict[tuple[str, str], Any] = {}

    def recall(self, thread_id: str, query: dict, limit: int = 10) -> list[Fact]:
        fact_type = str(query.get("type") or "").strip()
        matched = [f for f in self.facts if f.thread_id == thread_id and (not fact_type or f.type == fact_type)]
        # 与 FactStore 对齐：返回新到旧
        return list(reversed(matched))[:limit]

    def write_fact(self, thread_id: str, fact: Fact) -> str:
        if not fact.thread_id:
            fact.thread_id = thread_id
        self.facts.append(fact)
        return fact.id

    def snapshot(self, thread_id: str) -> dict:
        v = self.checkpoints.get((thread_id, "last_snapshot"))
        return v if isinstance(v, dict) else {}

    def checkpoint(self, thread_id: str, key: str, value: Any) -> None:
        self.checkpoints[(thread_id, key)] = value

    def get_checkpoint(self, thread_id: str, key: str) -> Any:
        return self.checkpoints.get((thread_id, key))


class _DummyAgent:
    def __init__(self) -> None:
        self.captured_history: list[dict[str, str]] = []
        self.last_user_input = ""
        self.calls = 0
        self.next_reply = "ok"
        self.next_messages: list[dict[str, Any]] = []

    async def invoke(
        self,
        user_input: str,
        session_id: str = "default",
        request_id: str = "",
        history: list[dict[str, str]] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        self.last_user_input = user_input
        self.captured_history = list(history or [])
        return {"reply": self.next_reply, "messages": list(self.next_messages)}


class _TwoTurnAgent:
    def __init__(self) -> None:
        self.calls = 0
        self.last_user_input = ""
        self.second_user_input = ""

    async def invoke(
        self,
        user_input: str,
        session_id: str = "default",
        request_id: str = "",
        history: list[dict[str, str]] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        self.last_user_input = user_input
        if "【用户当前消息】\n你怎么知道的？" in user_input:
            self.second_user_input = user_input
            return {"reply": "依据在上一次工具观察中。", "messages": []}
        return {
            "reply": "先给一版 AU0 计划。",
            "messages": [
                {
                    "type": "tool",
                    "name": "analyze_market",
                    "tool_call_id": "tc_prev_01",
                    "content": '{"status":"success","symbol":"AU0","interval":"1h","trend":"震荡"}',
                }
            ],
        }


def test_conversation_service_deduplicates_trailing_current_user_message():
    session_mgr = _DummySessionManager(
        history=[
            {"role": "assistant", "text": "上一轮回复"},
            {"role": "user", "text": "这条会重复"},
        ]
    )
    agent = _DummyAgent()
    service = ConversationService(
        agent=agent,  # type: ignore[arg-type]
        session_manager=session_mgr,  # type: ignore[arg-type]
    )

    asyncio.run(
        service.run(
            text="这条会重复",
            session_id="s1",
            history_limit=8,
        )
    )

    # light-only 链路不再向 agent 透传原始 history，历史由摘要首屏承接。
    assert agent.captured_history == []


def test_conversation_service_prefers_memory_api_history_when_available():
    seed = [
        Fact(thread_id="s2", type="recent_message", payload={"role": "assistant", "text": "old-a"}),
        Fact(thread_id="s2", type="recent_message", payload={"role": "user", "text": "old-u"}),
    ]
    memory_api = _DummyMemoryAPI(seed_facts=seed)
    session_mgr = _DummySessionManager(history=[])
    agent = _DummyAgent()
    service = ConversationService(
        agent=agent,  # type: ignore[arg-type]
        session_manager=session_mgr,  # type: ignore[arg-type]
        memory_api=memory_api,  # type: ignore[arg-type]
    )

    asyncio.run(
        service.run(
            text="current-q",
            session_id="s2",
            history_limit=8,
        )
    )

    # light-only 链路不再透传原始 history。
    assert agent.captured_history == []


def test_conversation_service_writes_tool_observation_facts():
    session_mgr = _DummySessionManager(history=[])
    memory_api = _DummyMemoryAPI()
    agent = _DummyAgent()
    agent.next_reply = "结论：保持观察。"
    agent.next_messages = [
        {
            "type": "tool",
            "name": "analyze_market",
            "tool_call_id": "tc_001",
            "content": '{"status":"success","symbol":"AU0","interval":"1h","trend":"震荡"}',
        }
    ]
    service = ConversationService(
        agent=agent,  # type: ignore[arg-type]
        session_manager=session_mgr,  # type: ignore[arg-type]
        memory_api=memory_api,  # type: ignore[arg-type]
    )

    envelope = asyncio.run(
        service.run(
            text="你怎么知道 888 的？",
            session_id="s4",
            history_limit=8,
        )
    )
    assert "结论：保持观察。" in envelope.reply_text

    tool_facts = [f for f in memory_api.facts if f.thread_id == "s4" and f.type == "tool_observation"]
    assert len(tool_facts) == 1
    payload = tool_facts[0].payload
    assert payload.get("tool") == "analyze_market"
    assert "AU0" in str(payload.get("summary") or "")


def test_memory_api_only_mode_skips_legacy_session_io(monkeypatch):
    monkeypatch.setenv("MARKETASSAGENT_FEATURE_MEMORY_API_ONLY_MODE", "true")
    session_mgr = _DummySessionManager(history=[{"role": "assistant", "text": "legacy"}])
    memory_api = _DummyMemoryAPI(
        seed_facts=[
            Fact(thread_id="s5", type="recent_message", payload={"role": "assistant", "text": "mem-a"}),
            Fact(thread_id="s5", type="recent_message", payload={"role": "user", "text": "mem-u"}),
        ]
    )
    agent = _DummyAgent()
    service = ConversationService(
        agent=agent,  # type: ignore[arg-type]
        session_manager=session_mgr,  # type: ignore[arg-type]
        memory_api=memory_api,  # type: ignore[arg-type]
    )

    asyncio.run(service.run(text="q", session_id="s5", history_limit=8))

    # memory-only 模式下，不应读写 legacy session 历史
    assert session_mgr.user_messages == []
    assert session_mgr.replies == []
    assert session_mgr.recent_calls == []
    assert agent.captured_history == []


def test_two_turn_light_context_does_not_preinject_tool_observation(monkeypatch):
    monkeypatch.setenv("MARKETASSAGENT_FEATURE_MEMORY_API_ONLY_MODE", "true")
    memory_api = _DummyMemoryAPI()
    session_mgr = _DummySessionManager(history=[])
    agent = _TwoTurnAgent()
    service = ConversationService(
        agent=agent,  # type: ignore[arg-type]
        session_manager=session_mgr,  # type: ignore[arg-type]
        memory_api=memory_api,  # type: ignore[arg-type]
    )

    # 第 1 轮：产生 tool_observation
    first = asyncio.run(service.run(text="先看下 AU0", session_id="s6", history_limit=8))
    assert "先给一版 AU0 计划。" in first.reply_text

    # 第 2 轮：追问来源，输入上下文不再预注入上一轮 tool observation，依赖工具按需补证
    second = asyncio.run(service.run(text="你怎么知道的？", session_id="s6", history_limit=8))
    assert "依据在上一次工具观察中。" in second.reply_text
    assert "【历史对话摘要】" in agent.second_user_input
    assert "analyze_market" not in agent.second_user_input
    assert "tc_prev_01" not in agent.second_user_input
