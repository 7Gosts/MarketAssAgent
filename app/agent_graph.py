"""统一聊天编排 StateGraph：capability → compose → session 更新 → 可选压缩。

所有 task_type 的能力执行在此完成，不再委托 agent_facade。
"""
from __future__ import annotations

import contextvars
import json
import os
import threading
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from loguru import logger

from app.agent_state import ChatPostRouteState
from app.agent_schemas import AgentRequest, AgentResponse, DEFAULT_FALLBACK_MESSAGE
from app.capabilities.compare_facts import run_compare_facts_bundle
from app.capabilities.quote_facts import run_quote_facts_bundle
from app.capabilities.research_facts import build_research_facts_bundle
from app.executors.facts_bundle import build_evidence_source, merge_facts_bundle
from app.market_data.resolver import normalize_route_payloads
from app.market_data.snapshots import (
    fetch_market_snapshots,
    merge_snapshot_facts_bundle,
    snapshot_output_refs,
)
from app.discovery_flow import run_asset_resolution_pipeline
from app.session_state import SessionState, SessionStateStore
from app.session_manager import SessionManager
from app.writer import safe_grounded_write
from tools.llm.client import LLMClientError, generate_context_reply, generate_session_compact_summary


_GRAPH_LOCK = threading.Lock()
_COMPILED_GRAPH: Any = None

_CTX: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "chat_post_route_ctx", default=None
)

_COMPACT_RECENT_THRESHOLD = 24


def unified_chat_agent_enabled() -> bool:
    """默认开启；`AGENT_UNIFIED_GRAPH=0` 关闭并回退旧路径。"""
    v = os.getenv("AGENT_UNIFIED_GRAPH", "1").strip().lower()
    return v not in {"0", "false", "no", "off"}


def _pipeline_trace_enabled() -> bool:
    v = (
        os.getenv("AGENT_PIPELINE_LOG", "").strip().lower()
        or os.getenv("FEISHU_PIPELINE_LOG", "").strip().lower()
    )
    return v in {"1", "true", "yes", "on"}


def _graph_pipeline(msg: str) -> None:
    if not _pipeline_trace_enabled():
        return
    logger.info("[AgentGraph] {}", msg)


def _ctx() -> dict[str, Any]:
    c = _CTX.get()
    if not c:
        raise RuntimeError("chat graph runtime context not set")
    return c


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


# ============ Fallback formatters (clean, no rigid "━━"/"【结论】") ============

def _minimal_sim_fallback(facts_bundle: dict[str, Any], *, display_preferences: dict[str, Any]) -> str:
    raw = facts_bundle.get("sim_account_facts") if isinstance(facts_bundle.get("sim_account_facts"), dict) else {}
    metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    prec = display_preferences.get("precision")
    if not isinstance(prec, int) or prec < 0:
        prec = 4

    def _fmt_num(x: Any) -> str:
        try:
            f = float(x)
        except (TypeError, ValueError):
            return str(x)
        s = f"{f:.{prec}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s

    lines: list[str] = ["【模拟账户摘要】"]
    if isinstance(metrics, dict) and metrics:
        for aid, m in metrics.items():
            if not isinstance(m, dict):
                lines.append(f" · {aid}: {m}")
                continue
            bal = m.get("balance")
            eq = m.get("equity")
            av = m.get("available")
            lines.append(
                f" · {aid}: 余额 {_fmt_num(bal)}, 可用 {_fmt_num(av)}, 权益 {_fmt_num(eq)}"
            )
    summary = str(raw.get("summary") or "").strip()
    if summary and not metrics:
        lines.append(summary[:2000])
    lines.append("仅供技术分析与程序化演示，不构成投资建议。")
    return "\n".join(lines)


def _fallback_quote(facts: dict[str, Any]) -> str:
    """quote 兜底：简洁价格 + 倾向。"""
    lines: list[str] = ["【价格快照】"]
    for it in (facts.get("items") or []):
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "")
        lp = it.get("last_price")
        tr = str(it.get("trend") or "").strip()
        iv = str(it.get("interval") or "").strip()
        bits = [f"{sym} {iv}".strip()]
        if lp is not None:
            bits.append(f"最新约 {lp}")
        if tr:
            bits.append(f"倾向：{tr}")
        lines.append(" · " + "，".join(x for x in bits if x))
    lines.append("仅供技术分析与程序化演示，不构成投资建议。")
    return "\n".join(lines)


