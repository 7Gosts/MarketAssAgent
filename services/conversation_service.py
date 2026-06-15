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
    ) -> None:
        self.agent = agent
        self.session_manager = session_manager
        self.planner = planner or ResponsePlanner()
        self.orchestrator = orchestrator or AssistantOrchestrator(agent)

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
        # 1. 保存用户消息
        self.session_manager.save_user_message(session_id, text)

        # 2. 读取最近历史
        history = self.session_manager.get_recent_messages(
            session_id, limit=history_limit
        )

        # 3. 先规划用户真正要的回答形态，再执行。
        plan = await self.planner.plan(text, session_summary=summarize_history(history))
        result = await self.orchestrator.run(
            text=text,
            plan=plan,
            session_id=session_id,
            history=history,
            invoke_fn=invoke_fn,
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
            plan=plan.model_dump(mode="json"),
        )

        # 5. 保存 assistant 回复（只有成功提取后才保存）
        if reply_text:
            self.session_manager.save_reply(session_id, reply_text)

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
            "polished_text",
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
            for key in ["text", "polished_text", "reply"]:
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
