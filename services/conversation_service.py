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
import re
import time
from typing import Any

from core.agent import MarketReActAgent
from core.fact_store import Fact
from core.memory_api import MemoryAPI
from core.profile import UserProfile
from config.runtime_config import is_feature_enabled
from memory.session_manager import MarketSessionManager
from services.assistant_orchestrator import AssistantOrchestrator
from services.envelope_builder import build_conversation_envelope
from core.planner import ResponsePlanner, summarize_history
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
        planner: ResponsePlanner | None = None,
        orchestrator: AssistantOrchestrator | None = None,
        memory_api: MemoryAPI | None = None,
    ) -> None:
        self.agent = agent
        self.session_manager = session_manager
        self.planner = planner or ResponsePlanner()
        self.orchestrator = orchestrator or AssistantOrchestrator(agent)
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

        # 3. 先规划用户真正要的回答形态，再执行。
        plan = await self.planner.plan(text, session_summary=summarize_history(history))
        await self._maybe_update_user_profile(
            thread_id=thread_id,
            user_message=text,
            assistant_reply="",
            plan=plan,
        )
        result = await self.orchestrator.run(
            text=text,
            plan=plan,
            session_id=session_id,
            history=history_for_invoke,
            invoke_fn=invoke_fn,
        )
        self._write_tool_observation_facts(
            thread_id=thread_id,
            result=result,
            request_id=request_id,
        )

        # 4. 提取回复文本（统一处理多种可能字段）
        reply_text = self._extract_reply_text(result)
        if plan.required_provenance:
            provenance_block = self._build_provenance_block(thread_id)
            if provenance_block:
                if reply_text:
                    reply_text = f"{reply_text}\n\n{provenance_block}"
                else:
                    reply_text = provenance_block
        self._dump_raw_llm_output(
            session_id=session_id,
            user_text=text,
            history=history,
            result=result,
            reply_text=reply_text,
            extra_meta=extra_meta or {},
            plan=plan.model_dump(mode="json"),
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
            plan=plan,
        )

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

    async def _maybe_update_user_profile(
        self,
        *,
        thread_id: str,
        user_message: str,
        assistant_reply: str,
        plan: Any,
    ) -> None:
        if not self.memory_api:
            return
        if not getattr(plan, "user_context_needed", False):
            return
        if not hasattr(self.memory_api, "get_user_profile") or not hasattr(self.memory_api, "update_user_profile"):
            return

        content = str(user_message or "").strip()
        if not content:
            return

        user_id = self._resolve_user_id_for_profile(thread_id)
        try:
            profile: UserProfile = await self.memory_api.get_user_profile(user_id)  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("memory_api.get_user_profile failed: %s", e)
            return

        changed = False
        style = self._extract_preferred_style(content)
        if style and style != profile.preferred_style:
            profile.preferred_style = style
            changed = True

        risk = self._extract_risk_profile(content)
        if risk and risk != profile.risk_profile:
            profile.risk_profile = risk
            changed = True

        symbols = self._extract_symbols_from_text(content)
        if symbols and any(k in content.lower() for k in ["偏好", "喜欢", "常做", "常看", "关注", "主要做"]):
            merged = list(dict.fromkeys(profile.favorite_symbols + symbols))
            if merged != profile.favorite_symbols:
                profile.favorite_symbols = merged[:20]
                changed = True

        timeframes = self._extract_timeframes(content)
        if timeframes and any(k in content.lower() for k in ["偏好", "喜欢", "常看", "周期", "时间框架"]):
            merged_tf = list(dict.fromkeys(profile.preferred_timeframes + timeframes))
            if merged_tf != profile.preferred_timeframes:
                profile.preferred_timeframes = merged_tf[:10]
                changed = True

        ratio = self._extract_max_position_ratio(content)
        if ratio is not None and ratio != profile.max_position_ratio:
            profile.max_position_ratio = ratio
            changed = True

        note = self._extract_profile_note(content)
        if note:
            merged_note = note if not profile.notes else f"{profile.notes}；{note}"
            if merged_note != profile.notes:
                profile.notes = merged_note[:300]
                changed = True

        if not changed:
            return

        source = self._detect_profile_update_source(user_message, assistant_reply)
        reason = f"从对话中提取：{self._truncate_text(content, 100)}"
        try:
            await self.memory_api.update_user_profile(  # type: ignore[attr-defined]
                profile,
                source=source,
                reason=reason,
            )
        except Exception as e:
            logger.warning("memory_api.update_user_profile failed: %s", e)

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
            summary = self._summarize_tool_content(text_content)
            payload = {
                "tool": tool_name,
                "summary": summary,
                "content": self._truncate_text(text_content, 1200),
            }
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

    def _build_provenance_block(self, thread_id: str, *, limit: int = 3) -> str:
        if not self.memory_api:
            return ""
        try:
            facts = self.memory_api.recall(thread_id, {"type": "tool_observation"}, limit=max(limit, 1))
        except Exception as e:
            logger.warning("memory_api.recall(tool_observation) failed: %s", e)
            return ""
        if not facts:
            return ""

        lines = ["**依据来源**"]
        for fact in facts[:limit]:
            payload = fact.payload if isinstance(fact.payload, dict) else {}
            provenance = fact.provenance if isinstance(fact.provenance, dict) else {}
            source = str(payload.get("tool") or fact.source or "unknown")
            summary = str(payload.get("summary") or "").strip()
            tool_call_id = str(provenance.get("tool_call_id") or "").strip()
            ts = str(fact.timestamp or "").strip()
            suffix = f"（tool_call_id: {tool_call_id}）" if tool_call_id else ""
            body = summary or "返回了结构化结果。"
            lines.append(f"- {ts} `{source}`: {body}{suffix}")
        return "\n".join(lines)

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

    def _resolve_user_id_for_profile(self, thread_id: str) -> str:
        tid = str(thread_id or "").strip()
        if tid.startswith("feishu_") and len(tid) > len("feishu_"):
            return tid[len("feishu_") :]
        return tid or "default_user"

    def _detect_profile_update_source(self, user_message: str, assistant_reply: str) -> str:
        content = f"{user_message}\n{assistant_reply}".lower()
        explicit_markers = [
            "我偏好",
            "我喜欢",
            "我习惯",
            "我不喜欢",
            "我不做",
            "我只做",
            "我风险",
            "my style",
            "my risk",
            "i prefer",
            "i only",
            "i don't",
        ]
        if any(marker in content for marker in explicit_markers):
            return "user_explicit"
        return "llm_inference"

    def _extract_preferred_style(
        self, text: str
    ) -> str | None:
        lowered = text.lower()
        if any(k in text for k in ["右侧", "右侧交易"]) or "right side" in lowered:
            return "right_side"
        if any(k in text for k in ["左侧", "左侧交易"]) or "left side" in lowered:
            return "left_side"
        if any(k in text for k in ["波段", "swing"]):
            return "swing"
        if any(k in text for k in ["短线", "超短", "scalp", "scalping"]):
            return "scalping"
        return None

    def _extract_risk_profile(self, text: str) -> str | None:
        lowered = text.lower()
        if any(k in text for k in ["保守", "稳健"]) or "conservative" in lowered:
            return "conservative"
        if any(k in text for k in ["平衡", "均衡"]) or "balanced" in lowered:
            return "balanced"
        if "激进" in text or "aggressive" in lowered:
            return "aggressive"
        return None

    def _extract_symbols_from_text(self, text: str) -> list[str]:
        upper = text.upper()
        tokens: list[str] = []
        for token in re.findall(r"[A-Z]{2,10}(?:USDT|USD)?|[0-9]{6}|AU[0-9]{1,4}", upper):
            if token in {"USD", "USDT"}:
                continue
            if token == "BTC":
                token = "BTCUSDT"
            elif token == "ETH":
                token = "ETHUSDT"
            tokens.append(token)
        return list(dict.fromkeys(tokens))

    def _extract_timeframes(self, text: str) -> list[str]:
        lowered = text.lower()
        out: list[str] = []
        if "15m" in lowered or "15分钟" in text:
            out.append("15m")
        if "1h" in lowered or "1小时" in text or "小时" in text:
            out.append("1h")
        if "4h" in lowered or "4小时" in text:
            out.append("4h")
        if "1d" in lowered or "日线" in text:
            out.append("1d")
        return out

    def _extract_max_position_ratio(self, text: str) -> float | None:
        m = re.search(r"(?:单仓|仓位).{0,10}?(\d{1,3}(?:\.\d+)?)\s*%", text)
        if not m:
            return None
        try:
            v = float(m.group(1))
        except ValueError:
            return None
        if v <= 0:
            return None
        if v > 100:
            return None
        return round(v / 100.0, 4)

    def _extract_profile_note(self, text: str) -> str:
        hints = ["不喜欢", "不做", "不追高", "只做", "偏好", "习惯"]
        if any(k in text for k in hints):
            return self._truncate_text(text.replace("\n", " "), 120)
        return ""
