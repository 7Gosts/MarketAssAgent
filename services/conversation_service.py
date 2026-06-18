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
from typing import Any

from core.agent import MarketReActAgent
from core.agent_context import build_direct_agent_input
from core.fact_store import Fact
from core.memory_api import MemoryAPI
from config.runtime_config import get_agent_context_limits, is_feature_enabled
from memory.session_manager import MarketSessionManager
from services.envelope_builder import build_conversation_envelope
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

        # 3. 唯一主链路：完整上下文直喂主 LLM。
        result, plan_payload = await self._run_direct_context_flow(
            text=text,
            session_id=session_id,
            thread_id=thread_id,
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

        return build_conversation_envelope(
            result=result,
            reply_text=reply_text,
            session_id=session_id,
            user_text=text,
            plan=None,
        )

    async def _run_direct_context_flow(
        self,
        *,
        text: str,
        session_id: str,
        thread_id: str,
        history_for_invoke: list[dict[str, str]],
        invoke_fn: Any | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        storage_key = self._resolve_user_id_for_profile(thread_id)
        user_profile = await self._load_user_profile_context(storage_key=storage_key)
        last_snapshot = self.memory_api.snapshot(thread_id) if self.memory_api else {}
        ctx_limits = get_agent_context_limits()
        recent_sources_limit = int(ctx_limits.get("max_recent_sources") or 3)
        recent_sources = self._load_recent_tool_sources(thread_id=thread_id, limit=recent_sources_limit)
        recent_conclusion = self._build_recent_conclusion_context(
            history=history_for_invoke,
            last_snapshot=last_snapshot,
        )

        direct_input_raw = build_direct_agent_input(
            user_text=text,
            session_id=session_id,
            storage_key=storage_key,
            user_profile=user_profile,
            last_snapshot=last_snapshot,
            recent_sources=recent_sources,
            recent_conclusion=recent_conclusion,
            max_recent_sources=recent_sources_limit,
            max_conclusion_chars=int(ctx_limits.get("max_conclusion_chars") or 240),
        )
        direct_input = build_direct_agent_input(
            user_text=text,
            session_id=session_id,
            storage_key=storage_key,
            user_profile=user_profile,
            last_snapshot=last_snapshot,
            recent_sources=recent_sources,
            recent_conclusion=recent_conclusion,
            max_chars=int(ctx_limits.get("max_chars") or 13434),
            max_recent_sources=recent_sources_limit,
            max_conclusion_chars=int(ctx_limits.get("max_conclusion_chars") or 240),
        )
        direct_context_truncated = len(direct_input) < len(direct_input_raw)
        direct_context_trimmed_chars = max(0, len(direct_input_raw) - len(direct_input))
        logger.info(
            "[ConversationService] direct context session_id=%s history_len=%s profile_keys=%s snapshot_keys=%s recent_sources=%s has_recent_conclusion=%s max_chars=%s truncated=%s dropped_chars=%s input_chars=%s input_preview=%r",
            session_id,
            len(history_for_invoke or []),
            sorted(list((user_profile or {}).keys()))[:8],
            sorted(list((last_snapshot or {}).keys()))[:8],
            len(recent_sources or []),
            bool(recent_conclusion),
            int(ctx_limits.get("max_chars") or 13434),
            direct_context_truncated,
            direct_context_trimmed_chars,
            len(direct_input),
            self._preview_debug_text(direct_input, max_len=260),
        )
        if invoke_fn is not None:
            result = await invoke_fn(
                direct_input,
                session_id=session_id,
                history=history_for_invoke,
            )
        else:
            result = await self.agent.invoke(
                direct_input,
                session_id=session_id,
                history=history_for_invoke,
                allowed_tools=[],
            )

        plan_payload = {
            "mode": "direct_context",
            "task_type": "agent_direct",
            "needs_snapshot": True,
            "user_context_needed": True,
            "storage_key": storage_key,
        }
        return result, plan_payload

    async def _load_user_profile_context(self, *, storage_key: str) -> dict[str, Any] | None:
        if not self.memory_api or not storage_key:
            return None
        if not hasattr(self.memory_api, "get_user_profile"):
            return None
        try:
            profile = await self.memory_api.get_user_profile(storage_key)  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("memory_api.get_user_profile(context) failed: %s", e)
            return None

        if hasattr(profile, "model_dump"):
            try:
                dumped = profile.model_dump(mode="json")  # type: ignore[call-arg]
                return dumped if isinstance(dumped, dict) else None
            except Exception:
                return None
        if isinstance(profile, dict):
            return profile
        return None

    def _load_recent_tool_sources(self, *, thread_id: str, limit: int) -> list[dict[str, Any]]:
        if not self.memory_api:
            return []
        try:
            facts = self.memory_api.recall(thread_id, {"type": "tool_observation"}, limit=max(limit, 1))
        except Exception as e:
            logger.warning("memory_api.recall(tool_observation context) failed: %s", e)
            return []

        rows: list[dict[str, Any]] = []
        for fact in facts[:limit]:
            payload = fact.payload if isinstance(fact.payload, dict) else {}
            provenance = fact.provenance if isinstance(fact.provenance, dict) else {}
            rows.append(
                {
                    "timestamp": str(fact.timestamp or "").strip(),
                    "tool": str(payload.get("tool") or fact.source or "unknown_tool").strip(),
                    "summary": str(payload.get("summary") or "").strip(),
                    "tool_call_id": str(provenance.get("tool_call_id") or "").strip(),
                }
            )
        return rows

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
        for key in ("analysis_result", "last_snapshot"):
            snap = result.get(key)
            if isinstance(snap, dict) and snap:
                return snap
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

    def _build_recent_conclusion_context(
        self,
        *,
        history: list[dict[str, str]],
        last_snapshot: dict[str, Any] | None,
    ) -> dict[str, str]:
        last_user_question = ""
        last_assistant_conclusion = ""

        for row in reversed(history or []):
            role = str(row.get("role") or "").strip()
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            if not last_assistant_conclusion and role == "assistant":
                last_assistant_conclusion = self._truncate_text(text.replace("\n", " "), 220)
                continue
            if not last_user_question and role == "user":
                last_user_question = self._truncate_text(text.replace("\n", " "), 160)
            if last_user_question and last_assistant_conclusion:
                break

        snapshot_hint = ""
        if isinstance(last_snapshot, dict) and last_snapshot:
            symbol = str(last_snapshot.get("symbol") or "").strip()
            interval = str(last_snapshot.get("interval") or "").strip()
            trend = str(last_snapshot.get("trend") or "").strip()
            price = last_snapshot.get("current_price")
            parts: list[str] = []
            if symbol:
                parts.append(symbol)
            if interval:
                parts.append(interval)
            if trend:
                parts.append(f"trend={trend}")
            if isinstance(price, (int, float)):
                parts.append(f"price={price}")
            snapshot_hint = ", ".join(parts)

        out = {
            "last_user_question": last_user_question,
            "last_assistant_conclusion": last_assistant_conclusion,
            "snapshot_hint": snapshot_hint,
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
            # 兜底：保留最小可读事实
            compact_payload = {
                "status": parsed.get("status"),
                "symbol": parsed.get("symbol"),
                "interval": parsed.get("interval"),
                "trend": parsed.get("trend"),
                "current_price": parsed.get("current_price"),
                "message": parsed.get("message"),
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
