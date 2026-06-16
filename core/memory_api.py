from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from core.fact_store import Fact, FactStore
from core.json_fact_store import JsonFactStore
from core.postgres_fact_store import PostgresFactStore
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
    """Default implementation backed by any FactStore (JSON by default)."""

    def __init__(self, store: FactStore):
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
        before_profile = old_profile.model_copy(deep=True)

        # 直接使用新 profile 的非默认值进行覆盖，兼容旧调用方传完整 UserProfile 的方式。
        for field in UserProfile.model_fields:
            if field in ("user_id", "updated_at", "audit_log"):
                continue
            new_val = getattr(profile, field)
            default_val = getattr(UserProfile(user_id=profile.user_id), field)
            if new_val != default_val:
                setattr(old_profile, field, new_val)

        changed_fields = _collect_profile_changed_fields(before_profile, old_profile)
        if not changed_fields:
            return before_profile

        score = _resolve_confidence(source=source, confidence=confidence)
        audit = ProfileUpdateAudit(
            timestamp=datetime.now(timezone.utc),
            source=source,
            confidence=score,
            changed_fields=changed_fields,
            before=before_profile.model_dump(mode="json", exclude={"audit_log"}),
            after=old_profile.model_dump(mode="json", exclude={"audit_log"}),
            reason=reason,
        )

        merged_log = list(before_profile.audit_log)
        merged_log.append(audit)
        old_profile.audit_log = merged_log[-100:]
        old_profile.updated_at = datetime.now(timezone.utc)

        payload = old_profile.model_dump(mode="json")
        self.store.write_fact(
            Fact(
                thread_id=f"user_profile_{old_profile.user_id}",
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
        return old_profile


def create_default_memory_api(
    *,
    repo_root: Path | None = None,
    backend: Literal["json", "postgres"] | None = None,
) -> DefaultMemoryAPI:
    """创建默认 MemoryAPI。

    backend=None 时从配置读取 memory.backend，默认 json。
    支持 "json" / "postgres"。SQLite memory backend 已移除。
    """
    if backend is None:
        backend = _get_memory_backend_from_config()

    if backend == "sqlite":
        raise ValueError("SQLite memory backend has been removed; use 'json' or 'postgres'")

    if backend == "json":
        output_dir = get_output_dir(repo_root=repo_root)
        output_dir.mkdir(parents=True, exist_ok=True)
        return DefaultMemoryAPI(
            store=JsonFactStore(
                facts_path=output_dir / "memory_facts.jsonl",
                checkpoints_path=output_dir / "memory_checkpoints.json",
            )
        )
    if backend == "postgres":
        return DefaultMemoryAPI(store=PostgresFactStore())
    raise ValueError(f"Unsupported memory backend: {backend}")


def _get_memory_backend_from_config() -> Literal["json", "postgres"]:
    try:
        from config.runtime_config import get_memory_config

        mem = get_memory_config()
        backend = str(mem.get("backend") or "json").strip().lower()
        if backend == "sqlite":
            raise ValueError("SQLite memory backend has been removed; use 'json' or 'postgres'")
        if backend in ("json", "postgres"):
            return backend  # type: ignore[return-value]
    except ValueError:
        raise
    except Exception:
        pass
    return "json"


def _collect_profile_changed_fields(old_profile: UserProfile, new_profile: UserProfile) -> list[str]:
    fields = [
        "preferred_style",
        "risk_profile",
        "market_bias",
        "favorite_symbols",
        "max_position_ratio",
        "preferred_timeframes",
        "notes",
        "observations",
        "style_history",
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
