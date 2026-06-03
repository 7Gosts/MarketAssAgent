from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.session_state import SessionState


ROUTER_FALLBACK_SYMBOL = "BTC_USDT"
ROUTER_FALLBACK_INTERVAL = "4h"


@dataclass(frozen=True)
class RouteContext:
    """Runtime route defaults shared by CLI / HTTP / Feishu adapters."""

    channel: str
    session_id: str
    user_id: str | None
    default_symbol: str
    default_interval: str
    market_default_symbols: list[str] = field(default_factory=list)
    recent_messages: list[dict[str, str]] = field(default_factory=list)
    risk_profile: str | None = None
    display_preferences: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "default_symbol": self.default_symbol,
            "default_interval": self.default_interval,
            "market_default_symbols": list(self.market_default_symbols),
            "recent_messages": list(self.recent_messages),
            "risk_profile": self.risk_profile,
            "display_preferences": dict(self.display_preferences),
            "options": dict(self.options),
        }


def load_market_default_symbols(*, repo_root: Path | None = None) -> list[str]:
    root = repo_root or Path(__file__).resolve().parents[1]
    path = root / "config" / "market_config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = data.get("default_symbols") if isinstance(data, dict) else []
    return [str(x).strip().upper() for x in (raw or []) if str(x).strip()]


def build_route_context(
    *,
    channel: str,
    session_id: str,
    user_id: str | None = None,
    request_default_symbol: str | None = None,
    request_default_interval: str | None = None,
    session_state: SessionState | None = None,
    recent_messages: list[dict[str, str]] | None = None,
    risk_profile: str | None = None,
    display_preferences: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    repo_root: Path | None = None,
) -> RouteContext:
    market_defaults = load_market_default_symbols(repo_root=repo_root)
    last_symbol = ""
    if session_state:
        if session_state.last_symbols:
            last_symbol = str(session_state.last_symbols[0] or "").strip().upper()
        elif session_state.last_symbol:
            last_symbol = str(session_state.last_symbol or "").strip().upper()

    default_symbol = (
        last_symbol
        or str(request_default_symbol or "").strip().upper()
        or (market_defaults[0] if market_defaults else "")
        or ROUTER_FALLBACK_SYMBOL
    )
    default_interval = (
        str((session_state.last_interval if session_state else "") or "").strip().lower()
        or str(request_default_interval or "").strip().lower()
        or ROUTER_FALLBACK_INTERVAL
    )
    return RouteContext(
        channel=str(channel or "unknown"),
        session_id=str(session_id or "unknown"),
        user_id=user_id,
        default_symbol=default_symbol,
        default_interval=default_interval,
        market_default_symbols=market_defaults,
        recent_messages=list(recent_messages or []),
        risk_profile=risk_profile.strip() if isinstance(risk_profile, str) and risk_profile.strip() else None,
        display_preferences=dict(display_preferences or {}),
        options=dict(options or {}),
    )
