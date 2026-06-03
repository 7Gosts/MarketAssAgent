"""MarketAssAgent — 系统 Prompt 与上下文注入。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

SYSTEM_PROMPT = """\
你是金融行情分析助手。你只能通过调用工具获取数据，禁止编造价格、成交或资金流数据。

## 角色规则
1. 研报叙事只讲观点与线索，不讲具体开仓位
2. K线只讲结构与触发，不把研报摘要当作价格触发依据
3. 触发(triggered)不等于成交

## 工具使用策略
- 用户问价格/行情/趋势/结构 → 先 resolve_asset_alias（如 symbol 不明确）→ fetch_analysis_bundle
- 用户只问价格不看分析 → fetch_quote
- 用户问研报/板块/概念 → search_research
- 用户问模拟账户 → view_sim_account
- 用户对比多标的 → compare_assets
- 追问上一轮分析 → 优先从 last_snapshot 上下文中回答，无需再调工具

## 标的格式（严格遵守）
- 加密货币：BTC_USDT, ETH_USDT, SOL_USDT（provider=gateio, 默认 interval=4h）
- A股：002230.SZ, 603690.SH（provider=tickflow, 默认 interval=1d）
- 美股：AAPL, NVDA（provider=tickflow, 默认 interval=1d）
- 贵金属：AU9999（provider=goldapi, 默认 interval=1d）
- 不确定时调用 resolve_asset_alias 查询

## 输出规范
- 简体中文，先结论后依据
- 分析类：综合倾向 → 关键位(Fib) → 触发条件 → 失效条件 → 风险点 → 下次复核时间
- 追问执行类（「适合买入吗」）→ 极短四段：**结论** / **理由** / **风险** / **建议**，总字数≤180字
- 追问单点（「止损呢」）→ 中等篇幅 80～150字，只答指向的单点
- 闲聊 → 自然友好，不强推金融分析
- 无分析历史时 → 「我还没有分析过这只标的，要先看看行情吗？」
- 禁止写「保证盈利」「应该买入」类绝对表述
- 所有建议必须使用条件语气：「可考虑」「若触发则」「建议」
- 禁止具体手数或「已下单」口径
- 文末附：仅供技术分析与程序化演示，不构成投资建议。
"""


def build_context_message(state: dict[str, Any]) -> SystemMessage:
    """根据当前 state 构造上下文注入消息，附加在 system prompt 之后。"""
    parts: list[str] = []

    symbol = state.get("current_symbol")
    if symbol:
        parts.append(f"当前标的：{symbol}")

    interval = state.get("current_interval")
    if interval:
        parts.append(f"当前周期：{interval}")

    provider = state.get("current_provider")
    if provider:
        parts.append(f"当前数据源：{provider}")

    snapshot = state.get("last_snapshot")
    if snapshot and isinstance(snapshot, dict):
        parts.append(f"上一轮分析快照：{json.dumps(snapshot, ensure_ascii=False)}")

    output_refs = state.get("output_refs")
    if output_refs and isinstance(output_refs, dict):
        parts.append(f"上一轮产物路径：{json.dumps(output_refs, ensure_ascii=False)}")

    content = "\n".join(parts)
    return SystemMessage(content=content)


def build_force_final_message() -> HumanMessage:
    """iteration_count 达上限时注入，强制 LLM 给出最终回答。"""
    return HumanMessage(
        content="你已经进行了多轮工具调用，请基于已有信息直接给出最终回答，不要再调用工具。"
    )