"""Writer — 撰稿模块，用 Writer LLM 对 Agent 原始输出做润色"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from config.runtime_config import get_router_config


_DEFAULT_WRITER_PROMPT = """你是一个金融市场分析撰稿人。你的任务是将 Agent 的原始技术分析输出润色为专业、易读的最终回复。

要求：
1. 保留所有核心分析结论和关键位数据（支撑/阻力/趋势判断/置信度）
2. 使用条件化语言（"若...则可考虑..."格式），不做绝对判断
3. 保持结构清晰：
   - 【行情结论】简明趋势判断
   - 【关键点】支撑/阻力位
   - 【结构分析】均线排列、量价关系
   - 【操作建议】条件化建议（含入场/止损/止盈参考）
   - 【风险提示】重要风险因素
4. 末尾必须附带免责声明："仅供技术分析与程序化演示，不构成投资建议。投资有风险，入市需谨慎。"
5. 不要添加 Agent 未提及的信息
6. 字数控制在 300-500 字以内，精炼不冗余"""


class Writer:
    """撰稿模块：用 Writer LLM 对 Agent 原始输出做润色"""

    def __init__(
        self,
        llm: Any | None = None,
        *,
        temperature: float | None = None,
    ) -> None:
        # 从 YAML 读取配置
        cfg = get_router_config()
        temp = temperature or float(cfg.get("writer_temperature", 0.3))

        # LLM 初始化：复用主 Agent 的 provider 配置
        if llm is None:
            from config.runtime_config import get_llm_runtime_settings, require_llm_model
            llm_settings = get_llm_runtime_settings()

            # writer_model 优先级：YAML 配置 > 主 LLM 配置
            writer_model = str(cfg.get("writer_model") or "").strip()
            model = writer_model or require_llm_model(llm_settings, context="Writer")

            llm = ChatOpenAI(
                model=model,
                temperature=temp,
                base_url=llm_settings.get("base_url") or None,
                api_key=llm_settings.get("api_key") or None,
            )
        self._llm = llm
        self._system_prompt = _DEFAULT_WRITER_PROMPT

    async def polish(
        self, raw_output: str, *, user_question: str = ""
    ) -> str:
        """润色 Agent 原始输出

        Args:
            raw_output: Agent 的原始分析文本
            user_question: 用户的原始问题（用于保持针对性）

        Returns:
            润色后的最终文本
        """
        user_content = (
            f"用户问题: {user_question}\n\nAgent原始输出:\n{raw_output}"
            if user_question
            else raw_output
        )

        messages = [
            SystemMessage(content=self._system_prompt),
            HumanMessage(content=user_content),
        ]
        response = await self._llm.ainvoke(messages)
        return response.content

    async def polish_or_fallback(
        self, raw_output: str, *, user_question: str = ""
    ) -> str:
        """安全润色：失败时返回原文"""
        try:
            return await self.polish(raw_output, user_question=user_question)
        except Exception as e:
            print(f"[Writer] 润色失败，返回原文: {e}")
            return raw_output