def _fallback_compare(facts: dict[str, Any]) -> str:
    """compare 兜底：横向对比行。"""
    lines: list[str] = ["【横向对比】"]
    for row in (facts.get("rows") or []):
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "")
        lp = row.get("last_price")
        tr = str(row.get("trend") or "").strip()
        seg = f" · {sym}："
        if lp is not None:
            seg += f"价约 {lp}；"
        if tr:
            seg += f"综合倾向 {tr}"
        lines.append(seg.rstrip("；"))
    lines.append("仅供技术分析与程序化演示，不构成投资建议。")
    return "\n".join(lines)


def _fallback_research(facts: dict[str, Any]) -> str:
    """research 兜底：研报线索。"""
    if not facts.get("ok"):
        return f"研报检索暂不可用：{facts.get('error') or 'unknown'}。仅供技术分析与程序化演示。"
    lines: list[str] = [f"【研报线索】关键词：{facts.get('keyword') or ''}"]
    for it in (facts.get("items") or []):
        if not isinstance(it, dict):
            continue
        t = str(it.get("title") or "").strip()
        org = str(it.get("org_name") or "").strip()
        if t:
            lines.append(f" · {t}" + (f"（{org}）" if org else ""))
    lines.append("以上为检索摘要线索，非官方观点背书。仅供技术分析与程序化演示。")
    return "\n".join(lines)


def _extract_followup_item(facts: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Any]:
    ov = facts.get("overview")
    if not isinstance(ov, dict):
        return {}, {}, {}, None
    items = ov.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return {}, {}, {}, None
    it = items[0]
    stats = it.get("stats") if isinstance(it.get("stats"), dict) else {}
    wy = it.get("wyckoff_123_v1") if isinstance(it.get("wyckoff_123_v1"), dict) else {}
    sel = wy.get("selected_setup") if isinstance(wy.get("selected_setup"), dict) else {}
    triggered = sel.get("triggered")
    return it, stats, sel, triggered


def _fallback_followup_execution(facts: dict[str, Any]) -> str:
    """execution 追问兜底：四段短答。"""
    it, stats, sel, triggered = _extract_followup_item(facts)
    if not it:
        return (
            "**结论**：暂无法判断，需先完成分析\n"
            "**理由**：缺少结构化技术快照\n"
            "**风险**：无有效 entry/stop 参考\n"
            "**建议**：请先发起标的分析\n"
            "仅供技术分析与程序化演示，不构成投资建议。"
        )
    trend = str(stats.get("trend") or "未知").strip()
    triggered_text = "待触发" if triggered is False else ("已触发" if triggered is True else "未知")
    if triggered is True:
        conclusion = "结构已触发，可考虑按计划执行"
        suggest = "小仓试探并严格设止损"
    elif triggered is False:
        conclusion = "暂不建议直接开仓，需等待触发"
        suggest = "先观察，待触发再评估"
    else:
        conclusion = "方向未明，需等待更清晰信号"
        suggest = "保持观察，勿强行开仓"
    risk = "止损位未明确" if not sel.get("stop") else f"跌破止损 {sel.get('stop')} 则结构失效"
    reason_bits = [f"趋势 {trend}", f"触发 {triggered_text}"]
    if sel.get("entry"):
        reason_bits.append(f"入场 {sel.get('entry')}")
    lines = [
        f"**结论**：{conclusion}",
        f"**理由**：{'；'.join(reason_bits)}",
        f"**风险**：{risk}",
        f"**建议**：{suggest}",
        "仅供技术分析与程序化演示，不构成投资建议。",
    ]
    return "\n".join(lines)


