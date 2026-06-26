# Response Contract 架构演进方案

**日期**: 2026-06-26  
**目标读者**: 工程维护者 / 本地执行型 Agent  
**状态**: 设计方案，尚未落地  
**目标**: 把“短问短答、详问详答”从 Prompt 调参升级为系统能力，减少行情回复啰嗦、上下文过重和输出不可控的问题。

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
  -> LangGraph ReAct 调用工具
  -> 主 LLM 生成最终回答
  -> Renderer
  -> 渠道发送
```

这条链路的优点是主 LLM 能拿到完整上下文，自主判断是否调用工具；问题是所有场景几乎共享同一套输入和同一套输出约束。短问句如“看看 ETH 行情”也可能被喂入较重的 history / user_profile / recent_sources，再由主 LLM 在第二轮生成长回答。

因此，继续只调 `core/prompt.py` 会有收益，但上限不高。更合理的方向是引入一层明确的输出契约，让系统先决定“本轮应该怎么回答”，再决定“需要哪些证据”，最后再生成表达。

---

## 2. 核心结论

目标架构不是把旧 Planner 恢复回来，也不是在 Adapter 层写关键词业务逻辑，而是在现有 Direct Context 主链路中引入三个轻量但清晰的层：

```text
用户消息
  -> ConversationService.run()
  -> Response Planner 生成 response_contract
  -> Evidence Builder 按 contract 取证据
  -> MarketReActAgent / Tool Flow 取得实时事实
  -> NLG Renderer 按 contract 渲染
  -> ConversationEnvelope
```

三层职责：

1. **决策层 Planner**  
   做 Query Classification，输出结构化 `response_contract`，定义本轮回答的模式、长度、结构和必需字段。

2. **证据层 Evidence Builder**  
   按 `response_contract` 动态拼上下文，只取必需证据，例如行情快照、1 条最近结论、可选用户画像，而不是每轮注入完整 profile/history/sources。

3. **表达层 NLG Renderer**  
   根据 `response_contract` 和结构化事实渲染文本。快答走稳定骨架，详答才允许展开推演。

---

## 3. Response Contract

`response_contract` 是这个方案的核心。它不是最终回答，也不是工具结果，而是对“本轮该如何回答”的结构化约束。

建议字段：

```json
{
  "response_mode": "quick",
  "task_type": "market_view",
  "symbols": ["ETH"],
  "intervals": ["1h"],
  "max_cn_chars": 220,
  "max_lines": 6,
  "sections": ["conclusion", "levels", "trigger_invalidation", "action"],
  "require_recent_klines": false,
  "require_risk_note": true,
  "allow_trade_plan": false,
  "evidence_policy": {
    "history_messages": 2,
    "recent_sources": 1,
    "user_profile": "minimal",
    "last_snapshot": true
  }
}
```

典型模式：

| 场景 | response_mode | 输出特点 |
| --- | --- | --- |
| “看看 ETH 行情” | `quick` | 结论、关键位、触发/失效、操作，短答 |
| “ETH 和 SOL 小时线对比” | `quick_compare` | 分标的短句 + 横向强弱 |
| “给出持仓计划” | `trade_plan` | 入场/止损/止盈/仓位/失效条件 |
| “详细复盘这波下跌” | `deep_dive` | 允许展开结构、量价、K 线与多周期 |
| “刚才说的点位还有效吗” | `follow_up` | 读取上一轮结论和当前快照，重点回答是否变化 |

---

## 4. 决策层 Planner

### 4.1 职责

Planner 只负责产出 `response_contract`：

- 判断用户问题是快答、详答、交易计划、复盘还是追问。
- 提取标的、周期、是否多标的、是否需要交易计划。
- 给出长度、行数、段落结构、证据策略。
- 明确哪些内容不应该出现，例如快答中不逐根复述 K 线、不追加“如果你愿意我可以继续”。

### 4.2 边界

Planner 不应该：

- 不调用行情 API。
- 不直接生成最终回答。
- 不决定交易方向。
- 不把用户问题压缩成不可追溯的摘要。
- 不替代 `MarketReActAgent` 的工具调用能力。

### 4.3 在当前项目里的落点

建议新增：

```text
application/planning/response_contract.py
application/planning/query_classifier.py
```

`ConversationService.run()` 仍是会话编排入口，但不再直接构造“统一大上下文”，而是先得到 contract：

```text
ConversationService.run()
  -> QueryClassifier.classify(text, light_history)
  -> ResponseContract
  -> EvidenceBuilder.build(contract)
