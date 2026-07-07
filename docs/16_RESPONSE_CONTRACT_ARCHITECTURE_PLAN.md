# 轻入口与按需上下文工具化改造计划

**日期**: 2026-06-27  
**目标读者**: 工程维护者 / 本地执行型 Agent  
**状态**: Step 1-5 已完成：light-only 主链路、上下文工具化、loop guardrail、旧 full 代码已清理、历史旧计划文档已删除  
**目标**: 把当前“每轮预注入较重 Direct Context”的链路，演进为“轻入口摘要 + LLM 自主按需取证”的循环式主链路，减少短问句被过度上下文化、输出变长和追问判断不稳的问题。

---

## 0. 实施替换记录（按步）

### 2026-06-27 / Step 1（已完成）

替换内容：

- 在 `ConversationService` 增加 light/full 双模式分支，light 模式构建 `build_light_agent_input`。
- 进入 light 模式时，不再把完整 `profile/recent_sources/recent_conclusion` 作为首屏 prompt 注入。
- 每轮写入 `turn_summary` 结构化 fact，light 首屏摘要优先从 `turn_summary` 生成，缺失时回退原始历史压缩。
- `core/graph.py` 增加 `agent_loop_trace` 事件，补齐 `tool_result` 轨迹。

被替换的旧行为：

- 旧行为是“所有场景先重型 Direct Context 预注入”。
- 新行为是“light 首屏只给摘要，完整证据靠 loop 按需查询工具”。

### 2026-06-27 / Step 2（已完成）

替换内容：

- 新增 `tools/context_memory.py`：
  - `get_last_snapshot`
  - `get_recent_tool_observations`
  - `search_conversation_summaries`（默认 12 条、最大 20 条、约 8000 字预算、硬上限 10000）
- 在 `tools/registry.py` 注册上述工具。
- 在 `app/factory.py` 注入运行时 `MemoryAPI` 给 context tools。
- 在 `core/prompt.py` 与 `core/agent_context.py` 明确“摘要首屏 -> context 工具补证 -> 必要时再拉行情”的调用策略。
- 新增测试 `tests/test_context_memory_tools.py`。

被替换的旧行为：

- 旧行为中，light loop 还没有可直接调用的“上下文补证工具”。
- 新行为中，LLM 可在 loop 内显式补证，而不是只能依赖首屏摘要或重新拉行情。

### 2026-06-27 / Step 3（已完成）

替换内容：

- 在 `core/graph.py` 增加 loop guardrail：
  - 单轮工具调用数超过阈值（默认 6）记录 warning。
  - 相同 `tool_name + args` 的重复调用记录 warning（同批重复 + 历史重复）。
  - 新增 `reason_continue` 事件，写入 `tool_call_count / tool_call_names / duplicate_tool_call_count`。
- 保留 `reason_start / tool_call / tool_result / final_answer_ready` 事件，实现完整短轨迹。
- 新增测试 `tests/test_graph_tool_guardrails.py`，覆盖签名归一化、重复计数和阈值读取。

被替换的旧行为：

- 旧行为只有“调用了什么工具”的日志，没有重复调用与调用规模预警。
- 新行为可直接定位“为什么循环变长/为什么重复取证”。

### 2026-06-27 / Step 4（已完成）

替换内容：

- 删除 `build_direct_agent_input` 及其预算裁剪路径，`core/agent_context.py` 收敛为 light 输入构造。
- 删除 `ConversationService` 的 full 分支、`get_agent_context_mode` 依赖，以及 full 预注入辅助函数。
- `config/runtime_config.py` 与 `config/analysis_defaults.example.yaml` 移除 full 模式与旧预算项（`max_recent_sources/max_conclusion_chars`）。
- `tests/test_direct_agent_context_flow.py` 收敛为 light-only 行为测试，移除 full 模式断言。

被替换的旧行为：

- 旧行为可通过开关回到 full 预注入链路。
- 新行为统一为 light 首屏 + 工具按需补证，不再提供 full 回退执行路径。

### 2026-06-27 / Step 5（已完成）

替换内容：

- `docs/INDEX.md` 重构为“当前有效文档 / 历史归档（只读）”双区。
- 为 `00/06` 历史文档增加统一归档提示，明确“不再作为当前施工依据”。
- 删除已完全过时的旧计划文档：
  - `08_AGENT_DIRECT_CONTEXT_PLAN.md`
  - `09_LLM_INPUT_OUTPUT_TUNING_PLAN.md`
  - `10_CODEX_STYLE_MEMORY_EXECUTION_PLAN.md`

