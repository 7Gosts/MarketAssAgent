from __future__ import annotations

import asyncio
from pathlib import Path

from core.json_fact_store import JsonFactStore
from core.memory_api import DefaultMemoryAPI
from core.profile import UserProfile
from application.services.conversation_service import ConversationService


class _SessionManagerStub:
    def __init__(self) -> None:
        self.history: list[dict[str, str]] = []

    def save_user_message(self, session_id: str, text: str) -> None:
        self.history.append({"role": "user", "text": text})

    def get_recent_messages(self, session_id: str, limit: int = 8) -> list[dict[str, str]]:
        return self.history[-limit:]

    def save_reply(self, session_id: str, reply: str) -> None:
        self.history.append({"role": "assistant", "text": reply})


class _AgentCaptureStub:
    def __init__(self) -> None:
        self.last_user_input = ""

    async def invoke(
        self,
        user_input: str,
        session_id: str = "default",
        history: list[dict[str, str]] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> dict[str, object]:
        self.last_user_input = user_input
        return {"reply": "ok", "messages": []}


def _json_memory_api(tmp_path: Path) -> DefaultMemoryAPI:
    return DefaultMemoryAPI(
        store=JsonFactStore(
            facts_path=tmp_path / "memory_facts.jsonl",
            checkpoints_path=tmp_path / "memory_checkpoints.json",
        )
    )


def test_memory_api_user_profile_roundtrip(tmp_path: Path):
    memory_api = _json_memory_api(tmp_path)
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
    memory_api = _json_memory_api(tmp_path)
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


def test_conversation_service_injects_profile_into_direct_context(tmp_path: Path):
    memory_api = _json_memory_api(tmp_path)
    seeded = UserProfile(
        user_id="user_xyz",
        preferred_style="right_side",
        risk_profile="aggressive",
        favorite_symbols=["BTCUSDT", "ETHUSDT"],
        preferred_timeframes=["1h"],
        notes="不追高",
    )
    asyncio.run(memory_api.update_user_profile(seeded, source="manual", reason="seed for context"))
    agent = _AgentCaptureStub()
    service = ConversationService(
        agent=agent,  # type: ignore[arg-type]
        session_manager=_SessionManagerStub(),  # type: ignore[arg-type]
        memory_api=memory_api,  # type: ignore[arg-type]
    )

    asyncio.run(
        service.run(
            text="我偏好右侧交易，风险激进，常看 BTC 和 ETH，常用 1h 周期，单仓 20%，不追高",
            session_id="feishu_user_xyz",
            history_limit=8,
        )
    )

    assert "【用户画像】" in agent.last_user_input
    assert "right_side" in agent.last_user_input
    assert "aggressive" in agent.last_user_input
    assert "BTCUSDT" in agent.last_user_input
    assert "ETHUSDT" in agent.last_user_input
    assert "不追高" in agent.last_user_input

    profile = asyncio.run(memory_api.get_user_profile("user_xyz"))
    assert profile.preferred_style == "right_side"
    assert profile.risk_profile == "aggressive"
    assert "BTCUSDT" in profile.favorite_symbols
    assert "ETHUSDT" in profile.favorite_symbols
    assert "1h" in profile.preferred_timeframes
    assert profile.notes
    # 当前主链路不再做规则层自动画像更新；这里只应保留 seed 记录。
    assert len(profile.audit_log) == 1
