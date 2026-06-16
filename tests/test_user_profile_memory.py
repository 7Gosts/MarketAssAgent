from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from core.fact_store import SQLiteFactStore
from core.memory_api import DefaultMemoryAPI
from core.orchestrator import AssistantOrchestrator
from core.planner import ResponsePlan
from core.profile import UserProfile
from services.conversation_service import ConversationService


class _SessionManagerStub:
    def __init__(self) -> None:
        self.history: list[dict[str, str]] = []

    def save_user_message(self, session_id: str, text: str) -> None:
        self.history.append({"role": "user", "text": text})

    def get_recent_messages(self, session_id: str, limit: int = 8) -> list[dict[str, str]]:
        return self.history[-limit:]

    def save_reply(self, session_id: str, reply: str) -> None:
        self.history.append({"role": "assistant", "text": reply})


class _PlannerStub:
    async def plan(self, user_message: str, session_summary: str = "") -> ResponsePlan:
        return ResponsePlan(
            task_type="chat",
            required_tools=[],
            response_style="brief",
            user_context_needed=True,
        )


class _OrchestratorStub:
    async def run(
        self,
        *,
        text: str,
        plan: ResponsePlan,
        session_id: str,
        history: list[dict[str, str]] | None = None,
        invoke_fn: Any | None = None,
    ) -> dict[str, Any]:
        return {"reply": "ok"}


def test_memory_api_user_profile_roundtrip(tmp_path: Path):
    memory_api = DefaultMemoryAPI(store=SQLiteFactStore(db_path=tmp_path / "memory.sqlite3"))
    profile = UserProfile(
        user_id="u_profile_1",
        preferred_style="right_side",
        risk_profile="aggressive",
        favorite_symbols=["BTCUSDT"],
        notes="偏好右侧交易",
    )

    updated = asyncio.run(
        memory_api.update_user_profile(
            profile,
            source="user_explicit",
            reason="用户明确表达：偏好右侧，风险激进",
        )
    )
    loaded = asyncio.run(memory_api.get_user_profile("u_profile_1"))

    assert loaded.user_id == "u_profile_1"
    assert loaded.preferred_style == "right_side"
    assert loaded.risk_profile == "aggressive"
    assert "BTCUSDT" in loaded.favorite_symbols
    assert updated.audit_log
    latest = updated.audit_log[-1]
    assert latest.source == "user_explicit"
    assert latest.confidence == 0.85
    assert "preferred_style" in latest.changed_fields
    assert "risk_profile" in latest.changed_fields
    assert "favorite_symbols" in latest.changed_fields


def test_memory_api_user_profile_audit_accumulates(tmp_path: Path):
    memory_api = DefaultMemoryAPI(store=SQLiteFactStore(db_path=tmp_path / "memory.sqlite3"))
    profile = UserProfile(user_id="u_profile_2", preferred_style="left_side")
    asyncio.run(memory_api.update_user_profile(profile, source="user_explicit", reason="用户明确风格"))

    profile2 = asyncio.run(memory_api.get_user_profile("u_profile_2"))
    profile2.risk_profile = "balanced"
    updated = asyncio.run(
        memory_api.update_user_profile(
            profile2,
            source="llm_inference",
            confidence=0.70,
            reason="从多轮对话推断风险偏好",
        )
    )

    assert len(updated.audit_log) == 2
    latest = updated.audit_log[-1]
    assert latest.source == "llm_inference"
    assert latest.confidence == 0.70
    assert latest.changed_fields == ["risk_profile"]


def test_orchestrator_build_context_includes_user_profile():
    class _Memory:
        def snapshot(self, thread_id: str) -> dict:
            return {}

        async def get_user_profile(self, user_id: str) -> UserProfile:
            return UserProfile(
                user_id=user_id,
                preferred_style="swing",
                risk_profile="balanced",
                favorite_symbols=["AU0"],
            )

    orchestrator = AssistantOrchestrator(
        agent_graph=object(),  # type: ignore[arg-type]
        memory_api=_Memory(),  # type: ignore[arg-type]
    )
    plan = ResponsePlan(
        task_type="chat",
        required_tools=[],
        response_style="brief",
        user_context_needed=True,
    )

    ctx = asyncio.run(
        orchestrator._build_context(
            plan,
            {"session_id": "feishu_u88", "thread_id": "feishu_u88", "user_id": "u88"},
        )
    )

    assert isinstance(ctx.get("user_profile"), dict)
    assert ctx["user_profile"]["preferred_style"] == "swing"
    assert ctx["user_profile"]["risk_profile"] == "balanced"


def test_conversation_service_updates_profile_from_user_text(tmp_path: Path):
    memory_api = DefaultMemoryAPI(store=SQLiteFactStore(db_path=tmp_path / "memory.sqlite3"))
    service = ConversationService(
        agent=object(),  # type: ignore[arg-type]
        session_manager=_SessionManagerStub(),  # type: ignore[arg-type]
        planner=_PlannerStub(),  # type: ignore[arg-type]
        orchestrator=_OrchestratorStub(),  # type: ignore[arg-type]
        memory_api=memory_api,  # type: ignore[arg-type]
    )

    asyncio.run(
        service.run(
            text="我偏好右侧交易，风险激进，常看 BTC 和 ETH，常用 1h 周期，单仓 20%，不追高",
            session_id="feishu_user_xyz",
            history_limit=8,
        )
    )

    profile = asyncio.run(memory_api.get_user_profile("user_xyz"))
    assert profile.preferred_style == "right_side"
    assert profile.risk_profile == "aggressive"
    assert profile.max_position_ratio == 0.2
    assert "BTCUSDT" in profile.favorite_symbols
    assert "ETHUSDT" in profile.favorite_symbols
    assert "1h" in profile.preferred_timeframes
    assert profile.notes
    assert profile.audit_log
    latest = profile.audit_log[-1]
    assert latest.source == "user_explicit"
    assert latest.confidence == 0.85
    assert latest.reason.startswith("从对话中提取：")