被替换的旧行为：

- 旧行为中，索引与旧计划文档容易被误当成当前实施标准。
- 新行为中，当前标准统一收敛到 16 文档与代码实现，过时计划已从仓库移除。

### 待统一清理（当前剩余）

- 如需进一步减负，可在单独批次将 11-15 报告迁移到 `docs/archive/`（当前先保持路径稳定，避免外链断裂）。

---

## 1. 背景

当前主链路已经收敛到 Direct Context：

```text
用户消息
  -> Feishu/Web Adapter
  -> ConversationService.run()
  -> 读取 history / user_profile / last_snapshot / recent_sources
  -> build_direct_agent_input()
  -> MarketReActAgent.invoke(direct_input, history=history_for_invoke, allowed_tools=[])
  -> LangGraph ReAct 循环
  -> 主 LLM 调用工具或生成最终回答
  -> ConversationEnvelope
  -> 渠道发送
```

这条链路已经解决了旧 Planner / Orchestrator 带来的主链路分叉问题，也具备 LangGraph `reason -> act -> reason` 的工具循环能力。当前真正的问题不是“缺少一个 planner”，而是第一轮输入过重：

- `ConversationService` 在调用 LLM 前统一读取并注入 `user_profile / last_snapshot / recent_sources / recent_conclusion`。
- 短问句、闲聊、规则解释、行情快答、交易计划都先经过同一套上下文拼装。
- LLM 容易把历史结论、用户画像、工具来源揉进正文，导致短问长答或回答焦点发散。

因此，优先方向不是新增前置 Query Classifier，而是把上下文从“默认预注入材料”改成“LLM 可按需调用的证据工具”。

---

## 2. 核心结论

目标架构保留当前 LangGraph ReAct loop，不新增独立 Planner 主链路：

```text
用户消息
  -> ConversationService.run()
  -> build_light_agent_input()
  -> MarketReActAgent.invoke(light_input, allowed_tools=all)
  -> LLM reason
     -> 可按需调用上下文工具、画像工具、历史工具、行情工具、台账工具
     -> 工具结果回灌 messages
     -> LLM 再判断证据是否充分
  -> LLM 给最终回答
  -> ConversationEnvelope
```

第一轮给 LLM 的不是“零上下文”，而是轻入口上下文：运行标识、滚动历史摘要、取证规则和用户当前问题。

```text
【运行上下文】
session_id: ...
storage_key: ...

【历史对话摘要】
最近对话摘要: ...
当前承接线索: ...
快照提示: symbol=..., interval=..., trend=..., price=...

【任务目标】
你的目标是充分回答用户当前问题，不是复述模板。
如果问题像追问、持仓延续、风险确认，优先按需查询上下文工具。
如果需要实时行情事实，再调用行情工具。

【用户当前消息】
...
```

改造重点：

- 不在进入 LLM 前预判任务类型。
- 不在 Adapter 层写业务判断。
- 不恢复旧 Planner / Orchestrator。
- 不把完整 profile/history/sources 每轮塞进 prompt，只保留滚动历史摘要作为承接线索。
- 把现有记忆材料封装成边界清楚的工具，由 LLM 自主取证。

---

## 3. 当前问题与目标行为

### 3.1 当前问题

当前 `build_direct_agent_input()` 的输入较完整，但它默认把所有场景都当成“可能需要上下文”的任务处理。这对追问很稳，对短问句不够经济。

典型副作用：

- “看看 ETH 行情” 可能带入上一轮结论和画像，输出过长。
- “解释一下 Spring 是什么” 也可能看到无关市场快照。
- “刚才那个支撑还有效吗” 需要上下文，但上下文是否足够、是否要刷新行情，应该由 LLM 在循环里判断。
- “我 1638 开空还拿着，下一步怎么做” 需要画像、台账、上一轮计划、当前行情，但这些材料不应该被所有问题默认加载。

### 3.2 目标行为

LLM 每轮进入同一个工具循环：

