from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal


ScopeType = Literal["private", "group", "web"]
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _clean(value: str) -> str:
    return str(value or "").strip()


def _crockford_128(value: bytes) -> str:
    number = int.from_bytes(value[:16], "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD_ALPHABET[number & 31])
        number >>= 5
    return "".join(reversed(chars))


def stable_id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(_clean(part) for part in parts).encode("utf-8")
    return f"{prefix}_{_crockford_128(hashlib.sha256(raw).digest())}"


@dataclass(frozen=True)
class ConversationScope:
    """A visibility boundary for conversation memory."""

    transport: str
    tenant_id: str
    visibility_scope: ScopeType
    visibility_scope_id: str
    actor_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "transport",
            "tenant_id",
            "visibility_scope",
            "visibility_scope_id",
            "actor_id",
        ):
            if not _clean(getattr(self, field_name)):
                raise ValueError(f"conversation scope missing {field_name}")
        if self.visibility_scope not in {"private", "group", "web"}:
            raise ValueError(f"invalid visibility_scope: {self.visibility_scope}")

    @property
    def scope_key(self) -> str:
        return stable_id(
            "scope",
            self.transport,
            self.tenant_id,
            self.visibility_scope,
            self.visibility_scope_id,
        )

    @property
    def session_id(self) -> str:
        return stable_id(
            "sess",
            self.transport,
            self.tenant_id,
            self.visibility_scope,
            self.visibility_scope_id,
        )

    @property
    def main_branch_id(self) -> str:
        return stable_id("br", self.session_id, "main")

    def to_meta(self) -> dict[str, str]:
        return {
            "transport": self.transport,
            "tenant_id": self.tenant_id,
            "visibility_scope": self.visibility_scope,
            "visibility_scope_id": self.visibility_scope_id,
            "actor_id": self.actor_id,
        }


def build_feishu_scope(
    *,
    tenant_id: str,
    open_id: str,
    user_id: str,
    chat_id: str,
    chat_type: str,
) -> ConversationScope:
    actor_id = _clean(open_id) or _clean(user_id)
    if not actor_id:
        raise ValueError("cannot determine Feishu actor identity")

    clean_chat_type = _clean(chat_type).lower()
    if clean_chat_type == "group":
        if not _clean(chat_id):
            raise ValueError("group conversation missing chat_id")
        visibility_scope: ScopeType = "group"
        visibility_scope_id = _clean(chat_id)
    else:
        visibility_scope = "private"
        visibility_scope_id = actor_id

    return ConversationScope(
        transport="feishu",
        tenant_id=_clean(tenant_id) or "default",
        visibility_scope=visibility_scope,
        visibility_scope_id=visibility_scope_id,
        actor_id=actor_id,
    )
