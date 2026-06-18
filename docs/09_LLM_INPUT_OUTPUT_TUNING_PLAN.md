# LLM 输入输出调优方案

**日期**: 2026-06-18  
**目标读者**: 本地执行型 Agent / 工程维护者  
**前置状态**: `08_AGENT_DIRECT_CONTEXT_PLAN.md` 已完成，Direct Context 已成为唯一主链路  
**目标**: 在不改变 Direct Context 架构的前提下，提升主 LLM 的工具决策质量、行情分析稳定性与交易建议可用性。

---

## 1. 这份文档解决什么问题

`08_AGENT_DIRECT_CONTEXT_PLAN.md` 解决的是“谁来理解用户意图”：

- 不再由 Planner / Orchestrator 预判用户要什么
- 改为把完整上下文交给主 LLM
- 由主 LLM 自主决定是否调用工具、如何组织回复

本文解决的是“主 LLM 拿到上下文之后，为什么有时仍然输出不够顺手，以及应该从哪里干预”：

- Direct Context 里到底给了主 LLM 什么
- 工具输出如何影响主 LLM 的后续表达
- Prompt 应该承担什么，不该承担什么
- 观测日志应该补到哪一层，才能支持后续持续调优

---

## 2. 与 `08_AGENT_DIRECT_CONTEXT_PLAN.md` 的关系

### 2.1 结论：**不冲突**

本文与 `08_AGENT_DIRECT_CONTEXT_PLAN.md` 的关系是：

- `08` 解决“主链路架构”
- `09` 解决“主链路定型后的输入输出质量”

它们是上下游关系，不是两套竞争方案。

### 2.2 不得回退的原则

本文任何优化都必须遵守以下边界：

1. 不恢复 Planner / Orchestrator 主链路。
2. 不在代码层做关键词意图路由。
3. 不在 Adapter 层增加业务判断。
4. 不把所有输出模板重新塞回一个超大 System Prompt。
5. 不把 `get_response_guidance` 重新做成分类器。

### 2.3 允许新增的优化层

在以上边界内，可以优化：

1. Direct Context 的可观测性
2. 工具输出结构
3. Prompt 的轻量引导
4. 调试日志与 replay 能力
5. 交易系统结构化信息的注入方式

---

## 3. 当前链路的真实工作方式

当前线上主链路：

```text
用户消息
  -> Feishu/Web Adapter
  -> ConversationService.run()
  -> 读取 history / user_profile / last_snapshot / recent_sources
  -> build_direct_agent_input()
  -> MarketReActAgent.invoke(direct_input, history=history_for_invoke, allowed_tools=[])
  -> LangGraph reason
  -> 主 LLM 决定 tool call
  -> ToolNode 执行工具
  -> LangGraph reason
  -> 主 LLM 基于工具结果生成最终回答
  -> Renderer
  -> 渠道发送
```

这个链路意味着：

1. **主 LLM 第一次看到的不是实时行情，而是上下文与当前用户消息**
2. **实时行情数据只会在 LLM 主动调用工具后才进入本轮上下文**
3. **主 LLM 的最终表达质量，强依赖工具返回的可消费程度**

---

## 4. 当前问题不应先怪 Prompt

在 Direct Context 架构下，回复不顺手通常有三类原因：

### 4.1 输入层问题

- Direct Context 太长、太杂、噪音太多
- 用户画像与 recent sources 的信息密度不够
- 历史消息里含有大量无关风格/格式残留

### 4.2 工具层问题

- 返回字段很多，但缺少“可直接成文”的摘要字段
- 多标的结果是原始聚合，不利于模型快速对比
- 返回事实和建议混杂，导致模型难以稳定引用

### 4.3 Prompt 层问题

- 输出风格约束不够，导致模型把关键结论埋到后文
- 事实边界约束不够，模型容易把观察位写成计划
- 工具使用策略说明不够清楚，导致选择不稳定

因此，调优顺序不应是“先继续堆 Prompt”，而应是：