1. 先读用户原问题、运行标识和滚动历史摘要。
2. 判断为了回答问题是否需要补充材料。
3. 如需上下文，调用记忆/画像/历史/台账工具。
4. 如需实时事实，调用行情工具。
5. 工具结果回到 messages 后继续判断。
6. 证据足够时停止调用工具并回答用户。

这让“回答用户问题”成为 loop 的终止条件，而不是让代码预先猜本轮应该注入哪些材料。

---

### 3.3 交易策略响应契约

本项目的行情能力定位是“交易解读与条件化策略”，不是确定性预测。

对用户提出的“复盘一下，如果及早关注会在哪里入场”“下次有类似机会能不能判断”“当前工具够不够做策略”这类问题，Agent 应遵守以下契约：

- 可判断的是：趋势结构、关键支撑/阻力、回踩/突破/反转机会、失效条件、止损逻辑、仓位与纪律建议。
- 不承诺的是：百分百预测、无误差择时、替代真实盘口深度、替代逐笔成交与突发消息监控。
- 回答目标是把“看盘”转成“有位置、有触发、有失效、有风控”的决策过程。
- 若用户要复盘入场点，优先输出可执行机会类型：回踩确认、突破确认、结构反转确认，而不是事后追涨式结论。
- 若用户问“下次能否判断”，回答应说明能识别标准机会，但仍以触发条件成立为前提。

当前工具组合足够支持大多数结构化交易策略：

| 决策层级 | 主要工具 / 证据 | 用途 |
| --- | --- | --- |
| 结构判断 | `analyze_market` / `evaluate_structure` | 趋势、震荡、阶段、结构切换 |
| 关键位 | `analyze_market` / `get_key_levels` / `analyze_fibonacci` | 支撑、阻力、回撤位、目标位、失效位 |
| 执行校准 | `fetch_market_data` | 需要逐根 K 线确认时补原始数据 |
| 上下文承接 | `get_last_snapshot` / `search_conversation_summaries` / `get_recent_tool_observations` | 复盘上一轮结论、承接“刚才那个点位” |
| 风控与复盘 | `get_user_profile` / `get_journal_status` / `simulate_open_position` | 仓位偏好、持仓状态、模拟计划 |

压缩信息与逐根 K 线的边界：

- 压缩信息适合做方向、位置、关键位、风险收益比和交易计划骨架。
- 逐根 K 线适合做短线择时、真假突破、回踩有效性、长影线、吞没、缩量/放量节奏等执行细节。
- 4h / 1d 波段问题通常先看结构和关键位；15m 以下、日内或紧止损问题，应按需调用 `fetch_market_data` 补 K 线细节。
- 更多细节不等于更好交易；当用户容易被低级别噪音干扰时，回答应强调周期、位置和纪律。

交易复盘与类似机会识别的推荐输出骨架：

```text
结论：这次更标准的机会不是追价，而是 A/B 两类确认。

1. 回踩确认：关键支撑、确认信号、止损/失效。
2. 突破确认：关键阻力、有效突破标准、假突破风险。
3. 更大周期低吸/反转：高周期支撑、适合的交易风格。

下次类似机会：
- 大级别方向
- 小级别回踩或突破
- K 线确认
- 失效条件
- 仓位与风险
```

该骨架是软约束。用户只要短答时，应压缩为自然段；用户明确要求复盘或方法论时，才展开。

---

## 4. 轻入口输入

新增 `build_light_agent_input()`，替代当前主链路里的重型 `build_direct_agent_input()` 作为默认入口。

这里的“轻”不是完全不带上下文，而是只带经过压缩的滚动历史摘要。摘要用于帮助 LLM 判断当前问题是否承接已有对话；完整画像、完整快照、工具观察和更早历史摘要集合仍通过工具按需读取。

建议落点：

```text
core/agent_context.py
```

建议输入：

```python
def build_light_agent_input(
    *,
    user_text: str,
    session_id: str,
    storage_key: str,
    conversation_summary: dict[str, str] | None = None,
) -> str:
    ...
```

建议输出：

