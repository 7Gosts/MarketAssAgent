from __future__ import annotations

import asyncio
from typing import Any

from core.fact_store import Fact
from core.orchestrator import AssistantOrchestrator
from core.planner import ResponsePlan
from services.conversation_service import ConversationService


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


class _DummyPlanner:
    def __init__(self) -> None:
        self.last_summary = ""

    async def plan(self, user_message: str, session_summary: str = "") -> ResponsePlan:
        self.last_summary = session_summary
        return ResponsePlan(task_type="chat", required_tools=[], response_style="brief")


class _DummyProvenancePlanner:
    async def plan(self, user_message: str, session_summary: str = "") -> ResponsePlan:
        return ResponsePlan(
            task_type="chat",
            required_tools=[],
            response_style="brief",
            required_provenance=True,
        )


class _DummyOrchestrator:
    def __init__(self) -> None:
        self.captured_history: list[dict[str, str]] = []

    async def run(
        self,
        *,
        text: str,
        plan: ResponsePlan,
        session_id: str,
        history: list[dict[str, str]] | None = None,
        invoke_fn: Any | None = None,
    ) -> dict[str, Any]:
        self.captured_history = list(history or [])
        return {"reply": "ok"}


class _DummyToolResultOrchestrator:
    async def run(
        self,
        *,
        text: str,
        plan: ResponsePlan,
        session_id: str,
        history: list[dict[str, str]] | None = None,
        invoke_fn: Any | None = None,
    ) -> dict[str, Any]:
        return {
            "reply": "结论：保持观察。",
            "messages": [
                {
                    "type": "tool",
                    "name": "analyze_market",
                    "tool_call_id": "tc_001",
                    "content": '{"status":"success","symbol":"AU0","interval":"1h","trend":"震荡"}',
                }
            ],
        }


class _TwoTurnPlanner:
    async def plan(self, user_message: str, session_summary: str = "") -> ResponsePlan:
        required = "怎么知道" in user_message
        return ResponsePlan(
            task_type="chat",
            required_tools=[],
            response_style="brief",
            required_provenance=required,
        )


class _TwoTurnOrchestrator:
    async def run(
        self,
        *,
        text: str,
        plan: ResponsePlan,
        session_id: str,
        history: list[dict[str, str]] | None = None,
        invoke_fn: Any | None = None,
    ) -> dict[str, Any]:
        if "怎么知道" in text:
            return {"reply": "依据在上一次工具观察中。"}
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


class _DummyMemoryAPI:
    def __init__(self, seed_facts: list[Fact] | None = None) -> None:
        self.facts = list(seed_facts or [])
        self.checkpoints: dict[tuple[str, str], Any] = {}

    def recall(self, thread_id: str, query: dict, limit: int = 10) -> list[Fact]:
        fact_type = str(query.get("type") or "").strip()
        matched = [f for f in self.facts if f.thread_id == thread_id and (not fact_type or f.type == fact_type)]
        # 与 SQLiteFactStore 对齐：返回新到旧
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


class _DummyChatLLM:
    def __init__(self) -> None:
        self.last_messages: list[Any] = []

    async def ainvoke(self, messages: list[Any]):
        self.last_messages = messages

        class _Resp:
            content = "chat-ok"

        return _Resp()


def test_conversation_service_deduplicates_trailing_current_user_message():
    session_mgr = _DummySessionManager(
        history=[
            {"role": "assistant", "text": "上一轮回复"},
            {"role": "user", "text": "这条会重复"},
        ]
    )
    planner = _DummyPlanner()
    orchestrator = _DummyOrchestrator()
    service = ConversationService(
        agent=object(),  # type: ignore[arg-type]
        session_manager=session_mgr,  # type: ignore[arg-type]
        planner=planner,  # type: ignore[arg-type]
        orchestrator=orchestrator,  # type: ignore[arg-type]
    )

    asyncio.run(
        service.run(
            text="这条会重复",
            session_id="s1",
            history_limit=8,
        )
    )

    assert orchestrator.captured_history == [{"role": "assistant", "text": "上一轮回复"}]


def test_conversation_service_prefers_memory_api_history_when_available():
    seed = [
        Fact(thread_id="s2", type="recent_message", payload={"role": "assistant", "text": "old-a"}),
        Fact(thread_id="s2", type="recent_message", payload={"role": "user", "text": "old-u"}),
    ]
    memory_api = _DummyMemoryAPI(seed_facts=seed)
    session_mgr = _DummySessionManager(history=[])
    planner = _DummyPlanner()
    orchestrator = _DummyOrchestrator()
    service = ConversationService(
        agent=object(),  # type: ignore[arg-type]
        session_manager=session_mgr,  # type: ignore[arg-type]
        planner=planner,  # type: ignore[arg-type]
        orchestrator=orchestrator,  # type: ignore[arg-type]
        memory_api=memory_api,  # type: ignore[arg-type]
    )

    asyncio.run(
        service.run(
            text="current-q",
            session_id="s2",
            history_limit=8,
        )
    )

    # 应保留旧历史，并移除本轮写入导致的末尾重复 user 文本
    assert orchestrator.captured_history == [
        {"role": "assistant", "text": "old-a"},
        {"role": "user", "text": "old-u"},
    ]


