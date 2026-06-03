"""研报搜索关键词：LLM 提取 + 规则降级（单点）。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

_RESEARCH_KEYWORD_EXTRACT_PAT_A = re.compile(
    r"^(?:请|帮我|麻烦|顺便|看看|看下|看一下|查下|查一下|搜一下|搜下|了解一下|了解)?(?P<kw>.+?)(?:的)?(?:研报|研报线索|机构观点|观点|配置逻辑)$"
)
_RESEARCH_KEYWORD_EXTRACT_PAT_B = re.compile(
    r"^(?:请|帮我|麻烦|顺便|看看|看下|看一下|查下|查一下|搜一下|搜下|了解一下|了解)?(?P<kw>.+?)(?:板块|概念|行业|归属|主题)$"
)
_RESEARCH_SIGNAL_PAT = re.compile(
    r"(研报|机构|卖方|首席|观点|怎么看\s*待|配置逻辑|叙事|板块|概念|归属|行业|主题|用研报工具|研报工具|研报客)",
    re.I,
)
_ANALYSIS_HINT_PAT = re.compile(
    r"(分析|结构|fib|威科夫|趋势|模板|触发|止损|入场|k线|行情\s*分析|技术\s*面|多周期|共振)",
    re.I,
)
_SPLIT_KW_PAT = re.compile(r"[、,，/]+")

SourceKind = Literal["llm", "rule"]


@dataclass(frozen=True)
class ResearchKeywordResult:
    keyword: str
    keywords: list[str]
    confidence: float
    reasoning: str
    is_research_intent: bool
    source: SourceKind

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "keywords": list(self.keywords),
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "is_research_intent": self.is_research_intent,
            "source": self.source,
        }


def looks_like_research_signal(text: str) -> bool:
    """是否像研报/板块/机构观点类请求（仅信号，不提取关键词）。"""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_RESEARCH_SIGNAL_PAT.search(raw)) and not bool(_ANALYSIS_HINT_PAT.search(raw))


def _split_compound_keywords(text: str) -> list[str]:
    parts = [p.strip() for p in _SPLIT_KW_PAT.split(text) if p.strip()]
    if len(parts) <= 1:
        return [text.strip()] if text.strip() else []
    out: list[str] = []
    for p in parts:
        if p and p not in out:
            out.append(p)
        if len(out) >= 3:
            break
    return out


def normalize_research_keywords(raw_keywords: list[str], *, max_items: int = 3) -> list[str]:
    out: list[str] = []
    for item in raw_keywords:
        for part in _split_compound_keywords(str(item or "").strip()):
            if part and part not in out:
                out.append(part)
            if len(out) >= max_items:
                return out
    return out


def _normalize_keywords(raw_keywords: list[str], *, max_items: int = 3) -> list[str]:
    return normalize_research_keywords(raw_keywords, max_items=max_items)


def extract_research_keywords_rule_fallback(text: str) -> list[str]:
    """规则降级：剥离前缀/后缀，并对顿号/逗号做轻量拆分。"""
    raw = (text or "").strip()
    if not raw:
        return []
    for pat in (_RESEARCH_KEYWORD_EXTRACT_PAT_A, _RESEARCH_KEYWORD_EXTRACT_PAT_B):
        m = pat.search(raw)
        if m:
            kw = str(m.group("kw") or "").strip().strip("的")
            if kw:
                return _normalize_keywords([kw])
    cleaned = re.sub(
        r"^(?:请|帮我|麻烦|顺便|用?\s*研报工具\s*|研报工具\s*)+",
        "",
        raw,
        flags=re.I,
    )
    cleaned = re.sub(
        r"^(?:请|帮我|麻烦|顺便|看看|看下|看一下|查下|查一下|搜一下|搜下|了解一下|了解)+",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(的)?(研报|研报线索|机构观点|观点|配置逻辑|板块|概念|行业|归属|主题)$", "", cleaned)
    cleaned = cleaned.strip(" 的，,。！？!?")
    if not cleaned:
        return []
    return _normalize_keywords([cleaned])


def extract_research_keyword_rule_fallback(text: str) -> str | None:
    """兼容旧接口：返回主关键词。"""
    kws = extract_research_keywords_rule_fallback(text)
    return kws[0] if kws else None


def _rule_fallback_result(text: str, *, reasoning: str = "规则降级") -> ResearchKeywordResult:
    kws = extract_research_keywords_rule_fallback(text)
    if not kws:
        return ResearchKeywordResult(
            keyword="",
            keywords=[],
            confidence=0.0,
            reasoning=reasoning,
            is_research_intent=looks_like_research_signal(text),
            source="rule",
        )
    return ResearchKeywordResult(
        keyword=kws[0],
        keywords=kws,
        confidence=0.4,
        reasoning=reasoning,
        is_research_intent=True,
        source="rule",
    )


def _normalize_llm_result(obj: dict[str, Any], *, text: str) -> ResearchKeywordResult:
    is_research = bool(obj.get("is_research_intent", False))
    raw_kws = obj.get("keywords")
    keywords: list[str] = []
    if isinstance(raw_kws, list):
        keywords = _normalize_keywords([str(x).strip() for x in raw_kws if str(x).strip()])
    primary = str(obj.get("keyword") or "").strip()
    if primary and primary not in keywords:
        keywords = _normalize_keywords([primary, *keywords])
    elif not keywords and primary:
        keywords = [primary]
    if not keywords and is_research:
        return _rule_fallback_result(text, reasoning="LLM 未返回关键词，规则补全")
    kw = keywords[0] if keywords else ""
    try:
        conf = float(obj.get("confidence") or 0.85)
    except (TypeError, ValueError):
        conf = 0.85
    return ResearchKeywordResult(
        keyword=kw,
        keywords=keywords,
        confidence=max(0.0, min(1.0, conf)),
        reasoning=str(obj.get("reasoning") or "").strip() or "LLM 提取",
        is_research_intent=is_research and bool(keywords),
        source="llm",
    )


def resolve_research_keyword(text: str, *, use_llm: bool = True) -> ResearchKeywordResult:
    """统一入口：优先 LLM，失败或未启用时规则降级。"""
    raw = (text or "").strip()
    if not raw:
        return ResearchKeywordResult(
            keyword="",
            keywords=[],
            confidence=0.0,
            reasoning="empty_input",
            is_research_intent=False,
            source="rule",
        )
    if use_llm:
        try:
            from tools.llm.client import LLMClientError, extract_research_keyword

            data = extract_research_keyword(raw)
            if isinstance(data, dict):
                return _normalize_llm_result(data, text=raw)
        except Exception as exc:
            return _rule_fallback_result(raw, reasoning=f"LLM 调用失败: {exc}")
    return _rule_fallback_result(raw, reasoning="规则降级（LLM 未启用）")