```text
【运行上下文】
session_id: feishu_xxx
storage_key: ou_xxx

【历史对话摘要】
最近对话摘要: 用户刚才在关注 ETH 1h 行情与支撑是否有效；上轮结论是 ETH 仍偏震荡，2400 附近是关键支撑，未突破前不宜追多。
当前承接线索: 本轮“刚才那个支撑”大概率指 ETH 1h 的 2400 附近支撑。
快照提示: ETHUSDT, 1h, trend=震荡, price=2420

【取证规则】
你的目标是充分回答用户当前问题。
追问、持仓延续、风险确认、来源追问时，先按需查询上下文工具。
需要实时行情、关键位、趋势或交易动作确认时，再调用行情工具。
资料不足时继续调用工具；资料充分时停止调用工具并直接回答。

【用户当前消息】
刚才那个支撑还有效吗？
```

`conversation_summary` 应由 `ConversationService` 优先从最近若干轮 `turn_summary` 结构化摘要构造，缺失时才回退到原始历史临时压缩；它不注册为 LLM 工具。它不是原始对话内容的拼接，而是“每次对话摘要”的滚动集合再压缩后的当前入口摘要。长度必须按中文摘要预算控制：

| 字段 | 上限 |
| --- | --- |
| `recent_dialogue_summary` | 500 中文字 |
| `current_carryover_hint` | 180 中文字 |
| `snapshot_hint` | 120 中文字 |
| `conversation_summary` 总量 | 800 中文字左右，硬上限 1000 中文字 |

每轮 assistant 回复保存后，应同步生成或更新一条短 `turn_summary`。它应尽量采用结构化字段，而不是任意自然语言长句。建议字段：

```json
{
  "timestamp": "...",
  "symbols": ["ETHUSDT"],
  "intervals": ["1h"],
  "current_price": 2420.0,
  "trend": "震荡",
  "key_levels": {
    "support": [2400],
    "resistance": [2480]
  },
  "stance": "wait_breakout",
  "invalidation": "跌破 2400 后原多头观察失效",
  "next_trigger": "放量站上 2480 再看顺势",
  "position_context": "",
  "user_preference_hint": ""
}
```

如果当轮没有行情分析，也可以只保留规则解释、用户偏好变化或持仓处理结论，但仍应尽量保持字段化，不保留完整分析过程。

`build_direct_agent_input()` 已在 Step 4 清理，主链路仅保留 light input。

---

## 5. 上下文工具化

把当前预注入的上下文能力改成工具。工具只读当前 `session_id / storage_key` 对应材料，不做交易判断。

建议新增：

```text
tools/context_memory.py
```

### 5.1 get_last_snapshot

读取上一轮市场快照。

输入：

```json
{"session_id": "feishu_xxx"}
```

输出建议：

```json
{
  "status": "success",
  "snapshot": {
    "symbol": "ETHUSDT",
    "interval": "1h",
    "timestamp": "...",
    "current_price": 2400.0,
    "trend": "震荡",
    "key_levels": {},
    "actionability": {},
    "raw_insights": "..."
  }
}
```

用途：

- “刚才那个点位还有效吗”
- “还能拿吗”
- “为什么你刚才说不适合做多”

### 5.2 get_recent_tool_observations

读取最近工具观察摘要，默认最多 3 条。

输入：

```json
{"session_id": "feishu_xxx", "limit": 3}
```

输出只保留 compact 内容：

```json
{
  "status": "success",
  "items": [
    {
      "tool": "analyze_market",
      "summary": "success / ETHUSDT / 1h / 震荡",
      "content": {"compact_summary_v1": {}},
      "tool_call_id": "..."
    }
  ]
}
```

用途：

- 来源追问。
- 复用上一轮工具事实。
- 避免重新拉行情。

### 5.3 get_user_profile

读取用户画像（可按需在回答中只使用最小字段）。

输入：

```json
{"storage_key": "ou_xxx"}
```

输出仅保留风险与表达相关字段：

```json
{
  "status": "success",
  "profile": { "...": "..." }
}
```

用途：

- 交易计划。
- 仓位建议。
- 风险确认。

### 5.4 search_conversation_summaries

按需读取历史对话摘要集合，而不是读取原始对话内容堆砌。每轮对话结束后应形成一条 `turn_summary`；该工具按时间或相关性返回摘要集合，供 LLM 补充多轮之前的上下文。

输入：

```json
{"session_id": "feishu_xxx", "limit": 20}
```

返回限制：

| 项目 | 上限 |
| --- | --- |
| 默认摘要条数 | 12 条 |
| 最大摘要条数 | 20 条 |
| 单条 `turn_summary` | 300 中文字 |
| 工具总返回 | 8000 中文字左右，硬上限 10000 中文字 |

