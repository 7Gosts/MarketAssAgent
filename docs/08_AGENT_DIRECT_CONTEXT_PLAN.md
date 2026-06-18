# Agent Direct Context 优化施工方案

**日期**: 2026-06-18  
**目标读者**: 本地低级 AI Agent / 执行型工程 Agent  
**状态**: Phase B + 兼容层收口已完成（Direct Context 唯一主链路）  
**核心原则**: 不用代码预判用户意图；把完整上下文交给主 LLM，由主 LLM 自主理解、调用工具、组织回复。

---

## 0. 当前进度（2026-06-18）

- 已完成：`docs/00_PROJECT_ARCHITECTURE.md` 增加“Planner 主链路与产品哲学冲突”的架构原则。
- 已完成：Direct Context 构造模块 `core/agent_context.py`。
- 已完成：`ConversationService.run()` 切为 Direct Context 唯一主流程，移除 planner/orchestrator 分支。
- 已完成：`app/factory.py` 不再初始化 planner/orchestrator，运行时只装配单 LLM 主链路。
- 已完成：移除 `agent_direct_context_mode` feature flag，避免双轨回流。
- 已完成：`get_response_guidance` 工具接入工具注册，并在 `core/prompt.py` 引导按需调用。
- 已完成：Direct Context 测试覆盖（画像读取失败降级、recent sources 格式/截断、memory-only 历史链路）。

> 注：下文 Phase A/B/C/D 保留为历史施工记录，不再作为待执行计划。涉及 `agent_direct_context_mode`、`core/planner.py`、`core/orchestrator.py`、`core/prompts.py`、`core/planner_prompt.py` 的步骤已失效，以本节“当前进度”和“最终验收”为准。

---

## 0.1 最终验收（当前代码）

当前实现已经符合本文最初方向：

| 验收项 | 当前状态 |
| --- | --- |
| 不用代码预判用户意图 | 已满足：生产链路无 Planner / Orchestrator |
| 完整上下文交给主 LLM | 已满足：`ConversationService` 构造 Direct Context 后调用 `MarketReActAgent.invoke` |
| history 不重复塞入 Direct Context | 已满足：history 仍通过 `agent.invoke(..., history=history_for_invoke)` 传入 |
| 每轮注入 compact user profile | 已满足：`core/agent_context.py` 只保留画像关键字段 |
| 注入 last_snapshot | 已满足：来自 MemoryAPI checkpoint snapshot |
| 注入 recent_sources | 已满足：最近 `tool_observation` 最多 3 条 |
| 主 LLM 自主决定工具调用 | 已满足：`agent.invoke(..., allowed_tools=[])` 表示图层暴露全量工具 |
| 递进提示而非大模板 | 已满足：`get_response_guidance` 作为按需工具注册 |
| 删除兼容层 | 已满足：planner/orchestrator/prompts 文件和旧测试已删除，guard 防回流 |

当前主链路：

```text
用户消息
  -> ConversationService.run()
  -> 读取 history / user_profile / last_snapshot / recent_sources
  -> build_direct_agent_input()
  -> MarketReActAgent.invoke(direct_input, history=history_for_invoke, allowed_tools=[])
  -> LangGraph ReAct 自主调用工具
  -> ConversationEnvelope
```

---

## 1. 背景与问题

当前主链路是：

```text
用户消息
  -> ConversationService.run()
  -> ResponsePlanner.plan()
  -> AssistantOrchestrator.run()
  -> MarketReActAgent.invoke()
  -> LangGraph ReAct + tools
  -> ConversationEnvelope
```

这套结构仍然能工作，但 `ResponsePlanner` 在业务上承担了“预先判断用户意图”的角色：

- 它先生成 `task_type / required_tools / response_style / needs_snapshot / user_context_needed / required_provenance`。
- Orchestrator 再根据 `task_type` 分流到直答或 Agent Flow。
- 这会让部分语义先被一个小规划器压缩，再交给真正执行的 LLM。

对于“行情分析助手”这个业务，用户表达经常很自然，例如：

- “刚才说的点位还能用吗？”
- “那我现在能不能轻仓？”
- “SOL 和 ETH 哪个更强？”
- “你怎么知道这个支撑有效？”

