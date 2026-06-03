"""路由后处理层：接收 LLM router 原始输出，产出完整 route dict。

从 planner.py 提取的确定性逻辑（不含 LLM 调用），供 PR3 去除 planner 后 agent_core 直调使用。
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from app.agent_schemas import AgentErrorCode, AgentErrorStage, ERROR_CODE_DEFAULTS
from app.feishu_asset_catalog import (
    FeishuAssetCatalog,
    canonical_tradable_symbol,
    canonical_tradable_symbol_list,
    get_catalog_for_repo,
    normalize_provider,
)
from app.intent_detectors import (
    extract_followup_type,
    looks_like_followup,
    resolve_followup_target,
)
from app.session_state import SessionState

TaskType = Literal["chat", "quote", "compare", "analysis", "research", "followup", "sim_account"]
ResponseMode = Literal["quick", "compare", "analysis", "narrative", "followup", "sim_account"]


class AgentRoutingError(Exception):
    """路由阶段的结构化错误异常。"""

    def __init__(
        self,
        message: str,
        *,
        code: AgentErrorCode,
        recoverable: bool = True,
        termination_reason: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        defaults = ERROR_CODE_DEFAULTS.get(code, {})
        default_stage = defaults.get("stage", AgentErrorStage.route)
        self.stage = default_stage if isinstance(default_stage, AgentErrorStage) else AgentErrorStage.route
        self.recoverable = recoverable
        self.termination_reason = termination_reason
        self.context = context or {}

    def to_agent_error(self) -> "app.agent_schemas.AgentError":
        from app.agent_schemas import AgentError
        return AgentError(
            code=self.code,
            stage=self.stage,
            recoverable=self.recoverable,
            message=str(self),
            termination_reason=self.termination_reason,
            context=self.context,
        )


def _repo_root() -> Any:
    from pathlib import Path
    return Path(__file__).resolve().parents[1]


def _feishu_asset_catalog() -> FeishuAssetCatalog:
    return get_catalog_for_repo(_repo_root())


def _router_conversation_context(session_state: SessionState | None) -> dict[str, Any] | None:
    if session_state is None:
        return None
    return {
        "last_action": session_state.last_action,
        "last_task_type": session_state.last_task_type,
        "last_symbols": list(session_state.last_symbols or []),
        "last_display_preferences": dict(session_state.last_display_preferences or {}),
        "last_sim_account_scope": session_state.last_sim_account_scope,
        "history_version": session_state.history_version,
        "compacted_summary": session_state.compacted_summary,
    }


def _ensure_followup_context(
    *,
    text: str,
    routed: dict[str, Any],
    session_state: SessionState | None,
    base_interval: str,
    base_provider: str | None,
    routed_symbol: str,
    routed_interval: str,
) -> tuple[dict[str, Any], str, str]:
    """补齐 followup_context：规则解析优先，LLM 已判 followup 时从 route/session 兜底。"""
    followup_context: dict[str, Any] = {}
    symbol = str(routed_symbol or "").strip().upper()
    interval = str(routed_interval or "").strip().lower()

    if session_state:
        followup_result = resolve_followup_target(text, session_state)
        if followup_result.get("resolved"):
            followup_context = dict(followup_result)
            symbol = str(followup_result.get("symbol") or symbol).strip().upper()
            interval = str(followup_result.get("interval") or interval).strip().lower()

    if not followup_context.get("symbol"):
        if not symbol and session_state:
            if session_state.last_symbol:
                symbol = str(session_state.last_symbol).strip().upper()
            elif session_state.last_symbols:
                symbol = str(session_state.last_symbols[0]).strip().upper()
        if symbol:
            ft = str(routed.get("followup_type") or "").strip() or extract_followup_type(text)
            followup_context = {
                "resolved": True,
                "symbol": symbol,
                "symbols": [symbol],
                "interval": interval or (
                    str(session_state.last_interval).strip().lower()
                    if session_state and session_state.last_interval
                    else base_interval
                ),
                "provider": base_provider or (
                    str(session_state.last_provider).strip().lower()
                    if session_state and session_state.last_provider
                    else None
                ),
                "followup_type": ft,
                "last_action": session_state.last_action if session_state else None,
                "last_task_type": session_state.last_task_type if session_state else None,
                "match_source": "llm_route_session_fallback",
            }
            if session_state and session_state.last_output_refs:
                followup_context["output_refs"] = dict(session_state.last_output_refs)

    if not interval and followup_context.get("interval"):
        interval = str(followup_context["interval"]).strip().lower()
    if not symbol and followup_context.get("symbol"):
        symbol = str(followup_context["symbol"]).strip().upper()

    return followup_context, symbol, interval or base_interval


# ── Interval helpers ──

_EXPLICIT_INTERVAL_PAT = re.compile(
    r"(?<!\w)(15m|30m|1h|4h|1d|1day)(?!\w)|15\s*分钟|30\s*分钟|1\s*小时|4\s*小时|四小时|日线|日k|日K|小时线|分钟线",
    re.I,
)

_INTERVAL_ALIAS = {
    "15分钟": "15m", "15 分钟": "15m",
    "30分钟": "30m", "30 分钟": "30m",
    "1小时": "1h", "1 小时": "1h", "小时线": "1h",
    "4小时": "4h", "4 小时": "4h", "四小时": "4h",
    "日线": "1d", "日k": "1d", "日K": "1d",
}


def _normalize_interval(value: str, default_interval: str) -> str:
    v = (value or "").strip().lower()
    if v in {"15m", "30m", "1h", "4h", "1d"}:
        return v
    return default_interval


def _to_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _has_explicit_interval(text: str) -> bool:
    return bool(_EXPLICIT_INTERVAL_PAT.search((text or "").strip()))


def _extract_explicit_intervals(text: str) -> list[str]:
    """从文本提取所有显式周期，去重保序，返回标准化列表。"""
    raw = (text or "").strip()
    found: list[str] = []
    for m in _EXPLICIT_INTERVAL_PAT.finditer(raw):
        token = m.group(0).strip().lower().replace(" ", "")
        iv = _INTERVAL_ALIAS.get(m.group(0)) if m.group(0) in _INTERVAL_ALIAS else None
        if iv is None:
            if token in {"15m", "30m", "1h", "4h", "1d", "1day"}:
                iv = "1d" if token == "1day" else token
        if iv and iv not in found:
            found.append(iv)
    return found


def _preferred_default_interval(symbol_upper: str, *, fallback_interval: str, catalog: FeishuAssetCatalog) -> str:
    if catalog.provider_for(symbol_upper) == "goldapi":
        return "1d"
    return _normalize_interval(fallback_interval, "4h")


def _resolve_analysis_interval(
    *,
    text: str,
    routed_interval: str,
    symbol_upper: str,
    fallback_interval: str,
    catalog: FeishuAssetCatalog,
) -> str:
    preferred_default = _preferred_default_interval(
        symbol_upper,
        fallback_interval=fallback_interval,
        catalog=catalog,
    )
    if not symbol_upper:
        return _normalize_interval(routed_interval, preferred_default)
    if not _has_explicit_interval(text) and catalog.provider_for(symbol_upper) == "goldapi":
        return preferred_default
    return _normalize_interval(routed_interval, preferred_default)


# ── Semantic pattern detectors ──

_QUOTE_PAT = re.compile(
    r"(现价|多少钱|什么价|价格多少|报价|最新价|当前价|点位多少|多少\s*钱|price\s*now|"
    r"how\s+much|当前\s*报价)",
    re.I,
)
_ANALYSIS_PAT = re.compile(
    r"(分析|结构|fib|威科夫|趋势|模板|触发|止损|入场|k线|行情\s*分析|技术\s*面|多周期|共振)",
    re.I,
)
_COMPARE_PAT = re.compile(
    r"(谁更强|谁更弱|对比|比较|哪个好|哪个更好|强弱|排序|横向|versus|vs\.?|相对强弱|更适合)",
    re.I,
)
_RESEARCH_PAT = re.compile(
    r"(研报|机构|卖方|首席|观点|怎么看\s*待|配置逻辑|叙事|板块|概念|归属|行业|主题)",
    re.I,
)
_GENERAL_CHAT_PAT = re.compile(
    r"(哈哈|讲个笑话|笑话|黄河之水|高颜值|优秀的人|写首诗|背首诗|作诗)",
    re.I,
)


def infer_task_type_from_text(
    text: str,
    *,
    legacy_action: str,
    symbol_count: int,
    with_research: bool,
) -> TaskType:
    raw = (text or "").strip()
    if legacy_action == "chat":
        return "chat"
    if legacy_action == "followup":
        return "followup"
    if legacy_action == "analyze_multi":
        if symbol_count >= 2 and _COMPARE_PAT.search(raw):
            return "compare"
        if _QUOTE_PAT.search(raw) and not _ANALYSIS_PAT.search(raw):
            return "quote"
        return "analysis"
    if legacy_action == "analyze":
        if with_research and _RESEARCH_PAT.search(raw) and not _ANALYSIS_PAT.search(raw):
            return "research"
        if symbol_count >= 2 and _COMPARE_PAT.search(raw):
            return "compare"
        if _QUOTE_PAT.search(raw) and not _ANALYSIS_PAT.search(raw):
            return "quote"
        return "analysis"
    return "analysis"


def plan_response_mode(task_type: TaskType) -> ResponseMode:
    if task_type in {"chat", "quote", "sim_account"}:
        return "quick"
    if task_type == "compare":
        return "compare"
    if task_type == "research":
        return "narrative"
    if task_type == "followup":
        return "followup"
    return "analysis"


def build_task_plan(
    *,
    task_type: TaskType,
    response_mode: ResponseMode,
    text: str,
    symbols: list[str],
    interval: str,
    provider: str | None,
    with_research: bool,
    research_keyword: str | None,
    question: str,
    output_refs: dict[str, str] | None = None,
    followup_context: dict[str, Any] | None = None,
    research_keywords: list[str] | None = None,
) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "task_type": task_type,
        "response_mode": response_mode,
        "symbols": list(symbols),
        "interval": interval,
        "provider": (provider or "").strip().lower() or None,
        "question": question,
        "with_research": bool(with_research),
        "research_keyword": research_keyword,
        "user_text": (text or "").strip(),
        "output_refs": dict(output_refs or {}),
        "followup_context": dict(followup_context or {}),
    }
    if research_keywords:
        plan["research_keywords"] = [str(k).strip() for k in research_keywords if str(k).strip()]
    return plan


def _finalize_analyze_multi_route(
    *,
    raw: str,
    payloads: list[dict[str, Any]],
    symbols: list[str],
    plan_interval: str,
    plan_provider: str | None,
    with_research: bool,
    research_keyword: str | None,
    question: str,
    extra_route_fields: dict[str, Any],
) -> dict[str, Any]:
    from app.market_data.resolver import ensure_payload_providers

    normalized_payloads = ensure_payload_providers(payloads)
    sym_count = max(len(symbols), len(normalized_payloads), 1)
    tt = infer_task_type_from_text(
        raw,
        legacy_action="analyze_multi",
        symbol_count=sym_count,
        with_research=with_research,
    )
    plan_syms = symbols or [str(p.get("symbol") or "") for p in normalized_payloads]
    return {
        "action": "analyze_multi",
        **extra_route_fields,
        "payloads": normalized_payloads,
        "task_type": tt,
        "response_mode": plan_response_mode(tt),
        "task_plan": build_task_plan(
            task_type=tt,
            response_mode=plan_response_mode(tt),
            text=raw,
            symbols=[s for s in plan_syms if s],
            interval=plan_interval,
            provider=plan_provider,
            with_research=with_research,
            research_keyword=research_keyword,
            question=question,
        ),
    }


def _route_plan_steps(routed: dict[str, Any]) -> dict[str, Any]:
    steps = routed.get("plan_steps")
    if not isinstance(steps, list) or not steps:
        return {}
    normalized_steps = [dict(step) for step in steps if isinstance(step, dict)]
    if not normalized_steps:
        return {}
    return {"plan_steps": normalized_steps}


# ── Pre-LLM shortcut detectors ──

def _extract_research_keyword(text: str) -> str | None:
    from app.research_keyword import extract_research_keyword_rule_fallback

    return extract_research_keyword_rule_fallback(text)


def _context_research_keywords(conversation_context: dict[str, Any] | None) -> tuple[str | None, list[str]]:
    if not isinstance(conversation_context, dict):
        return None, []
    ac = conversation_context.get("agent_context")
    if not isinstance(ac, dict):
        return None, []
    cq = ac.get("current_query")
    if not isinstance(cq, dict):
        return None, []
    kws = cq.get("research_keywords") if isinstance(cq.get("research_keywords"), list) else []
    kws = [str(k).strip() for k in kws if str(k).strip()]
    kw = str(cq.get("research_keyword") or "").strip() or (kws[0] if kws else None)
    return kw, kws


def looks_like_research_only_request(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_RESEARCH_PAT.search(raw)) and not _ANALYSIS_PAT.search(raw)


def looks_like_market_request(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(re.search(
        r"(行情|走势|分析|价格|现价|报价|买入|入场|止损|止盈|结构|k线|股票|黄金|比特币|以太坊)", raw, re.I
    ))


def looks_like_general_chat_request(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if looks_like_market_request(raw):
        return False
    if looks_like_research_only_request(raw):
        return False
    if _GENERAL_CHAT_PAT.search(raw):
        return True
    return False


def has_unconfigured_asset_mention(text: str, catalog: FeishuAssetCatalog) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    ascii_tokens = re.findall(r"[A-Za-z]{2,}(?:[_\dA-Za-z]+)?", raw)
    if any(canonical_tradable_symbol(tok, catalog) is None for tok in ascii_tokens):
        return True
    generic_chunks = {
        "看看", "看下", "看一下", "行情", "走势", "分析", "价格", "现价", "报价", "股票", "买入",
        "入场", "止损", "止盈", "结构", "公司", "今天", "最近", "一下", "这个", "这里", "怎么样",
        "虚拟币", "虚拟货币", "加密货币", "币圈", "A股", "美股", "港股", "金价", "现货黄金",
        "加密", "数字货币", "数字资产", "三个", "现在", "目前",
    }
    chinese_chunks = re.findall(r"[一-鿿]{2,8}", raw)
    for chunk in chinese_chunks:
        cleaned = chunk
        for word in generic_chunks:
            cleaned = cleaned.replace(word, "")
        cleaned = cleaned.strip()
        if len(cleaned) < 2:
            continue
        if catalog.resolve_symbols_from_text(cleaned):
            continue
        return True
    return False


def _build_general_chat_route(
    *,
    text: str,
    base_interval: str,
    base_provider: str | None,
) -> dict[str, Any]:
    tt: TaskType = "chat"
    return {
        "action": "chat",
        "chat_mode": "general",
        "task_type": tt,
        "response_mode": plan_response_mode(tt),
        "task_plan": build_task_plan(
            task_type=tt,
            response_mode=plan_response_mode(tt),
            text=text,
            symbols=[],
            interval=base_interval,
            provider=base_provider,
            with_research=False,
            research_keyword=None,
            question=text,
        ),
    }


def _build_autonomous_discovery_route(
    *,
    text: str,
    base_interval: str,
    base_provider: str | None,
    desired_task_type: TaskType,
) -> dict[str, Any]:
    return {
        "action": "discover_analyze",
        "task_type": desired_task_type,
        "response_mode": plan_response_mode(desired_task_type),
        "payload": {
            "query_text": text,
            "interval": base_interval,
            "provider": base_provider,
            "question": text,
            "use_rag": True,
            "use_llm_decision": True,
        },
        "task_plan": build_task_plan(
            task_type=desired_task_type,
            response_mode=plan_response_mode(desired_task_type),
            text=text,
            symbols=[],
            interval=base_interval,
            provider=base_provider,
            with_research=desired_task_type == "research",
            research_keyword=None,
            question=text,
        ),
    }


# ── Core post-processor ──


def _try_fresh_analysis_override(
    raw: str,
    session_state: SessionState | None,
    conversation_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """LLM 判 followup 时：规则 fresh + agent_context 双保险覆盖为 analyze。"""
    from app.intent_detectors import detect_fresh_analysis_route

    fresh_route = detect_fresh_analysis_route(raw, session_state)
    if fresh_route:
        return fresh_route

    if not isinstance(conversation_context, dict):
        return None
    agent_ctx = conversation_context.get("agent_context")
    if not isinstance(agent_ctx, dict):
        return None
    cq = agent_ctx.get("current_query") if isinstance(agent_ctx.get("current_query"), dict) else {}
    intent = str(cq.get("intent_type") or "").strip().lower()
    conf = float(agent_ctx.get("intent_confidence") or conversation_context.get("intent_confidence") or 0.0)
    if intent != "new_analysis" or conf < 0.9:
        return None

    symbols = [str(s).upper() for s in (cq.get("mentioned_symbols") or []) if s]
    if not symbols:
        return None

    interval = str(cq.get("suggested_interval") or (session_state.last_interval if session_state else "") or "4h").strip().lower()
    provider = session_state.last_provider if session_state else None
    try:
        provider = _feishu_asset_catalog().provider_for(symbols[0]) or provider
    except Exception:
        pass

    from app.market_data.resolver import build_market_payloads

    payloads = build_market_payloads(
        symbols,
        interval=interval or "4h",
        question=raw,
        provider_hint=provider,
    )
    tt = infer_task_type_from_text(
        raw,
        legacy_action="analyze_multi",
        symbol_count=len(symbols),
        with_research=False,
    )
    return {
        "action": "analyze_multi",
        "task_type": tt,
        "response_mode": plan_response_mode(tt),
        "payloads": payloads,
        "task_plan": build_task_plan(
            task_type=tt,
            response_mode=plan_response_mode(tt),
            text=raw,
            symbols=symbols,
            interval=interval or "4h",
            provider=payloads[0]["provider"] if payloads else provider,
            with_research=False,
            research_keyword=None,
            question=raw,
        ),
    }


def postprocess_llm_route(
    routed: dict[str, Any],
    *,
    text: str,
    default_symbol: str,
    default_interval: str,
    session_state: SessionState | None = None,
    recent_messages: list[dict[str, str]] | None = None,
    conversation_context: dict[str, Any] | None = None,
    skip_shortcuts: bool = False,
) -> dict[str, Any]:
    """接收 LLM router 原始输出 + 用户文本，产出完整 route dict。

    skip_shortcuts=True 时跳过 pre-LLM 短路（追问/闲聊/研报/未知资产），
    只做 LLM 输出的后处理。适用于 planner wrapper 已在调 LLM 前跑过短路。
    """
    raw = (text or "").strip()
    catalog = _feishu_asset_catalog()
    allowed = catalog.allowed_symbols

    ds = str(default_symbol or "").strip().upper()
    default_canon = canonical_tradable_symbol(ds, catalog)
    if default_canon is None and allowed:
        default_canon = sorted(allowed)[0]
    if default_canon is None:
        default_canon = "BTC_USDT"

    ctx_sym = None
    ctx_interval = default_interval
    ctx_provider = None
    if session_state:
        ctx_sym = session_state.last_symbol
        ctx_interval = session_state.last_interval or default_interval
        ctx_provider = session_state.last_provider

    base_symbol = canonical_tradable_symbol(str(ctx_sym or ""), catalog) or default_canon
    base_interval = _normalize_interval(str(ctx_interval or default_interval), default_interval)
    lp = str(ctx_provider or "").strip().lower()
    base_provider = lp if lp in {"tickflow", "gateio", "goldapi"} else None

    if not raw:
        raise AgentRoutingError(
            "empty user message",
            code=AgentErrorCode.route_empty_message,
            recoverable=False,
            termination_reason="user_input_empty",
        )

    # ── Pre-LLM 短路（skip_shortcuts=True 时跳过）──
    if not skip_shortcuts:
        # 1. 追问检测
        if session_state and looks_like_followup(raw):
            followup_result = resolve_followup_target(raw, session_state)
            if followup_result.get("resolved"):
                tt = "followup"
                return {
                    "action": "followup",
                    "task_type": tt,
                    "response_mode": plan_response_mode(tt),
                    "followup_context": followup_result,
                    "task_plan": build_task_plan(
                        task_type=tt,
                        response_mode=plan_response_mode(tt),
                        text=raw,
                        symbols=followup_result.get("symbols") or [followup_result.get("symbol")] if followup_result.get("symbol") else [],
                        interval=followup_result.get("interval") or base_interval,
                        provider=followup_result.get("provider") or base_provider,
                        with_research=False,
                        research_keyword=None,
                        question=raw,
                        output_refs=followup_result.get("output_refs"),
                        followup_context=followup_result,
                    ),
                }

        # 2. 闲聊短路
        if looks_like_general_chat_request(raw):
            return _build_general_chat_route(
                text=raw,
                base_interval=base_interval,
                base_provider=base_provider,
            )

        # 3. 纯研报短路
        if looks_like_research_only_request(raw):
            from app.agent_runtime_config import load_agent_runtime_config
            from app.research_keyword import resolve_research_keyword

            rt = load_agent_runtime_config()
            ctx_kw, ctx_kws = _context_research_keywords(conversation_context)
            if ctx_kws:
                research_keyword = ctx_kw
                research_keywords = ctx_kws
            else:
                result = resolve_research_keyword(raw, use_llm=rt.effective_research_keyword_llm())
                research_keyword = result.keyword or None
                research_keywords = list(result.keywords)
            tt: TaskType = "research"
            payload: dict[str, Any] = {
                "symbol": "",
                "provider": base_provider,
                "interval": base_interval,
                "question": raw,
                "use_rag": True,
                "use_llm_decision": True,
                "with_research": True,
                "research_keyword": research_keyword,
            }
            if research_keywords:
                payload["research_keywords"] = research_keywords
            return {
                "action": "analyze",
                "payload": payload,
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[],
                interval=base_interval,
                provider=base_provider,
                with_research=True,
                research_keyword=research_keyword,
                research_keywords=research_keywords or None,
                question=raw,
            ),
            }

        pre_resolved_symbols = catalog.resolve_symbols_from_text(raw)

        # 未知资产 → discover_analyze
        looks_like_new_asset = False
        if looks_like_market_request(raw) and not pre_resolved_symbols:
            is_contextual_followup = bool(ctx_sym) and not any(w in raw for w in ["看看", "看下", "看一下", "查一下"])
            if not is_contextual_followup and has_unconfigured_asset_mention(raw, catalog):
                desired_task_type = infer_task_type_from_text(
                    raw,
                    legacy_action="analyze",
                    symbol_count=1,
                    with_research=False,
                )
                return _build_autonomous_discovery_route(
                    text=raw,
                base_interval=base_interval,
                    base_provider=base_provider,
                    desired_task_type=desired_task_type,
                )

    # 4. LLM router 输出的后处理
    extra_route_fields = _route_plan_steps(routed)
    action = str(routed.get("action") or "").strip().lower()
    pre_resolved_symbols = pre_resolved_symbols if not skip_shortcuts else catalog.resolve_symbols_from_text(raw)

    if action == "chat":
        chat_reply = str(routed.get("chat_reply") or "").strip()
        if not chat_reply:
            raise AgentRoutingError(
                "chat route missing chat_reply",
                code=AgentErrorCode.route_missing_chat_reply,
                recoverable=True,
                termination_reason="llm_output_invalid",
                context={"action": action},
            )
        tt = "chat"
        return {
            "action": "chat",
            "chat_reply": chat_reply,
            **extra_route_fields,
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[],
                interval=base_interval,
                provider=base_provider,
                with_research=False,
                research_keyword=None,
                question=chat_reply,
            ),
        }

    if action == "clarify":
        clarify_message = str(routed.get("clarify_message") or "请补充缺失的信息").strip()
        routed_symbol = str(routed.get("symbol") or "").strip()
        tt = "chat"
        return {
            "action": "clarify",
            "clarify_message": clarify_message,
            "symbol": routed_symbol,
            **extra_route_fields,
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[routed_symbol] if routed_symbol else [],
                interval=base_interval,
                provider=base_provider,
                with_research=False,
                research_keyword=None,
                question=clarify_message,
            ),
        }

    if action == "sim_account":
        scope = str(routed.get("scope") or "overview").strip()
        routed_account_id = str(routed.get("account_id") or "").strip()
        routed_symbol = str(routed.get("symbol") or "").strip()
        tt = "sim_account"
        return {
            "action": "sim_account",
            "scope": scope,
            "account_id": routed_account_id or None,
            "symbol": routed_symbol or None,
            **extra_route_fields,
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[routed_symbol] if routed_symbol else [],
                interval=base_interval,
                provider=base_provider,
                with_research=False,
                research_keyword=None,
                question=raw,
            ),
        }

    if action == "followup":
        fresh_route = _try_fresh_analysis_override(raw, session_state, conversation_context)
        if fresh_route:
            return fresh_route

        routed_symbol = str(routed.get("symbol") or "").strip()
        routed_interval = str(routed.get("interval") or "").strip().lower()
        routed_question = str(routed.get("question") or "").strip()
        followup_context, routed_symbol, routed_interval = _ensure_followup_context(
            text=raw,
            routed=routed,
            session_state=session_state,
            base_interval=base_interval,
            base_provider=base_provider,
            routed_symbol=routed_symbol,
            routed_interval=routed_interval,
        )
        if not followup_context.get("symbol"):
            raise AgentRoutingError(
                "followup route missing symbol",
                code=AgentErrorCode.followup_missing_symbol,
                recoverable=True,
                termination_reason="followup_missing_symbol",
            )
        tt = "followup"
        return {
            "action": "followup",
            "followup_context": followup_context,
            **extra_route_fields,
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[routed_symbol] if routed_symbol else [],
                interval=routed_interval or base_interval,
                provider=base_provider,
                with_research=False,
                research_keyword=None,
                question=routed_question or raw,
                followup_context=followup_context,
            ),
        }

    if action == "display_adjustment":
        display_prefs: dict[str, Any] = {}
        if routed.get("precision") is not None:
            display_prefs["decimal_places"] = int(routed["precision"])
        if routed.get("compact") is not None:
            display_prefs["compact"] = bool(routed["compact"])
        if routed.get("repeat") is not None:
            display_prefs["repeat_last"] = bool(routed["repeat"])
        tt = "chat"
        return {
            "action": "display_adjustment",
            "display_preferences": display_prefs,
            **extra_route_fields,
            "task_type": tt,
            "response_mode": "quick",
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode="quick",
                text=raw,
                symbols=list(session_state.last_symbols or []) if session_state else [],
                interval=session_state.last_interval or base_interval if session_state else base_interval,
                provider=base_provider,
                with_research=False,
                research_keyword=None,
                question=raw,
            ),
        }

    if action == "discover_analyze":
        query_text = str(routed.get("query_text") or raw).strip()
        routed_question = str(routed.get("question") or "").strip()
        hint_market = str(routed.get("hint_market") or "").strip()
        desired_task_type = infer_task_type_from_text(
            raw, legacy_action="analyze", symbol_count=1, with_research=False,
        )
        return _build_autonomous_discovery_route(
            text=raw,
            base_interval=base_interval,
            base_provider=base_provider,
            desired_task_type=desired_task_type,
        )

    if action in {"research", "concept_board"}:
        ctx_kw, ctx_kws = _context_research_keywords(conversation_context)
        research_keyword = str(
            routed.get("keyword") or routed.get("research_keyword") or routed.get("symbol") or ctx_kw or ""
        ).strip() or None
        research_keywords = ctx_kws
        raw_kws = routed.get("research_keywords")
        if isinstance(raw_kws, list):
            merged = [str(k).strip() for k in raw_kws if str(k).strip()]
            if merged:
                research_keywords = merged
        if research_keyword and research_keyword not in research_keywords:
            research_keywords = [research_keyword, *research_keywords] if research_keywords else [research_keyword]
        if not research_keyword and research_keywords:
            research_keyword = research_keywords[0]
        routed_symbol = str(routed.get("symbol") or "").strip().upper()
        routed_question = str(routed.get("question") or "").strip()
        tt = "research"
        payload: dict[str, Any] = {
            "symbol": routed_symbol,
            "provider": normalize_provider(routed.get("provider"), symbol_upper=routed_symbol, catalog=catalog),
            "interval": base_interval,
            "question": routed_question or raw,
            "use_rag": True,
            "use_llm_decision": True,
            "with_research": True,
            "research_keyword": research_keyword,
        }
        if research_keywords:
            payload["research_keywords"] = research_keywords
        return {
            "action": "analyze",
            **extra_route_fields,
            "payload": payload,
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[routed_symbol] if routed_symbol else [],
                interval=base_interval,
                provider=normalize_provider(routed.get("provider"), symbol_upper=routed_symbol, catalog=catalog),
                with_research=True,
                research_keyword=research_keyword,
                research_keywords=research_keywords or None,
                question=routed_question or raw,
            ),
        }

    if action in ("analyze", "analyze_multi"):
        from app.market_data.resolver import build_market_payload, normalize_route_payloads

        if action == "analyze_multi":
            pre_payloads = normalize_route_payloads(routed)
            if pre_payloads:
                symbols_from_payloads = [
                    str(p.get("symbol") or "").strip().upper() for p in pre_payloads if p.get("symbol")
                ]
                plan_interval = str(pre_payloads[0].get("interval") or base_interval)
                plan_provider = str(pre_payloads[0].get("provider") or base_provider or "")
                with_research = _to_bool(routed.get("with_research"), default=False)
                global_kw = str(routed.get("research_keyword") or "").strip() or None
                routed_question = str(routed.get("question") or "").strip()
                return _finalize_analyze_multi_route(
                    raw=raw,
                    payloads=pre_payloads,
                    symbols=symbols_from_payloads,
                    plan_interval=plan_interval,
                    plan_provider=plan_provider or None,
                    with_research=with_research,
                    research_keyword=global_kw,
                    question=routed_question or raw,
                    extra_route_fields=extra_route_fields,
                )

        routed_symbols = canonical_tradable_symbol_list(routed.get("symbols"), catalog)
        if not routed_symbols and pre_resolved_symbols:
            routed_symbols = list(pre_resolved_symbols)
        routed_interval = str(routed.get("interval") or "").strip().lower()
        routed_intervals = routed.get("intervals")
        routed_question = str(routed.get("question") or "").strip()
        with_research = _to_bool(routed.get("with_research"), default=False)
        global_kw = str(routed.get("research_keyword") or "").strip() or None

        if not routed_symbols:
            if looks_like_market_request(raw) and has_unconfigured_asset_mention(raw, catalog):
                return _build_autonomous_discovery_route(
                    text=raw,
                    base_interval=base_interval,
                    base_provider=base_provider,
                    desired_task_type=infer_task_type_from_text(
                        raw,
                        legacy_action="analyze",
                        symbol_count=1,
                        with_research=with_research,
                    ),
                )
            raise AgentRoutingError(
                "analyze route missing valid symbols",
                code=AgentErrorCode.route_missing_symbols,
                recoverable=True,
                termination_reason="llm_output_invalid",
                context={"action": action},
            )

        # 多标的
        if len(routed_symbols) > 1:
            payloads: list[dict[str, Any]] = []
            provider_hint = str(routed.get("provider") or base_provider or "")
            for sym in routed_symbols:
                interval_value = _resolve_analysis_interval(
                    text=raw,
                    routed_interval=routed_interval,
                    symbol_upper=sym,
                    fallback_interval=base_interval,
                    catalog=catalog,
                )
                rk = (global_kw or catalog.research_keyword_for(sym) or None) if with_research else None
                payloads.append(
                    build_market_payload(
                        symbol=sym,
                        interval=interval_value,
                        question=routed_question or raw,
                        provider_hint=provider_hint,
                        with_research=with_research,
                        research_keyword=rk,
                    )
                )
            plan_interval = payloads[0]["interval"] if payloads else _normalize_interval(routed_interval, base_interval)
            return _finalize_analyze_multi_route(
                raw=raw,
                payloads=payloads,
                symbols=list(routed_symbols),
                plan_interval=plan_interval,
                plan_provider=payloads[0]["provider"] if payloads else provider_hint,
                with_research=with_research,
                research_keyword=global_kw,
                question=routed_question or raw,
                extra_route_fields=extra_route_fields,
            )

        # 单标的 — 多周期 fan-out
        single = routed_symbols[0]
        provider_hint = str(routed.get("provider") or base_provider or "")
        explicit_intervals = _extract_explicit_intervals(raw)
        if isinstance(routed_intervals, list) and routed_intervals:
            llm_ivs = [str(x).strip().lower() for x in routed_intervals if str(x).strip()]
            for ei in explicit_intervals:
                if ei not in llm_ivs:
                    llm_ivs.append(ei)
            explicit_intervals = llm_ivs
        if len(explicit_intervals) > 1:
            rk = (global_kw or catalog.research_keyword_for(single) or None) if with_research else None
            payloads = [
                build_market_payload(
                    symbol=single,
                    interval=iv,
                    question=routed_question or raw,
                    provider_hint=provider_hint,
                    with_research=with_research,
                    research_keyword=rk,
                )
                for iv in explicit_intervals
            ]
            return _finalize_analyze_multi_route(
                raw=raw,
                payloads=payloads,
                symbols=[single],
                plan_interval=explicit_intervals[0],
                plan_provider=payloads[0]["provider"] if payloads else provider_hint,
                with_research=with_research,
                research_keyword=rk,
                question=routed_question or raw,
                extra_route_fields=extra_route_fields,
            )

        # 单标的单周期
        iv = _resolve_analysis_interval(
            text=raw,
            routed_interval=routed_interval,
            symbol_upper=single,
            fallback_interval=base_interval,
            catalog=catalog,
        )
        rk = (global_kw or catalog.research_keyword_for(single) or None) if with_research else None
        payload_one = build_market_payload(
            symbol=single,
            interval=iv,
            question=routed_question or raw,
            provider_hint=provider_hint,
            with_research=with_research,
            research_keyword=rk,
        )
        return _finalize_analyze_multi_route(
            raw=raw,
            payloads=[payload_one],
            symbols=[single],
            plan_interval=iv,
            plan_provider=payload_one["provider"],
            with_research=with_research,
            research_keyword=rk,
            question=routed_question or raw,
            extra_route_fields=extra_route_fields,
        )

    raise AgentRoutingError(
        f"unknown route action: {action or '<empty>'}",
        code=AgentErrorCode.route_unknown_action,
        recoverable=False,
        termination_reason="invalid_route_output",
        context={"action": action},
    )


def log_routed_preview(routed: dict[str, Any], *, logger_label: str = "[PostProcessor]") -> None:
    import os
    from loguru import logger

    if os.getenv("FEISHU_ROUTE_DEBUG", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    if not isinstance(routed, dict):
        return
    keys = (
        "action", "task_type", "plan_steps", "symbol", "symbols", "interval",
        "question", "provider", "with_research", "research_keyword",
        "followup_context", "output_refs",
    )
    preview = {k: routed.get(k) for k in keys if k in routed}
    line = json.dumps(preview, ensure_ascii=False)
    logger.debug("{} route_debug {}", logger_label, line[:600])