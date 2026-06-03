"""智能上下文构建：三层 Context + 动态 transcript（P1 无 Pre-Judge）。"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.agent_context import AgentContext, HistoryPolicy
from app.agent_schemas import AgentRequest
from app.intent_detectors import (
    _infer_interval_from_text,
    _resolve_symbols_from_text,
    detect_fresh_analysis_route,
    detect_followup_route,
    detect_general_chat_route,
    extract_followup_type,
    looks_like_fresh_analysis_request,
)
from app.research_keyword import looks_like_research_signal, resolve_research_keyword
from app.session_manager import SessionManager
from app.session_state import SessionState


_KNOWLEDGE_CHAT_PAT = re.compile(
    r"(了解.{0,12}吗|什么是|是什么|什么意思|怎么理解|介绍一下|科普)",
    re.I,
)
_FUZZY_MARKERS = ("看看", "查一下", "现在", "目前", "了解一下", "想了解")


@dataclass(frozen=True)
class ContextConfig:
    enabled: bool = True
    pre_judge_enabled: bool = False
    research_keyword_enabled: bool = True
    followup_transcript_rounds: int = 4
    new_analysis_transcript_rounds: int = 0
    long_history_pre_judge_threshold: int = 6
    log_context_explain: bool = True
    expose_in_response: bool = False


def load_context_config() -> ContextConfig:
    from app.agent_runtime_config import load_agent_runtime_config

    rt = load_agent_runtime_config()
    return ContextConfig(
        enabled=rt.context_enabled,
        pre_judge_enabled=rt.effective_pre_judge(),
        research_keyword_enabled=rt.effective_research_keyword_llm(),
        followup_transcript_rounds=rt.followup_transcript_rounds,
        new_analysis_transcript_rounds=rt.new_analysis_transcript_rounds,
        long_history_pre_judge_threshold=rt.long_history_pre_judge_threshold,
        log_context_explain=rt.log_context_explain,
        expose_in_response=rt.expose_context_in_response,
    )


@dataclass
class RuleHit:
    hit: bool = False
    confidence: str = "none"  # high | medium | low | none
    intent_type: str = "unknown"
    history_policy: HistoryPolicy = "minimal"
    suggested_interval: str | None = None
    followup_type: str | None = None
    mentioned_symbols: list[str] = field(default_factory=list)
    rule_name: str = ""
    research_keyword: str | None = None
    research_keywords: list[str] = field(default_factory=list)
    research_confidence: float = 0.0
    research_reasoning: str = ""
    is_research_intent: bool = False
    research_extractor_source: str = ""
    research_extractor_latency_ms: float = 0.0


class ContextBuilder:
    def __init__(
        self,
        session_mgr: SessionManager,
        *,
        config: ContextConfig | None = None,
    ) -> None:
        self.session_mgr = session_mgr
        self.config = config or load_context_config()

    def build(self, request: AgentRequest, session_state: SessionState) -> AgentContext:
        text = str(request.text or "").strip()
        long_term = self._build_long_term(request, session_state)
        short_term = self._build_short_term(session_state)

        all_recent = self._load_all_recent(request.session_id)
        rule_hit = self._apply_rule_prefill(text, session_state)

        current_query = self._build_current_query_shell(text)
        meta: dict[str, Any] = {
            "pre_judge_skipped": True,
            "pre_judge_called": False,
            "rule_hit": rule_hit.hit,
            "all_recent_count": len(all_recent),
        }

        if rule_hit.hit:
            current_query.update(
                {
                    "intent_type": rule_hit.intent_type,
                    "mentioned_symbols": list(rule_hit.mentioned_symbols),
                    "suggested_interval": rule_hit.suggested_interval,
                    "followup_type": rule_hit.followup_type,
                }
            )
            self._apply_research_fields(
                current_query,
                meta,
                keyword=rule_hit.research_keyword,
                keywords=list(rule_hit.research_keywords),
                confidence=rule_hit.research_confidence,
                reasoning=rule_hit.research_reasoning,
                is_research_intent=rule_hit.is_research_intent,
                source=rule_hit.research_extractor_source,
            )
            history_policy = rule_hit.history_policy
            intent_confidence = 0.95 if rule_hit.confidence == "high" else 0.75
            if rule_hit.intent_type == "research" and rule_hit.research_confidence:
                intent_confidence = max(intent_confidence, float(rule_hit.research_confidence))
            if rule_hit.research_extractor_latency_ms:
                meta["research_extractor_latency_ms"] = rule_hit.research_extractor_latency_ms
            meta["rule_name"] = rule_hit.rule_name
            meta["pre_judge_skipped"] = True
        elif self._should_pre_judge(text=text, rule_hit=rule_hit, recent_messages=all_recent):
            t0 = time.perf_counter()
            judged = self._pre_judge_intent(text, long_term, short_term)
            meta["pre_judge_latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
            current_query.update(judged)
            intent_type = str(judged.get("intent_type") or "unknown").strip().lower()
            if intent_type == "execution_question":
                current_query["followup_type"] = "execution"
            if intent_type == "research" or judged.get("is_research_intent"):
                self._apply_research_fields(
                    current_query,
                    meta,
                    keyword=judged.get("research_keyword"),
                    keywords=list(judged.get("research_keywords") or []),
                    confidence=float(judged.get("confidence") or 0.7),
                    reasoning=str(judged.get("reasoning") or ""),
                    is_research_intent=bool(judged.get("is_research_intent")),
                    source="pre_judge",
                )
                if not current_query.get("research_keywords"):
                    self._fill_research_from_extractor(text, current_query, meta)
            history_policy = _coerce_policy(judged.get("history_policy"), judged.get("intent_type"))
            intent_confidence = float(judged.get("confidence") or 0.7)
            if intent_confidence < 0.6:
                history_policy = "minimal"
            meta["pre_judge_called"] = True
            meta["pre_judge_skipped"] = False
            meta["pre_judge_reasoning"] = judged.get("reasoning")
        else:
            current_query["intent_type"] = "unknown"
            history_policy = "minimal"
            intent_confidence = 0.5
            meta["pre_judge_skipped"] = True

        transcript = self._select_transcript(request.session_id, history_policy)
        meta["transcript_count"] = len(transcript)

        ctx = AgentContext(
            long_term=long_term,
            short_term=short_term,
            current_query=current_query,
            meta=meta,
            history_policy=history_policy,
            intent_confidence=intent_confidence,
            router_transcript=transcript,
        )
        if self.config.log_context_explain:
            logger.info("[ContextBuilder] {}", ctx.explain_brief())
        return ctx

    def _build_long_term(self, request: AgentRequest, session_state: SessionState) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for entry in session_state.recent_analyses or []:
            if not isinstance(entry, dict):
                continue
            for sym in entry.get("symbols") or []:
                s = str(sym or "").strip().upper()
                if s:
                    counts[s] = counts.get(s, 0) + 1
            one = str(entry.get("symbol") or "").strip().upper()
            if one:
                counts[one] = counts.get(one, 0) + 1
        preferred = [k for k, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))][:5]

        default_interval = str(session_state.last_interval or "4h").strip().lower() or "4h"
        trading_style = "short_term" if default_interval in {"15m", "30m", "1h"} else "swing"

        risk_profile = request.context.get("risk_profile")
        if not isinstance(risk_profile, str):
            risk_profile = None

        return {
            "preferred_symbols": preferred,
            "default_interval": default_interval,
            "risk_profile": risk_profile,
            "display_preferences": dict(session_state.last_display_preferences or {}),
            "trading_style": trading_style,
        }

    def _build_short_term(self, session_state: SessionState) -> dict[str, Any]:
        summaries: list[dict[str, Any]] = []
        for entry in list(session_state.recent_analyses or [])[:5]:
            if not isinstance(entry, dict):
                continue
            summaries.append(
                {
                    "action": entry.get("action"),
                    "symbol": entry.get("symbol"),
                    "symbols": list(entry.get("symbols") or []),
                    "interval": entry.get("interval"),
                    "question": (str(entry.get("question") or "")[:120] or None),
                }
            )
        return {
            "compacted_summary": (session_state.compacted_summary or "").strip() or None,
            "recent_analyses": summaries,
            "history_version": int(session_state.history_version or 0),
        }

    def _build_current_query_shell(self, text: str) -> dict[str, Any]:
        cleaned = " ".join((text or "").split())
        symbols = _resolve_symbols_from_text(text)
        return {
            "text": text,
            "cleaned_text": cleaned,
            "intent_type": "unknown",
            "mentioned_symbols": symbols,
            "suggested_interval": _infer_interval_from_text(text, default="4h"),
            "followup_type": None,
            "needs_clarification": False,
            "research_keyword": None,
            "research_keywords": [],
            "research_confidence": 0.0,
            "research_reasoning": "",
            "is_research_intent": False,
        }

    def _apply_research_fields(
        self,
        current_query: dict[str, Any],
        meta: dict[str, Any],
        *,
        keyword: str | None,
        keywords: list[str],
        confidence: float,
        reasoning: str,
        is_research_intent: bool,
        source: str,
    ) -> None:
        kws = [str(k).strip() for k in keywords if str(k).strip()]
        kw = str(keyword or "").strip() or (kws[0] if kws else "")
        if kw and kw not in kws:
            kws = [kw, *kws]
        current_query["research_keyword"] = kw or None
        current_query["research_keywords"] = kws
        current_query["research_confidence"] = float(confidence or 0.0)
        current_query["research_reasoning"] = str(reasoning or "").strip()
        current_query["is_research_intent"] = bool(is_research_intent and kws)
        if current_query["is_research_intent"]:
            current_query["intent_type"] = "research"
        if source:
            meta["research_extractor_source"] = source
        if kw:
            meta["research_kw"] = kw
            meta["research_kws_count"] = len(kws)

    def _fill_research_from_extractor(
        self,
        text: str,
        current_query: dict[str, Any],
        meta: dict[str, Any],
    ) -> None:
        t0 = time.perf_counter()
        result = resolve_research_keyword(text, use_llm=self.config.research_keyword_enabled)
        meta["research_extractor_latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        self._apply_research_fields(
            current_query,
            meta,
            keyword=result.keyword,
            keywords=list(result.keywords),
            confidence=result.confidence,
            reasoning=result.reasoning,
            is_research_intent=result.is_research_intent,
            source=result.source,
        )

    def _resolve_research_rule_hit(self, text: str) -> RuleHit | None:
        if not looks_like_research_signal(text):
            return None
        t0 = time.perf_counter()
        result = resolve_research_keyword(text, use_llm=self.config.research_keyword_enabled)
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        if not result.is_research_intent or not result.keywords:
            return None
        conf_label = "high" if result.confidence >= 0.75 else "medium"
        return RuleHit(
            hit=True,
            confidence=conf_label,
            intent_type="research",
            history_policy="minimal",
            rule_name="research_llm",
            research_keyword=result.keyword,
            research_keywords=list(result.keywords),
            research_confidence=result.confidence,
            research_reasoning=result.reasoning,
            is_research_intent=True,
            research_extractor_source=result.source,
            research_extractor_latency_ms=latency_ms,
        )

    def _load_all_recent(self, session_id: str) -> list[dict[str, str]]:
        rounds = max(4, int(self.session_mgr.config.llm_memory_rounds or 4))
        return self.session_mgr.get_recent_messages(session_id, limit=max(2, rounds * 2))

    def _apply_rule_prefill(self, text: str, session_state: SessionState) -> RuleHit:
        if detect_fresh_analysis_route(text, session_state):
            symbols = _resolve_symbols_from_text(text)
            return RuleHit(
                hit=True,
                confidence="high",
                intent_type="new_analysis",
                history_policy="minimal",
                suggested_interval=_infer_interval_from_text(
                    text,
                    default=str(session_state.last_interval or "4h"),
                ),
                mentioned_symbols=symbols,
                rule_name="fresh_analysis",
            )

        if _KNOWLEDGE_CHAT_PAT.search(text) and not looks_like_fresh_analysis_request(text):
            if not looks_like_research_signal(text):
                return RuleHit(
                    hit=True,
                    confidence="high",
                    intent_type="knowledge_chat",
                    history_policy="none",
                    rule_name="knowledge_chat",
                )

        if detect_general_chat_route(text):
            return RuleHit(
                hit=True,
                confidence="high",
                intent_type="general_chat",
                history_policy="none",
                rule_name="general_chat",
            )

        research_hit = self._resolve_research_rule_hit(text)
        if research_hit:
            return research_hit

        followup_route = detect_followup_route(text, session_state)
        if followup_route:
            tp = followup_route.get("task_plan") if isinstance(followup_route.get("task_plan"), dict) else {}
            fc = followup_route.get("followup_context") if isinstance(followup_route.get("followup_context"), dict) else {}
            ft = str(fc.get("followup_type") or extract_followup_type(text) or "general")
            symbols = [str(s).strip().upper() for s in (tp.get("symbols") or []) if s]
            intent = "execution_question" if ft == "execution" else "followup"
            return RuleHit(
                hit=True,
                confidence="high",
                intent_type=intent,
                history_policy="recent_4",
                suggested_interval=str(tp.get("interval") or session_state.last_interval or "4h"),
                followup_type=ft,
                mentioned_symbols=symbols,
                rule_name="followup",
            )

        return RuleHit(hit=False, confidence="none", intent_type="unknown", history_policy="minimal")

    def _should_pre_judge(
        self,
        *,
        text: str,
        rule_hit: RuleHit,
        recent_messages: list[dict[str, str]],
    ) -> bool:
        if not self.config.pre_judge_enabled:
            return False
        if rule_hit.confidence == "high":
            return False
        if any(m in text for m in _FUZZY_MARKERS):
            return True
        if len(recent_messages) >= int(self.config.long_history_pre_judge_threshold):
            return True
        return False

    def _pre_judge_intent(
        self,
        text: str,
        long_term: dict[str, Any],
        short_term: dict[str, Any],
    ) -> dict[str, Any]:
        from tools.llm.client import pre_judge_query_intent

        try:
            return pre_judge_query_intent(text=text, long_term=long_term, short_term=short_term)
        except Exception as exc:
            logger.warning("[ContextBuilder] pre_judge failed err={}", exc)
            return {
                "intent_type": "unknown",
                "confidence": 0.5,
                "reasoning": "pre_judge_failed",
                "mentioned_symbols": _resolve_symbols_from_text(text),
                "suggested_interval": _infer_interval_from_text(text, default="4h"),
                "history_policy": "minimal",
                "needs_clarification": False,
            }

    def _select_transcript(self, session_id: str, policy: HistoryPolicy) -> list[dict[str, str]]:
        if policy in {"minimal", "none"}:
            return []
        if policy == "recent_4":
            n = max(2, int(self.config.followup_transcript_rounds) * 2)
            return self.session_mgr.get_recent_messages(session_id, limit=n)
        if policy == "full":
            n = max(8, int(self.config.followup_transcript_rounds) * 4)
            return self.session_mgr.get_recent_messages(session_id, limit=n)
        return []


def _coerce_policy(raw: Any, intent_type: Any) -> HistoryPolicy:
    policy = str(raw or "").strip().lower()
    if policy in {"minimal", "recent_4", "full", "none"}:
        return policy  # type: ignore[return-value]
    intent = str(intent_type or "").strip().lower()
    if intent in {"new_analysis", "research"}:
        return "minimal"
    if intent in {"followup", "execution_question", "clarification"}:
        return "recent_4"
    if intent in {"general_chat", "knowledge_chat"}:
        return "recent_4"
    return "minimal"
