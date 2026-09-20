"""JSONL 事件会话的唯一编排入口。"""

from __future__ import annotations

import uuid
from typing import Any

from application.services.envelope_builder import build_conversation_envelope
from config.runtime_config import get_agent_context_limits
from core.agent import MarketReActAgent
from core.conversation_scope import ConversationScope
from infrastructure.memory.context_compactor import ContextBudget, ContextOverflow, SessionCompactor
from infrastructure.memory.event_store import LocalSessionEventStore, SessionEvent
from infrastructure.memory.session_journal import SessionEventJournal
from schemas.conversation import ConversationEnvelope
from utils.logging_utils import get_logger
from utils.crash_injection import crash_if_requested


logger = get_logger(__name__)


class ConversationService:
    """Persist inbound facts first, then build every model context from the event log."""

    def __init__(
        self,
        *,
        agent: MarketReActAgent,
        event_store: LocalSessionEventStore,
    ) -> None:
        self.agent = agent
        self.event_store = event_store
        self.compactor = SessionCompactor(event_store)

    async def run(
        self,
        *,
        text: str,
        session_id: str,
        extra_meta: dict[str, Any] | None = None,
    ) -> ConversationEnvelope:
        event_meta = dict(extra_meta or {})
        scope = self._event_scope(session_id=session_id, extra_meta=event_meta)
        event_session_id = self.event_store.resolve_session_id(
            scope=scope,
            preferred_session_id=scope.session_id,
        )
        with self.event_store.turn_guard(scope=scope, session_id=event_session_id):
            return await self._run_turn(
                text=text,
                extra_meta=event_meta,
                scope=scope,
                event_session_id=event_session_id,
            )

    async def _run_turn(
        self,
        *,
        text: str,
        extra_meta: dict[str, Any],
        scope: ConversationScope,
        event_session_id: str,
    ) -> ConversationEnvelope:
        external_message_id = str(extra_meta.get("external_message_id") or uuid.uuid4().hex)
        journal = SessionEventJournal(
            store=self.event_store,
            scope=scope,
            session_id=event_session_id,
        )
        journal.ensure_session()
        journal.recover_incomplete_tool_calls(self.agent.registry)
        accepted = journal.accept_user_message(
            text=text,
            transport=scope.transport,
            tenant_or_app_id=scope.tenant_id,
            external_message_id=external_message_id,
        )
        if accepted.deduplicated:
            previous = self._completed_turn(
                scope=scope,
                session_id=event_session_id,
                user_event_id=accepted.event.event_id,
            )
            if previous is not None:
                return self._deduplicated_envelope(
                    scope=scope,
                    session_id=event_session_id,
                    external_message_id=external_message_id,
                    user_text=text,
                    assistant_event=previous,
                )
            if self._turn_has_indeterminate_tool(
                scope=scope,
                session_id=event_session_id,
                user_event_id=accepted.event.event_id,
            ):
                return self._abort_with_local_reply(
                    journal=journal,
                    scope=scope,
                    session_id=event_session_id,
                    user_event_id=accepted.event.event_id,
                    request_id=external_message_id,
                    user_text=text,
                    reason="indeterminate_tool_result",
                    reply_text=(
                        "上一条请求的工具执行结果不确定，为避免重复操作，本次没有自动重试。"
                        "请先核对订单或持仓状态。"
                    ),
                    deduplicated=True,
                )
            abort_reason = self._turn_abort_reason(
                scope=scope,
                session_id=event_session_id,
                user_event_id=accepted.event.event_id,
            )
            if abort_reason:
                return self._abort_with_local_reply(
                    journal=journal,
                    scope=scope,
                    session_id=event_session_id,
                    user_event_id=accepted.event.event_id,
                    request_id=external_message_id,
                    user_text=text,
                    reason=abort_reason,
                    reply_text="上一条请求未完成，本次没有自动重放。请重新发送一条新消息后再试。",
                    deduplicated=True,
                )

        limits = get_agent_context_limits()
        budget = ContextBudget(
            context_window_tokens=int(limits["context_window_tokens"]),
            reserve_tokens=int(limits["reserve_tokens"]),
            recent_fraction=float(limits["recent_fraction"]),
            estimator_margin=float(limits["estimator_margin"]),
        )
        try:
            context = self.compactor.prepare(
                scope=scope,
                session_id=event_session_id,
                system_prompt=self.agent.prompt,
                tool_schemas=self.agent.registry.schemas(),
                budget=budget,
            )
        except ContextOverflow:
            return self._abort_with_local_reply(
                journal=journal,
                scope=scope,
                session_id=event_session_id,
                user_event_id=accepted.event.event_id,
                request_id=external_message_id,
                user_text=text,
                reason="context_overflow",
                reply_text="当前消息超过模型上下文上限，已完整记录但未发送给模型。请缩短后重试。",
                deduplicated=accepted.deduplicated,
            )

        try:
            result = await self.agent.invoke(
                text,
                session_id=event_session_id,
                request_id=external_message_id,
                context_messages=context.messages,
                event_journal=journal,
                turn_user_event_id=accepted.event.event_id,
                checkpoint_event_id=context.checkpoint_event_id,
                evidence_index=context.evidence_index,
            )
        except Exception:
            try:
                journal.append_turn_aborted(
                    turn_user_event_id=accepted.event.event_id,
                    abort_nonce=external_message_id,
                    reason="provider_error",
                )
            except Exception:
                logger.exception("failed to persist turn abort session_id=%s", event_session_id)
            raise

        reply_text = _extract_reply_text(result)
        assistant_event_id = str(((result.get("metadata") or {}).get("last_assistant_event_id") or ""))
        if not assistant_event_id:
            raise RuntimeError("agent completed without a persisted assistant event")
        envelope = build_conversation_envelope(
            result=result,
            reply_text=reply_text,
            session_id=event_session_id,
            user_text=text,
            plan=None,
        )
        envelope.meta.update({"request_id": external_message_id, "deduplicated": False})
        envelope.pending_delivery = self._delivery_token(
            scope=scope,
            session_id=event_session_id,
            assistant_event_id=assistant_event_id,
        )
        return envelope

    def begin_delivery(
        self,
        envelope: ConversationEnvelope,
        *,
        transport: str,
        tenant_or_app_id: str,
        destination: str,
    ) -> bool:
        token = dict(envelope.pending_delivery or {})
        if not token:
            return True
        scope = self._scope_from_delivery_token(token)
        journal = SessionEventJournal(
            store=self.event_store,
            scope=scope,
            session_id=str(token["session_id"]),
        )
        planned = journal.append_delivery_planned(
            transport=transport,
            tenant_or_app_id=tenant_or_app_id,
            destination=destination,
            assistant_event_id=str(token["assistant_event_id"]),
        )
        crash_if_requested("after_delivery_planned_before_started")
        events = self.event_store.read_branch(scope=scope, session_id=journal.session_id)
        results = [
            event for event in events
            if event.event_type == "delivery/result"
            and str((event.payload or {}).get("delivery_planned_event_id") or "") == planned.event_id
        ]
        if results:
            envelope.pending_delivery = {}
            return False
        starts = [
            event for event in events
            if event.event_type == "delivery/started"
            and str((event.payload or {}).get("delivery_planned_event_id") or "") == planned.event_id
        ]
        if starts:
            journal.append_delivery_result(
                planned_event_id=planned.event_id,
                status="unknown",
                error="process_interrupted_after_delivery_started",
            )
            envelope.pending_delivery = {}
            return False
        journal.append_delivery_started(planned_event_id=planned.event_id, attempt_no=1)
        token["planned_event_id"] = planned.event_id
        envelope.pending_delivery = token
        return True

    def finish_delivery(
        self,
        envelope: ConversationEnvelope,
        *,
        status: str,
        provider_message_id: str = "",
        error: str = "",
    ) -> None:
        token = dict(envelope.pending_delivery or {})
        if not token:
            return
        planned_event_id = str(token.get("planned_event_id") or "")
        if not planned_event_id:
            raise ValueError("delivery has not been started")
        scope = self._scope_from_delivery_token(token)
        journal = SessionEventJournal(
            store=self.event_store,
            scope=scope,
            session_id=str(token["session_id"]),
        )
        journal.append_delivery_result(
            planned_event_id=planned_event_id,
            status=status,
            provider_message_id=provider_message_id,
            error=error,
        )
        envelope.pending_delivery = {}

    @staticmethod
    def _event_scope(*, session_id: str, extra_meta: dict[str, Any]) -> ConversationScope:
        visibility_scope = str(extra_meta.get("visibility_scope") or "web")
        visibility_scope_id = str(extra_meta.get("visibility_scope_id") or session_id)
        actor_id = str(extra_meta.get("actor_id") or visibility_scope_id)
        return ConversationScope(
            transport=str(extra_meta.get("transport") or "web"),
            tenant_id=str(extra_meta.get("tenant_id") or "local"),
            visibility_scope=visibility_scope,  # type: ignore[arg-type]
            visibility_scope_id=visibility_scope_id,
            actor_id=actor_id,
        )

    @staticmethod
    def _delivery_token(
        *,
        scope: ConversationScope,
        session_id: str,
        assistant_event_id: str,
    ) -> dict[str, Any]:
        return {
            **scope.to_meta(),
            "session_id": session_id,
            "assistant_event_id": assistant_event_id,
        }

    @staticmethod
    def _scope_from_delivery_token(token: dict[str, Any]) -> ConversationScope:
        return ConversationScope(
            transport=str(token["transport"]),
            tenant_id=str(token["tenant_id"]),
            visibility_scope=str(token["visibility_scope"]),  # type: ignore[arg-type]
            visibility_scope_id=str(token["visibility_scope_id"]),
            actor_id=str(token["actor_id"]),
        )

    def _completed_turn(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        user_event_id: str,
    ) -> SessionEvent | None:
        events = self.event_store.read_branch(scope=scope, session_id=session_id)
        in_turn = False
        final = None
        for event in events:
            if event.event_id == user_event_id:
                in_turn = True
                continue
            if in_turn and event.event_type == "user/message":
                break
            if in_turn and event.event_type == "assistant/local_message":
                final = event
            elif in_turn and event.event_type == "assistant/message" and not (event.payload or {}).get("tool_calls"):
                final = event
        return final

    def _abort_with_local_reply(
        self,
        *,
        journal: SessionEventJournal,
        scope: ConversationScope,
        session_id: str,
        user_event_id: str,
        request_id: str,
        user_text: str,
        reason: str,
        reply_text: str,
        deduplicated: bool,
    ) -> ConversationEnvelope:
        journal.append_turn_aborted(
            turn_user_event_id=user_event_id,
            abort_nonce=request_id,
            reason=reason,
        )
        assistant_event = journal.append_local_assistant_message(
            turn_user_event_id=user_event_id,
            reason=reason,
            content=reply_text,
        )
        envelope = build_conversation_envelope(
            result={"error": reason},
            reply_text=reply_text,
            session_id=session_id,
            user_text=user_text,
            plan=None,
        )
        envelope.meta.update({"request_id": request_id, "deduplicated": deduplicated})
        envelope.pending_delivery = self._delivery_token(
            scope=scope,
            session_id=session_id,
            assistant_event_id=assistant_event.event_id,
        )
        return envelope

    def _turn_has_indeterminate_tool(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        user_event_id: str,
    ) -> bool:
        in_turn = False
        for event in self.event_store.read_branch(scope=scope, session_id=session_id):
            if event.event_id == user_event_id:
                in_turn = True
                continue
            if in_turn and event.event_type == "user/message":
                break
            if (
                in_turn
                and event.event_type == "tool/result"
                and str((event.payload or {}).get("status") or "") == "unknown"
            ):
                return True
        return False

    def _turn_abort_reason(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        user_event_id: str,
    ) -> str:
        for event in self.event_store.read_branch(scope=scope, session_id=session_id):
            if (
                event.event_type == "turn/aborted"
                and str((event.payload or {}).get("turn_user_event_id") or "") == user_event_id
            ):
                return str((event.payload or {}).get("reason") or "aborted")
        return ""

    def _deduplicated_envelope(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        external_message_id: str,
        user_text: str,
        assistant_event: SessionEvent,
    ) -> ConversationEnvelope:
        envelope = build_conversation_envelope(
            result={},
            reply_text=str((assistant_event.payload or {}).get("content") or ""),
            session_id=session_id,
            user_text=user_text,
            plan=None,
        )
        envelope.meta.update({"request_id": external_message_id, "deduplicated": True})
        if not self._delivery_is_terminal(scope, session_id, assistant_event.event_id):
            envelope.pending_delivery = self._delivery_token(
                scope=scope,
                session_id=session_id,
                assistant_event_id=assistant_event.event_id,
            )
        return envelope

    def _delivery_is_terminal(
        self,
        scope: ConversationScope,
        session_id: str,
        assistant_event_id: str,
    ) -> bool:
        events = self.event_store.read_branch(scope=scope, session_id=session_id)
        plan_ids = {
            event.event_id
            for event in events
            if event.event_type == "delivery/planned"
            and str((event.payload or {}).get("assistant_event_id") or "") == assistant_event_id
        }
        return any(
            event.event_type == "delivery/result"
            and str((event.payload or {}).get("delivery_planned_event_id") or "") in plan_ids
            for event in events
        )


def _extract_reply_text(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    for key in ("reply", "output_text", "text"):
        if result.get(key):
            return str(result[key]).strip()
    for message in reversed(result.get("messages") or []):
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if content:
            return str(content).strip()
    return ""
