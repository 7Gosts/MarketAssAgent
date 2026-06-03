"""Agent 三层上下文契约（long_term / short_term / current_query）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

HistoryPolicy = Literal["minimal", "recent_4", "full", "none"]


@dataclass
class AgentContext:
    long_term: dict[str, Any]
    short_term: dict[str, Any]
    current_query: dict[str, Any]
    meta: dict[str, Any]
    history_policy: HistoryPolicy = "minimal"
    intent_confidence: float = 0.0
    router_transcript: list[dict[str, str]] = field(default_factory=list)

    def router_recent_messages(self) -> list[dict[str, str]]:
        return [dict(m) for m in self.router_transcript if isinstance(m, dict)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "long_term": dict(self.long_term),
            "short_term": dict(self.short_term),
            "current_query": dict(self.current_query),
            "meta": dict(self.meta),
            "history_policy": self.history_policy,
            "intent_confidence": round(float(self.intent_confidence or 0.0), 4),
            "router_transcript_count": len(self.router_transcript),
        }

    def explain_brief(self) -> str:
        cq = self.current_query if isinstance(self.current_query, dict) else {}
        intent = str(cq.get("intent_type") or "unknown")
        conf = float(self.intent_confidence or 0.0)
        skipped = bool(self.meta.get("pre_judge_skipped"))
        called = bool(self.meta.get("pre_judge_called"))
        rule = str(self.meta.get("rule_name") or "")
        bits = [
            f"intent={intent}",
            f"policy={self.history_policy}",
            f"conf={conf:.2f}",
            f"transcript={len(self.router_transcript)}",
        ]
        if rule:
            bits.append(f"rule={rule}")
        if intent == "research":
            kw = str(cq.get("research_keyword") or "")
            kws = cq.get("research_keywords") if isinstance(cq.get("research_keywords"), list) else []
            if kw:
                bits.append(f"kw={kw}")
            if len(kws) > 1:
                bits.append(f"kws={len(kws)}")
        if called:
            bits.append("pre_judge=1")
        elif skipped:
            bits.append("pre_judge=skip")
        return " ".join(bits)
