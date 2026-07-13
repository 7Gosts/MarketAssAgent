"""ConversationService — 唯一会话记忆编排层

职责：
- 保存用户消息
- 读取最近历史
- 调用 agent.invoke(..., history=...)
- 提取回复文本
- 保存 assistant 回复
- 返回统一 ConversationEnvelope

禁止在 adapter / route 层重复实现上述流程。
"""

from __future__ import annotations

import json
import os
import time
import inspect
from typing import Any

from core.agent import MarketReActAgent
from core.agent_context import build_light_agent_input
from core.fact_store import Fact
from core.memory_api import MemoryAPI
from config.runtime_config import get_agent_context_limits, is_feature_enabled
from infrastructure.memory.session_manager import MarketSessionManager
from application.services.envelope_builder import build_conversation_envelope
from schemas.conversation import ConversationEnvelope
from utils.logging_utils import get_logger
from utils.runtime_paths import get_debug_dir


logger = get_logger(__name__)


class ConversationService:
    """会话服务：统一编排记忆读写 + Agent 调用"""

    def __init__(
        self,
        agent: MarketReActAgent,
        session_manager: MarketSessionManager,
        memory_api: MemoryAPI | None = None,
    ) -> None:
        self.agent = agent
        self.session_manager = session_manager
        self.memory_api = memory_api
        self.memory_api_only_mode = bool(memory_api) and is_feature_enabled(
            "memory_api_only_mode",
            default=False,
        )

    async def run(
        self,
        *,
        text: str,
        session_id: str,
        history_limit: int = 8,
        invoke_fn: Any | None = None,
        extra_meta: dict[str, Any] | None = None,
    ) -> ConversationEnvelope:
        """
        执行一次带记忆的 Agent 调用。

        Args:
            invoke_fn: 可选的自定义调用函数（用于 chat 路径等）。
                       默认使用 self.agent.invoke。

        Returns:
            ConversationEnvelope: 统一展示协议。
        """
        thread_id = session_id
        request_id = f"{session_id}:{int(time.time() * 1000)}"

        # 1. 保存用户消息（memory_api_only_mode 下不再写 legacy session 历史）
        if not self.memory_api_only_mode:
            self.session_manager.save_user_message(session_id, text)
        self._write_message_fact(thread_id, role="user", text=text, request_id=request_id)

        # 2. 读取最近历史
        history = self._load_history_for_context(
            session_id=session_id,
            thread_id=thread_id,
            history_limit=history_limit,
        )
        history_for_invoke = self._prepare_history_for_invoke(history, text)

        # 3. 唯一主链路：light 首屏输入 + loop 按需补证。
        result, plan_payload = await self._run_light_context_flow(
            text=text,
            session_id=session_id,
            thread_id=thread_id,
            request_id=request_id,
            history_for_invoke=history_for_invoke,
            invoke_fn=invoke_fn,
        )

        self._write_tool_observation_facts(
            thread_id=thread_id,
            result=result,
            request_id=request_id,
        )

        # 4. 提取回复文本（统一处理多种可能字段）
        reply_text = self._extract_reply_text(result)
        self._dump_raw_llm_output(
            session_id=session_id,
            user_text=text,
            history=history,
            result=result,
            reply_text=reply_text,
            extra_meta=extra_meta or {},
            plan=plan_payload,
        )

        # 5. 保存 assistant 回复（只有成功提取后才保存）
        if reply_text:
            if not self.memory_api_only_mode:
                self.session_manager.save_reply(session_id, reply_text)
            self._write_message_fact(
                thread_id,
                role="assistant",
                text=reply_text,
                request_id=request_id,
            )

        # 6. 更新 last_snapshot checkpoint（新记忆层启用时）
        snapshot = self._extract_snapshot(result)
        if snapshot:
            self._save_snapshot_checkpoint(thread_id, snapshot)
        self._write_turn_summary_fact(
            thread_id=thread_id,
            user_text=text,
            reply_text=reply_text,
            snapshot=snapshot,
            request_id=request_id,
        )

        return build_conversation_envelope(
            result=result,
            reply_text=reply_text,
            session_id=session_id,
            user_text=text,
            plan=None,
        )

    async def _run_light_context_flow(
        self,
        *,
        text: str,
        session_id: str,
        thread_id: str,
        request_id: str,
        history_for_invoke: list[dict[str, str]],
        invoke_fn: Any | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        storage_key = self._resolve_user_id_for_profile(thread_id)
        last_snapshot = self.memory_api.snapshot(thread_id) if self.memory_api else {}
        ctx_limits = get_agent_context_limits()

        conversation_summary, summary_source = self._build_light_conversation_summary(
            thread_id=thread_id,
            history=history_for_invoke,
            last_snapshot=last_snapshot,
        )
        direct_input = build_light_agent_input(
            user_text=text,
            session_id=session_id,
            storage_key=storage_key,
            conversation_summary=conversation_summary,
            max_chars=int(ctx_limits.get("max_chars") or 13434),
            max_summary_chars=int(ctx_limits.get("max_summary_chars") or 1000),
        )
        invoke_history: list[dict[str, str]] = []
        logger.info(
            "[ConversationService] light context session_id=%s history_len=%s summary_keys=%s snapshot_keys=%s summary_chars=%s input_chars=%s input_preview=%r",
            session_id,
            len(history_for_invoke or []),
            sorted(list((conversation_summary or {}).keys())),
            sorted(list((last_snapshot or {}).keys()))[:8],
            len(json.dumps(conversation_summary, ensure_ascii=False)) if conversation_summary else 0,
            len(direct_input),
            self._preview_debug_text(direct_input, max_len=260),
        )
        logger.info(
            "[ConversationService] light summary source session_id=%s source=%s",
            session_id,
            summary_source,
        )
        if invoke_fn is not None:
            invoke_kwargs: dict[str, Any] = {
                "session_id": session_id,
                "history": invoke_history,
            }
            if self._callable_accepts_kwarg(invoke_fn, "request_id"):
                invoke_kwargs["request_id"] = request_id
            result = await invoke_fn(direct_input, **invoke_kwargs)
        else:
            result = await self.agent.invoke(
                direct_input,
                session_id=session_id,
                request_id=request_id,
                history=invoke_history,
                allowed_tools=[],
            )

        plan_payload = {
            "mode": "light_context",
            "task_type": "agent_direct",
            "needs_snapshot": True,
            "user_context_needed": True,
            "storage_key": storage_key,
            "input_mode": "light",
        }
        return result, plan_payload

    def _extract_reply_text(self, result: Any) -> str:
        """从 agent 返回结果中提取回复文本（兼容多种字段）"""
        if not isinstance(result, dict):
            return ""

        # 优先级顺序
        candidates = [
            "reply",
            "output_text",
            "text",
        ]

        for key in candidates:
            if key in result and result[key]:
                return str(result[key]).strip()

        # 尝试从 recommendation 中提取
        rec = result.get("recommendation") or {}
        if isinstance(rec, dict):
            for key in ["text", "reply"]:
                if key in rec and rec[key]:
                    return str(rec[key]).strip()

        # 兜底：尝试从 messages 中取最后一条 assistant 消息
        messages = result.get("messages") or []
        for msg in reversed(messages):
            if hasattr(msg, "content") and msg.content:
                return str(msg.content).strip()
            if isinstance(msg, dict) and msg.get("content"):
                return str(msg["content"]).strip()

        return ""

    def _extract_snapshot(self, result: Any) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return None
        primary_snapshot: dict[str, Any] = {}
        for key in ("analysis_result", "last_snapshot"):
            snap = result.get(key)
            if isinstance(snap, dict) and snap:
                primary_snapshot = dict(snap)
                break

        tool_snapshot = self._extract_snapshot_from_tool_messages(result.get("messages") or [])
        merged_snapshot = self._merge_non_empty_dict(primary_snapshot, tool_snapshot)
        return merged_snapshot if self._is_meaningful_snapshot(merged_snapshot) else None

    def _extract_snapshot_from_tool_messages(self, messages: list[Any]) -> dict[str, Any]:
        for msg in reversed(messages or []):
            msg_type = str(self._message_attr(msg, "type") or "").strip().lower()
            if msg_type != "tool":
                continue
            tool_name = str(self._message_attr(msg, "name") or "").strip()
            if tool_name and tool_name != "analyze_market":
                continue
            raw_content = self._coerce_message_content(self._message_attr(msg, "content"))
            payload = self._try_parse_json(raw_content)
            if not isinstance(payload, dict):
                continue
            snapshot = self._extract_snapshot_from_analyze_market_payload(payload)
            if snapshot:
                return snapshot
        return {}

    def _extract_snapshot_from_analyze_market_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
        raw_snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        source = analysis or raw_snapshot or payload

        snapshot: dict[str, Any] = {
            "symbol": str(source.get("symbol") or payload.get("symbol") or "").strip(),
            "interval": str(source.get("interval") or payload.get("interval") or "").strip(),
            "trend": str(source.get("trend") or "").strip(),
            "timestamp": str(source.get("timestamp") or payload.get("timestamp") or "").strip(),
            "raw_insights": str(source.get("raw_insights") or "").strip(),
        }

        current_price = source.get("current_price")
        if isinstance(current_price, (int, float)):
            snapshot["current_price"] = current_price

        for key in (
            "levels_v2",
            "key_levels",
            "actionability",
            "trigger_conditions",
            "invalidation_conditions",
        ):
            value = source.get(key)
            if isinstance(value, dict) and value:
                snapshot[key] = value

        return {k: v for k, v in snapshot.items() if v not in (None, "", [], {})}

    def _merge_non_empty_dict(
        self,
        primary: dict[str, Any],
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(fallback or {})
        for key, value in (primary or {}).items():
            if value in (None, "", [], {}):
                continue
            merged[key] = value
        return merged

    def _is_meaningful_snapshot(self, snapshot: dict[str, Any]) -> bool:
        if not isinstance(snapshot, dict) or not snapshot:
            return False
        for key in ("symbol", "interval", "trend", "current_price", "levels_v2", "actionability"):
            value = snapshot.get(key)
            if value not in (None, "", [], {}):
                return True
        return False

    def _try_parse_json(self, raw: str) -> Any:
        try:
            return json.loads(str(raw or "").strip())
        except Exception:
            return None

    def _save_snapshot_checkpoint(self, thread_id: str, snapshot: dict[str, Any]) -> None:
        if not self.memory_api:
            return
        try:
            self.memory_api.checkpoint(thread_id, "last_snapshot", snapshot)
        except Exception as e:
            logger.warning("memory_api.checkpoint(last_snapshot) failed: %s", e)

    def _write_message_fact(
        self,
        thread_id: str,
        *,
        role: str,
        text: str,
        request_id: str,
    ) -> None:
        if not self.memory_api:
            return
        clean_text = str(text or "").strip()
        if not clean_text:
            return
        try:
            fact = Fact(
                thread_id=thread_id,
                source="conversation_service",
                type="recent_message",
                payload={"role": role, "text": clean_text},
                tags=["message", role],
                provenance={"request_id": request_id},
            )
            self.memory_api.write_fact(thread_id, fact)
        except Exception as e:
            logger.warning("memory_api.write_fact(recent_message) failed: %s", e)

    def _write_tool_observation_facts(
        self,
        *,
        thread_id: str,
        result: dict[str, Any],
        request_id: str,
    ) -> None:
        if not self.memory_api or not isinstance(result, dict):
            return
        messages = result.get("messages") or []
        seen_ids: set[str] = set()
        for msg in messages:
            msg_type = self._message_attr(msg, "type")
            if str(msg_type or "").strip().lower() != "tool":
                continue
            tool_name = str(self._message_attr(msg, "name") or "").strip() or "unknown_tool"
            tool_call_id = str(self._message_attr(msg, "tool_call_id") or "").strip()
            dedup_key = tool_call_id or f"{tool_name}:{self._truncate_text(str(self._message_attr(msg, 'content') or ''), 80)}"
            if dedup_key in seen_ids:
                continue
            seen_ids.add(dedup_key)

            raw_content = self._message_attr(msg, "content")
            text_content = self._coerce_message_content(raw_content)
            compact_content, content_stats = self._build_tool_observation_content(text_content)
            summary = self._summarize_tool_content(compact_content)
            payload = {
                "tool": tool_name,
                "summary": summary,
                "content": self._truncate_text(compact_content, 900),
                "content_meta": content_stats,
            }
            logger.info(
                "[ConversationService] tool observation compacted tool=%s raw_chars=%s compact_chars=%s field_count=%s omitted_hint=%s",
                tool_name,
                content_stats.get("raw_chars"),
                content_stats.get("compact_chars"),
                content_stats.get("compact_field_count"),
                content_stats.get("omit_candidates"),
            )
            provenance = {"request_id": request_id}
            if tool_call_id:
                provenance["tool_call_id"] = tool_call_id

            try:
                self.memory_api.write_fact(
                    thread_id,
                    Fact(
                        thread_id=thread_id,
                        source=tool_name,
                        type="tool_observation",
                        payload=payload,
                        provenance=provenance,
                        tags=["tool", tool_name],
                    ),
                )
            except Exception as e:
                logger.warning("memory_api.write_fact(tool_observation) failed: %s", e)

    def _load_history_for_context(
        self,
        *,
        session_id: str,
        thread_id: str,
        history_limit: int,
    ) -> list[dict[str, str]]:
        if self.memory_api_only_mode:
            if not self.memory_api:
                # 配置异常时降级，避免完全失忆
                return self.session_manager.get_recent_messages(session_id, limit=history_limit)
            memory_history = self._recall_recent_messages_from_memory_api(
                thread_id=thread_id,
                limit=max(8, history_limit * 2),
            )
            if history_limit > 0:
                return memory_history[-history_limit:]
            return memory_history
        legacy_history = self.session_manager.get_recent_messages(session_id, limit=history_limit)
        if not self.memory_api:
            return legacy_history

        memory_history = self._recall_recent_messages_from_memory_api(
            thread_id=thread_id,
            limit=max(8, history_limit * 2),
        )
        if not memory_history:
            return legacy_history
        # 迁移期兼容：优先保留信息量更大的一侧。
        if len(memory_history) < len(legacy_history):
            return legacy_history
        return memory_history[-history_limit:] if history_limit > 0 else memory_history

    def _recall_recent_messages_from_memory_api(
        self,
        *,
        thread_id: str,
        limit: int,
    ) -> list[dict[str, str]]:
        if not self.memory_api:
            return []
        try:
            facts = self.memory_api.recall(thread_id, {"type": "recent_message"}, limit=limit)
        except Exception as e:
            logger.warning("memory_api.recall(recent_message) failed: %s", e)
            return []

        out: list[dict[str, str]] = []
        # recall 返回新到旧，转为旧到新
        for fact in reversed(facts):
            payload = fact.payload if isinstance(fact.payload, dict) else {}
            role = str(payload.get("role") or "").strip()
            text = str(payload.get("text") or "").strip()
            if role in {"user", "assistant"} and text:
                out.append({"role": role, "text": text})
        return out

    def _prepare_history_for_invoke(
        self,
        history: list[dict[str, str]],
        current_text: str,
    ) -> list[dict[str, str]]:
        out = list(history or [])
        clean_current = str(current_text or "").strip()
        # 当前轮 user 已在历史里时，去掉末尾重复项，避免 invoke 时再 append 造成双注入。
        while out:
            last = out[-1]
            if str(last.get("role") or "").strip() != "user":
                break
            if str(last.get("text") or "").strip() != clean_current:
                break
            out.pop()
        return out


    def _build_light_conversation_summary(
        self,
        *,
        thread_id: str,
        history: list[dict[str, str]],
        last_snapshot: dict[str, Any] | None,
    ) -> tuple[dict[str, str], str]:
        turn_summaries = self._load_recent_turn_summaries(thread_id=thread_id, limit=4)
        if turn_summaries:
            return self._build_light_summary_from_turn_summaries(
                turn_summaries=turn_summaries,
                last_snapshot=last_snapshot,
            ), "turn_summary"

        recent_rows: list[str] = []
        for row in (history or [])[-4:]:
            role = str(row.get("role") or "").strip()
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            prefix = "用户" if role == "user" else "助手"
            recent_rows.append(f"{prefix}: {self._truncate_text(text.replace(chr(10), ' '), 120)}")

        recent_dialogue_summary = "；".join(recent_rows)
        snapshot_hint = self._build_snapshot_hint(last_snapshot)
        current_carryover_hint = ""
        if snapshot_hint and recent_dialogue_summary:
            current_carryover_hint = "当前问题若承接上一轮分析，优先先核对快照与最近工具观察，再决定是否刷新行情。"
        elif snapshot_hint:
            current_carryover_hint = "当前问题若涉及上一轮行情或交易计划，优先先核对该快照。"
        elif recent_dialogue_summary:
            current_carryover_hint = "当前问题可能承接已有对话主题，必要时补查历史摘要。"

        out = {
            "recent_dialogue_summary": recent_dialogue_summary,
            "current_carryover_hint": current_carryover_hint,
            "snapshot_hint": snapshot_hint,
        }
        return {k: v for k, v in out.items() if v}, "history_fallback"

    def _load_recent_turn_summaries(
        self,
        *,
        thread_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self.memory_api:
            return []
        try:
            facts = self.memory_api.recall(thread_id, {"type": "turn_summary"}, limit=max(limit, 1))
        except Exception as e:
            logger.warning("memory_api.recall(turn_summary) failed: %s", e)
            return []

        out: list[dict[str, Any]] = []
        for fact in reversed(facts):
            payload = fact.payload if isinstance(fact.payload, dict) else {}
            if not payload:
                continue
            row = dict(payload)
            row.setdefault("timestamp", str(fact.timestamp or "").strip())
            out.append(row)
        return out

    def _build_light_summary_from_turn_summaries(
        self,
        *,
        turn_summaries: list[dict[str, Any]],
        last_snapshot: dict[str, Any] | None,
    ) -> dict[str, str]:
        recent_rows: list[str] = []
        for item in (turn_summaries or [])[-4:]:
            row = self._render_turn_summary_line(item)
            if row:
                recent_rows.append(row)
        latest = turn_summaries[-1] if turn_summaries else {}
        snapshot_hint = self._build_snapshot_hint(last_snapshot) or self._build_snapshot_hint_from_turn_summary(latest)
        out = {
            "recent_dialogue_summary": "；".join(recent_rows),
            "current_carryover_hint": self._build_turn_summary_carryover_hint(
                latest_summary=latest,
                snapshot_hint=snapshot_hint,
            ),
            "snapshot_hint": snapshot_hint,
        }
        return {k: v for k, v in out.items() if v}

    @staticmethod
    def _callable_accepts_kwarg(func: Any, name: str) -> bool:
        try:
            return name in inspect.signature(func).parameters
        except (TypeError, ValueError):
            return False

    def _render_turn_summary_line(self, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        parts: list[str] = []
        symbol = self._first_string(payload.get("symbols"))
        interval = self._first_string(payload.get("intervals"))
        if symbol:
            parts.append(symbol)
        if interval:
            parts.append(interval)

        price = payload.get("current_price")
        if isinstance(price, (int, float)):
            parts.append(f"价={price}")

        trend = str(payload.get("trend") or "").strip()
        if trend:
            parts.append(f"趋势={trend}")

        key_levels = payload.get("key_levels") if isinstance(payload.get("key_levels"), dict) else {}
        support = self._first_scalar(key_levels.get("support"))
        resistance = self._first_scalar(key_levels.get("resistance"))
        if support not in (None, ""):
            parts.append(f"支撑={support}")
        if resistance not in (None, ""):
            parts.append(f"阻力={resistance}")

        stance = str(payload.get("stance") or "").strip()
        if stance:
            parts.append(f"立场={stance}")

        next_trigger = str(payload.get("next_trigger") or "").strip()
        if next_trigger:
            parts.append(f"触发={self._truncate_text(next_trigger, 48)}")

        conclusion = str(payload.get("assistant_conclusion") or "").strip()
        if conclusion:
            parts.append(f"结论={self._truncate_text(conclusion, 72)}")

        if not parts:
            question = str(payload.get("user_question") or "").strip()
            if question:
                parts.append(f"话题={self._truncate_text(question, 72)}")
        return " / ".join(parts)

    def _build_snapshot_hint_from_turn_summary(self, payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return ""
        parts: list[str] = []
        symbol = self._first_string(payload.get("symbols"))
        interval = self._first_string(payload.get("intervals"))
        trend = str(payload.get("trend") or "").strip()
        price = payload.get("current_price")
        key_levels = payload.get("key_levels") if isinstance(payload.get("key_levels"), dict) else {}
        support = self._first_scalar(key_levels.get("support"))
        resistance = self._first_scalar(key_levels.get("resistance"))
        if symbol:
            parts.append(symbol)
        if interval:
            parts.append(interval)
        if trend:
            parts.append(f"trend={trend}")
        if isinstance(price, (int, float)):
            parts.append(f"price={price}")
        if support not in (None, ""):
            parts.append(f"support={support}")
        if resistance not in (None, ""):
            parts.append(f"resistance={resistance}")
        return ", ".join(parts)

    def _build_turn_summary_carryover_hint(
        self,
        *,
        latest_summary: dict[str, Any],
        snapshot_hint: str,
    ) -> str:
        symbol = self._first_string(latest_summary.get("symbols"))
        interval = self._first_string(latest_summary.get("intervals"))
        key_levels = latest_summary.get("key_levels") if isinstance(latest_summary.get("key_levels"), dict) else {}
        support = self._first_scalar(key_levels.get("support"))
        resistance = self._first_scalar(key_levels.get("resistance"))
        next_trigger = str(latest_summary.get("next_trigger") or "").strip()
        parts: list[str] = []
        if symbol:
            parts.append(symbol)
        if interval:
            parts.append(interval)
        if support not in (None, "") and resistance not in (None, ""):
            parts.append(f"关键位更可能是支撑 {support} / 阻力 {resistance}")
        elif support not in (None, ""):
            parts.append(f"关键支撑更可能是 {support}")
        elif resistance not in (None, ""):
            parts.append(f"关键阻力更可能是 {resistance}")
        if next_trigger:
            parts.append(f"优先核对触发条件：{self._truncate_text(next_trigger, 48)}")
        elif snapshot_hint:
            parts.append("若问题承接上一轮分析，优先核对快照与最近工具观察")
        return "；".join(parts)

    def _build_snapshot_hint(self, last_snapshot: dict[str, Any] | None) -> str:
        if not isinstance(last_snapshot, dict) or not last_snapshot:
            return ""
        parts: list[str] = []
        symbol = str(last_snapshot.get("symbol") or "").strip()
        interval = str(last_snapshot.get("interval") or "").strip()
        trend = str(last_snapshot.get("trend") or "").strip()
        price = last_snapshot.get("current_price")
        timestamp = str(last_snapshot.get("timestamp") or "").strip()
        levels_v2 = last_snapshot.get("levels_v2") if isinstance(last_snapshot.get("levels_v2"), dict) else {}
        if symbol:
            parts.append(symbol)
        if interval:
            parts.append(interval)
        if trend:
            parts.append(f"trend={trend}")
        if isinstance(price, (int, float)):
            parts.append(f"price={price}")
        if timestamp:
            parts.append(f"ts={self._truncate_text(timestamp, 32)}")
        support = levels_v2.get("nearest_support")
        resistance = levels_v2.get("nearest_resistance")
        if support not in (None, ""):
            parts.append(f"support={support}")
        if resistance not in (None, ""):
            parts.append(f"resistance={resistance}")
        return ", ".join(parts)

    def _write_turn_summary_fact(
        self,
        *,
        thread_id: str,
        user_text: str,
        reply_text: str,
        snapshot: dict[str, Any] | None,
        request_id: str,
    ) -> None:
        if not self.memory_api:
            return
        payload = self._build_turn_summary_payload(
            user_text=user_text,
            reply_text=reply_text,
            snapshot=snapshot,
        )
        if not payload:
            return
        try:
            self.memory_api.write_fact(
                thread_id,
                Fact(
                    thread_id=thread_id,
                    source="conversation_service",
                    type="turn_summary",
                    payload=payload,
                    provenance={"request_id": request_id},
                    tags=["turn_summary", "summary"],
                ),
            )
            logger.info(
                "[ConversationService] turn summary saved thread_id=%s symbol=%s interval=%s trend=%s",
                thread_id,
                self._first_string(payload.get("symbols")) or "-",
                self._first_string(payload.get("intervals")) or "-",
                str(payload.get("trend") or "-"),
            )
        except Exception as e:
            logger.warning("memory_api.write_fact(turn_summary) failed: %s", e)

    def _build_turn_summary_payload(
        self,
        *,
        user_text: str,
        reply_text: str,
        snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "user_question": self._truncate_text(str(user_text or "").replace("\n", " ").strip(), 160),
            "assistant_conclusion": self._truncate_text(str(reply_text or "").replace("\n", " ").strip(), 220),
        }
        if isinstance(snapshot, dict) and snapshot:
            symbol = str(snapshot.get("symbol") or "").strip()
            interval = str(snapshot.get("interval") or "").strip()
            trend = str(snapshot.get("trend") or "").strip()
            current_price = snapshot.get("current_price")
            stance = ""
            next_trigger = ""
            invalidation = ""
            position_context = ""

            actionability = snapshot.get("actionability") if isinstance(snapshot.get("actionability"), dict) else {}
            invalidation_conditions = (
                snapshot.get("invalidation_conditions")
                if isinstance(snapshot.get("invalidation_conditions"), dict)
                else {}
            )
            trigger_conditions = (
                snapshot.get("trigger_conditions")
                if isinstance(snapshot.get("trigger_conditions"), dict)
                else {}
            )

            if symbol:
                payload["symbols"] = [symbol]
            if interval:
                payload["intervals"] = [interval]
            if isinstance(current_price, (int, float)):
                payload["current_price"] = current_price
            if trend:
                payload["trend"] = trend

            key_levels = self._extract_turn_summary_key_levels(snapshot)
            if key_levels:
                payload["key_levels"] = key_levels

            stance = str(actionability.get("bias") or "").strip()
            next_trigger = str(actionability.get("wait_condition") or "").strip()
            invalidation = str(
                invalidation_conditions.get("time_stop_rule")
                or invalidation_conditions.get("stop")
                or ""
            ).strip()
            if trigger_conditions:
                side = str(trigger_conditions.get("side") or "").strip()
                entry = trigger_conditions.get("entry")
                stop = trigger_conditions.get("stop")
                if side in {"long", "short"}:
                    position_context = f"{side} entry={entry} stop={stop}"

            raw_insights = str(snapshot.get("raw_insights") or "").strip()
            if raw_insights:
                payload["structure_hint"] = self._truncate_text(raw_insights, 120)
            if stance:
                payload["stance"] = stance
            if invalidation:
                payload["invalidation"] = self._truncate_text(invalidation, 80)
            if next_trigger:
                payload["next_trigger"] = self._truncate_text(next_trigger, 80)
            if position_context:
                payload["position_context"] = self._truncate_text(position_context, 80)

        return {k: v for k, v in payload.items() if v not in ("", [], {}, None)}

    def _extract_turn_summary_key_levels(self, snapshot: dict[str, Any]) -> dict[str, list[Any]]:
        levels_v2 = snapshot.get("levels_v2") if isinstance(snapshot.get("levels_v2"), dict) else {}
        key_levels = snapshot.get("key_levels") if isinstance(snapshot.get("key_levels"), dict) else {}
        support: list[Any] = []
        resistance: list[Any] = []

        raw_support = key_levels.get("support")
        raw_resistance = key_levels.get("resistance")
        if isinstance(raw_support, list):
            support.extend(raw_support[:2])
        if isinstance(raw_resistance, list):
            resistance.extend(raw_resistance[:2])

        nearest_support = levels_v2.get("nearest_support")
        nearest_resistance = levels_v2.get("nearest_resistance")
        if nearest_support not in (None, "") and nearest_support not in support:
            support.insert(0, nearest_support)
        if nearest_resistance not in (None, "") and nearest_resistance not in resistance:
            resistance.insert(0, nearest_resistance)

        out = {
            "support": support[:2],
            "resistance": resistance[:2],
        }
        return {k: v for k, v in out.items() if v}

    def _dump_raw_llm_output(
        self,
        *,
        session_id: str,
        user_text: str,
        history: list[dict[str, Any]],
        result: Any,
        reply_text: str,
        extra_meta: dict[str, Any],
        plan: dict[str, Any],
    ) -> None:
        if os.getenv("MARKETASSAGENT_DEBUG_RAW_OUTPUT", "0").strip().lower() not in {"1", "true", "yes", "on"}:
            return
        try:
            debug_dir = get_debug_dir()
            debug_dir.mkdir(parents=True, exist_ok=True)
            target = debug_dir / "llm_raw_outputs.jsonl"
            record = {
                "ts": time.time(),
                "session_id": session_id,
                "channel": "feishu" if session_id.startswith("feishu_") else "web_or_other",
                "user_text": user_text,
                "history_len": len(history),
                "plan": plan,
                "reply_text_pre_renderer": reply_text,
                "raw_result": self._to_jsonable(result),
                "extra_meta": extra_meta,
            }
            with target.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("debug raw output dump failed: %s", e)

    def _to_jsonable(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(k): self._to_jsonable(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._to_jsonable(v) for v in value]
        if isinstance(value, tuple):
            return [self._to_jsonable(v) for v in value]
        if hasattr(value, "model_dump"):
            try:
                return self._to_jsonable(value.model_dump())  # type: ignore[call-arg]
            except Exception:
                pass
        if hasattr(value, "content"):
            payload = {
                "type": value.__class__.__name__,
                "content": getattr(value, "content", None),
            }
            tool_calls = getattr(value, "tool_calls", None)
            if tool_calls is not None:
                payload["tool_calls"] = self._to_jsonable(tool_calls)
            return payload
        return repr(value)

    def _message_attr(self, msg: Any, key: str) -> Any:
        if isinstance(msg, dict):
            return msg.get(key)
        return getattr(msg, key, None)

    def _coerce_message_content(self, raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        if isinstance(raw, (dict, list)):
            try:
                return json.dumps(raw, ensure_ascii=False)
            except Exception:
                return str(raw)
        return str(raw)

    def _truncate_text(self, text: str, max_len: int) -> str:
        val = str(text or "")
        if len(val) <= max_len:
            return val
        return val[: max(0, max_len - 3)] + "..."

    def _first_string(self, value: Any) -> str:
        if isinstance(value, list):
            for item in value:
                text = str(item or "").strip()
                if text:
                    return text
            return ""
        return str(value or "").strip()

    def _first_scalar(self, value: Any) -> Any:
        if isinstance(value, list):
            for item in value:
                if item not in (None, "", [], {}):
                    return item
            return None
        return value

    def _preview_debug_text(self, text: str, max_len: int = 200) -> str:
        raw = " ".join(str(text or "").split())
        if not raw:
            return ""
        if os.getenv("MARKETASSAGENT_LOG_FULL_CONTEXT", "0").strip().lower() in {"1", "true", "yes", "on"}:
            return raw
        return self._truncate_text(raw, max_len)

    def _summarize_tool_content(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return "返回为空。"
        try:
            parsed = json.loads(raw)
        except Exception:
            return self._truncate_text(raw.replace("\n", " "), 120)
        if isinstance(parsed, dict):
            status = str(parsed.get("status") or "").strip()
            symbol = str(parsed.get("symbol") or "").strip()
            interval = str(parsed.get("interval") or "").strip()
            trend = str(parsed.get("trend") or "").strip()
            parts = [p for p in [status, symbol, interval, trend] if p]
            if parts:
                return " / ".join(parts)
            keys = list(parsed.keys())[:4]
            return f"返回字段: {', '.join(keys)}"
        if isinstance(parsed, list):
            return f"返回列表，共 {len(parsed)} 项。"
        return self._truncate_text(raw.replace("\n", " "), 120)

    def _build_tool_observation_content(self, text_content: str) -> tuple[str, dict[str, Any]]:
        raw = str(text_content or "")
        stats: dict[str, Any] = {
            "raw_chars": len(raw),
            "compact_chars": 0,
            "compact_field_count": 0,
            "omit_candidates": [],
        }
        try:
            parsed = json.loads(raw)
        except Exception:
            compact = self._truncate_text(raw, 900)
            stats["compact_chars"] = len(compact)
            return compact, stats

        if not isinstance(parsed, dict):
            compact = self._truncate_text(raw, 900)
            stats["compact_chars"] = len(compact)
            return compact, stats

        compact_payload: dict[str, Any] = {}
        if isinstance(parsed.get("compact_summary_v1"), dict):
            compact_payload["compact_summary_v1"] = parsed.get("compact_summary_v1")
            omit = parsed.get("compact_summary_v1", {}).get("omit_candidates")
            if isinstance(omit, list):
                stats["omit_candidates"] = omit[:4]
        elif isinstance(parsed.get("comparison_brief_v1"), dict):
            compact_payload["comparison_brief_v1"] = parsed.get("comparison_brief_v1")
        else:
            analysis = parsed.get("analysis") if isinstance(parsed.get("analysis"), dict) else {}
            # 兜底：保留最小可读事实
            compact_payload = {
                "status": parsed.get("status"),
                "symbol": parsed.get("symbol") or analysis.get("symbol"),
                "interval": parsed.get("interval") or analysis.get("interval"),
                "trend": parsed.get("trend") or analysis.get("trend"),
                "current_price": parsed.get("current_price") or analysis.get("current_price"),
                "message": parsed.get("message") or analysis.get("raw_insights"),
            }
            compact_payload = {k: v for k, v in compact_payload.items() if v not in (None, "", [], {})}

        if isinstance(parsed.get("output_meta_v1"), dict):
            compact_payload["output_meta_v1"] = parsed.get("output_meta_v1")

        compact = self._coerce_message_content(compact_payload)
        compact = self._truncate_text(compact, 900)
        stats["compact_chars"] = len(compact)
        stats["compact_field_count"] = len(compact_payload.keys())
        return compact, stats

    def _resolve_user_id_for_profile(self, thread_id: str) -> str:
        tid = str(thread_id or "").strip()
        if tid.startswith("feishu_") and len(tid) > len("feishu_"):
            return tid[len("feishu_") :]
        return tid or "default_user"