这类问题最好由主 LLM 在完整上下文中理解，而不是先由代码或独立 Planner 预判。

---

## 2. 目标架构

目标主链路：

```text
用户消息
  -> ConversationService.run()
  -> 读取 history / last_snapshot / user_profile / recent_sources
  -> 构造 Direct Agent Context
  -> MarketReActAgent.invoke(full_context_user_input, history, allowed_tools=[])
  -> 主 LLM 先看能力地图；需要更细说明时主动调用 get_response_guidance
  -> LangGraph ReAct 自主决定是否调用工具
  -> ConversationEnvelope
```

### 2.1 关键变化

| 当前 | 目标 |
| --- | --- |
| 每轮先跑 `ResponsePlanner` | 默认不跑 Planner |
| `task_type` 先决定执行路径 | 主 LLM 在完整上下文中自行判断 |
| Orchestrator 根据 task 分流 | Orchestrator 退为可选兼容层或删除 |
| `required_tools` 影响工具暴露 | 直接暴露全量工具，由 System Prompt 约束选择 |
| `user_context_needed` 决定是否注入画像 | 每轮注入 compact user profile |
| `required_provenance` 决定是否追加来源 | 每轮注入 recent sources，主 LLM 按需引用 |
| 大 prompt 一次性塞完整格式规则 | 常驻能力地图 + LLM 自主按需调用 `get_response_guidance` |

### 2.2 不做的事

- 不新增关键词路由。
- 不用代码根据 symbol / 周期 / 关键词判断用户意图。
- 不把 Planner 改成另一个规则分类器。
- 不在 Feishu / Web Adapter 层增加业务逻辑。
- 不把所有行情/交易/复盘输出模板一次性塞进 System Prompt。

### 2.3 推荐提示策略：Capability Map + Self-Requested Guidance Tool

本方案采用“递进提示加载”，对应前期讨论中的 **方案 B**：

```text
常驻 System Prompt
  -> 只告诉主 LLM：你有哪些能力、什么时候应调用工具、事实边界是什么
Direct Context
  -> 只提供事实材料：history / user_profile / last_snapshot / recent_sources / 当前消息
get_response_guidance 工具
  -> 当主 LLM 判断自己需要更具体工作说明时，由它主动调用
```

这不是 Planner：

- 不由代码判断用户意图。
- 不由独立 LLM 先给 `task_type`。
- 不强制外部分类再执行。
- 只是给主 LLM 一个“可按需翻阅的短说明书”。

`get_response_guidance` 只返回写作/决策指导，不读取行情、不调用外部 API、不决定业务路径。

必须避免的实现：

- 不要让 `get_response_guidance` 接收原始用户消息并返回分类结果。
- 不要让 `get_response_guidance` 选择工具。
- 不要在 `ConversationService` 中用代码决定 guidance 类型。
- 不要把 guidance 调用作为每轮必经步骤。
- 不要把 guidance 文本原样输出给用户。

---

## 3. 新上下文格式

新增一个 Direct Context 文本块，作为本轮 `user_input` 的前置上下文。

建议格式：

```text
【运行上下文】
session_id: {session_id}
storage_key: {storage_key}

【用户画像】
{compact_user_profile_json_or_empty}

【上一轮市场快照】
{last_snapshot_json_or_empty}

【最近工具来源】
- {timestamp} {tool}: {summary}
- {timestamp} {tool}: {summary}

【用户当前消息】
{user_message}
```

要求：

- `用户当前消息` 必须保留原文，不要改写。
- `history` 仍通过 `agent.invoke(..., history=history_for_invoke)` 传入，不要重复塞进 Direct Context。
- `user_profile` 只放 compact 字段，避免把完整审计日志塞给 LLM。
- `last_snapshot` 只保留最近一次 checkpoint。
- `recent_sources` 默认最多 3 条。

---

## 4. 分阶段施工

### 历史 Phase A: 加 Direct Context + Guidance Tool 模式

目标：保留 Planner 旧链路，但新增可切换的新链路；同时加入 `get_response_guidance`，让主 LLM 按需获取短提示，而不是一次性背完整规则。

#### A1. 新增配置

修改 `config/analysis_defaults.yaml`：

```yaml
feature_flags:
  agent_direct_context_mode: true
```

