from __future__ import annotations

from datetime import datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from core.agent import MarketReActAgent
from core.planner import ResponsePlan
from core.prompts import get_full_prompt
from services.envelope_builder import EnvelopeBuilder
from utils.logging_utils import get_logger


TOOL_GROUP_MAP: dict[str, list[str]] = {
    "market_data": ["fetch_market_data"],
    "technical_analysis": ["analyze_market", "get_key_levels", "evaluate_structure", "analyze_multi"],  # analyze_market 为默认首选工具
    "research": ["search_research_reports"],
    "sim_account": ["simulate_open_position", "get_journal_status"],
    "journal": ["get_journal_status"],
}

logger = get_logger(__name__)


class AssistantOrchestrator:
    """
    助手编排器 - 根据 ResponsePlan 决定执行路径
    这是把 'Planner' 真正落地的核心层
    """

    def __init__(
        self,
        agent_graph: MarketReActAgent,
        chat_llm: Any,
        tools_registry: Any,
        envelope_builder: EnvelopeBuilder,
    ):
        self.agent_graph = agent_graph
        self.chat_llm = chat_llm
        self.tools_registry = tools_registry
        self.envelope_builder = envelope_builder

    async def execute(self, plan: ResponsePlan, user_message: str, session: dict[str, Any]) -> dict[str, Any]:
        """主执行入口"""
        trace = {
            "task_type": plan.task_type,
            "required_tools": list(plan.required_tools),
            "allowed_tools": [],
            "actual_tools_called": [],
            "timestamp": datetime.now().isoformat(),
        }
        allowed_tools = self._filter_tools_by_plan(plan)
        trace["allowed_tools"] = allowed_tools
        context = await self._build_context(plan, session)
        context["allowed_tools"] = allowed_tools
        context["trace"] = trace

        if plan.task_type == "chat":
            result = await self._handle_chat(user_message, session, context)
        elif plan.task_type == "rule_explain":
            result = await self._handle_rule_explain(user_message, session, context)
        elif plan.task_type in ["market_view", "trade_plan", "position_review", "comparison"]:
            result = await self._handle_agent_flow(plan, user_message, session, context, allowed_tools)
        elif plan.task_type in ["journal_review", "watchlist"]:
            result = await self._handle_journal_related(plan, user_message, session, context, allowed_tools)
        else:
            result = await self._handle_default(plan, user_message, session, context)

        trace["actual_tools_called"] = result.get("actual_tools_called", [])
        logger.info("[ORCHESTRATOR TRACE] %s", trace)
        result["orchestrator_trace"] = trace
        return result

    # Backward-compatible entry used by current ConversationService.
    async def run(
        self,
        *,
        text: str,
        plan: ResponsePlan,
        session_id: str,
        history: list[dict[str, str]] | None = None,
        invoke_fn: Any | None = None,
    ) -> dict[str, Any]:
        if invoke_fn is not None:
            return await invoke_fn(text, session_id=session_id, history=history)
        session = {
            "session_id": session_id,
            "history": history or [],
        }
        return await self.execute(plan, text, session)

    # 已知的大类名称（用于判断 required_tools 里是“大类”还是“具体工具名”）
    _KNOWN_TOOL_GROUPS: set[str] = {
        "market_data",
        "technical_analysis",
        "research",
        "sim_account",
        "journal",
    }

    def _filter_tools_by_plan(self, plan: ResponsePlan) -> list[str]:
        """根据 Plan 过滤可用工具。

        设计原则（弱化强制性）：
        - 默认返回全量工具，让 LLM 自主选择。
        - 只有当 required_tools 明确指定了**具体工具名**时，才做精确过滤。
        - 如果 required_tools 里只有大类，则忽略限制，返回全量工具。
        """
        all_tool_names = [getattr(t, "name", "") for t in self.tools_registry if getattr(t, "name", "")]
        if not plan.required_tools:
            return all_tool_names

        # 判断 required_tools 里是否包含“具体工具名”（而非仅大类）
        has_specific_tool = any(
            item not in self._KNOWN_TOOL_GROUPS
            for item in plan.required_tools
        )

        if not has_specific_tool:
            # 只有大类或空 → 返回全量工具，让 LLM 自主决策
            return all_tool_names

        # 只有当明确指定了具体工具名时，才做精确过滤
        selected: list[str] = []
        for group in plan.required_tools:
            for tool_name in TOOL_GROUP_MAP.get(group, []):
                if tool_name in all_tool_names and tool_name not in selected:
                    selected.append(tool_name)
        return selected if selected else all_tool_names

    async def _build_context(self, plan: ResponsePlan, session: dict[str, Any]) -> dict[str, Any]:
        """构建增强上下文"""
        ctx = {
            "plan": plan.model_dump(mode="json"),
            "user_profile": session.get("user_profile") if plan.user_context_needed else None,
            "last_snapshot": session.get("last_snapshot") if plan.needs_snapshot else None,
            "key_focus": plan.key_focus,
        }
        return ctx

    async def _handle_chat(self, user_message: str, session: dict[str, Any], context: dict[str, Any]):
        """纯闲聊路径"""
        response = await self.chat_llm.ainvoke([HumanMessage(content=user_message)])
        return {
            "reply": str(response.content),
            "plan": context.get("plan"),
            "actual_tools_called": [],
        }

    async def _handle_rule_explain(self, user_message: str, session: dict[str, Any], context: dict[str, Any]):
        """规则解释路径"""
        response = await self.chat_llm.ainvoke(
            [
                SystemMessage(content="你是交易规则解释助手，请直接、清晰、专业回答。"),
                HumanMessage(content=f"用户询问交易规则：{user_message}"),
            ]
        )
        return {
            "reply": str(response.content),
            "plan": context.get("plan"),
            "actual_tools_called": [],
        }

    async def _handle_agent_flow(
        self,
        plan: ResponsePlan,
        user_message: str,
        session: dict[str, Any],
        context: dict[str, Any],
        allowed_tools: list[str],
    ):
        """走 ReAct 主流程"""
        history = session.get("history") or []
        input_state = get_full_prompt(plan, user_message)
        result = await self.agent_graph.invoke(
            input_state,
            session_id=session.get("session_id", "default"),
            history=history,
            allowed_tools=allowed_tools,
        )
        result["actual_tools_called"] = _extract_actual_tools_called(result)
        return result

    async def _handle_journal_related(
        self,
        plan: ResponsePlan,
        user_message: str,
        session: dict[str, Any],
        context: dict[str, Any],
        allowed_tools: list[str],
    ):
        """复盘、台账相关"""
        return await self._handle_agent_flow(plan, user_message, session, context, allowed_tools)

    async def _handle_default(
        self,
        plan: ResponsePlan,
        user_message: str,
        session: dict[str, Any],
        context: dict[str, Any],
    ):
        """兜底"""
        return await self._handle_agent_flow(
            plan,
            user_message,
            session,
            context,
            context.get("allowed_tools", []),
        )


def _extract_actual_tools_called(result: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for msg in result.get("messages") or []:
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls and isinstance(msg, dict):
            tool_calls = msg.get("tool_calls")
        for call in tool_calls or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    return names