```text
先看观测日志
  -> 再判断是输入层问题、工具层问题还是 Prompt 层问题
  -> 最后做定点干预
```

---

## 5. 为什么必须把过程看透

如果看不到“主 LLM 实际看到了什么”和“工具到底返回了什么”，后续优化只能靠猜。

必须看透的原因：

1. 只有看到 Direct Context，才能判断上下文是不是太重、太脏、太弱。
2. 只有看到 tool call，才能判断主 LLM 是否真的理解了用户意图。
3. 只有看到工具原始输出，才能判断模型是在“提炼”还是“瞎补”。
4. 只有把这三层串起来，才能知道该改 Prompt、改工具返回，还是改记忆注入。

---

## 6. 当前推荐的可观测面

### 6.1 已落地日志

当前代码已具备以下观测点：

1. **Feishu 入站日志**
   - 用户消息文本预览
   - `open_id / chat_id / session_id`

2. **ConversationService Direct Context 摘要**
   - `history_len`
   - `profile_keys`
   - `snapshot_keys`
   - `recent_sources`
   - `input_preview`

3. **LangGraph reason / tool call 日志**
   - `reason start`
   - `last_user_preview`
   - `tool call name`
   - `tool call args`
   - `no tool call response_preview`

4. **Feishu 发送层日志**
   - renderer 前回复预览
   - `interactive/post/text` 路径

### 6.2 下一步建议补强的观测点

1. 工具执行后增加结构摘要日志
   - `tool_name`
   - `status`
   - `symbol`
   - `interval`
   - `trend`
   - `current_price`

2. 将关键中间结果落盘到 debug
   - direct context 原文
   - tool call 参数
   - tool 原始返回
   - reply_text_pre_renderer

3. replay 时支持按 `session_id` 过滤一轮完整请求

---

## 7. 从哪里干预最有收益

### 架构原则

**追问场景默认视为对上一轮有效分析的延续，优先复用会话内已有的结构化快照与历史文本完成理解；只有当已有信息不足，或问题具有明显时效性、需要给出当前交易动作判断时，才刷新行情工具。**

### 7.1 第一优先级：工具输出结构

这是收益最高的干预点。

原因：

- 主 LLM 的最终回答主要是“消费工具事实”
- 工具输出如果不利于消费，Prompt 再强也只能二次加工
- 结构化摘要越稳定，最终回复越稳定

建议为统一后的 `analyze_market` 增加面向 LLM 的稳定字段：

- `current_price`
- `trend`
- `trend_reason`
- `support_summary`
- `resistance_summary`
- `volume_note`
- `structure_note`
- `risk_note`
- `actionability`

对于多标的对比，额外增加：

- `relative_strength_rank`
- `best_candidate`
- `weakest_candidate`
- `comparison_brief`
- `why_best`
- `why_wait`

当前已落地（2026-06-18）：

- `analyze_market` 单标的返回新增：
  - `compact_summary_v1`（主 LLM 优先消费的短摘要）
  - `output_meta_v1`（`analysis_field_count/compact_field_count/analysis_chars/compact_chars/compression_ratio`）
- `analyze_market` 多标的返回新增：
  - `comparison_brief_v1`（最强/最弱与分布简述）
  - `output_meta_v1`（多标的总量压缩指标）
- `ConversationService` 在写 `tool_observation` 时优先写 compact 内容，并日志打印：
  - `raw_chars`
  - `compact_chars`
  - `compact_field_count`
  - `omit_candidates`

### 7.2 第二优先级：Prompt 的轻量约束

Prompt 适合做“薄约束”，不适合承担业务数据整理职责。

建议保留的 Prompt 方向：

1. 开头先写当前价格与趋势
2. 未触发条件不得写成已触发
3. 观察位不得写成入场位
4. 工具未返回的价格与目标位不得自行补写
5. 多标的问题优先比较相对强弱，不要平均发力

### 7.3 第三优先级：Direct Context 的紧凑化

