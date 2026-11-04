"""JSONL 事件会话的唯一编排入口。"""

from __future__ import annotations

import uuid
from typing import Any

from application.services.envelope_builder import build_conversation_envelope
from core.agent import MarketReActAgent
from core.conversation_scope import ConversationScope
from infrastructure.memory.context_compactor import ContextOverflow
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
                user_seq=accepted.event.seq,
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
                user_seq=accepted.event.seq,
            ):
                return self._abort_with_local_reply(
                    journal=journal,
                    scope=scope,
                    session_id=event_session_id,
                    user_seq=accepted.event.seq,
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
                user_seq=accepted.event.seq,
            )
            if abort_reason:
                return self._abort_with_local_reply(
                    journal=journal,
                    scope=scope,
                    session_id=event_session_id,
                    user_seq=accepted.event.seq,
                    request_id=external_message_id,
                    user_text=text,
                    reason=abort_reason,
                    reply_text="上一条请求未完成，本次没有自动重放。请重新发送一条新消息后再试。",
                    deduplicated=True,
                )

        try:
            result = await self.agent.invoke(
                text,
                session_id=event_session_id,
                request_id=external_message_id,
                event_journal=journal,
                turn_user_seq=accepted.event.seq,
            )
        except ContextOverflow as exc:
            logger.warning("context budget unavailable session_id=%s reason=%s", event_session_id, exc)
            return self._abort_with_local_reply(
                journal=journal,
                scope=scope,
                session_id=event_session_id,
                user_seq=accepted.event.seq,
                request_id=external_message_id,
                user_text=text,
                reason="context_overflow",
                reply_text=(
                    "本轮上下文无法在模型预算内完成压缩，已保留原始记录。请缩小问题范围后重试；"
                    "本轮已执行的操作不会自动重放。"
                ),
                deduplicated=accepted.deduplicated,
            )
        except Exception:
            try:
                journal.append_turn_aborted(
                    turn_user_seq=accepted.event.seq,
                    abort_nonce=external_message_id,
                    reason="provider_error",
                )
            except Exception:
                logger.exception("failed to persist turn abort session_id=%s", event_session_id)
            raise

        reply_text = _extract_reply_text(result)
        assistant_seq = int((result.get("metadata") or {}).get("last_assistant_seq") or 0)
        if not assistant_seq:
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
            assistant_seq=assistant_seq,
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
            assistant_seq=int(token["assistant_seq"]),
        )
        crash_if_requested("after_delivery_planned_before_started")
        events = self.event_store.read_branch(scope=scope, session_id=journal.session_id)
        results = [
            event for event in events
            if event.event_type == "delivery/result"
            and (event.payload or {}).get("delivery_planned_seq") == planned.seq
        ]
        if results:
            envelope.pending_delivery = {}
            return False
        starts = [
            event for event in events
            if event.event_type == "delivery/started"
            and (event.payload or {}).get("delivery_planned_seq") == planned.seq
        ]
        if starts:
            journal.append_delivery_result(
                planned_seq=planned.seq,
                status="unknown",
                error="process_interrupted_after_delivery_started",
            )
            envelope.pending_delivery = {}
            return False
        journal.append_delivery_started(planned_seq=planned.seq, attempt_no=1)
        token["planned_seq"] = planned.seq
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
        planned_seq = int(token.get("planned_seq") or 0)
        if not planned_seq:
            raise ValueError("delivery has not been started")
        scope = self._scope_from_delivery_token(token)
        journal = SessionEventJournal(
            store=self.event_store,
            scope=scope,
            session_id=str(token["session_id"]),
        )
        journal.append_delivery_result(
            planned_seq=planned_seq,
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
        assistant_seq: int,
    ) -> dict[str, Any]:
        return {
            **scope.to_meta(),
            "session_id": session_id,
            "assistant_seq": assistant_seq,
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
        user_seq: int,
    ) -> SessionEvent | None:
        events = self.event_store.read_branch(scope=scope, session_id=session_id)
        in_turn = False
        final = None
        for event in events:
            if event.seq == user_seq:
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
        user_seq: int,
        request_id: str,
        user_text: str,
        reason: str,
        reply_text: str,
        deduplicated: bool,
    ) -> ConversationEnvelope:
        journal.append_turn_aborted(
            turn_user_seq=user_seq,
            abort_nonce=request_id,
            reason=reason,
        )
        assistant_event = journal.append_local_assistant_message(
            turn_user_seq=user_seq,
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
            assistant_seq=assistant_event.seq,
        )
        return envelope

    def _turn_has_indeterminate_tool(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        user_seq: int,
    ) -> bool:
        in_turn = False
        for event in self.event_store.read_branch(scope=scope, session_id=session_id):
            if event.seq == user_seq:
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
        user_seq: int,
    ) -> str:
        for event in self.event_store.read_branch(scope=scope, session_id=session_id):
            if (
                event.event_type == "turn/aborted"
                and (event.payload or {}).get("turn_user_seq") == user_seq
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
        if not self._delivery_is_terminal(scope, session_id, assistant_event.seq):
            envelope.pending_delivery = self._delivery_token(
                scope=scope,
                session_id=session_id,
                assistant_seq=assistant_event.seq,
            )
        return envelope

    def _delivery_is_terminal(
        self,
        scope: ConversationScope,
        session_id: str,
        assistant_seq: int,
    ) -> bool:
        events = self.event_store.read_branch(scope=scope, session_id=session_id)
        plan_ids = {
            event.seq
            for event in events
            if event.event_type == "delivery/planned"
            and (event.payload or {}).get("assistant_seq") == assistant_seq
        }
        return any(
            event.event_type == "delivery/result"
            and (event.payload or {}).get("delivery_planned_seq") in plan_ids
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