用途：

- 用户说“刚才那个”“上一条”“你刚说的”但 light input 摘要不足时。
- 复杂追问需要回看多轮之前的主题、仓位、计划或风险提醒时。
- 避免把原始 history 直接堆给 LLM。

---

## 6. Prompt 调整

`core/prompt.py` 的方向应从“默认你已经看到完整上下文”改为“你先看到短摘要；如需完整证据，可以按需取证”。

建议新增或强化的规则：

- 你的最终目标是回答用户当前问题，不是完成固定报告。
- 第一轮只看到滚动历史摘要是正常情况；如需完整证据，主动调用上下文工具。
- 历史摘要用于判断是否承接已有对话，不足以直接支撑交易动作时，应继续取证或刷新行情。
- 追问、持仓延续、风险确认、来源追问时，优先查询 `get_last_snapshot`、`get_recent_tool_observations` 或 `search_conversation_summaries`。
- 交易计划、仓位建议、还能不能拿时，按需查询 `get_user_profile` 和台账工具。
- 需要实时行情、当前价格、关键位、趋势、开仓动作确认时，调用 `analyze_market`。
- 不要每轮都先查全部上下文；只拿回答当前问题所需的证据。
- 当证据不足时继续调用工具；证据充分时停止调用工具并回答。

工具策略也要避免滥用：

- 同一轮不要用相同参数重复调用同一个上下文工具。
- 简单规则解释、概念问答、闲聊可以不调用任何工具。
- 来源追问优先查最近工具观察，不要直接重新分析行情。

---

## 7. ConversationService 改造

当前 `ConversationService._run_direct_context_flow()` 做了较重的上下文预加载。

改造后：

```text
ConversationService.run()
  -> 保存 user message / recent_message fact
  -> 构造 conversation_summary（最近一轮问答 + 快照提示）
  -> build_light_agent_input(text, session_id, storage_key, conversation_summary)
  -> agent.invoke(light_input, session_id, history=small_history_or_empty)
  -> LangGraph loop 自主调用上下文工具和行情工具
  -> 写 tool_observation / last_snapshot / assistant message
  -> build_conversation_envelope()
```

建议阶段性策略：

- Phase 1 默认只传 light input。
- history 建议先传空列表，避免和 `conversation_summary` 双重注入；如需兼容，可传最近 1 轮并观察重复率。
- 已移除 full 回退执行开关，运行时统一走 light 主链路。
- debug 日志同时记录 `input_mode=light`、`summary_chars`、`input_chars`、`tool_calls`、`reply_len`。

---

## 8. 工具实现边界

上下文工具属于 evidence source，不是业务 planner。

必须遵守：

- 工具只返回事实材料，不判断最终任务类型。
- 工具不决定交易方向。
- 工具不生成最终回复。
- 工具输出必须 compact，避免把重上下文换一种形式塞回 LLM。
- 工具内部可以复用 `MemoryAPI.snapshot()`、`MemoryAPI.recall()`、`get_user_profile()`、`MarketSessionManager.get_recent_messages()`。
- `conversation_summary` 是入口输入的一部分，由服务内部临时生成，不作为 LLM 工具暴露，避免和 light input 出现两份最近摘要。
- 历史对话工具返回的是 `turn_summary` 集合，不返回大段原始 user/assistant 文本。

已完成的下沉与复用：

- `memory_api.snapshot(thread_id)` -> `get_last_snapshot`
- `tool_observation facts` -> `get_recent_tool_observations`
- `turn_summary facts` -> `search_conversation_summaries`
- `recent_message` facts / session history -> light 首屏 `conversation_summary` 生成

---

## 9. 防循环与可观测性

按需工具化后，主要风险是工具调用次数上升或重复调用。

需要记录：

```text
input_mode
summary_chars
input_chars
session_id
storage_key
tool_call_count
tool_call_names
duplicate_tool_call_count
prompt_tokens
completion_tokens
reply_len
latency_ms
```

建议增加图层保护：

- 同一轮相同 `tool_name + args` 超过 1 次时记录 warning。
- 单轮工具调用数超过阈值时记录 warning，默认阈值可设 6。
- 上下文工具返回为空时，LLM 应继续用已有材料回答或说明信息不足，不要反复查询。