def test_orchestrator_chat_path_includes_history_messages():
    chat_llm = _DummyChatLLM()
    orchestrator = AssistantOrchestrator(
        agent_graph=object(),  # type: ignore[arg-type]
        chat_llm=chat_llm,
        tools_registry=[],
    )

    plan = ResponsePlan(task_type="chat", required_tools=[], response_style="brief")
    asyncio.run(
        orchestrator.run(
            text="current",
            plan=plan,
            session_id="s3",
            history=[
                {"role": "assistant", "text": "历史A"},
                {"role": "user", "text": "历史B"},
            ],
        )
    )

    contents = [getattr(m, "content", "") for m in chat_llm.last_messages]
    assert contents == ["历史A", "历史B", "current"]


def test_conversation_service_appends_provenance_block_when_required():
    session_mgr = _DummySessionManager(history=[])
    memory_api = _DummyMemoryAPI()
    service = ConversationService(
        agent=object(),  # type: ignore[arg-type]
        session_manager=session_mgr,  # type: ignore[arg-type]
        planner=_DummyProvenancePlanner(),  # type: ignore[arg-type]
        orchestrator=_DummyToolResultOrchestrator(),  # type: ignore[arg-type]
        memory_api=memory_api,  # type: ignore[arg-type]
    )

    envelope = asyncio.run(
        service.run(
            text="你怎么知道 888 的？",
            session_id="s4",
            history_limit=8,
        )
    )
    text = envelope.reply_text
    assert "结论：保持观察。" in text
    assert "**依据来源**" in text
    assert "analyze_market" in text


def test_memory_api_only_mode_skips_legacy_session_io(monkeypatch):
    monkeypatch.setenv("MARKETASSAGENT_FEATURE_MEMORY_API_ONLY_MODE", "true")
    session_mgr = _DummySessionManager(history=[{"role": "assistant", "text": "legacy"}])
    memory_api = _DummyMemoryAPI(
        seed_facts=[
            Fact(thread_id="s5", type="recent_message", payload={"role": "assistant", "text": "mem-a"}),
            Fact(thread_id="s5", type="recent_message", payload={"role": "user", "text": "mem-u"}),
        ]
    )
    orchestrator = _DummyOrchestrator()
    service = ConversationService(
        agent=object(),  # type: ignore[arg-type]
        session_manager=session_mgr,  # type: ignore[arg-type]
        planner=_DummyPlanner(),  # type: ignore[arg-type]
        orchestrator=orchestrator,  # type: ignore[arg-type]
        memory_api=memory_api,  # type: ignore[arg-type]
    )

    asyncio.run(service.run(text="q", session_id="s5", history_limit=8))

    # memory-only 模式下，不应读写 legacy session 历史
    assert session_mgr.user_messages == []
    assert session_mgr.replies == []
    assert session_mgr.recent_calls == []
    assert orchestrator.captured_history == [
        {"role": "assistant", "text": "mem-a"},
        {"role": "user", "text": "mem-u"},
    ]


def test_two_turn_provenance_uses_previous_tool_observation(monkeypatch):
    monkeypatch.setenv("MARKETASSAGENT_FEATURE_MEMORY_API_ONLY_MODE", "true")
    memory_api = _DummyMemoryAPI()
    session_mgr = _DummySessionManager(history=[])
    service = ConversationService(
        agent=object(),  # type: ignore[arg-type]
        session_manager=session_mgr,  # type: ignore[arg-type]
        planner=_TwoTurnPlanner(),  # type: ignore[arg-type]
        orchestrator=_TwoTurnOrchestrator(),  # type: ignore[arg-type]
        memory_api=memory_api,  # type: ignore[arg-type]
    )

    # 第 1 轮：产生 tool_observation
    first = asyncio.run(service.run(text="先看下 AU0", session_id="s6", history_limit=8))
    assert "先给一版 AU0 计划。" in first.reply_text

    # 第 2 轮：追问来源，应引用上一轮 observation
    second = asyncio.run(service.run(text="你怎么知道的？", session_id="s6", history_limit=8))
    assert "**依据来源**" in second.reply_text
    assert "analyze_market" in second.reply_text
    assert "tc_prev_01" in second.reply_text