```

---

## 5. 证据层 Evidence Builder

### 5.1 当前问题

`core/agent_context.py` 当前承担的是 Direct Context 拼装职责，主要输入包括：

- `user_text`
- `session_id / storage_key`
- `user_profile`
- `last_snapshot`
- `recent_sources`
- `recent_conclusion`

这在通用对话里很稳，但在短行情问句里容易过重。上下文越宽，最终回答越容易把历史结论、用户画像、工具来源都揉进正文。

### 5.2 目标

Evidence Builder 应该按 `response_contract.evidence_policy` 拉取证据：

```text
quick market_view
  -> 当前消息
  -> last_snapshot 最小字段
  -> 必要时 1 条 recent source
  -> 不注入完整 user_profile

trade_plan
  -> 当前消息
  -> 当前行情工具结果
  -> risk_profile / account 配置
  -> 最近持仓或上一轮计划

deep_dive
  -> 当前消息
  -> 多周期行情结果
  -> recent_sources
  -> history 中相关片段
```

### 5.3 在当前项目里的落点

建议新增：

```text
application/evidence/evidence_builder.py
application/evidence/evidence_policy.py
```

保留 `core/agent_context.py` 作为底层渲染工具，但不要让它决定“该注入什么”。决策应上移到 Evidence Builder。

长期形态：

```text
EvidenceBuilder
  -> MemoryAPI.recall_recent_messages(...)
  -> MemoryAPI.snapshot(...)
  -> MemoryAPI.recall(tool_observation)
  -> UserProfileRepository / MemoryAPI.get_user_profile(...)
  -> EvidencePacket
```

`EvidencePacket` 应该是结构化对象，最后才根据调用模型需要转成 prompt 文本。

---

## 6. 表达层 NLG Renderer

### 6.1 当前问题

现在最终回复主要由主 LLM 根据 System Prompt、Direct Context、工具结果自由生成。Prompt 能约束风格，但很难稳定控制：

- 字数
- 行数
- 是否重复
- 是否收尾邀约
- 是否把支撑/阻力埋到后文
- 是否把快答写成小报告

### 6.2 目标

NLG Renderer 接收结构化事实和 `response_contract`，负责最终表达：

```text
MarketFacts + ResponseContract
  -> NLG Renderer
  -> reply_text
```

快答可以模板化：

```text
结论：{symbol} {interval} {bias}，{one_reason}。
关键位：阻力 {resistance}，支撑 {support}。
触发：{trigger_condition}。
失效：{invalidation_condition}。
操作：{action_bias}；{risk_note}。
```

详答才交给 LLM 生成更自然的段落，但仍然受 `sections/max_cn_chars/required_fields` 约束。

### 6.3 在当前项目里的落点

建议新增：

```text
application/rendering/nlg_renderer.py
application/rendering/response_templates.py
```

现有 Feishu/Web renderer 继续只做渠道格式适配：

```text
NLG Renderer
  -> 产出 reply_text / structured_blocks
  -> Feishu Renderer 负责卡片或文本发送
  -> Web Presenter 负责 Web 展示
```

不要把业务表达规则塞到 `infrastructure/adapters/renderers/feishu_renderer.py`，否则 Web 与 Feishu 会分叉。

---

## 7. 策略与生成解耦

交易方向、风控阈值和可交易性判断不应由最终生成文本临场决定。

建议新增 Policy Engine：

```text
domain/policy/
  market_policy.py
  risk_policy.py
  trade_plan_policy.py
```

职责：

- 判断趋势、结构、触发位、失效位是否满足策略条件。
- 计算或校验 RR、仓位、止损距离。
- 给 NLG 提供结构化结论，例如 `actionability=observe/open_short/wait_pullback`。

LLM 的职责应更偏向解释：

- 把结构化结论说清楚。
- 根据用户上下文选择表达详略。
- 在事实不足时明确观望或要求补充。

---

## 8. 双模型分工

建议把模型使用分层：

| 层 | 推荐模型 | 说明 |
| --- | --- | --- |
| Query Classification | 小模型 / 本地规则 + 小模型兜底 | 输出 `response_contract`，成本低、延迟低 |
| Evidence Compression | 小模型或确定性摘要 | 压缩 history/source，不做交易判断 |
| Deep Analysis NLG | 主模型 | 复杂复盘、解释、跨资产比较 |
| Quick Reply | 模板或小模型 | 快问无需每次大模型长生成 |

这不是回到旧 Planner，而是把“回答形态”变成显式契约。主 LLM 仍可以负责复杂理解和工具调用，但快问不需要每次走重表达。

---

## 9. 可观测性闭环

必须按场景记录以下指标：

```text
response_mode
task_type
symbols
intervals
prompt_tokens
completion_tokens
reply_len
cn_chars
line_count
tool_calls
contract_violation_count
forbidden_phrase_hit
latency_ms
```

建议把“啰嗦率”作为线上 SLO：

```text
quick_lint_violation_rate = quick 场景中超过字数/行数/禁用词约束的比例
```

目标：

- quick 场景 `cn_chars <= 260` 的通过率 >= 95%
- quick 场景不出现收尾邀约句
- trade_plan 场景必须包含触发、止损或失效、目标或退出条件
- deep_dive 场景允许长，但必须结构清楚且无明显重复

现有 `scripts/smoke_response_style.py` 可以演进成 contract 回归测试：

```text
输入样例
  -> 生成 response_contract
  -> 生成 reply_text
  -> 校验长度、结构、禁用词、必需字段
