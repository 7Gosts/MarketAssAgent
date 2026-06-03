"""统一 Agent Core 入口。

这是项目的智能体主入口，所有平台（飞书、CLI、HTTP）都应调用它。

流程：AgentRequest → intent_pipeline/planner → unified graph → AgentResponse
所有 task_type 统一经由 agent_graph 的 capability→compose→session→compact 管道，
不再有平台分支或 facade 委托。
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from app.agent_schemas import (
    AgentRequest,
    AgentResponse,
    AgentError,
    AgentErrorCode,
    AgentErrorStage,
    TaskType,
    ResponseMode,
)
from app.route_postprocessor import AgentRoutingError, log_routed_preview, postprocess_llm_route, _router_conversation_context
from tools.llm.client import decide_feishu_route
from app.feishu_asset_catalog import get_catalog_for_repo
from app.rag_index import get_or_create_rag_index, RagIndex
from app.session_state import SessionState, SessionStateStore, get_global_session_store
from app.session_manager import SessionManager
from app.context_builder import ContextBuilder, load_context_config
from app.agent_runtime_config import ensure_agent_runtime_startup_logged, load_agent_runtime_config
from app.agent_graph import run_post_route_chat_graph, unified_chat_agent_enabled
from app.intent_detectors import apply_intent_pipeline, build_research_route
from app.errors import AgentRuntimeError
from app.route_context import ROUTER_FALLBACK_INTERVAL, ROUTER_FALLBACK_SYMBOL, build_route_context


def _pipeline_log_enabled() -> bool:
    v = (
        os.getenv("AGENT_PIPELINE_LOG", "").strip().lower()
        or os.getenv("FEISHU_PIPELINE_LOG", "").strip().lower()
    )
    return v in {"1", "true", "yes", "on"}


def _log_pipeline(stage: str, detail: str) -> None:
    if not _pipeline_log_enabled():
        return
    logger.info("[AgentCore] pipeline {} {}", stage, detail)


def _exception_summary(exc: Exception) -> str:
    return (
        f"type={type(exc).__name__} "
        f"str={str(exc)!r} "
        f"repr={exc!r}"
    )


def _route_summary(route: dict[str, Any]) -> str:
    if not isinstance(route, dict):
        return repr(route)
    tp = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
    syms = tp.get("symbols") or route.get("symbols") or route.get("symbol")
    iv = tp.get("interval") or route.get("interval") or ""
    pv = tp.get("provider") or route.get("provider") or ""
    bits = [
        f"action={route.get('action')!s}",
        f"task_type={route.get('task_type')!s}",
        f"symbols={syms!s}",
        f"interval={iv!s}",
        f"provider={pv!s}",
    ]
    return ", ".join(bits)


def _error_context_for(exc: Exception, *, classifier_rule: str) -> dict[str, Any]:
    return {
        "exception_type": type(exc).__name__,
        "exception_module": type(exc).__module__,
        "classifier_rule": classifier_rule,
        "raw_message_preview": str(exc)[:240],
    }


def _repo_root_default() -> Path:
    return Path(__file__).resolve().parents[1]


def _fallback_text_for_route(route: dict[str, Any] | None, *, request_text: str) -> str:
    route = route if isinstance(route, dict) else {}
    action = str(route.get("action") or "").strip().lower()
    task_type = str(route.get("task_type") or "").strip().lower()
    raw = (request_text or "").strip()
    if action == "chat" or task_type == "chat":
        return "我这轮普通聊天回复没有稳定生成，但我还在。你可以换个说法，我也可以继续陪你聊。"
    if action == "discover_analyze":
        return "我正在尝试自动识别这个标的，但这轮还没稳定完成。你可以补充市场、交易所或直接给代码，我会继续接着做。"
    if not raw:
        return "我还没收到有效内容。你可以随便聊，也可以直接发想看的标的。"
    return "这轮金融分析没有稳定完成。你可以让我重试，或补充标的、市场、周期等线索。"


def _resolve_session_handles() -> tuple[SessionManager | None, SessionStateStore]:
    raw = get_global_session_store()
    if isinstance(raw, SessionManager):
        return raw, raw.store
    return None, raw


def _prepare_session_for_request(request: AgentRequest, session_mgr: SessionManager | None) -> None:
    if session_mgr is None:
        return
    session_mgr.load_session(
        request.session_id,
        user_id=request.user_id,
        channel=str(request.channel or ""),
    )
    if str(request.channel or "") == "feishu":
        session_mgr.maybe_migrate_legacy_feishu(request.session_id)


def _build_agent_context(
    request: AgentRequest,
    session_mgr: SessionManager | None,
    session_state: SessionState,
) -> None:
    """构建三层上下文并写入 request.context（context.enabled=false 时彻底走固定历史）。"""
    rt = load_agent_runtime_config()
    if not rt.context_enabled or session_mgr is None:
        request.context.pop("agent_context", None)
        conv = _router_conversation_context(session_state) or {}
        request.context["conversation_context"] = conv
        if session_mgr and not request.context.get("recent_messages"):
            rounds = int(session_mgr.config.llm_memory_rounds or 0)
            if rounds > 0:
                request.context["recent_messages"] = session_mgr.get_recent_messages(
                    request.session_id,
                    limit=max(2, rounds * 2),
                )
        n = len(request.context.get("recent_messages") or [])
        _log_pipeline("context", f"mode=legacy_fixed_history transcript={n} {rt.summary_line()}")
        return

    cfg = load_context_config()
    ctx = ContextBuilder(session_mgr, config=cfg).build(request, session_state)
    request.context["agent_context"] = ctx.to_dict()
    request.context["recent_messages"] = ctx.router_recent_messages()
    conv = _router_conversation_context(session_state) or {}
    conv["agent_context"] = ctx.to_dict()
    conv["history_policy"] = ctx.history_policy
    conv["intent_confidence"] = ctx.intent_confidence
    request.context["conversation_context"] = conv
    _log_pipeline(
        "context",
        f"mode=smart {rt.summary_line()} {ctx.explain_brief()}",
    )



def _maybe_expose_agent_context(request: AgentRequest, response: AgentResponse) -> AgentResponse:
    """debug：expose_in_response=true 时在 meta 附带 context_explain。"""
    cfg = load_context_config()
    if not cfg.expose_in_response:
        return response
    ac = request.context.get("agent_context")
    if not isinstance(ac, dict):
        return response
    meta = dict(response.meta or {})
    meta["context_explain"] = {
        "history_policy": ac.get("history_policy"),
        "intent_confidence": ac.get("intent_confidence"),
        "current_query": ac.get("current_query"),
        "meta": ac.get("meta"),
    }
    response.meta = meta
    return response


def _route_meta_from_response(response: AgentResponse | None) -> dict[str, Any]:
    if response is None:
        return {}
    meta = response.meta if isinstance(response.meta, dict) else {}
    route = meta.get("route") if isinstance(meta.get("route"), dict) else {}
    tp = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
    symbols = tp.get("symbols") or []
    return {
        "action": route.get("action") or response.task_type,
        "symbol": symbols[0] if symbols else None,
        "interval": tp.get("interval"),
    }


def _persist_session_after_request(
    session_mgr: SessionManager | None,
    request: AgentRequest,
    response: AgentResponse | None,
) -> None:
    if session_mgr is None:
        return
    try:
        session_mgr.save_session(
            request.session_id,
            user_id=request.user_id,
            channel=str(request.channel or ""),
        )
        user_text = str(request.text or "").strip()
        if user_text:
            session_mgr.append_message(request.session_id, "user", user_text)
        if response and str(response.reply_text or "").strip():
            meta = _route_meta_from_response(response)
            session_mgr.append_message(
                request.session_id,
                "assistant",
                str(response.reply_text or "").strip(),
                action=str(meta.get("action") or "") or None,
                symbol=str(meta.get("symbol") or "") or None,
                interval=str(meta.get("interval") or "") or None,
                question=user_text or None,
            )
    except Exception as exc:
        logger.warning("[AgentCore] persist_session failed session_id={} err={}", request.session_id, exc)


def _get_session_state(request: AgentRequest, store: SessionStateStore) -> SessionState:
    ctx_state = request.context.get("session_state")
    if isinstance(ctx_state, SessionState):
        return ctx_state
    return store.get(request.session_id)


def _build_repair_recent_messages(
    recent_messages: list[dict[str, Any]] | None,
    *,
    route_exc: AgentRoutingError,
) -> list[dict[str, Any]]:
    """为单次 reroute 追加结构化修正提示。"""
    repaired = [dict(msg) for msg in (recent_messages or []) if isinstance(msg, dict)]
    repaired.append(
        {
            "role": "assistant",
            "text": (
                "上一轮路由失败。"
                f"error_code={route_exc.code.value}; "
                f"termination_reason={route_exc.termination_reason or route_exc.code.value}; "
                "请根据用户原句、tradable_assets 和默认周期重新选择合法 action。"
                "如果仍无法确定，就返回 action=chat 并自然提示用户补充必要信息。"
            ),
        }
    )
    return repaired


def _try_route_from_agent_context(request: AgentRequest) -> dict[str, Any] | None:
    """Smart path：从 ContextBuilder.current_query 构造 research route。"""
    ac = request.context.get("agent_context")
    if not isinstance(ac, dict):
        return None
    cq = ac.get("current_query")
    if not isinstance(cq, dict):
        return None
    if str(cq.get("intent_type") or "").strip().lower() != "research":
        return None
    if not cq.get("is_research_intent"):
        return None
    kws = cq.get("research_keywords") if isinstance(cq.get("research_keywords"), list) else []
    kws = [str(k).strip() for k in kws if str(k).strip()]
    if not kws:
        return None
    kw = str(cq.get("research_keyword") or "").strip() or kws[0]
    text = str(request.text or "").strip()
    _log_pipeline("routed_from_context", f"research_keywords={kws!s}")
    return build_research_route(text, research_keyword=kw, research_keywords=kws)


def _classify_execute_exception(*, exc: Exception, task_type: str) -> AgentError:
    """对执行阶段异常做结构化分类。"""
    raw = str(exc)
    lower = raw.lower()
    upper = raw.upper()

    if isinstance(exc, AgentRuntimeError):
        return AgentError(
            code=exc.code,
            stage=exc.stage,
            recoverable=exc.recoverable,
            message=raw,
            termination_reason=exc.termination_reason,
            context={**_error_context_for(exc, classifier_rule="typed_runtime_error"), **getattr(exc, "context", {})},
        )

    if isinstance(exc, TimeoutError) or "timeout" in lower or "超时" in raw:
        return AgentError(
            code=AgentErrorCode.execute_provider_timeout,
            stage=AgentErrorStage.execute,
            recoverable=True,
            message=raw,
            termination_reason="provider_timeout",
            context=_error_context_for(exc, classifier_rule="timeout_text_or_type"),
        )

    if "RAG" in upper:
        return AgentError(
            code=AgentErrorCode.rag_unavailable,
            stage=AgentErrorStage.infra,
            recoverable=True,
            message=raw,
            termination_reason="rag_unavailable",
            context=_error_context_for(exc, classifier_rule="rag_text"),
        )

    if task_type == "followup" and "追问所需的分析产物不存在" in raw:
        return AgentError(
            code=AgentErrorCode.followup_output_missing,
            stage=AgentErrorStage.execute,
            recoverable=True,
            message=raw,
            termination_reason="followup_output_missing",
            context=_error_context_for(exc, classifier_rule="followup_output_missing_text"),
        )

    if "postgres" in lower or "数据库" in raw:
        return AgentError(
            code=AgentErrorCode.db_unavailable,
            stage=AgentErrorStage.infra,
            recoverable=True,
            message=raw,
            termination_reason="db_unavailable",
            context=_error_context_for(exc, classifier_rule="database_text"),
        )

    if "backend" in lower or "后端" in raw:
        return AgentError(
            code=AgentErrorCode.analysis_backend_unavailable,
            stage=AgentErrorStage.infra,
            recoverable=True,
            message=raw,
            termination_reason="analysis_backend_unavailable",
            context=_error_context_for(exc, classifier_rule="backend_text"),
        )

    if task_type == "quote":
        return AgentError(
            code=AgentErrorCode.execute_quote_failed,
            stage=AgentErrorStage.execute,
            recoverable=True,
            message=raw,
            termination_reason="quote_execution_failed",
            context=_error_context_for(exc, classifier_rule="task_type_quote"),
        )

    if task_type in {"analysis", "compare", "research", "followup"}:
        return AgentError(
            code=AgentErrorCode.execute_analysis_failed,
            stage=AgentErrorStage.execute,
            recoverable=True,
            message=raw,
            termination_reason="analysis_execution_failed",
            context=_error_context_for(exc, classifier_rule="task_type_analysis_like"),
        )

    return AgentError(
        code=AgentErrorCode.unknown,
        stage=AgentErrorStage.execute,
        recoverable=True,
        message=raw,
        termination_reason="execute_unknown_error",
        context=_error_context_for(exc, classifier_rule="fallback_unknown"),
    )


def handle_request(request: AgentRequest) -> AgentResponse:
    """统一 Agent Core 入口函数。

    Args:
        request: 统一请求对象（来自飞书、CLI、HTTP）

    Returns:
        统一响应对象（包含 reply_text、facts_bundle、meta）
    """
    ensure_agent_runtime_startup_logged()
    session_mgr, session_store = _resolve_session_handles()
    _prepare_session_for_request(request, session_mgr)
    response: AgentResponse | None = None
    try:
        response = _handle_request_inner(request, session_store, session_mgr)
        return response
    finally:
        _persist_session_after_request(session_mgr, request, response)


def _handle_request_inner(
    request: AgentRequest,
    session_store: SessionStateStore,
    session_mgr: SessionManager | None,
) -> AgentResponse:
    start_ts = time.time()

    session_state = _get_session_state(request, session_store)
    _build_agent_context(request, session_mgr, session_state)
    session_store.reset_route_attempts(request.session_id)
    recent_messages = request.context.get("recent_messages")
    route_context = build_route_context(
        channel=request.channel,
        session_id=request.session_id,
        user_id=request.user_id,
        request_default_symbol=request.default_symbol,
        request_default_interval=request.default_interval,
        session_state=session_state,
        recent_messages=recent_messages if isinstance(recent_messages, list) else None,
        risk_profile=request.context.get("risk_profile") if isinstance(request.context.get("risk_profile"), str) else None,
        display_preferences=request.context.get("display_preferences") if isinstance(request.context.get("display_preferences"), dict) else None,
        options=request.options,
        repo_root=_repo_root_default(),
    )
    request.context["route_context"] = route_context.to_dict()

    repo_root = _repo_root_default()
    rag_index = request.context.get("rag_index")

    route: dict[str, Any] = {}
    task_type: TaskType = "analysis"
    route_succeeded = False
    request_succeeded = False
    reroute_recent_messages = list(recent_messages) if isinstance(recent_messages, list) else None
    max_route_attempts = 2

    try:
        if rag_index is None:
            rag_index = get_or_create_rag_index(repo_root / "output")

        text_preview = (request.text or "").strip().replace("\n", " ")
        if len(text_preview) > 160:
            text_preview = text_preview[:157] + "..."
        _log_pipeline(
            "begin",
            f"channel={request.channel!s} session_id={request.session_id!s} "
            f"unified_graph={unified_chat_agent_enabled()} text_preview={text_preview!s}",
        )

        for attempt in range(1, max_route_attempts + 1):
            try:
                route = None
                if unified_chat_agent_enabled():
                    route = _try_route_from_agent_context(request)
                    if route is None:
                        skip_research = bool(request.context.get("agent_context"))
                        route = apply_intent_pipeline(
                            request.text,
                            session_state,
                            skip_research_shortcut=skip_research,
                        )
                if route is None:
                    # 直调 LLM router + 后处理（不再经过 planner）
                    catalog = get_catalog_for_repo(repo_root)
                    conv_ctx = request.context.get("conversation_context")
                    if not isinstance(conv_ctx, dict):
                        conv_ctx = _router_conversation_context(session_state)
                    routed_raw = decide_feishu_route(
                        text=request.text,
                        default_symbol=route_context.default_symbol,
                        default_interval=route_context.default_interval,
                        recent_messages=reroute_recent_messages,
                        tradable_assets=catalog.tradable_assets_for_prompt(),
                        conversation_context=conv_ctx,
                    )
                    route = postprocess_llm_route(
                        routed_raw,
                        text=request.text,
                        default_symbol=route_context.default_symbol,
                        default_interval=route_context.default_interval,
                        session_state=session_state,
                        recent_messages=reroute_recent_messages,
                        conversation_context=conv_ctx if isinstance(conv_ctx, dict) else None,
                        skip_shortcuts=True,
                    )
                route_succeeded = True

                # 处理 clarify 动作（多轮交互）
                if route.get("action") == "clarify":
                    clarify_msg = route.get("clarify_message") or "请补充缺失的信息。"
                    # 暂存待补全意图
                    pending = {
                        "action": "analyze",  # 目前 clarify 主要针对分析
                        "symbol": route.get("symbol"),
                        "symbols": route.get("symbols"),
                        "provider": route.get("provider"),
                        "task_plan": route.get("task_plan"),
                    }
                    session_state.pending_intent = pending
                    session_store.update(session_state)

                    _log_pipeline(
                        "clarify_pending",
                        f"message={clarify_msg!r} pending_symbol={pending.get('symbol')!r} "
                        f"task_plan_interval={((pending.get('task_plan') or {}).get('interval'))!r}",
                    )
                    _log_pipeline("clarify", _route_summary(route))
                    return AgentResponse(
                        task_type="chat",
                        response_mode="quick",
                        reply_text=clarify_msg,
                    )

                # 处理 clarify_partial 动作（部分补全，继续追问）
                if route.get("action") == "clarify_partial":
                    updated_pending = route["updated_pending"]
                    session_state.pending_intent = updated_pending
                    session_store.update(session_state)
                    missing_fields = route.get("still_missing", [])
                    missing_display = {
                        "标的": "标的（如 BTC、NVDA、AU9999）",
                        "周期": "周期（如 15m、1h、4h、1d）",
                    }
                    missing_parts = "、".join(missing_display.get(f, f) for f in missing_fields)
                    clarify_msg = f"收到，还需要补充：{missing_parts}。"
                    _log_pipeline(
                        "clarify_partial",
                        f"updated_fields={set(updated_pending.keys()) - set(route.get('still_missing', []))} "
                        f"still_missing={missing_fields}",
                    )
                    return AgentResponse(
                        task_type="chat",
                        response_mode="quick",
                        reply_text=clarify_msg,
                    )

                # 如果成功解析出完整意图，清理 pending_intent
                if session_state.pending_intent:
                    session_state.pending_intent = None
                    session_store.update(session_state)

                break
            except AgentRoutingError as route_exc:
                logger.warning(
                    "[AgentCore] route_error attempt={}/{} code={} msg={}",
                    attempt,
                    max_route_attempts,
                    route_exc.code.value,
                    route_exc,
                )

                session_store.record_error(
                    request.session_id,
                    error_code=route_exc.code.value,
                    error_stage=route_exc.stage.value,
                    error_message=str(route_exc),
                    recoverable=route_exc.recoverable,
                )

                should_reroute = (
                    route_exc.stage == AgentErrorStage.route and
                    route_exc.recoverable and
                    attempt < max_route_attempts
                )
                if should_reroute:
                    reroute_recent_messages = _build_repair_recent_messages(
                        reroute_recent_messages,
                        route_exc=route_exc,
                    )
                    continue

                termination_reason = route_exc.termination_reason or route_exc.code.value
                if (
                    route_exc.stage == AgentErrorStage.route and
                    route_exc.recoverable and
                    attempt >= max_route_attempts
                ):
                    termination_reason = "max_route_attempts_reached"

                session_store.record_final_termination(
                    request.session_id,
                    termination_reason=termination_reason,
                    final_error_code=route_exc.code.value,
                )

                agent_error = AgentError(
                    code=route_exc.code,
                    stage=route_exc.stage,
                    recoverable=route_exc.recoverable,
                    message=str(route_exc),
                    termination_reason=termination_reason,
                    context=route_exc.context,
                )

                return AgentResponse.error(
                    error_msg=str(route_exc),
                    fallback_text=_fallback_text_for_route(route, request_text=request.text),
                    agent_error=agent_error,
                )

        if not route_succeeded:
            raise RuntimeError("route loop exited without success or terminal response")

        log_routed_preview(route)
        _log_pipeline("routed", _route_summary(route))

        task_type = str(route.get("task_type") or "analysis")

        # 统一图：所有 task_type 经由 agent_graph 的 capability→compose→session→compact
        if unified_chat_agent_enabled():
            try:
                _log_pipeline("graph_enter", _route_summary(route))
                resp = run_post_route_chat_graph(
                    route=route,
                    request=request,
                    session_state=session_state,
                    session_store=session_store,
                    rag_index=rag_index,
                    session_manager=session_mgr,
                )
                request_succeeded = True
                elapsed_ms = (time.time() - start_ts) * 1000.0
                _log_pipeline(
                    "graph_done",
                    f"elapsed_ms={elapsed_ms:.0f} reply_chars={len(resp.reply_text or '')}",
                )
                return _maybe_expose_agent_context(request, resp)
            except Exception as exc:
                logger.warning(
                    "[AgentCore] unified_graph_error {} route={} request_text={!r}",
                    _exception_summary(exc),
                    _route_summary(route),
                    request.text,
                )
                agent_error = _classify_execute_exception(exc=exc, task_type=task_type)
                session_store.record_error(
                    request.session_id,
                    error_code=agent_error.code.value,
                    error_stage=agent_error.stage.value,
                    error_message=str(exc),
                    recoverable=agent_error.recoverable,
                )
                session_store.record_final_termination(
                    request.session_id,
                    termination_reason=agent_error.termination_reason or agent_error.code.value,
                    final_error_code=agent_error.code.value,
                )
                return AgentResponse.error(
                    error_msg=str(exc),
                    fallback_text=_fallback_text_for_route(route, request_text=request.text),
                    agent_error=agent_error,
                )

        # AGENT_UNIFIED_GRAPH=0 时，走简化直出路径（无 facade 委托）
        return _fallback_direct_execute(
            route=route,
            request=request,
            task_type=task_type,
            rag_index=rag_index,
            session_store=session_store,
            session_state=session_state,
            session_manager=session_mgr,
        )

    except Exception as exc:
        logger.warning(
            "[AgentCore] executor_error {} route={} task_type={} session_id={} request_text={!r}",
            _exception_summary(exc),
            _route_summary(route) if isinstance(route, dict) else repr(route),
            task_type,
            request.session_id if isinstance(request, AgentRequest) else "",
            request.text if isinstance(request, AgentRequest) else "",
        )
        agent_error = _classify_execute_exception(exc=exc, task_type=task_type)

        session_store.record_error(
            request.session_id,
            error_code=agent_error.code.value,
            error_stage=agent_error.stage.value,
            error_message=str(exc),
            recoverable=agent_error.recoverable,
        )

        session_store.record_final_termination(
            request.session_id,
            termination_reason=agent_error.termination_reason or agent_error.code.value,
            final_error_code=agent_error.code.value,
        )
        return AgentResponse.error(
            error_msg=str(exc),
            fallback_text=_fallback_text_for_route(route, request_text=request.text),
            agent_error=agent_error,
        )

    finally:
        if route_succeeded and not request_succeeded:
            task_plan = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
            symbols = task_plan.get("symbols") or []
            rc = request.context.get("route_context") if isinstance(request.context.get("route_context"), dict) else {}
            interval = task_plan.get("interval") or rc.get("default_interval") or request.default_interval
            provider = task_plan.get("provider")
            question = task_plan.get("question") or request.text

            session_store.update_from_route(
                request.session_id,
                action=str(route.get("action") or task_type),
                task_type=task_type,
                symbol=symbols[0] if symbols else None,
                symbols=symbols,
                interval=interval,
                provider=provider,
                question=question,
            )

        try:
            if route_succeeded and request_succeeded:
                session_store.record_success(
                    request.session_id,
                    termination_reason="success",
                )
        except Exception:
            pass


def _fallback_direct_execute(
    *,
    route: dict[str, Any],
    request: AgentRequest,
    task_type: str,
    rag_index: Any,
    session_store: SessionStateStore,
    session_state: SessionState,
    session_manager: SessionManager | None = None,
) -> AgentResponse:
    """AGENT_UNIFIED_GRAPH=0 时的简化直出路径。

    不再委托 agent_facade，而是直接调用统一图（保证行为一致）。
    此函数仅在环境变量显式关闭统一图时被调用。
    """
    try:
        resp = run_post_route_chat_graph(
            route=route,
            request=request,
            session_state=session_state,
            session_store=session_store,
            rag_index=rag_index,
            session_manager=session_manager,
        )
        return resp
    except Exception as exc:
        logger.warning("[AgentCore] fallback_direct_execute_error exc={}", exc)
        agent_error = _classify_execute_exception(exc=exc, task_type=task_type)
        session_store.record_error(
            request.session_id,
            error_code=agent_error.code.value,
            error_stage=agent_error.stage.value,
            error_message=str(exc),
            recoverable=agent_error.recoverable,
        )
        session_store.record_final_termination(
            request.session_id,
            termination_reason=agent_error.termination_reason or agent_error.code.value,
            final_error_code=agent_error.code.value,
        )
        return AgentResponse.error(
            error_msg=str(exc),
            fallback_text="分析执行失败。请稍后重试或简化问题。",
            agent_error=agent_error,
        )


def run_agent(request: AgentRequest) -> AgentResponse:
    """run_agent 是 handle_request 的别名。"""
    return handle_request(request)


# ============ 兼容旧接口 ============

def handle_user_request_compat(
    *,
    text: str,
    channel: str = "feishu",
    user_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """兼容旧 agent_facade.handle_user_request 接口。"""
    ctx = context or {}
    request = AgentRequest(
        channel=str(channel),
        session_id=str(user_id or ctx.get("session_id") or "unknown"),
        text=text,
        user_id=user_id,
        default_symbol=str(ctx.get("default_symbol") or ROUTER_FALLBACK_SYMBOL),
        default_interval=str(ctx.get("default_interval") or ROUTER_FALLBACK_INTERVAL),
        context=ctx,
        options=ctx.get("options") or {},
    )

    response = handle_request(request)

    return {
        "task_type": response.task_type,
        "response_mode": response.response_mode,
        "final_text": response.reply_text,
        "facts_bundle": response.facts_bundle,
        "legacy_action": str(response.task_type),
        "meta": response.meta,
    }