读取仍走现有 `is_feature_enabled("agent_direct_context_mode")`。

#### A2. 新增上下文构造模块

新增文件：`core/agent_context.py`

建议接口：

```python
from __future__ import annotations

import json
from typing import Any


def build_direct_agent_input(
    *,
    user_text: str,
    session_id: str,
    storage_key: str,
    user_profile: dict[str, Any] | None,
    last_snapshot: dict[str, Any] | None,
    recent_sources: list[dict[str, Any]] | None,
) -> str:
    ...
```

实现要求：

- 使用 `json.dumps(..., ensure_ascii=False, default=str)` 序列化 dict。
- profile 只保留这些字段：
  - `preferred_style`
  - `risk_profile`
  - `market_bias`
  - `favorite_symbols`
  - `preferred_timeframes`
  - `max_position_ratio`
  - `notes`
  - `observations` 最近 5 条
  - `style_history` 最近 5 条
- recent_sources 每条只保留：
  - `timestamp`
  - `tool`
  - `summary`
  - `tool_call_id`
- 空内容输出 `无`，不要输出 Python `None`。

#### A3. 修改 ConversationService.run()

文件：`services/conversation_service.py`

在读取 history 后，判断 direct mode：

```python
direct_mode = is_feature_enabled("agent_direct_context_mode", default=True)
```

如果 direct mode 为 true：

1. 不调用 `self.planner.plan(...)`。
2. 不调用 `_maybe_update_user_profile(...)` 规则兜底。
3. 读取 user profile：
   - `storage_key = self._resolve_user_id_for_profile(thread_id)`
   - `profile = await self.memory_api.get_user_profile(storage_key)`，失败则置空并 warning。
4. 读取 last_snapshot：
   - `snapshot = self.memory_api.snapshot(thread_id)`。
5. 读取 recent tool sources：
   - `self.memory_api.recall(thread_id, {"type": "tool_observation"}, limit=3)`。
6. 调用 `build_direct_agent_input(...)`。
7. 直接执行：

```python
result = await self.agent.invoke(
    direct_input,
    session_id=session_id,
    history=history_for_invoke,
    allowed_tools=[],
)
```

8. 构造一个最小 plan-like metadata，用于 envelope/debug：

```python
plan_meta = {
    "mode": "direct_context",
    "task_type": "agent_direct",
    "needs_snapshot": True,
    "user_context_needed": True,
}
```

注意：如果 `build_conversation_envelope` 强依赖 `ResponsePlan`，先新增兼容函数或允许 `plan` 为 dict；不要为了兼容而重新调用 Planner。

如果 direct mode 为 false，保留原 Planner 链路。

#### A4. 调整 Prompt：常驻能力地图

文件：`core/prompt.py`

把 `core/prompt.py` 调整成“短常驻规则 + 能力地图”。不要把所有输出模板塞进去。

建议新增或改写为以下方向：

```text
【能力地图】
你可以根据用户当前问题自主选择：
- 行情分析：需要实时行情、趋势、关键位、量价结构时，可调用 analyze_market。
- 多标的对比：用户比较两个或多个标的时，也调用 analyze_market，并通过 `symbol_interval_map` 传入多个标的。
- 交易计划：用户问入场、止损、止盈、仓位时，先确认事实充分；事实不足时调用工具。
- 持仓/台账：用户问还能不能拿、减仓、已有仓位时，可结合 get_journal_status 和 last_snapshot。
- 规则解释：用户问概念、方法、规则时，可直接解释，不要强行调用行情工具。
- 用户画像：用户明确表达偏好、风险态度、风格变化时，可调用 update_user_profile。
- 来源追溯：用户问依据、来源、怎么知道时，优先参考最近工具来源。

【递进提示】
- 如果你已经理解用户问题，但不确定某类任务应该如何组织回复，可以调用 get_response_guidance。
- get_response_guidance 只提供短说明，不替你判断用户意图。
- 不要每轮都调用 get_response_guidance；只有交易计划、持仓复盘、复杂对比、来源追问等需要更严谨结构时再调用。

【上下文使用】
- 输入可能包含【运行上下文】【用户画像】【上一轮市场快照】【最近工具来源】【用户当前消息】。
- 这些上下文是事实材料，最终回复不要机械复述全部上下文。
- 用户追问风险、买入建议、还能不能拿、刚才点位是否有效时，优先参考上一轮市场快照。
- 用户问“依据/来源/怎么知道”时，优先引用最近工具来源；没有来源时明确说明当前上下文不足。
- 用户画像只能用于调整风险表达和仓位建议，不要把画像内容原样暴露给用户。
```