Direct Context 不是越多越好。

建议：

- user_profile 只保留关键画像字段
- last_snapshot 保持单轮快照，不叠历史
- recent_sources 最多 3 条，且只保留摘要
- 后续可按需增加“最近一次交易计划摘要”，但不要塞完整台账

### 7.4 第四优先级：追问承接机制

如果要让项目的追问体验更接近 Codex，最值得补的不是“更多落盘文件”，而是一个更自然的追问承接机制。

推荐策略：

1. 默认把当前问题视为对本会话上一轮分析的延续。
2. 优先读取：
   - `last_snapshot`
   - 最近 `tool_observation`
   - 最近 user/assistant 文本
3. 先让主 LLM 基于这些材料判断：
   - 这是解释型追问，还是时效性交易追问
   - 旧分析是否足以回答
4. 只有在以下情况才刷新 `analyze_market`：
   - 缺少对应标的/周期的有效分析事实
   - 用户明确问“现在能不能做 / 还有效吗 / 现在开多开空”
   - 旧分析时间明显过久，无法支撑当前交易动作判断

这条路线的目标不是减少 LLM 能力，而是减少不必要的重复分析，同时保留交易问题应有的事实刷新能力。

### 7.5 第五优先级：交易系统结构化注入

如果要让 LLM 更擅长“能不能开仓 / 仓位怎么放 / 还能不能拿”，最有效的不是继续加提示词，而是补结构化输入：

- 当前持仓
- 已挂单
- 最大允许单笔亏损
- 最大仓位占比
- 最近一次计划的 `entry / stop / invalidation`

这些应以结构化字段注入，而不是自然语言大段描述。

---

## 8. 日志样例分析方法

当看到一段典型日志时，按下面顺序分析：

### 8.1 第一步：看 Direct Context

关注：

- 历史条数是否过多
- profile 是否含有明显有效偏好
- last_snapshot 是否为空
- recent_sources 是否足以支撑“你刚才说过”的追问

### 8.2 第二步：看第一次 reason 的 tool call

关注：

- 是否选对了工具
- 是否选对了 symbol / interval
- 是否有不必要的额外工具调用

### 8.3 第三步：看第二次 reason 的最终成文

关注：

- 是否复用了工具事实
- 是否遗漏当前价格 / 趋势
- 是否把观察条件误写成执行建议
- 是否出现格式过重、表述过满的问题

---

## 9. 对近期日志的工程判断

以最近的调试日志为例，当前系统表现出以下特征：

1. 主 LLM 已能正确从自然语言里提取 `symbol + interval`
2. 对“有没有开单机会”这类请求，会先调用 `analyze_market`，再按需调用 `get_response_guidance("trade_plan")`
3. 说明当前“Capability Map + Self-Requested Guidance Tool”路线是生效的
4. `ConversationService` 日志中 `snapshot_keys=[]`，说明上一轮分析结果没有稳定沉淀到 `last_snapshot` checkpoint；这会削弱后续“还能不能拿 / 有没有开单机会 / 刚才点位还有效吗”这类追问的连续性
5. 但最终输出仍偏标题化、报告化，说明：
   - Prompt 的风格约束还可以继续轻收
   - 更关键的是 `analyze_market` 返回仍缺少“可直接成文”的摘要层

因此，后续最值得做的不是恢复 Planner，而是：

1. 优化技术分析工具输出摘要层
2. 修复/增强 `last_snapshot` 的沉淀链路，确保最近一次有效分析能进入追问上下文
3. 增加事实边界类 Prompt 约束
4. 继续用日志验证工具调用与最终成文之间的关系

---

## 10. 推荐执行顺序

### Phase 0：先补追问承接，不新增文件沉淀

修改：

- `services/conversation_service.py`
- `core/agent_context.py`
- `core/prompt.py`

目标：

- 不新增“每次分析一份文件”的产物层
- 让主链路先复用已有会话记忆，再决定是否刷新工具
- 让追问体验更接近 Codex 式连续对话