```

---

## 10. 落地顺序

### Phase 1: 引入 Response Contract

不改业务主链路，只新增结构：

```text
application/planning/response_contract.py
application/planning/query_classifier.py
```

验收：

- “看看 ETH 行情” 能生成 `response_mode=quick`
- “给出持仓计划” 能生成 `response_mode=trade_plan`
- “详细复盘” 能生成 `response_mode=deep_dive`
- 不改变现有 Agent 调用结果

### Phase 2: Evidence Slots 替代全量注入

把 `build_direct_agent_input()` 的输入来源改为 `EvidencePacket`：

```text
ResponseContract
  -> EvidencePolicy
  -> EvidencePacket
  -> Prompt Input
```

验收：

- quick 场景不注入完整 profile/history/sources
- follow_up 场景能读取上一轮结论
- trade_plan 场景能读取风险画像或账户配置
- 日志能看到 evidence slot 命中情况

### Phase 3: 结构化事实到 NLG Renderer

工具结果先转结构化 facts，再渲染：

```text
Tool Result
  -> MarketFacts
  -> Policy Decision
  -> NLG Renderer
```

验收：

- 快答稳定在 6 行左右
- 支撑、阻力、触发、失效字段稳定出现
- 禁用词和收尾邀约不再依赖 Prompt 运气

### Phase 4: Policy Engine 独立

把交易动作与风控判断从 LLM 表达中剥离：

```text
domain/policy/
  -> actionability
  -> risk
  -> rr
  -> invalidation
```

验收：

- LLM 不再凭空给仓位
- 交易计划必须来自结构化 policy 输出
- smoke 测试能校验计划完整性

---

## 11. 与现有文档的关系

- `08_AGENT_DIRECT_CONTEXT_PLAN.md` 记录 Direct Context 成为主链路的历史施工方案。
- `09_LLM_INPUT_OUTPUT_TUNING_PLAN.md` 解决 Direct Context 定型后的 Prompt、工具输出和可观测性调优。
- 本文进一步提出下一代架构：用 `response_contract` 把“回答形态、证据选择、表达渲染”显式化。

本文不要求立刻推翻 Direct Context，而是建议把 Direct Context 从“统一大上下文”演进为“由 contract 驱动的证据包”。

---

## 12. 推荐目录形态

目标目录可以逐步演进为：

```text
application/
  planning/
    response_contract.py
    query_classifier.py
  evidence/
    evidence_builder.py
    evidence_policy.py
  rendering/
    nlg_renderer.py
    response_templates.py
  services/
    conversation_service.py

domain/
  policy/
    market_policy.py
    risk_policy.py
    trade_plan_policy.py

core/
  agent.py
  agent_context.py
  memory_api.py
  prompt.py
```

分层原则：

- `application/planning` 决定本轮怎么答。
- `application/evidence` 决定本轮给模型什么。
- `domain/policy` 决定交易和风控规则。
- `application/rendering` 决定最终怎么说。
- `infrastructure/adapters` 只负责渠道接入和发送。

---

## 13. 最小验收样例

建议先用以下样例做回归：

| 输入 | 预期 contract | 关键验收 |
| --- | --- | --- |
| 看看 ETH 行情 | `quick / market_view` | <= 6 行，含结论和关键位 |
| 你看看 ETH、SOL 的小时线 | `quick_compare / market_view` | 分标的短答，最后横向比较 |
| 我 1638 开空还拿着，下一步怎么做 | `trade_plan / position_follow_up` | 必须有触发、止损或失效、分批处理 |
| 详细复盘 ETH 这波下跌 | `deep_dive / review` | 允许展开，但需要结构化 |
| 刚才那个支撑还有效吗 | `follow_up / market_view` | 必须引用上一轮结论和当前变化 |

这组样例通过后，再考虑扩展到黄金、股票、多周期和用户画像场景。
