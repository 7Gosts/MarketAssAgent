"""基于会话状态 + 用户文本的确定性意图检测（优先于宽泛正则截流）。

用于「展示偏好 / 含糊行情 / 模拟账户追问 / 追问」等可在不调 LLM 时安全兜底的场景。
追问检测逻辑已内联，不再依赖 intent_followup / followup_resolver。
"""
from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.session_state import SessionState


# ── 展示偏好 ──

_DECIMAL_PAT = re.compile(
    r"(?:精确|保留|只显示|只要|显示|改成|改为|用)?\s*(\d{1,2})\s*位\s*小数"
    r"|(\d{1,2})\s*位\s*小数"
    r"|小数\s*(?:点后\s*)?(\d{1,2})\s*位",
    re.I,
)
_BRIEF_PAT = re.compile(r"(简短点|简单说|一句话|精简|再短一点|短一点|概括一下)", re.I)
_REPEAT_PAT = re.compile(r"(再说一遍|重复一下|刚才那个|上一句|上一次回复)", re.I)

# ── 含糊行情 ──

_VAGUE_MARKET_PAT = re.compile(
    r"(最新行情|看下行情|行情怎么样|现在什么价|当前价格|最新价格|现价多少|报价多少|什么价位|走势怎么样)",
    re.I,
)
_EXPLICIT_SYMBOL_PAT = re.compile(
    r"(BTC|ETH|SOL|AU9999|NVDA|AAPL|_[A-Z]{3,}|USDT)",
    re.I,
)
_ANALYSIS_EXPLICIT_PAT = re.compile(
    r"(分析|结构|fib|威科夫|趋势|模板|触发|止损|入场|k线|行情\s*分析|技术\s*面|多周期|共振)",
    re.I,
)

# ── 模拟账户 ──

_SIM_KW = (
    "余额", "资金", "账户", "持仓", "订单", "成交", "仓位", "模拟账户",
    "挂单", "纸单", "入金", "提金", "权益", "可用",
)

# ── 追问（内联自 intent_followup / followup_resolver）──

_FOLLOWUP_PATTERNS = [
    r"(这个|那个|它|他|她|这只|那只|这|那)\s*(入场|触发|止损|止盈|盈亏比|风险|分析|结构|行情|走势)",
    r"(刚才|上一轮|上次|之前|刚才说的|上次说的)\s*(的|分析|行情|标的|那个|这个)",
    r"(它的|这个的|那个的)\s*(入场|止损|止盈|盈亏比|触发条件)",
    r"(继续|接着|追问|再说|再看)\s*(刚才|之前|上次)",
    r"(补充|展开|详细|深入)\s*(说|讲|解释|分析)",
    r"(还|再|继续)\s*(有|看|问|说)",
    r"(开多|开空|做多|做空|开仓|入场).{0,16}(可以|能不能|行吗|好吗|吗|不)",
    r"(可以|能不能|行吗|推荐).{0,12}(开多|开空|做多|做空|入场|开仓|买|卖)",
    r"(现在|直接).{0,8}(开多|开空|做多|做空|入场|开仓)",
]

_FRESH_ANALYSIS_VIEW_PAT = re.compile(
    r"(看看|看下|看一下|查一下|分析下|分析|走势|行情|结构|k线|短线|超短|日内|现在.*怎么样|当前.*怎么样|帮我看)",
    re.I,
)
_SHORT_INTERVAL_PAT = re.compile(r"短线|超短|日内|scalp", re.I)
_INTERVAL_TOKENS = ("15m", "30m", "1h", "4h", "1d")

_NEW_REQUEST_PATTERNS = [
    r"(看下|看|查下|查|搜下|搜一下|找下|找一下|帮我|请|麻烦)",
    r"(研报|板块|概念|行业|主题|观点|机构)",
    r"(分析|行情|走势|技术)",
]

# ── Provider 模式 ──

_PROVIDER_PATTERNS = [
    (re.compile(r"(gateio|gate\.io|gate\s*io)", re.I), "gateio"),
    (re.compile(r"(tickflow|tick\s*flow)", re.I), "tickflow"),
    (re.compile(r"(goldapi|gold\s*api|黄金api)", re.I), "goldapi"),
]