def _fallback_followup(facts: dict[str, Any], *, followup_type: str | None = None) -> str:
    """followup 兜底：追问回复。"""
    ft = str(followup_type or "").strip().lower()
    if ft == "execution":
        return _fallback_followup_execution(facts)

    lines: list[str] = ["【追问回复】"]
    it, stats, sel, triggered = _extract_followup_item(facts)
    if it:
        triggered_text = "待触发" if triggered is False else ("已触发" if triggered is True else "未知")
        lines.append(f" · 标的：{it.get('symbol') or '?'} {it.get('interval') or ''}")
        lines.append(f" · 趋势：{stats.get('trend') or '未知'}")
        lines.append(f" · 触发状态：{triggered_text}")
        if sel.get("entry"):
            lines.append(f" · 入场：{sel.get('entry')}")
        if sel.get("stop"):
            lines.append(f" · 止损：{sel.get('stop')}")
        if sel.get("tp1"):
            lines.append(f" · 止盈1：{sel.get('tp1')}")
        if sel.get("tp2"):
            lines.append(f" · 止盈2：{sel.get('tp2')}")
    else:
        lines.append(" · 无有效分析产物")
    lines.append("仅供技术分析与程序化演示，不构成投资建议。")
    return "\n".join(lines)


def _fallback_analysis(narrative_facts: dict[str, Any]) -> str:
    """analysis 兜底：从 narrative facts 产出简洁分析摘要（不使用━━/【结论】等僵硬格式）。"""
    lines: list[str] = []
    sym = str(narrative_facts.get("symbol") or "UNKNOWN")
    interval = str(narrative_facts.get("interval") or "N/A")
    trend = str(narrative_facts.get("trend") or "").strip()
    last_price = narrative_facts.get("last_price")
    regime = str(narrative_facts.get("regime_label") or "").strip()

    header_bits = [sym, interval]
    if trend:
        header_bits.append(f"倾向{trend}")
    if regime:
        header_bits.append(regime)
    if last_price is not None:
        header_bits.append(f"约{last_price}")
    lines.append(" · " + "，".join(x for x in header_bits if x))

    ft = narrative_facts.get("fixed_template") if isinstance(narrative_facts.get("fixed_template"), dict) else {}
    if ft:
        for key in ("综合倾向", "触发条件", "失效条件"):
            val = str(ft.get(key) or "").strip()
            if val and val != "待补充":
                lines.append(f" · {key}：{val}")

    wy = narrative_facts.get("wyckoff_123_v1") if isinstance(narrative_facts.get("wyckoff_123_v1"), dict) else {}
    sel = wy.get("selected_setup") if isinstance(wy.get("selected_setup"), dict) else None
    if sel:
        triggered = sel.get("triggered")
        triggered_text = "待触发" if triggered is False else ("已触发" if triggered is True else "未知")
        lines.append(f" · 威科夫123 {sel.get('side', '?')}：{triggered_text}")

    ms = narrative_facts.get("ma_snapshot") if isinstance(narrative_facts.get("ma_snapshot"), dict) else {}
    if ms:
        ma_bits = []
        if ms.get("sma20") is not None:
            ma_bits.append(f"SMA20={_fmt_px(ms['sma20'])}")
        if ms.get("sma60") is not None:
            ma_bits.append(f"SMA60={_fmt_px(ms['sma60'])}")
        if ma_bits:
            lines.append(" · " + "，".join(ma_bits))

    lines.append("仅供技术分析与程序化演示，不构成投资建议。")
    return "\n".join(lines)