保留现有工具选择策略。

#### A5. 新增 Response Guidance 工具

新增文件：`tools/response_guidance.py`

建议实现：

```python
from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool


GuidanceType = Literal[
    "market_view",
    "comparison",
    "trade_plan",
    "position_review",
    "rule_explain",
    "provenance",
    "profile_update",
]


_GUIDANCE: dict[str, str] = {
    "market_view": (
        "行情分析回复应先给结论，再列关键事实：趋势、关键支撑/阻力、量价变化。"
        "只有机会明确时才给条件化操作建议；方向不清时给观察条件。"
    ),
    "comparison": (
        "多标的对比应聚焦相对强弱、结构差异、关键触发条件。"
        "如果没有明显优势，不要强行选边。"
    ),
    "trade_plan": (
        "交易计划必须包含：入场触发、止损、止盈/目标、仓位、失效条件。"
        "事实不足时先调用行情或台账工具，不要凭空给价格。"
    ),
    "position_review": (
        "持仓复盘应优先关注风险、是否破坏原计划、是否需要减仓或移动止损。"
        "结合 last_snapshot、用户画像和台账，不要只复述行情。"
    ),
    "rule_explain": (
        "规则解释应直接、清晰、少术语。可以给例子，但不要强行调用行情工具。"
    ),
    "provenance": (
        "来源追问应优先引用最近工具来源和 last_snapshot。"
        "没有可靠来源时明确说明上下文不足，不要编造依据。"
    ),
    "profile_update": (
        "画像更新只在用户明确表达偏好、风险态度、风格变化时执行。"
        "更新时必须写 reason 和 confidence；优先追加 observations/style_history。"
    ),
}


@tool
def get_response_guidance(guidance_type: GuidanceType) -> str:
    """获取某类回复的短指导。由主 LLM 在需要更严谨结构时主动调用。"""
    return _GUIDANCE.get(guidance_type, "保持简洁、客观、条件化表达。")
```

修改 `tools/registry.py`：

- 安全导入 `get_response_guidance`。
- 在 `get_all_tools()` 中注册它。

注意：

- `get_response_guidance` 是“提示工具”，不是业务工具。
- 它不应读取配置、不访问网络、不读写 memory。
- 它的描述要短，避免 LLM 每轮都调用。

#### A6. 测试

新增测试文件：`tests/test_direct_agent_context_flow.py`

测试用例：

1. `agent_direct_context_mode=true` 时不调用 `planner.plan`。
2. direct input 中包含 `storage_key`、`用户当前消息`、`上一轮市场快照`。
3. direct mode 调用 `agent.invoke(..., allowed_tools=[])`。
4. `user_profile` 读取失败时不影响回复。
5. direct mode 下仍写入 user/assistant recent_message facts。
6. `get_response_guidance("trade_plan")` 返回入场、止损、止盈、仓位、失效条件。
7. `tools.registry.get_all_tools()` 包含 `get_response_guidance`。

建议使用 fake agent：

```python
class FakeAgent:
    async def invoke(self, user_input, session_id="default", history=None, allowed_tools=None):
        self.last_user_input = user_input
        self.last_allowed_tools = allowed_tools
        return {"reply": "测试回复", "messages": []}
```

验收命令：

```bash
python3 -m pytest -q tests/test_direct_agent_context_flow.py tests/test_phase_c_memory_flow.py tests/test_prompts_storage_key.py
python3 -m pytest -q tests/test_response_guidance_tool.py
```

---

### 历史 Phase B: 默认 Direct Context，弱化 Planner

目标：新链路稳定后，把 Planner 从主路径移出。

#### B1. app/factory.py

文件：`app/factory.py`

修改：

- `ConversationService(...)` 不再显式传 `planner=ResponsePlanner()`。
- 如果 `ConversationService.__init__` 中 direct mode 默认为 true，可以延迟创建 Planner。