def _extract_decimal_places(text: str) -> int | None:
    m = _DECIMAL_PAT.search((text or "").strip())
    if not m:
        return None
    for g in m.groups():
        if g is None:
            continue
        try:
            n = int(g)
        except ValueError:
            continue
        if 0 <= n <= 8:
            return n
    return None


def _merge_prefs_from_text(text: str, base: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    p = _extract_decimal_places(text)
    if p is not None:
        out["precision"] = p
    if _BRIEF_PAT.search(text):
        out["compact"] = True
    if _REPEAT_PAT.search(text):
        out["repeat"] = True
    return out


def detect_display_preference(
    text: str,
    session_state: SessionState,
) -> dict[str, Any] | None:
    """若存在上一轮 facts 且本轮为展示类偏好，返回 display_adjustment 路由片段。"""
    raw = (text or "").strip()
    if not raw:
        return None
    if not session_state.last_facts_bundle:
        return None
    if not (
        _extract_decimal_places(raw) is not None
        or _BRIEF_PAT.search(raw)
        or _REPEAT_PAT.search(raw)
    ):
        return None
    prefs = _merge_prefs_from_text(raw, dict(session_state.last_display_preferences or {}))
    return {
        "task_type": "display_adjustment",
        "action": "display_adjustment",
        "response_mode": "quick",
        "scope": None,
        "display_preferences": prefs,
    }


def _find_matching_analysis_entry(text: str, session_state: SessionState) -> dict[str, Any] | None:
    """从 recent_analyses 中按用户文本匹配分析条目。

    匹配逻辑：
    1. 尝试用 catalog.resolve_symbols_from_text() 从文本中提取标的
    2. 命中则匹配包含该标的的最近条目
    3. 未命中则返回最近一条（无显式标的 → 最近一条）
    """
    recent = session_state.recent_analyses
    if not recent:
        return None

    try:
        from app.feishu_asset_catalog import get_catalog_for_repo
        catalog = get_catalog_for_repo()
        resolved = catalog.resolve_symbols_from_text(text, min_score=80)
    except Exception:
        resolved = []

    if resolved:
        target = resolved[0].upper()
        for entry in recent:
            entry_symbols = [s.upper() for s in (entry.get("symbols") or [])]
            entry_symbol = str(entry.get("symbol") or "").upper()
            if target in entry_symbols or target == entry_symbol:
                return entry
        return None  # symbol 不在历史中

    # 无显式 symbol → 最近一条
    return recent[0]


def detect_ambiguous_market_intent(
    text: str,
    session_state: SessionState,
) -> dict[str, Any] | None:
    """无明确标的的行情/报价请求：承接上一轮标的走 quote。"""
    raw = (text or "").strip()
    if not raw:
        return None
    if _EXPLICIT_SYMBOL_PAT.search(raw):
        return None
    if _ANALYSIS_EXPLICIT_PAT.search(raw):
        return None
    if not _VAGUE_MARKET_PAT.search(raw):
        return None
    syms = [str(s).strip().upper() for s in (session_state.last_symbols or []) if s]
    if not syms and session_state.last_symbol:
        syms = [str(session_state.last_symbol).strip().upper()]
    # fallback 到 recent_analyses 第一条
    if not syms and session_state.recent_analyses:
        first = session_state.recent_analyses[0]
        syms = [str(s).strip().upper() for s in (first.get("symbols") or []) if s]
        if not syms and first.get("symbol"):
            syms = [str(first["symbol"]).strip().upper()]
    if not syms:
        return None
    from app.market_data.resolver import build_market_payloads

    interval = session_state.last_interval or "4h"
    payloads = build_market_payloads(
        syms,
        interval=interval,
        question=raw,
        provider_hint=session_state.last_provider,
    )
    return {
        "task_type": "quote",
        "action": "analyze_multi",
        "response_mode": "quick",
        "payloads": payloads,
        "task_plan": {
            "task_type": "quote",
            "response_mode": "quick",
            "symbols": syms,
            "interval": interval,
            "provider": payloads[0]["provider"] if payloads else session_state.last_provider,
            "question": raw,
            "with_research": False,
            "research_keyword": None,
            "user_text": raw,
            "output_refs": dict(session_state.last_output_refs or {}),
            "followup_context": {},
        },
    }


def _scope_from_text(text: str) -> str | None:
    raw = (text or "").strip()
    if re.search(r"成交|fill", raw, re.I):
        return "fills"
    if re.search(r"委托|订单(?!.*成交)", raw, re.I) or "挂单" in raw:
        return "orders"
    if "持仓" in raw or re.search(r"position", raw, re.I):
        return "positions"
    if re.search(r"余额|资金|权益|overview", raw, re.I):
        return "overview"
    if re.search(r"健康|对账", raw, re.I):
        return "health"
    return None


def detect_sim_account_intent(
    text: str,
    session_state: SessionState,
) -> dict[str, Any] | None:
    """模拟账户意图：展示偏好已在外层排除。"""
    raw = (text or "").strip()
    if not raw:
        return None
    if session_state.last_task_type == "sim_account":
        scope = _scope_from_text(raw)
        if scope:
            return {
                "task_type": "sim_account",
                "action": "sim_account",
                "response_mode": "quick",
                "scope": scope,
                "task_plan": {
                    "task_type": "sim_account",
                    "response_mode": "quick",
                    "symbols": list(session_state.last_symbols or []),
                    "interval": session_state.last_interval or "4h",
                    "provider": session_state.last_provider,
                    "question": raw,
                    "with_research": False,
                    "research_keyword": None,
                    "user_text": raw,
                    "output_refs": {},
                    "followup_context": {},
                },
            }
    if any(kw in raw for kw in _SIM_KW):
        return {
            "task_type": "sim_account",
            "action": "sim_account",
            "response_mode": "quick",
            "scope": "overview",
            "task_plan": {
                "task_type": "sim_account",
                "response_mode": "quick",
                "symbols": [],
                "interval": session_state.last_interval or "4h",
                "provider": session_state.last_provider,
                "question": raw,
                "with_research": False,
                "research_keyword": None,
                "user_text": raw,
                "output_refs": {},
                "followup_context": {},
            },
        }
    return None


def _infer_interval_from_text(text: str, *, default: str = "4h") -> str:
    raw_lower = (text or "").strip().lower()
    for iv in _INTERVAL_TOKENS:
        if iv in raw_lower:
            return iv
    if _SHORT_INTERVAL_PAT.search(text or ""):
        return "1h"
    return default


def _resolve_symbols_from_text(text: str) -> list[str]:
    try:
        from app.feishu_asset_catalog import get_catalog_for_repo

        return get_catalog_for_repo().resolve_symbols_from_text(text, min_score=80)
    except Exception:
        return []


def looks_like_fresh_analysis_request(text: str) -> bool:
    """用户显式点名标的并请求查看/分析 → 新分析，不应走 followup。"""
    raw = (text or "").strip()
    if not raw:
        return False
    symbols = _resolve_symbols_from_text(raw)
    if not symbols and not _EXPLICIT_SYMBOL_PAT.search(raw):
        return False
    if _FRESH_ANALYSIS_VIEW_PAT.search(raw):
        return True
    if _ANALYSIS_EXPLICIT_PAT.search(raw):
        return True
    return False


def detect_fresh_analysis_route(
    text: str,
    session_state: SessionState | None = None,
) -> dict[str, Any] | None:
    """显式新分析请求：规则层直接产出 analyze route（优先于 LLM followup 误判）。"""
    raw = (text or "").strip()
    if not looks_like_fresh_analysis_request(raw):
        return None
    symbols = _resolve_symbols_from_text(raw)
    if not symbols and _EXPLICIT_SYMBOL_PAT.search(raw):
        # ETH/BTC 等裸 token 已在 looks_like 中通过 _EXPLICIT_SYMBOL_PAT
        symbols = _resolve_symbols_from_text(raw) or []
    if not symbols:
        return None

    default_iv = "4h"
    if session_state and session_state.last_interval:
        default_iv = str(session_state.last_interval)
    interval = _infer_interval_from_text(raw, default=default_iv)

    try:
        from app.feishu_asset_catalog import get_catalog_for_repo

        catalog = get_catalog_for_repo()
    except Exception:
        catalog = None

    default_provider = None
    if catalog is not None:
        default_provider = catalog.provider_for(symbols[0].upper())
    if not default_provider and session_state:
        default_provider = session_state.last_provider

    from app.market_data.resolver import build_market_payloads
    from app.route_postprocessor import build_task_plan, infer_task_type_from_text, plan_response_mode

    sym_list = [str(s).strip().upper() for s in symbols]
    payloads = build_market_payloads(
        sym_list,
        interval=interval,
        question=raw,
        provider_hint=default_provider,
    )
    tt = infer_task_type_from_text(
        raw,
        legacy_action="analyze_multi",
        symbol_count=len(sym_list),
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
            symbols=sym_list,
            interval=interval,
            provider=payloads[0]["provider"] if payloads else default_provider,
            with_research=False,
            research_keyword=None,
            question=raw,
        ),
    }