同时增加一份结构化 `agent_loop_trace` 日志，用于显化本轮工具调用过程。它记录的是动作轨迹，不记录模型私有长推理。建议事件：

```text
reason_start
tool_call(name, args_preview)
tool_result(name, compact_summary)
reason_continue(next_focus)
final_answer(reply_preview)
```

`next_focus` 应是短操作说明，例如：

- `先核对上一轮快照`
- `已拿到 ETH 1h 行情，下一步判断是否适合开仓`
- `工具事实已足够，准备生成最终回答`

这些保护优先做日志，不急着硬拦截。先观察实际调用形态，再决定是否在图层做强约束。

---

## 10. 落地顺序

### Phase 1: 轻入口输入

修改：

```text
core/agent_context.py
application/services/conversation_service.py
core/prompt.py
```

目标：

- 新增 `build_light_agent_input()`。
- `ConversationService` 已收敛为 light-only input mode。
- light 模式下不再预注入完整 `profile / snapshot / recent_sources / recent_conclusion`，只注入 800 中文字左右的滚动历史摘要。

验收：

- “解释一下 Spring 是什么” 不注入市场快照。
- “看看 ETH 行情” 首轮 input 明显变短。
- “刚才那个支撑还有效吗” 首轮 input 能看到上一轮摘要，但完整快照仍需按需查工具。
- 已删除 full mode 测试，保留 light 流程与工具补证回归测试。

### Phase 2: 上下文读取工具

新增：

```text
tools/context_memory.py
tests/test_context_memory_tools.py
```

注册到：

```text
tools/registry.py
```

目标：

- LLM 可主动读取 `last_snapshot / recent_tool_observations / user_profile / conversation_summaries`。
- 工具输出 compact，字段稳定。

验收：

- “刚才那个支撑还有效吗” 会先查上下文工具。
- “你怎么知道的” 会查最近工具观察，而不是直接重新拉行情。
- “我这单还能拿吗” 会查画像或台账，再按需查行情。

### Phase 3: Prompt 与 loop 调优

修改：

```text
core/prompt.py
core/graph.py
```

目标：

- Prompt 明确“资料不足继续取证，资料充分停止工具调用”。
- 图层记录工具调用计数与重复调用。
- 保持 `get_response_guidance` 是短指导工具，不做分类器。

验收：

- 简单问题不会每轮先查全部上下文工具。
- 复杂交易计划可以连续调用画像、台账、行情工具后再回答。
- 同一轮重复工具调用在日志中可见。

### Phase 4: 输出约束回归

修改：

```text
scripts/smoke_response_style.py
tests/test_direct_agent_context_flow.py
tests/test_phase_c_memory_flow.py
```

目标：

- 建立 light mode 的行为回归。
- 用样例监控快答长度、追问承接、来源追问、交易计划完整性。

验收样例：

| 输入 | 预期行为 |
| --- | --- |
| 解释一下 Spring 是什么 | 不查行情，不注入市场快照，直接解释 |
| 看看 ETH 行情 | 调用 `analyze_market`，短答当前价格、趋势、关键位 |
| 刚才那个支撑还有效吗 | light input 先提供滚动摘要；如摘要不足，再查 `get_last_snapshot` 或 `search_conversation_summaries`，必要时刷新行情 |
| 你怎么知道的 | 查 `get_recent_tool_observations`，说明依据类别 |
| 我 1638 开空还拿着，下一步怎么做 | 查画像/台账/上一轮快照，必要时刷新行情，给条件化处理 |

---

## 11. 暂不做的事

这轮改造不做：

- 不新增前置 Planner / QueryClassifier。
- 不让小模型先分类用户意图。
- 不把快答模板做成主链路。
- 不新增独立 NLG Renderer。
- 不新增 `domain/policy` 策略引擎。
- Direct Context builder 已在 Step 4 清理，不再作为运行时回退路径。

这些可以作为后续阶段，但前提是 light loop 已经跑稳，并且日志证明仍有明确瓶颈。

---

## 12. 与现有文档的关系

- 旧计划文档 `08/09/10` 已删除，避免与当前实现冲突。
- 本文作为当前实施主文档，替代原先“Response Contract / Planner / Evidence Builder / NLG Renderer”路线，重点为“轻入口 + 上下文工具化”。

长期仍可保留 `response_contract` 作为输出约束概念，但它不应成为当前第一阶段的前置分类器。
