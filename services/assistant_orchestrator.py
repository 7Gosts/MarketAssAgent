from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from core.agent import MarketReActAgent
from schemas.response_plan import ResponsePlan


_ASSISTANT_DIRECT_PROMPT = """你是一个谨慎、直接、懂市场的助手。

目标：先理解用户真正要解决的问题，再给出有帮助的回答。

原则：
- 不要把所有问题都写成行情分析报告。
- 不需要实时数据的问题，直接解释或对话。
- 需要行情、开单、仓位判断时，结合工具结果和用户目标回答。
- 语言自然、清楚、克制，像一个靠谱的交易助手。
- 不要暴露 Thought / Action / Observation。
- 不确定时说明条件，不要编造实时数据。
"""


class AssistantOrchestrator:
    """Executes a ResponsePlan with either direct LLM chat or tool-capable agent."""

    def __init__(self, agent: MarketReActAgent):
        self.agent = agent

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

        if not plan.needs_tools:
            return await self._direct_reply(text=text, plan=plan, history=history)

        planned_text = self._build_agent_input(text, plan)
        return await self.agent.invoke(planned_text, session_id=session_id, history=history)

    async def _direct_reply(
        self,
        *,
        text: str,
        plan: ResponsePlan,
        history: list[dict[str, str]] | None,
    ) -> dict[str, Any]:
        messages: list[Any] = [SystemMessage(content=_ASSISTANT_DIRECT_PROMPT)]
        for item in history or []:
            content = str(item.get("text") or "")
            if not content:
                continue
            if item.get("role") == "user":
                messages.append(HumanMessage(content=content))
            else:
                messages.append(AIMessage(content=content))

        messages.append(
            HumanMessage(
                content=(
                    f"任务类型: {plan.task_type}\n"
                    f"回答段落: {', '.join(plan.sections)}\n"
                    f"用户问题: {text}"
                )
            )
        )
        response = await self.agent.llm.ainvoke(messages)
        return {"reply": str(response.content).strip(), "plan": plan.model_dump(mode="json")}

    def _build_agent_input(self, text: str, plan: ResponsePlan) -> str:
        hints = []
        if plan.symbol_hint:
            hints.append(f"标的优先按 {plan.symbol_hint} 处理")
        if plan.interval_hint:
            hints.append(f"周期优先按 {plan.interval_hint} 处理")

        hint_text = "\n".join(f"- {item}" for item in hints) or "- 无"
        return (
            "请作为市场助手处理用户请求。\n"
            f"任务类型: {plan.task_type}\n"
            f"需要的回答段落: {', '.join(plan.sections)}\n"
            f"工具使用提示:\n{hint_text}\n\n"
            "要求：需要数据就调用工具；最终回答贴合用户问题，不要写模板化报告，不要暴露推理过程。\n\n"
            f"用户原话: {text}"
        )