def looks_like_followup(text: str) -> bool:
    """判断文本是否为追问模式。"""
    raw = (text or "").strip()
    if not raw:
        return False
    for pat in _FOLLOWUP_PATTERNS:
        if re.search(pat, raw, re.I):
            return True
    for pat in _NEW_REQUEST_PATTERNS:
        if re.search(pat, raw, re.I):
            return False
    return False


def extract_followup_type(text: str) -> str:
    """提取追问类型。"""
    raw = (text or "").strip().lower()
    if re.search(r"(入场|触发|entry|trigger)", raw, re.I):
        return "entry"
    if re.search(r"(止损|stop)", raw, re.I):
        return "stop"
    if re.search(r"(止盈|tp|take\s*profit)", raw, re.I):
        return "tp"
    if re.search(r"(盈亏比|风险收益|risk\s*reward|rr)", raw, re.I):
        return "risk_reward"
    if re.search(r"(开多|开空|做多|做空|开仓|入场)", raw, re.I) and re.search(
        r"(可以|能不能|行吗|好吗|推荐|吗)", raw, re.I
    ):
        return "execution"
    if re.search(r"(状态|持仓|仓位|是否|有没有|当前|现价)", raw, re.I):
        return "status"
    if re.search(r"(为什么|原因|逻辑|理由|怎么|如何)", raw, re.I):
        return "rationale"
    return "general"


