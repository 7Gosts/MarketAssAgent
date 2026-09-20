from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.conversation_scope import ConversationScope
from infrastructure.memory.context_builder import (
    BuiltSessionContext,
    SessionContextBuilder,
    estimate_messages_tokens,
    estimate_text_tokens,
)
from infrastructure.memory.event_store import LocalSessionEventStore, SessionEvent
from infrastructure.memory.session_journal import SessionEventJournal
from utils.crash_injection import crash_if_requested


class ContextOverflow(RuntimeError):
    pass


@dataclass(frozen=True)
class ContextBudget:
    context_window_tokens: int = 65536
    reserve_tokens: int = 16384
    recent_fraction: float = 0.30
    estimator_margin: float = 0.08

    @property
    def prompt_tokens(self) -> int:
        return max(1, int(self.context_window_tokens) - int(self.reserve_tokens))


class SessionCompactor:
    """Create immutable, extractive checkpoints at complete turn boundaries."""

    _VISIBLE_TYPES = {
        "user/message",
        "assistant/message",
        "assistant/local_message",
        "tool/call_planned",
        "tool/result",
    }

    def __init__(self, store: LocalSessionEventStore) -> None:
        self.store = store
        self.builder = SessionContextBuilder(store)

    def prepare(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        system_prompt: str,
        tool_schemas: list[dict[str, Any]],
        budget: ContextBudget,
    ) -> BuiltSessionContext:
        built = self.builder.build(
            scope=scope,
            session_id=session_id,
            system_prompt=system_prompt,
        )
        if self._request_tokens(built.messages, tool_schemas, budget) <= budget.prompt_tokens:
            return self._with_estimate(built, tool_schemas, budget)

        events = self.store.read_branch(scope=scope, session_id=session_id)
        checkpoint = self.builder._latest_checkpoint(events)
        visible = self.builder._events_after_checkpoint(events, checkpoint)
        visible = self.builder._filter_aborted_turns(visible)
        user_indexes = [index for index, event in enumerate(visible) if event.event_type == "user/message"]
        if len(user_indexes) < 2:
            raise ContextOverflow("current turn exceeds the model context budget")

        max_summary_tokens = max(128, int(budget.prompt_tokens * (1.0 - budget.recent_fraction)))
        selected: tuple[int, str, list[str]] | None = None
        for cut_index in user_indexes[1:]:
            covered = visible[:cut_index]
            retained = visible[cut_index:]
            summary, source_ids = self._extractive_summary(
                previous_checkpoint=checkpoint,
                covered_events=covered,
                max_tokens=max_summary_tokens,
                margin=budget.estimator_margin,
            )
            messages, _ = self.builder.render_messages(
                scope=scope,
                system_prompt=system_prompt,
                visible_events=retained,
                checkpoint_summary=summary,
            )
            if self._request_tokens(messages, tool_schemas, budget) <= budget.prompt_tokens:
                selected = (cut_index, summary, source_ids)
                break

        if selected is None:
            raise ContextOverflow("current turn exceeds the model context budget")

        cut_index, summary, source_ids = selected
        covered_events = visible[:cut_index]
        retained_events = visible[cut_index:]
        covered_context = [event for event in covered_events if event.event_type in self._VISIBLE_TYPES]
        if not covered_context or not retained_events:
            raise ContextOverflow("no complete historical turn can be compacted")

        journal = SessionEventJournal(store=self.store, scope=scope, session_id=session_id)
        crash_if_requested("before_compaction_commit")
        journal.append_compaction(
            summary=summary,
            covered_from_event_id=covered_context[0].event_id,
            covered_to_event_id=covered_events[-1].event_id,
            first_kept_event_id=retained_events[0].event_id,
            input_tokens=built.estimated_tokens,
            source_event_ids=source_ids,
            lower_checkpoint_event_ids=[checkpoint.event_id] if checkpoint else [],
        )
        compacted = self.builder.build(
            scope=scope,
            session_id=session_id,
            system_prompt=system_prompt,
        )
        compacted = self._with_estimate(compacted, tool_schemas, budget)
        if compacted.estimated_tokens > budget.prompt_tokens:
            raise ContextOverflow("compacted context still exceeds the model context budget")
        return compacted

    @staticmethod
    def _with_estimate(
        built: BuiltSessionContext,
        tool_schemas: list[dict[str, Any]],
        budget: ContextBudget,
    ) -> BuiltSessionContext:
        return BuiltSessionContext(
            messages=built.messages,
            evidence_index=built.evidence_index,
            checkpoint_event_id=built.checkpoint_event_id,
            estimated_tokens=SessionCompactor._request_tokens(
                built.messages,
                tool_schemas,
                budget,
            ),
        )

    @staticmethod
    def _request_tokens(
        messages: list[Any],
        tool_schemas: list[dict[str, Any]],
        budget: ContextBudget,
    ) -> int:
        tool_text = json.dumps(tool_schemas, ensure_ascii=False, separators=(",", ":"), default=str)
        return estimate_messages_tokens(messages, margin=budget.estimator_margin) + estimate_text_tokens(
            tool_text,
            margin=budget.estimator_margin,
        )

    @staticmethod
    def _extractive_summary(
        *,
        previous_checkpoint: SessionEvent | None,
        covered_events: list[SessionEvent],
        max_tokens: int,
        margin: float,
    ) -> tuple[str, list[str]]:
        lines: list[str] = []
        source_ids: list[str] = []
        if previous_checkpoint is not None:
            payload = previous_checkpoint.payload or {}
            previous = str(payload.get("summary") or "").strip()
            if previous:
                lines.append(previous)
            source_ids.extend(str(item) for item in payload.get("source_event_ids") or [] if str(item))

        for event in covered_events:
            payload = event.payload or {}
            line = ""
            if event.event_type == "user/message":
                line = f"[mem:{event.event_id}] 用户原话：{payload.get('text') or ''}"
            elif event.event_type == "assistant/message":
                line = f"[mem:{event.event_id}] 助手原话：{payload.get('content') or ''}"
            elif event.event_type == "assistant/local_message":
                line = f"[mem:{event.event_id}] 系统回复：{payload.get('content') or ''}"
            elif event.event_type == "tool/result":
                tool = str(payload.get("tool_name") or "tool")
                result = json.dumps(payload.get("result"), ensure_ascii=False, separators=(",", ":"), default=str)
                line = f"[mem:{event.event_id}] 工具事实({tool})：{result}"
            if not line:
                continue
            source_ids.append(event.event_id)
            candidate = "\n".join([*lines, line])
            if estimate_text_tokens(candidate, margin=margin) <= max_tokens:
                lines.append(line)
                continue
            estimated_chars_per_token = max(1.0, 1.15 * (1.0 + margin))
            max_summary_chars = max(96, int(max_tokens / estimated_chars_per_token))
            remaining = max(0, max_summary_chars - len("\n".join(lines)))
            if remaining > 96:
                suffix = "...[内容按预算省略]"
                lines.append(line[: max(1, remaining - len(suffix))] + suffix)
            break

        unique_source_ids = list(dict.fromkeys(source_ids))
        return "\n".join(lines), unique_source_ids