### Phase 0.1：最小代码改造方案（仅设计）

#### A. 在 `ConversationService` 增加追问候选上下文

当前 `build_direct_agent_input()` 只注入：

- `user_profile`
- `last_snapshot`
- `recent_sources`
- 当前用户消息

建议新增一个轻量字段块：

```text
【最近对话结论】
- 上一轮用户问题: ...
- 上一轮助手结论摘要: ...
- 若有 last_snapshot: symbol=..., interval=..., trend=...
```

实现建议：

- 从 `history` 中提取最近 1 轮 user/assistant 对
- assistant 文本做截断摘要，不要整段塞入
- 如果 `last_snapshot` 有值，优先引用 `symbol / interval / trend / key_levels`

目的：

- 让主 LLM 更容易判断“用户是不是在追问刚才那次分析”
- 减少它从长 history 里自己翻找线索的成本

#### B. 在 Prompt 中增加一条追问策略

建议新增规则：

- 当用户问题明显是在追问上一轮分析时，先优先复用 `last_snapshot`、最近工具来源和最近对话结论完成判断。
- 只有在事实不足，或用户问题要求当前时点交易动作确认时，再调用 `analyze_market` 刷新行情。

注意：

- 这不是代码路由
- 也不是强制不调工具
- 只是把“先复用已有分析，再按需刷新”作为默认偏好交给主 LLM

#### C. 给 `last_snapshot` 增加“可用于追问”的最低字段保证

当前 `last_snapshot` 有时为空，或者字段不足。

建议最低保证：

- `symbol`
- `interval`
- `timestamp`
- `current_price`
- `trend`
- `key_levels`
- `structure_signals`
- `raw_insights`

如果上一轮是多标的分析，可考虑：

- `focus_symbol`
- 或 `latest_symbol_snapshots`（最多 2~3 个）

目的：

- 让主 LLM 不必完全依赖上一轮自然语言文本
- 提高“还能不能做 / 刚才点位还有效吗”这类追问的结构化支撑

#### D. 不做的事

这轮改造明确不做：

1. 不新增“每次分析都写一份独立语料文件”
2. 不新增代码关键词分类器
3. 不把“是否追问”做成硬规则路由
4. 不把所有历史文本完整塞回 Direct Context

#### E. 验收标准

完成后，理想行为应是：

1. 用户刚问完 “看看 ETH 1h 行情”，下一句问 “那现在适合开多吗”
   - 主 LLM 能先识别这是 ETH 1h 的追问
   - 若 `last_snapshot` 足够新且信息充分，可先基于已有分析形成判断
   - 若问题要求当前动作确认，再补一次 `analyze_market`

2. 用户问 “为什么刚才说不适合做多”
   - 不应再无脑刷新行情
   - 应优先复用上一轮分析结论进行解释

3. 用户隔很久再问 “ETH 现在还能开多吗”
   - 应更倾向刷新 `analyze_market`

### Phase 1：工具输出可消费化

修改：

- `tools/technical_analysis.py`

目标：

- 给统一后的 `analyze_market` 增加稳定摘要字段
- 不破坏现有原始分析结果

### Phase 2：Prompt 轻约束补强

修改：

- `core/prompt.py`

目标：

- 增加事实边界约束
- 压低标题化、报告化倾向
- 保持“先价格/趋势，再展开分析”的输出节奏

### Phase 3：Debug / Replay 增强

修改：

- `services/conversation_service.py`
- `core/graph.py`
- `scripts/replay_debug_output.py`

目标：

- 能按 session 回放单轮请求
- 能对比 tool call 与最终回复

---

## 11. 关键原则

1. 架构不要回退到 Planner / Orchestrator。
2. Prompt 只做轻约束，不做业务规则引擎。
3. 工具输出是主 LLM 能力上限的重要决定因素。
4. 先做可观测，再做调优；先改工具输出，再改 Prompt。
5. 任何“交易建议”都必须尽量绑定真实字段，避免自由脑补。