def resolve_followup_target(
    text: str,
    session_state: SessionState,
    *,
    prefer_local_facts: bool = True,
) -> dict[str, Any]:
    """解析追问目标：优先从 recent_analyses 匹配，再 fallback 到 last_*。"""
    if not looks_like_followup(text):
        return {"resolved": False, "reason": "非追问模式"}

    # 优先从 recent_analyses 匹配
    matched_entry = _find_matching_analysis_entry(text, session_state)
    if matched_entry:
        followup_type = extract_followup_type(text)
        result: dict[str, Any] = {
            "resolved": True,
            "symbol": matched_entry.get("symbol"),
            "symbols": matched_entry.get("symbols", []),
            "interval": matched_entry.get("interval"),
            "provider": matched_entry.get("provider"),
            "followup_type": followup_type,
            "last_action": matched_entry.get("action"),
            "last_task_type": matched_entry.get("task_type"),
            "last_question": matched_entry.get("question"),
            "match_source": "recent_analyses",
        }
        if prefer_local_facts and matched_entry.get("output_refs"):
            result["output_refs"] = matched_entry["output_refs"]
        return result

    # fallback 到原有 last_* 逻辑
    if session_state.last_action not in {
        "analysis", "research", "quote", "compare", "followup",
        "analyze", "analyze_multi", "sim_account",
    }:
        return {"resolved": False, "reason": "上一轮非分析任务"}

    target_symbol = session_state.last_symbol
    target_symbols = session_state.last_symbols

    if not target_symbol and not target_symbols:
        return {"resolved": False, "reason": "上一轮无标的"}

    followup_type = extract_followup_type(text)

    result: dict[str, Any] = {
        "resolved": True,
        "symbol": target_symbol,
        "symbols": target_symbols,
        "interval": session_state.last_interval,
        "provider": session_state.last_provider,
        "followup_type": followup_type,
        "last_action": session_state.last_action,
        "last_task_type": session_state.last_task_type,
        "last_question": session_state.last_question,
        "match_source": "last_fields",
    }
    if prefer_local_facts and session_state.last_output_refs:
        result["output_refs"] = session_state.last_output_refs
    return result