def _fmt_px(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(x) >= 1000:
        return f"{x:,.2f}"
    if abs(x) >= 1:
        return f"{x:.2f}"
    return f"{x:.4f}"


# ============ Capability helpers ============


def _capability_resolve_and_analyze(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    result = run_asset_resolution_pipeline(
        repo_root=_repo_root(),
        route=route,
        request_text=request.text,
        request_context=request.context if isinstance(request.context, dict) else None,
        session_state=_ctx().get("session_state"),
    )

    if result.get("kind") == "chat_fallback":
        message = str(result.get("message") or "").strip()
        return {"facts_bundle": {}, "skip_compose_llm": True, "reply_text": message}

    fb = result.get("value") if isinstance(result.get("value"), dict) else {}
    clean = {k: v for k, v in fb.items() if not str(k).startswith("_")}
    out: dict[str, Any] = {"facts_bundle": clean, "skip_compose_llm": False}
    if fb.get("_output_refs"):
        out["_output_refs"] = fb["_output_refs"]
    if fb.get("_narrative_facts"):
        out["_narrative_facts"] = fb["_narrative_facts"]
    return out


def _capability_sim(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    from app.capabilities import view_sim_account_state

    scope = str(route.get("scope") or "overview").strip()
    account_id = route.get("account_id") or None
    symbol = route.get("symbol") or None
    cap = view_sim_account_state(scope=scope, account_id=account_id, symbol=symbol)
    cap_d = cap.to_dict()
    tp = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
    symbols = list(tp.get("symbols") or [])
    if not symbols and symbol:
        symbols = [str(symbol).strip().upper()]
    fb = merge_facts_bundle(
        task_type="sim_account",
        response_mode="quick",
        user_question=request.text,
        symbols=symbols,
        sim_account_facts=cap_d,
        evidence_sources=[
            build_evidence_source(
                source_path="postgres:sim_account",
                source_type="journal",
                symbol=symbols[0] if symbols else None,
            )
        ],
        risk_flags=["normal"],
        trace={"executors": ["sim_account_capability"], "scope": scope},
    )
    return fb


def _capability_quote(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    from app.market_data.resolver import build_market_payload, normalize_route_payloads

    payloads = normalize_route_payloads(route)
    if not payloads:
        tp = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
        pay = route.get("payload") if isinstance(route.get("payload"), dict) else {}
        sym = str(pay.get("symbol") or (tp.get("symbols") or [""])[0] or "").strip()
        if not sym:
            raise RuntimeError("quote capability missing symbol")
        payloads = [
            build_market_payload(
                symbol=sym,
                interval=str(pay.get("interval") or tp.get("interval") or "4h"),
                question=str(pay.get("question") or tp.get("question") or request.text),
                provider_hint=str(pay.get("provider") or tp.get("provider") or "gateio"),
                use_rag=bool(pay.get("use_rag", True)),
            )
        ]
    return run_quote_facts_bundle(
        repo_root=_repo_root(),
        user_question=request.text,
        payloads=payloads,
    )


def _capability_research(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    pay = dict(route.get("payload") or {})
    tp = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
    if not pay.get("research_keyword") and tp.get("research_keyword"):
        pay["research_keyword"] = tp.get("research_keyword")
    if not pay.get("research_keywords") and tp.get("research_keywords"):
        pay["research_keywords"] = tp.get("research_keywords")
    if not pay.get("symbol") and tp.get("symbols"):
        syms = tp.get("symbols") or []
        if syms:
            pay["symbol"] = syms[0]
    fb, _kw = build_research_facts_bundle(
        rag_index=_ctx()["rag_index"],
        user_question=request.text,
        payload=pay,
    )
    return fb


def _capability_compare(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    payloads = normalize_route_payloads(route)
    if not payloads:
        raise RuntimeError("compare capability missing payloads")
    return run_compare_facts_bundle(
        repo_root=_repo_root(),
        user_question=request.text,
        payloads=payloads,
    )


def _capability_multi_analysis(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    """批量行情分析：单标的 = payloads 宽度 1。"""
    payloads = normalize_route_payloads(route)
    if not payloads:
        raise RuntimeError("multi_analysis capability missing payloads")

    symbols = [str(p.get("symbol") or "") for p in payloads]
    repo_root = _repo_root()
    cf = fetch_market_snapshots(repo_root=repo_root, payloads=payloads)
    fb = merge_snapshot_facts_bundle(
        compare_result=cf,
        user_question=request.text,
        symbols=symbols,
    )
    output_refs = snapshot_output_refs(cf)
    if output_refs:
        return {**fb, "_output_refs": output_refs}
    return fb


def _capability_context_reply(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    """统一上下文回复：替代 followup/chat/display_adjustment。
    LLM 从对话历史理解上下文，自行判断回复类型，不读磁盘产物。"""
    session_mgr: SessionManager | None = _ctx().get("session_manager")
    session_state: SessionState | None = _ctx().get("session_state")

    conversation_history: list[dict[str, str]] = []
    if session_mgr:
        conversation_history = session_mgr.get_recent_messages(request.session_id, limit=8)

    session_context = {
        "last_action": getattr(session_state, "last_action", None),
        "last_task_type": getattr(session_state, "last_task_type", None),
        "last_symbols": list(getattr(session_state, "last_symbols", []) or []),
        "last_interval": getattr(session_state, "last_interval", None),
    }

    display_preferences = route.get("display_preferences")
    if display_preferences:
        session_context["display_preferences"] = display_preferences

    try:
        result = generate_context_reply(
            user_question=request.text,
            conversation_history=conversation_history,
            session_context=session_context,
        )
    except LLMClientError:
        result = {"text": "我还在，你可以继续提问，或者让我看看某只股票的行情。"}

    fb = merge_facts_bundle(
        task_type="context_reply",
        response_mode="quick",
        user_question=request.text,
        symbols=list(getattr(session_state, "last_symbols", []) or []),
        conversation_history=conversation_history,
        evidence_sources=[{
            "source_path": "conversation_history",
            "source_type": "memory",
        }],
        trace={"executors": ["context_reply"]},
    )
    return {"facts_bundle": fb, "skip_compose_llm": True, "reply_text": result.get("text", "")}


# ============ Graph nodes ============

def capability_node(state: ChatPostRouteState) -> dict[str, Any]:
    rt = _ctx()
    request: AgentRequest = rt["request"]
    session_state: SessionState = rt["session_state"]
    route = state.get("route") or {}
    tt = str(route.get("task_type") or "analysis").strip().lower()
    action = str(route.get("action") or "").strip().lower()
    _graph_pipeline(f"node=capability task_type={tt!s} action={action!s}")

    if tt in {"chat", "followup", "display_adjustment"} or action in {"chat", "followup", "context_reply"}:
        return _capability_context_reply(route, request)

    if action == "discover_analyze":
        raw = _capability_resolve_and_analyze(route, request)
        if raw.get("skip_compose_llm"):
            return raw
        clean = {k: v for k, v in raw.items() if not str(k).startswith("_")}
        out: dict[str, Any] = {"facts_bundle": clean, "skip_compose_llm": False}
        if raw.get("_output_refs"):
            out["_output_refs"] = raw["_output_refs"]
        if raw.get("_narrative_facts"):
            out["_narrative_facts"] = raw["_narrative_facts"]
        return out

    if tt == "sim_account":
        fb = _capability_sim(route, request)
        return {"facts_bundle": fb, "skip_compose_llm": False}

    if tt == "quote" and action in {"analyze", "analyze_multi"}:
        fb = _capability_quote(route, request)
        return {"facts_bundle": fb, "skip_compose_llm": False}

    if tt == "research":
        fb = _capability_research(route, request)
        return {"facts_bundle": fb, "skip_compose_llm": False}

    if tt == "compare":
        fb = _capability_compare(route, request)
        return {"facts_bundle": fb, "skip_compose_llm": False}

    if action in ("analyze_multi", "analyze"):
        payloads = normalize_route_payloads(route)
        if action == "analyze" and not payloads:
            raise RuntimeError(f"capability analyze route missing payloads task_type={tt!s}")
        _graph_pipeline(
            f"capability market_analysis payloads={len(payloads)} "
            f"symbols={[str(p.get('symbol') or '') for p in payloads]}"
        )
        fb = _capability_multi_analysis(
            {**route, "action": "analyze_multi", "payloads": payloads},
            request,
        )
        clean = {k: v for k, v in fb.items() if not str(k).startswith("_")}
        out: dict[str, Any] = {"facts_bundle": clean, "skip_compose_llm": False}
        if fb.get("_output_refs"):
            out["_output_refs"] = fb["_output_refs"]
        return out

    raise RuntimeError(f"capability unsupported route action={action!s} task_type={tt!s}")


def compose_node(state: ChatPostRouteState) -> dict[str, Any]:
    rt = _ctx()
    request: AgentRequest = rt["request"]
    route = state.get("route") or {}
    tt = str(route.get("task_type") or "analysis").strip().lower()
    rm = str(route.get("response_mode") or "analysis").strip().lower()
    _graph_pipeline(
        f"node=compose task_type={tt!s} response_mode={rm!s} "
        f"skip_llm={bool(state.get('skip_compose_llm'))}"
    )

    if state.get("skip_compose_llm") and state.get("reply_text"):
        return {"reply_text": str(state.get("reply_text") or "")}

    fb = state.get("facts_bundle") if isinstance(state.get("facts_bundle"), dict) else {}

    # Merge display_preferences
    writer_tt = tt
    writer_rm = rm
    if tt == "display_adjustment":
        if isinstance(fb.get("task_type"), str) and fb.get("task_type"):
            writer_tt = str(fb.get("task_type"))
        if isinstance(fb.get("response_mode"), str) and fb.get("response_mode"):
            writer_rm = str(fb.get("response_mode"))

    prefs: dict[str, Any] = {}
    if isinstance(route.get("display_preferences"), dict):
        prefs = dict(route["display_preferences"])
    if isinstance(state.get("display_preferences"), dict):
        prefs = {**prefs, **state["display_preferences"]}
    if tt == "sim_account":
        simf = fb.get("sim_account_facts") if isinstance(fb.get("sim_account_facts"), dict) else {}
        dd = simf.get("default_display_prefs") if isinstance(simf.get("default_display_prefs"), dict) else {}
        if dd:
            prefs = {**dd, **prefs}

    followup_type = ""
    if writer_tt == "followup":
        followup_type = str(fb.get("followup_type") or "").strip().lower()
        if not followup_type:
            fc = route.get("followup_context") if isinstance(route.get("followup_context"), dict) else {}
            followup_type = str(fc.get("followup_type") or "").strip().lower()
        if followup_type == "execution":
            writer_rm = "followup_execution"
        elif writer_rm == "followup" or not writer_rm:
            writer_rm = "followup"
        _graph_pipeline(
            f"node=compose followup_type={followup_type!s} writer_rm={writer_rm!s}"
        )

    out = safe_grounded_write(
        facts_bundle=fb,
        user_question=request.text,
        task_type=writer_tt,
        response_mode=writer_rm,
        display_preferences=prefs or None,
    )
    if out and str(out.get("text") or "").strip():
        return {"reply_text": str(out["text"]).strip()}

    # Fallback chain: grounded writer failed → task-specific minimal fallback
    fallback_text = _compose_fallback(fb, state, tt, rm, request.text)
    return {"reply_text": fallback_text}


def _compose_fallback(
    fb: dict[str, Any],
    state: ChatPostRouteState,
    tt: str,
    rm: str,
    user_question: str,
) -> str:
    """Task-specific fallback when grounded writer is unavailable."""
    if tt == "sim_account" or tt == "display_adjustment":
        prefs: dict[str, Any] = {}
        route = state.get("route") or {}
        if isinstance(route.get("display_preferences"), dict):
            prefs = dict(route["display_preferences"])
        if isinstance(state.get("display_preferences"), dict):
            prefs = {**prefs, **state["display_preferences"]}
        return _minimal_sim_fallback(fb, display_preferences=prefs)

    if tt == "chat":
        return "我这次没有稳定生成回复。你可以补一句标的/周期，或让我重新分析。"

    # Extract raw facts for fallback
    market_facts = fb.get("market_facts") if isinstance(fb.get("market_facts"), dict) else {}
    research_facts = fb.get("research_facts") if isinstance(fb.get("research_facts"), dict) else {}
    followup_facts = fb.get("followup_facts") if isinstance(fb.get("followup_facts"), dict) else {}
    compare_facts = fb.get("compare_facts") if isinstance(fb.get("compare_facts"), dict) else {}
    narrative_facts = state.get("_narrative_facts") if isinstance(state.get("_narrative_facts"), dict) else {}
    if not narrative_facts:
        analysis_facts = market_facts.get("analysis_facts") if isinstance(market_facts.get("analysis_facts"), dict) else {}
        narrative_facts = analysis_facts

    if tt == "quote":
        raw = market_facts
        items = raw.get("items") if isinstance(raw.get("items"), list) else None
        if items:
            return _fallback_quote({"items": items})
        return DEFAULT_FALLBACK_MESSAGE

    if tt == "compare":
        rows = compare_facts.get("rows") if isinstance(compare_facts.get("rows"), list) else None
        if rows:
            return _fallback_compare({"rows": rows})
        raw_rows = market_facts.get("compare_summary", {}).get("rows") if isinstance(market_facts.get("compare_summary"), dict) else None
        if raw_rows:
            return _fallback_compare({"rows": raw_rows})
        return DEFAULT_FALLBACK_MESSAGE

    if tt == "research":
        return _fallback_research(research_facts)

    if tt == "followup":
        ft = str(fb.get("followup_type") or "").strip().lower()
        if not ft:
            route = state.get("route") or {}
            fc = route.get("followup_context") if isinstance(route.get("followup_context"), dict) else {}
            ft = str(fc.get("followup_type") or "").strip().lower()
        return _fallback_followup(followup_facts, followup_type=ft or None)

    # analysis: use narrative_facts
    if narrative_facts:
        return _fallback_analysis(narrative_facts)

    return DEFAULT_FALLBACK_MESSAGE


def update_session_node(state: ChatPostRouteState) -> dict[str, Any]:
    rt = _ctx()
    request: AgentRequest = rt["request"]
    store: SessionStateStore = rt["session_store"]
    route = state.get("route") or {}
    action = str(route.get("action") or route.get("task_type") or "chat").strip().lower()
    tt = str(route.get("task_type") or "chat").strip().lower()
    tp = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
    symbols = list(tp.get("symbols") or [])
    _graph_pipeline(
        f"node=update_session action={action!s} "
        f"symbols={symbols!s} interval={tp.get('interval')!s}"
    )
    st = store.get(request.session_id)
    fb = state.get("facts_bundle") if isinstance(state.get("facts_bundle"), dict) else {}
    try:
        st.last_facts_bundle = json.loads(json.dumps(fb, ensure_ascii=False)) if fb else {}
    except (TypeError, ValueError):
        st.last_facts_bundle = {}
    prefs: dict[str, Any] = {}
    if isinstance(route.get("display_preferences"), dict):
        prefs = dict(route["display_preferences"])
    if isinstance(state.get("display_preferences"), dict):
        prefs = {**dict(st.last_display_preferences or {}), **state["display_preferences"], **prefs}
    st.last_display_preferences = prefs
    if tt == "sim_account":
        st.last_sim_account_scope = str(route.get("scope") or tp.get("scope") or "overview").strip()

    # For analysis, save output_refs from capability node
    output_refs = dict(tp.get("output_refs") or {})
    if isinstance(state.get("_output_refs"), dict):
        output_refs = {**output_refs, **state["_output_refs"]}

    st.history_version = int(st.history_version or 0) + 1
    store.update_from_route(
        request.session_id,
        action=action,
        task_type=tt,
        symbol=symbols[0] if symbols else None,
        symbols=symbols,
        interval=str(tp.get("interval") or "").strip() or None,
        provider=str(tp.get("provider") or "").strip() or None,
        question=str(tp.get("question") or request.text).strip(),
        output_refs=output_refs,
    )
    store.update(st)
    return {"history_version": st.history_version}


def compact_node(state: ChatPostRouteState) -> dict[str, Any]:
    rt = _ctx()
    request: AgentRequest = rt["request"]
    store: SessionStateStore = rt["session_store"]
    session_mgr: SessionManager | None = rt.get("session_manager")
    recent = request.context.get("recent_messages")
    n = len(recent) if isinstance(recent, list) else 0
    threshold = _COMPACT_RECENT_THRESHOLD
    if session_mgr is not None:
        threshold = int(session_mgr.config.compact_threshold or _COMPACT_RECENT_THRESHOLD)
    _graph_pipeline(f"node=compact recent_messages={n} threshold={threshold}")
    if n < threshold:
        return {}
    if session_mgr is None or not session_mgr.config.compact_enabled:
        st = store.get(request.session_id)
        line = f"[auto-compact] recent_messages~{n} 条；history_version={st.history_version}。"
        prev = (st.compacted_summary or "").strip()
        st.compacted_summary = (prev + "\n" + line).strip() if prev else line
        store.update(st)
        return {"compacted_summary": st.compacted_summary}

    history = session_mgr.get_full_history_for_compact(request.session_id)
    if len(history) < threshold:
        return {}
    st = store.get(request.session_id)
    prior = (st.compacted_summary or "").strip() or None
    try:
        summary = generate_session_compact_summary(history=history, prior_summary=prior)
    except LLMClientError as exc:
        logger.warning("[AgentGraph] compact LLM failed session_id={} err={}", request.session_id, exc)
        line = f"[auto-compact-fallback] recent_messages~{n} 条；history_version={st.history_version}。"
        summary = (prior + "\n" + line).strip() if prior else line

    session_mgr.save_compacted_summary(request.session_id, summary)
    st = store.get(request.session_id)
    keep = max(4, int(session_mgr.config.llm_memory_rounds or 4) * 2)
    session_mgr.truncate_history_keep_last(request.session_id, keep=keep)
    return {"compacted_summary": st.compacted_summary}


def _build_graph() -> Any:
    workflow = StateGraph(ChatPostRouteState)
    workflow.add_node("capability", capability_node)
    workflow.add_node("compose", compose_node)
    workflow.add_node("update_session", update_session_node)
    workflow.add_node("compact", compact_node)
    workflow.add_edge(START, "capability")
    workflow.add_edge("capability", "compose")
    workflow.add_edge("compose", "update_session")
    workflow.add_edge("update_session", "compact")
    workflow.add_edge("compact", END)
    return workflow.compile()


def get_chat_post_route_graph() -> Any:
    global _COMPILED_GRAPH
    with _GRAPH_LOCK:
        if _COMPILED_GRAPH is None:
            _COMPILED_GRAPH = _build_graph()
        return _COMPILED_GRAPH


def run_post_route_chat_graph(
    *,
    route: dict[str, Any],
    request: AgentRequest,
    session_state: SessionState,
    session_store: SessionStateStore,
    rag_index: Any,
    session_manager: SessionManager | None = None,
) -> AgentResponse:
    initial: ChatPostRouteState = {
        "route": dict(route),
        "task_type": str(route.get("task_type") or "analysis"),
        "response_mode": str(route.get("response_mode") or "analysis"),
        "action": str(route.get("action") or ""),
        "facts_bundle": {},
        "display_preferences": dict(route.get("display_preferences") or {}),
        "reply_text": "",
        "skip_compose_llm": False,
    }
    _graph_pipeline(
        f"graph.invoke start task_type={initial['task_type']!s} action={initial['action']!s}"
    )
    graph = get_chat_post_route_graph()
    token = _CTX.set(
        {
            "request": request,
            "session_state": session_state,
            "session_store": session_store,
            "session_manager": session_manager,
            "rag_index": rag_index,
        }
    )
    try:
        out = graph.invoke(initial)
    finally:
        _CTX.reset(token)

    reply_text = str(out.get("reply_text") or "").strip() or DEFAULT_FALLBACK_MESSAGE
    tt = str(route.get("task_type") or "analysis")
    rm = str(route.get("response_mode") or "analysis")
    meta: dict[str, Any] = {"route": dict(route), "unified_graph": True}
    fb = out.get("facts_bundle") if isinstance(out.get("facts_bundle"), dict) else {}
    simf = fb.get("sim_account_facts") if isinstance(fb.get("sim_account_facts"), dict) else {}
    if simf:
        meta["domain"] = simf.get("domain")
        meta["intent"] = simf.get("intent")
        if isinstance(simf.get("meta"), dict):
            meta["capability_meta"] = simf["meta"]
        ev = simf.get("evidence_sources")
        if isinstance(ev, list):
            meta["evidence_sources"] = ev
    return AgentResponse(
        task_type=tt,
        response_mode=rm,
        reply_text=reply_text,
        facts_bundle=fb or None,
        meta=meta,
    )