建议：

```python
self.planner = planner
```

只有 legacy mode 需要 planner 时再创建：

```python
if not direct_mode:
    planner = self.planner or ResponsePlanner()
```

目的：Direct Context 模式下启动时不要创建第二个 ChatOpenAI 客户端。

#### B2. core/prompts.py

当前 `core/prompts.py` 依赖 `ResponsePlan`。Direct 模式后它只服务 legacy planner path。

处理方式：

- 文件保留，但顶部注释标记为 legacy planner path。
- 不再从新链路引用 `get_full_prompt`。

#### B3. core/orchestrator.py

短期保留，但只服务 legacy mode。

要求：

- Direct mode 不调用 `AssistantOrchestrator.run()`。
- 保留测试覆盖 legacy path。

#### B4. 日志与 Debug

`ConversationService._dump_raw_llm_output(...)` 的 `plan` 字段在 direct mode 写：

```json
{"mode": "direct_context"}
```

这样 debug 文件可以明确区分旧链路和新链路。

验收命令：

```bash
python3 -m pytest -q
bash scripts/feishu_dev.sh
bash scripts/web_dev.sh
```

人工验收：

- 飞书问：“看看 ETH 1h 行情”
- 飞书追问：“刚才那个点位还能用吗？”
- Web 问：“SOL 和 ETH 哪个更强？”
- Web 追问：“依据是什么？”

期望：

- 不出现 planner fallback 日志。
- 回复能参考 history / snapshot。
- 需要行情时主 LLM 能自主调用统一的 `analyze_market`。
- 不需要行情的规则解释不强行调用工具。

---

### 可选后续 Phase C: 图内自选 Guidance 节点

目标：当 `get_response_guidance` 工具模式稳定后，可以考虑把 Guidance 从“普通工具”升级为图内节点，使它更可观测、更容易控制，但仍然由主 LLM 主动请求。

这对应前期讨论中的 **方案 C**，不是当前首选施工项。

#### C1. 适用条件

只有满足以下条件才进入本阶段：

- Direct Context 已默认启用并稳定。
- `get_response_guidance` 的调用频率可接受。
- 主 LLM 确实会在复杂任务中主动取 guidance。
- 团队希望把 guidance 和业务工具列表分离。

#### C2. 目标结构

```text
reason
  -> if guidance_requested: guidance_node
  -> act tools
  -> reason
  -> supervisor/final
```

说明：

- `guidance_node` 不判断用户意图。
- `guidance_node` 只根据主 LLM 请求的 `guidance_hint` 返回短提示。
- `guidance_text` 注入下一轮 reason 上下文。
- 主 LLM 仍然决定是否调用行情、画像、台账等工具。

#### C3. 施工要点

候选修改文件：

- `core/state.py`：增加 `guidance_hint: str | None`、`guidance_text: str | None`。
- `core/response_guidance.py`：从 `tools/response_guidance.py` 抽出纯函数 `get_guidance_text(kind: str) -> str`。
- `core/graph.py`：增加 `guidance` node，并在 `reason` 后增加条件边。
- `core/prompt.py`：提示 LLM 可请求 guidance，但不要暴露内部字段给用户。

建议先不要删除 `get_response_guidance` 工具，等图内节点验证稳定后再移除工具版本。

#### C4. 验收

新增测试：

- `tests/test_graph_guidance_node.py`

测试点：

- LLM 请求 guidance 时，graph 会写入 `guidance_text`。
- 未请求 guidance 时，不进入 guidance node。
- guidance node 不调用外部 API。
- 最终用户回复不暴露 `guidance_hint` / `guidance_text` 原始字段。

---

### 历史 Phase D: 删除 Planner 主链路遗留

目标：确认 Direct Context 稳定后，清理预规划架构，降低维护成本。

#### D1. 删除或归档

候选文件：

- `core/planner.py`
- `core/planner_prompt.py`
- `core/prompts.py`
- `core/orchestrator.py`
- `services/assistant_orchestrator.py`

删除前先全局搜索：

```bash
rg -n "ResponsePlanner|ResponsePlan|AssistantOrchestrator|get_full_prompt|PLANNER_SYSTEM_PROMPT"
```