def detect_followup_route(
    text: str,
    session_state: SessionState,
) -> dict[str, Any] | None:
    """追问检测，产出 planner 兼容的 route dict。"""
    if not looks_like_followup(text):
        return None
    fr = resolve_followup_target(text, session_state)
    if not fr.get("resolved"):
        return None
    symbols = fr.get("symbols") or ([fr["symbol"]] if fr.get("symbol") else [])
    return {
        "action": "followup",
        "task_type": "followup",
        "response_mode": "followup",
        "followup_context": fr,
        "task_plan": {
            "task_type": "followup",
            "response_mode": "followup",
            "symbols": [str(s).upper() for s in symbols if s],
            "interval": fr.get("interval"),
            "provider": fr.get("provider"),
            "question": text,
            "with_research": False,
            "research_keyword": None,
            "user_text": text,
            "output_refs": fr.get("output_refs") or {},
            "followup_context": fr,
        },
    }


# ── 闲聊 / 研报短路（从 planner 迁移）──

_GENERAL_CHAT_PAT = re.compile(
    r"(哈哈|讲个笑话|笑话|黄河之水|高颜值|优秀的人|写首诗|背首诗|作诗)",
    re.I,
)
_MARKET_HINT_PAT = re.compile(
    r"(行情|走势|分析|价格|现价|报价|买入|入场|止损|止盈|结构|k线|股票|黄金|比特币|以太坊)",
    re.I,
)
_RESEARCH_ONLY_PAT = re.compile(
    r"(研报|机构|卖方|首席|观点|怎么看\s*待|配置逻辑|叙事|板块|概念|归属|行业|主题)",
    re.I,
)
_ANALYSIS_HINT_PAT = re.compile(
    r"(分析|结构|fib|威科夫|趋势|模板|触发|止损|入场|k线|行情\s*分析|技术\s*面|多周期|共振)",
    re.I,
)
_RESEARCH_KEYWORD_EXTRACT_PAT_A = re.compile(
    r"^(?:请|帮我|麻烦|顺便|看看|看下|看一下|查下|查一下|搜一下|搜下|了解一下|了解)?(?P<kw>.+?)(?:的)?(?:研报|研报线索|机构观点|观点|配置逻辑)$"
)
_RESEARCH_KEYWORD_EXTRACT_PAT_B = re.compile(
    r"^(?:请|帮我|麻烦|顺便|看看|看下|看一下|查下|查一下|搜一下|搜下|了解一下|了解)?(?P<kw>.+?)(?:板块|概念|行业|归属|主题)$"
)


