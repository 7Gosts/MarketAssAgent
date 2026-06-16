from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from core.fact_store import Fact, SQLiteFactStore
from core.profile import ProfileUpdateAudit, UserProfile
from utils.runtime_paths import get_output_dir


ProfileUpdateSource = Literal["user_explicit", "llm_inference", "manual"]


class MemoryAPI(Protocol):
    def recall(self, thread_id: str, query: dict[str, Any], limit: int = 10) -> list[Fact]:
        ...

    def write_fact(self, thread_id: str, fact: Fact) -> str:
        ...

    def snapshot(self, thread_id: str) -> dict[str, Any]:
        ...

    def checkpoint(self, thread_id: str, key: str, value: Any) -> None:
        ...

    def get_checkpoint(self, thread_id: str, key: str) -> Any:
        ...

    async def get_user_profile(self, user_id: str) -> UserProfile:
        ...

    async def update_user_profile(
        self,
        profile: UserProfile,
        *,
        source: ProfileUpdateSource = "llm_inference",
        reason: str = "",
        confidence: float | None = None,
    ) -> UserProfile:
        ...


class DefaultMemoryAPI:
    """Phase A default implementation backed by SQLiteFactStore."""

    def __init__(self, store: SQLiteFactStore):
        self.store = store

    def recall(self, thread_id: str, query: dict[str, Any], limit: int = 10) -> list[Fact]:
        return self.store.recall(thread_id=thread_id, query=query, limit=limit)

    def write_fact(self, thread_id: str, fact: Fact) -> str:
        if not fact.thread_id:
            fact.thread_id = thread_id
        return self.store.write_fact(fact)

    def snapshot(self, thread_id: str) -> dict[str, Any]:
        snap = self.get_checkpoint(thread_id, "last_snapshot")
        if isinstance(snap, dict):
            return snap
        return {}

    def checkpoint(self, thread_id: str, key: str, value: Any) -> None:
        self.store.set_checkpoint(thread_id=thread_id, key=key, value=value)

    def get_checkpoint(self, thread_id: str, key: str) -> Any:
        return self.store.get_checkpoint(thread_id=thread_id, key=key)

    async def get_user_profile(self, user_id: str) -> UserProfile:
        fact = self.store.get_latest_fact(
            thread_id=f"user_profile_{user_id}",
            fact_type="user_profile",
        )
        if fact and fact.payload:
            try:
                return UserProfile(**fact.payload)
            except Exception:
                pass
        return UserProfile(user_id=user_id)

    async def update_user_profile(
        self,
        profile: UserProfile,
        *,
        source: ProfileUpdateSource = "llm_inference",
        reason: str = "",
        confidence: float | None = None,
    ) -> UserProfile:
        old_profile = await self.get_user_profile(profile.user_id)
        changed_fields = _collect_profile_changed_fields(old_profile, profile)
        if not changed_fields:
            return old_profile

        score = _resolve_confidence(source=source, confidence=confidence)
        audit = ProfileUpdateAudit(
            timestamp=datetime.now(timezone.utc),
            source=source,
            confidence=score,
            changed_fields=changed_fields,
            before=old_profile.model_dump(mode="json", exclude={"audit_log"}),
            after=profile.model_dump(mode="json", exclude={"audit_log"}),
            reason=reason,
        )

        merged_log = list(old_profile.audit_log)
        merged_log.append(audit)
        profile.audit_log = merged_log[-100:]
        profile.updated_at = datetime.now(timezone.utc)
        payload = profile.model_dump(mode="json")
        self.store.write_fact(
            Fact(
                thread_id=f"user_profile_{profile.user_id}",
                source=source,
                type="user_profile",
                payload=payload,
                tags=["user_profile", "persistent"],
                provenance={
                    "source": source,
                    "reason": reason,
                    "confidence": f"{score:.2f}",
                },
            )
        )
        return profile


def create_default_memory_api(*, repo_root: Path | None = None) -> DefaultMemoryAPI:
    output_dir = get_output_dir(repo_root=repo_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "memory_store.sqlite3"
    return DefaultMemoryAPI(store=SQLiteFactStore(db_path=db_path))


def _collect_profile_changed_fields(old_profile: UserProfile, new_profile: UserProfile) -> list[str]:
    fields = [
        "preferred_style",
        "risk_profile",
        "favorite_symbols",
        "max_position_ratio",
        "preferred_timeframes",
        "notes",
    ]
    changed: list[str] = []
    for field in fields:
        if getattr(old_profile, field) != getattr(new_profile, field):
            changed.append(field)
    return changed


def _resolve_confidence(*, source: ProfileUpdateSource, confidence: float | None) -> float:
    if confidence is not None:
        return max(0.0, min(1.0, float(confidence)))
    if source == "user_explicit":
        return 0.85
    if source == "manual":
        return 0.95
    return 0.65
