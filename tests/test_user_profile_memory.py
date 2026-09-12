from __future__ import annotations

import asyncio
from pathlib import Path

from core.json_fact_store import JsonFactStore
from core.memory_api import DefaultMemoryAPI
from core.profile import UserProfile
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