def _looks_like_general_chat(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if _MARKET_HINT_PAT.search(raw):
        return False
    if _RESEARCH_ONLY_PAT.search(raw) and not _ANALYSIS_HINT_PAT.search(raw):
        return False
    return bool(_GENERAL_CHAT_PAT.search(raw))


def _looks_like_research_only(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_RESEARCH_ONLY_PAT.search(raw)) and not bool(_ANALYSIS_HINT_PAT.search(raw))


def _extract_research_keyword(text: str) -> str | None:
    from app.research_keyword import extract_research_keyword_rule_fallback

    return extract_research_keyword_rule_fallback(text)


def build_research_route(
    text: str,
    *,
    research_keyword: str | None,
    research_keywords: list[str] | None = None,
) -> dict[str, Any]:
    """构造 research route；关键词来自 ContextBuilder 或 legacy 解析。"""
    kws = [str(k).strip() for k in (research_keywords or []) if str(k).strip()]
    kw = str(research_keyword or "").strip() or (kws[0] if kws else None)
    if kw and kw not in kws:
        kws = [kw, *kws]
    return {
        "action": "analyze",
        "payload": {
            "symbol": "",
            "provider": None,
            "interval": "4h",
            "question": text,
            "use_rag": True,
            "use_llm_decision": True,
            "with_research": True,
            "research_keyword": kw,
            "research_keywords": kws,
        },
        "task_type": "research",
        "response_mode": "narrative",
        "task_plan": {
            "task_type": "research",
            "response_mode": "narrative",
            "symbols": [],
            "interval": "4h",
            "provider": None,
            "question": text,
            "with_research": True,
            "research_keyword": kw,
            "research_keywords": kws,
            "user_text": text,
            "output_refs": {},
            "followup_context": {},
        },
    }


def detect_research_only_route(text: str) -> dict[str, Any] | None:
    """Legacy：识别纯研报请求（context 关闭时用 resolve_research_keyword）。"""
    if not _looks_like_research_only(text):
        return None
    from app.agent_runtime_config import load_agent_runtime_config
    from app.research_keyword import resolve_research_keyword

    rt = load_agent_runtime_config()
    result = resolve_research_keyword(text, use_llm=rt.effective_research_keyword_llm())
    if not result.is_research_intent or not result.keywords:
        return None
    return build_research_route(
        text,
        research_keyword=result.keyword,
        research_keywords=list(result.keywords),
    )


def detect_general_chat_route(text: str) -> dict[str, Any] | None:
    """识别明显闲聊（笑话/诗等），返回 chat route，绕过 LLM。"""
    if not _looks_like_general_chat(text):
        return None
    return {
        "action": "chat",
        "chat_mode": "general",
        "task_type": "chat",
        "response_mode": "quick",
        "task_plan": {
            "task_type": "chat",
            "response_mode": "quick",
            "symbols": [],
            "interval": "4h",
            "provider": None,
            "question": text,
            "with_research": False,
            "research_keyword": None,
            "user_text": text,
            "output_refs": {},
            "followup_context": {},
        },
    }


def apply_intent_pipeline(
    text: str,
    session_state: SessionState,
    *,
    skip_research_shortcut: bool = False,
) -> dict[str, Any] | None:
    """按优先级返回完整 route dict；未命中则返回 None 交由 LLM router。"""
    d = detect_display_preference(text, session_state)
    if d:
        return _finalize_display_route(d, text, session_state)

    # 1. 优先尝试补全「待确认意图」（多轮交互）
    d = detect_pending_intent_completion(text, session_state)
    if d:
        return d

    # 2. 闲聊短路（笑话/诗等）——不调 LLM，省 token
    d = detect_general_chat_route(text)
    if d:
        return d

    # 3. 纯研报短路（legacy：context 关闭时）
    if not skip_research_shortcut:
        d = detect_research_only_route(text)
        if d:
            return d

    d = detect_fresh_analysis_route(text, session_state)
    if d:
        return d

    d = detect_ambiguous_market_intent(text, session_state)
    if d:
        return d
    d = detect_followup_route(text, session_state)
    if d:
        return d
    return None


def detect_pending_intent_completion(
    text: str,
    session_state: SessionState,
) -> dict[str, Any] | None:
    """如果 session_state 中有待补全意图，尝试用当前输入补全它。

    三路并行检测：interval / symbol / provider。
    若只补全了部分字段，返回 clarify_partial 让 agent_core 继续追问。
    """
    pending = session_state.pending_intent
    if not pending:
        return None

    raw = (text or "").strip()
    raw_lower = raw.lower()

    # ── 1. Interval 补全（保留原有逻辑）──
    valid_intervals = {"15m", "30m", "1h", "4h", "1d"}
    found_interval = None
    for iv in valid_intervals:
        if iv in raw_lower:
            found_interval = iv
            break

    # ── 2. Symbol 补全（通过 catalog）──
    found_symbols: list[str] | None = None
    found_provider: str | None = None
    pending_symbols = pending.get("symbols") or ([pending["symbol"]] if pending.get("symbol") else None)
    if not pending_symbols:
        try:
            from app.feishu_asset_catalog import get_catalog_for_repo
            catalog = get_catalog_for_repo()
            resolved = catalog.resolve_symbols_from_text(raw, min_score=80)
            if resolved:
                found_symbols = resolved
                # 用 catalog 自动推断 provider
                for sym in resolved:
                    p = catalog.provider_for(sym.upper())
                    if p:
                        found_provider = p
                        break
        except Exception:
            pass

    # ── 3. Provider 补全（通过正则）──
    found_provider_from_regex: str | None = None
    if not found_provider and not pending.get("provider"):
        for pat, provider_name in _PROVIDER_PATTERNS:
            if pat.search(raw):
                found_provider_from_regex = provider_name
                break
        found_provider = found_provider_from_regex

    # ── 判断是否有任何补全 ──
    has_any_fill = found_interval or found_symbols or found_provider
    if not has_any_fill:
        return None

    # ── 构造补全后的 route ──
    new_route = dict(pending)
    if found_interval:
        new_route["interval"] = found_interval
    if found_symbols:
        new_route["symbols"] = found_symbols
        new_route["symbol"] = found_symbols[0]
    if found_provider:
        new_route["provider"] = found_provider

    if "task_plan" in new_route and isinstance(new_route["task_plan"], dict):
        tp = dict(new_route["task_plan"])
        if found_interval:
            tp["interval"] = found_interval
        if found_symbols:
            tp["symbols"] = found_symbols
        if found_provider:
            tp["provider"] = found_provider
        new_route["task_plan"] = tp

    # ── 检查是否仍有缺失字段（部分补全 → clarify_partial）──
    still_missing = []
    if not (new_route.get("symbols") or new_route.get("symbol")):
        still_missing.append("标的")
    if not new_route.get("interval"):
        still_missing.append("周期")

    if still_missing:
        return {
            "action": "clarify_partial",
            "updated_pending": new_route,
            "still_missing": still_missing,
        }

    # 全部补全 → 清理 pending_intent 标记
    new_route["_pending_completed"] = True
    return new_route


def _finalize_display_route(
    fragment: dict[str, Any],
    text: str,
    session_state: SessionState,
) -> dict[str, Any]:
    prefs = dict(fragment.get("display_preferences") or {})
    return {
        "action": "display_adjustment",
        "task_type": "display_adjustment",
        "response_mode": "quick",
        "task_plan": {
            "task_type": "display_adjustment",
            "response_mode": "quick",
            "symbols": list(session_state.last_symbols or []),
            "interval": session_state.last_interval,
            "provider": session_state.last_provider,
            "question": text,
            "with_research": False,
            "research_keyword": None,
            "user_text": text,
            "output_refs": dict(session_state.last_output_refs or {}),
            "followup_context": {},
        },
        "display_preferences": prefs,
    }


def _finalize_sim_route(fragment: dict[str, Any], text: str) -> dict[str, Any]:
    scope = str(fragment.get("scope") or "overview").strip()
    tp = dict(fragment.get("task_plan") or {})
    tp.setdefault("question", text)
    return {
        "action": "sim_account",
        "task_type": "sim_account",
        "response_mode": "quick",
        "scope": scope,
        "task_plan": tp,
    }


def recent_messages_for_router(
    messages: list[BaseMessage],
    *,
    limit_pairs: int = 10,
) -> list[dict[str, str]]:
    """将 LangChain messages 转为 decide_feishu_route 所需的 recent_messages。"""
    out: list[dict[str, str]] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            c = str(m.content or "").strip()
            if c:
                out.append({"role": "user", "text": c})
        elif isinstance(m, AIMessage):
            c = str(m.content or "").strip()
            if c:
                out.append({"role": "assistant", "text": c})
    max_items = max(2, int(limit_pairs) * 2)
    return out[-max_items:]