如果还有测试或文档引用，按以下原则处理：

- 生产路径引用：迁移到 Direct Context。
- 测试引用：改为 direct mode 测试或删除旧测试。
- 文档引用：更新为历史说明。

#### D2. 更新架构文档

修改：

- `docs/00_PROJECT_ARCHITECTURE.md`
- `docs/06_AGENT_MEMORY_ARCHITECTURE.md`
- `docs/INDEX.md`

把主流程改成：

```text
ConversationService -> Direct Context Builder -> MarketReActAgent -> LangGraph tools
```

#### D3. 增加 CI 守卫

新增或扩展脚本：

```bash
scripts/guard_no_planner_main_path.py
```

守卫内容：

- 禁止 `app/factory.py` 创建 `ResponsePlanner()`。
- 禁止 `services/conversation_service.py` 调用 `planner.plan(...)`。
- 如果保留 `core/planner.py` 作为实验模块，必须放在允许列表中。

验收命令：

```bash
python3 -m pytest -q
python3 scripts/guard_no_legacy_memory_path.py
python3 scripts/guard_no_planner_main_path.py
```

---

## 5. 风险与回滚（历史记录）

> 当前已进入 Direct Context 唯一主链路，`agent_direct_context_mode` feature flag 已删除；本节保留为当时迁移阶段的风险记录，不代表当前可用回滚步骤。

### 5.1 风险

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 上下文过长 | 每轮都注入 profile/snapshot/sources | profile compact；sources 限 3；history_limit 保持 8 |
| 工具调用变多 | 主 LLM 自主判断，可能更积极调用工具 | `core/prompt.py` 保留“简单问题少调用工具”约束；`get_response_guidance` 明确不要每轮调用 |
| Guidance 滥用 | LLM 每轮都先调用 `get_response_guidance` | 工具描述写清“只在复杂任务或结构不确定时调用”；测试观察 trace |
| 来源追问不稳定 | 不再由 Planner 自动追加 provenance block | recent_sources 每轮注入，并在 prompt 中明确引用规则 |
| 画像误更新 | 不再用 Planner 判断 profile_update | 由 LLM 工具调用更新；禁用规则兜底或只保留显式低风险逻辑 |
| legacy 测试失败 | 旧测试依赖 ResponsePlan | 分阶段迁移测试，不一次性删除 |

### 5.2 回滚

当前不再支持通过 feature flag 回滚到 Planner 链路。如需回滚，只能从 Git 恢复已删除的 Planner/Orchestrator 文件和 `ConversationService` 旧分支；这应作为故障恢复动作，而不是日常灰度策略。

---

## 6. 实施检查清单（历史记录）

> 当前清单已完成；保留用于理解迁移过程。

交给执行型 Agent 时，按顺序完成：

1. 新增 `core/agent_context.py`。
2. 新增 `feature_flags.agent_direct_context_mode`。
3. 修改 `ConversationService.run()`，direct mode 绕过 Planner/Orchestrator。
4. 新增 `tools/response_guidance.py`，并注册到 `tools/registry.py`。
5. 修改 `core/prompt.py`，加入能力地图、递进提示、上下文使用规则。
6. 新增 direct context 与 response guidance 单测。
7. 跑 Phase A 验收命令。
8. 人工测试 Feishu/Web。
9. 稳定后做 Phase B，避免 direct mode 启动时创建 Planner LLM。
10. Phase C 图内 Guidance 节点仅作为后续可选优化。
11. 再做 Phase D 删除 legacy planner 主链路。

---

## 7. Definition of Done

完成后必须满足：

- Feishu 和 Web 都走 `ConversationService.run()`。
- 默认链路不调用 `ResponsePlanner.plan()`。
- 默认链路不根据代码规则判断用户意图。
- 主 LLM 能看到 history、user_profile、last_snapshot、recent_sources。
- 主 LLM 自主决定是否调用工具。
- 主 LLM 可以按需调用 `get_response_guidance` 获取短提示。
- 常驻 System Prompt 不包含冗长行情/交易/复盘模板。
- 用户追问“刚才/之前/依据/怎么知道”时，有足够上下文可用。
- `python3 -m pytest -q` 通过。
- docs 中主架构图与实际代码一致。
