# DeepSeek Harness、Pi Agent 与 MarketAssAgent 对比报告

**日期**: 2026-09-03  
**检索口径**: 以 DeepSeek Harness 与 Pi 官方仓库、官方文档为主；MarketAssAgent 以当前仓库代码和有效架构文档为准。外部项目均处于快速迭代中，以下结论对应 2026-09-03 检索到的 default branch 状态。

## 1. 摘要结论

先给结论：三者不是同一层面的直接竞品。

| 项目 | 本质定位 | 最强能力 | 主要代价 |
| --- | --- | --- | --- |
| DeepSeek Harness（DSH） | 通用 Agent Harness / 可组合运行平台 | 插件化、配置组合、工具安全管线、持久会话、多种运行面 | 体系大、插件与 Cordis 学习成本高；当前仍是 developer preview |
| Pi Agent | 极简终端编程 Agent Harness | 小内核、低约束、TypeScript 扩展、会话分支与压缩 | 默认安全边界较弱；很多能力需要自己写扩展 |
| MarketAssAgent | 金融行情与模拟交易垂直应用 | 领域工具、行情快照、纸账户订单状态、飞书/Web 入口 | 不是通用 Harness；插件、策略审批、通用沙箱能力还不完整 |

最重要的架构判断：

1. DSH 解决“如何把 Agent 平台拆成可替换插件并安全运行”。
2. Pi 解决“如何用尽可能小的核心做一个可深度定制的 coding agent”。
3. MarketAssAgent 解决“如何把 LLM 放进金融分析、会话承接和模拟交易业务闭环”。

因此，MarketAssAgent 不适合直接改造成 DSH 或 Pi 的复刻版。更合适的方向是：保留现有金融领域边界，吸收 DSH 的工具策略/事件审计 seam，以及 Pi 的 Agent 状态、压缩和扩展机制。

## 2. 对比范围与资料来源

### 2.1 名称确认

用户所说的 “DeepSeek haness” 按官方项目名称解释为 **DeepSeek Harness**，简称 **dsh**，官方仓库为 `deepseek-ai/deepseek-harness`。

“Pi Agent”按官方 Pi Coding Agent 解释，官方仓库当前位于 `earendil-works/pi`；不要与搜索结果中的第三方 `pi_agent_rust` 混为一谈。

### 2.2 外部资料

- [DeepSeek Harness README](https://github.com/deepseek-ai/deepseek-harness/blob/master/README.md)：项目定位、安装、运行面、developer preview 状态。
- [DeepSeek Harness Architecture](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)：Cordis、插件树、profile/bundle、Agent loop、session log 和扩展 seam。
- [DeepSeek Harness Tools](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/README.md)：工具 schema、工具限制、allow/deny/ask、执行管线和 PTC 模式。
- [DeepSeek Harness Base Bundle](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/bundle/base/README.md)：默认工具、持久会话、工作区写入限制和审批策略。
- [DeepSeek Harness Safety Notice](https://github.com/deepseek-ai/deepseek-harness/blob/master/SAFETY.md)：实验性状态和安全责任边界。
- [Pi Agent README](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md)：默认工具、模式、会话、技能、扩展和模型供应商。
- [Pi Agent Core README](https://github.com/earendil-works/pi/blob/main/packages/agent/README.md)：Agent state、工具循环、事件流、上下文转换和工具执行模式。
- [Pi Agent Core types](https://github.com/earendil-works/pi/blob/main/packages/agent/src/types.ts)：`beforeToolCall`、`afterToolCall`、`transformContext`、steering/follow-up 等接口。
- [Pi Agent site](https://pi.dev/)：官方产品入口与文档入口。

外部仓库在检索时的参考状态：DeepSeek Harness 根包版本为 `0.1.2-alpha.5`，参考 commit 为 `49a606bc5b5934603f22a26957a07dc799ab029`；Pi monorepo 根包版本为 `0.0.3`，参考 commit 为 `e266507b606b9552fa277252644054afd4384b11`。版本、commit 和文档会继续变化，不能把这些版本号当成长期兼容承诺。

## 3. 三条主链路

### 3.1 DeepSeek Harness

```text
dsh profile
  -> ordered bundles
  -> profile/home/CLI patch
  -> Cordis plugin tree
  -> agent turn/step events
  -> prompt sections + visible tool schemas
  -> tools/pre-execute
  -> guards
  -> tools/execute
  -> tools/post-execute
  -> tool result
  -> append-only SessionEvent log
```

DSH 的关键不是某一个 Agent 类，而是“所有能力都是插件”：模型适配器、工具注册表、session、agent loop、sandbox、审批、telemetry 都通过 Cordis context 组合。profile 决定 bundle，patch 按层覆盖配置；应用启动仍统一落到 `dsh` CLI。

### 3.2 Pi Agent

```text
Pi CLI / RPC / SDK
  -> Agent state
  -> runAgentLoop
  -> provider stream
  -> assistant message
  -> tool calls
  -> beforeToolCall
  -> tool execution（默认可并行）
  -> afterToolCall
  -> toolResult message
  -> next turn / steering / follow-up
  -> JSONL session tree + compaction/branch summary
```

Pi 的核心是一个有状态的 Agent 和一个可订阅的事件循环。它把 `AgentMessage[]` 转换为 LLM 消息，在工具执行前后提供 hook；coding-agent 在核心之上提供 TUI、会话管理、扩展、技能、prompt template 和 package。

### 3.3 MarketAssAgent

```text
Web / Feishu transport
  -> runtime/app/factory.py
  -> ConversationService.run()
  -> light summary + last_snapshot + user input
  -> MarketReActAgent.invoke()
  -> LangGraph: reason -> act(ToolNode) -> reason
  -> supervisor
  -> ConversationEnvelope
  -> MemoryAPI / session history / PostgreSQL trading tables
```

当前代码中，`ConversationService` 是唯一会话编排入口；`MarketReActAgent` 使用 LangGraph ReAct loop；`tools/registry.py` 统一注册工具；模拟交易由 `paper_orders`、`journal_ideas`、`journal_events` 三表承载。LLM 可以选择行情、上下文、研报、画像和模拟交易工具，但状态真相由代码和数据库维护。

## 4. 核心能力矩阵

| 维度 | DeepSeek Harness | Pi Agent | MarketAssAgent |
| --- | --- | --- | --- |
| 目标用户 | Agent 应用开发者、自动化用户 | 软件开发者、终端用户、扩展作者 | 金融分析与模拟交易用户 |
| 核心抽象 | Cordis plugin/context/profile/bundle | Agent/AgentState/AgentMessage/Extension | ConversationService + LangGraph + domain tools |
| 编排粒度 | turn → step → request → tool pipeline | prompt → turn → tool batch → next turn | reason → act → reason → supervisor |
| 模型可见工具 | `ctx.tools` schema 动态组装，可 scoped restrict | Agent state 中的 `AgentTool[]` | `get_all_tools()` 注册后由 LangChain bind |
| 工具安全 | allow/deny/ask、monotonic guard、超时、结果处理、sandbox/approval | `beforeToolCall`/`afterToolCall`，系统权限默认继承宿主 | 工具内部参数校验、prompt 约束、Graph 层过滤、领域状态校验 |
| 工具呈现 | native function calling、PTC、both | 原生 tool calling | LangChain tool calling |
| 工具并行 | 有并发上限和工具并行安全分类 | 默认 parallel，可切 sequential | 当前按 LangGraph/ToolNode 执行，未形成领域级并行策略 |
| 会话真相 | append-only SessionEvent log + projection | JSONL tree，支持 resume、branch、compaction | JSON/JSONL MemoryAPI + light summary；Graph checkpointer 当前未启用 |
| 记忆策略 | 从事件日志投影 model context | 原始会话树 + compaction/branch summary | `turn_summary`、`last_snapshot`、`analysis_snapshot`、tool observation |
| 扩展方式 | 插件、bundle、profile patch、service/event seam | TypeScript extensions、skills、prompt、themes、Pi packages | 直接改 Python 模块、注册工具、调整 prompt/领域服务 |
| 通用工具 | 文件、shell、搜索、HTTP、subagent、goal、plan 等 | 默认 read/write/edit/bash，其他靠扩展 | 行情、研报、记忆、画像、模拟交易 |
| 运行面 | Web、headless、SDK、ACP | TUI、print/JSON、RPC、SDK | Web、Feishu 长连接、HTTP API |
| 领域状态 | 通用 | 通用 | 交易订单、成交、关闭、取消和行情快照 |
| 安全定位 | 有较完整安全组件，但官方明确仍是实验版本 | 依赖宿主权限；官方建议容器化/沙箱 | 业务写入边界较明确，但没有通用宿主权限沙箱 |

## 5. DeepSeek Harness 的设计特点

### 5.1 “一切皆插件”比普通工具注册更深

MarketAssAgent 的工具注册是 `get_all_tools()` 返回一组 LangChain tools；DSH 则把工具、Agent、LLM、session、sandbox 等都变成 Cordis plugin service。这样做的收益是：

- 可以按 profile 组合不同运行面，而不复制一套 Agent。
- 一个能力可以被后续 patch 替换，注册和卸载具有作用域。
- 工具 schema、执行器、策略和 UI 展示可以分开挂载。
- 外部插件不需要修改 Harness 核心源码。

代价是配置和生命周期更复杂。对于只有行情分析和模拟订单的单一业务，完整引入 Cordis 会明显增加维护面。

### 5.2 工具执行是显式安全管线

DSH 的工具执行不是“模型调函数”这么简单，而是：

```text
参数 schema 验证
  -> tools/pre-execute：allow / deny / ask
  -> monotonic guards：后续不能把 deny 改回 allow
  -> execute wrapper：超时、重试、信号传播
  -> 工具主体
  -> post-execute：检查/替换结果
  -> finalize content
  -> observe-only result
```

这对 MarketAssAgent 最有价值，因为“创建模拟单、取消订单、平仓”已经属于写操作。当前项目把校验放在工具和 service 内部，DSH 风格可以再增加一个统一的动作策略 seam，把“需要确认、允许执行、拒绝执行、审计信息”从每个工具中抽出来。

### 5.3 持久会话以事件日志为真相

DSH 将模型可见内容定义为必须可从 session log 重建的内容，fork、resume、transcript、projection 和 UI 都从日志派生。这比当前 MarketAssAgent 的多通道记忆更统一，但也要求所有新的 model-visible context 都设计对应事件。

### 5.4 PTC 是工具规模扩展方案

DSH 支持把多个工具以 `run_code` 加生成 SDK 的方式呈现给模型，减少模型直接看到的工具 schema 数量，并允许程序化组合工具调用。对于 MarketAssAgent，短期工具数量不大，native tool calling 足够；只有在行情、研报、回测、组合管理工具明显增长后，PTC 才值得评估。

## 6. Pi Agent 的设计特点

### 6.1 小核心、强扩展

Pi 默认只给模型 `read`、`write`、`edit`、`bash` 四个工具，并明确不把 subagent、plan mode 等放进核心。用户通过 TypeScript extension、skill、prompt template、theme 和 package 自己增加能力。

这与 DSH 的“平台内置完整能力”形成对比：

- Pi 的默认面更小，启动和理解成本更低。
- Pi 的扩展自由度很高，但扩展质量和安全边界由使用者负责。
- Pi 更适合作为可嵌入的 coding agent 内核，而不是金融业务状态平台。

### 6.2 Agent loop 的可编程性很强

Pi 的 Agent API 对以下状态和动作直接开放：

- 当前 model、thinking level、tools、messages。
- `transformContext`：每次请求前裁剪或注入上下文。
- `beforeToolCall` / `afterToolCall`：工具前置拦截和结果后处理。
- `steering` / `follow-up` 队列：工作中插入用户指令或排队后续任务。
- parallel/sequential 工具执行模式。
- 结构化事件流供 TUI、RPC 和 SDK 消费。

MarketAssAgent 当前的 `ConversationService` 和 LangGraph loop 更偏应用编排；如果未来需要中途打断分析、动态切换上下文、长任务续跑，Pi 的这些 seam 值得借鉴。

### 6.3 Pi 的安全边界不能直接照搬

Pi 官方 README 明确说明它没有内置的文件、进程、网络、凭据权限系统，默认继承启动进程的权限；第三方扩展可以执行任意代码。Pi 建议使用容器、微虚拟机或其他 sandbox。

这对 MarketAssAgent 的启示是：Pi 的“极简和自由”不能直接等价为“适合交易写操作”。订单取消、平仓、外部发送等操作仍需由业务 service 做身份、状态和审计校验。

## 7. MarketAssAgent 的现状与差异

### 7.1 已经做对的地方

相对通用 Harness，本项目有三项明确优势：

1. **领域边界清晰**：行情分析、分析快照、用户画像、模拟订单属于明确的金融业务域。
2. **业务状态不交给 LLM**：订单状态由 repository/service/reconciliation 维护，LLM 只选择工具并解释结果。
3. **交易动作有结构化入口**：开单、查询、同步、取消通过工具进入三表，而不是从自然语言回复反推写库。

最近补齐的 `cancel_paper_order` 也遵循这一原则：要求精确 `order_id`，校验 session，只允许 `pending_trigger`，通过 `cancelled` 状态和 `order_cancelled` 事件软取消，不物理删除。

### 7.2 当前与两个 Harness 的主要差距

#### 工具治理

当前项目有 Graph 层的工具名过滤和工具调用去重日志，但还没有类似 DSH 的统一 `pre-execute` 策略管线。创建、取消等写操作的安全规则主要分布在具体工具/service 中。

#### 会话真相

当前项目有 `turn_summary`、`last_snapshot`、`analysis_snapshot` 和 `recent_tool_observation` 多种结构化承接方式；这些能力实用，但不是单一 append-only session log 的投影体系。LangGraph `checkpointer` 与 `store` 当前也未启用。

#### 扩展模型

工具 registry 是稳定 seam，但新增能力通常需要改 Python 源码并重新部署，不是安装一个第三方插件包即可完成。对于当前单业务项目这反而更容易审计；当业务扩展到回测、组合、宏观、通知和多代理时，维护成本会升高。

#### 运行面与任务控制

Web、Feishu 和 HTTP 入口已经够用，但没有 Pi 那样成熟的 steering/follow-up、会话分支、交互式压缩，也没有 DSH 那样统一的 headless/SDK/ACP profile 组合。

## 8. 适用场景判断

| 场景 | 首选 | 原因 |
| --- | --- | --- |
| 快速搭建通用本地 Agent 平台，允许文件/shell/网络/插件 | DSH | profile、bundle、工具策略、sandbox、Web/headless/SDK 已成体系 |
| 终端内做代码任务，想自己决定工具、UI 和工作流 | Pi | 核心小、扩展 API 直接、会话分支与压缩适合 coding workflow |
| 行情分析、模拟交易、飞书通知、订单状态审计 | MarketAssAgent | 现有领域模型、工具和三表状态比通用 Harness 更贴合 |
| 多租户、多工作区、第三方插件市场 | DSH 起点更好 | 其 profile/plugin/context 设计更接近平台产品 |
| 需要高度定制但不想引入完整平台 | Pi 起点更好 | 通过 extension/package 增量组合 |
| 需要严格业务状态机而非自由执行 | MarketAssAgent 起点更好 | 业务 service/repository 可对每个动作做状态约束 |

## 9. 对 MarketAssAgent 的建议

### P0：建立统一的写操作策略 seam

借鉴 DSH 的 `tools/pre-execute + guard`，但只覆盖金融写操作，不改造所有工具：

```text
LLM tool call
  -> 参数 schema 验证
  -> action policy：create / cancel / close / update
  -> session / user / order ownership 校验
  -> 是否需要确认
  -> domain service 状态校验
  -> repository transaction
  -> journal event + tool observation
```

当前 `cancel_paper_order` 已在 service 内完成精确订单号、会话归属、活跃状态和幂等处理；下一步可以把“需要确认”和审计字段抽成公共策略，而不是复制到未来的平仓、修改止损工具。

### P1：逐步收敛记忆为“事件 + 投影”

不建议立即复制 DSH 全套 Cordis session。可以在现有 MemoryAPI 上增加轻量 projection：

- 原始消息和工具观察继续保留。
- 每轮写入统一的 `turn_event` 或等价事实。
- `last_snapshot`、当前交易上下文、用户承接线索由 projection 生成。
- 失败时保留当前 JSON/JSONL fallback。

这样能减少当前 `turn_summary`、checkpoint、recent message 等通道之间的语义漂移，同时不影响现有会话入口。

### P2：借鉴 Pi 增加长任务控制

在不改变 `ConversationService.run()` 对外接口的前提下，按需增加：

- `steering`：用户在行情/研报长任务执行中插入纠偏。
- `follow-up`：上一轮完成后排队下一项复盘。
- 工具执行模式：只读行情工具可并行，交易写工具强制串行。
- 可观察的 turn/tool event stream，供 Web/Feishu 展示。

这比直接引入通用 subagent 或 plan mode 更贴合当前业务。

### P3：只有出现真实多业务插件需求时再引入插件包

当前 `tools/registry.py` 是够用的深模块边界。只有当以下需求同时出现时，才考虑 profile/plugin：

- 多个独立业务域需要独立发布。
- 用户或部署方需要在不改主仓库的情况下安装工具。
- 同一套 Agent 需要不同工具集、策略和运行面。

届时可以先做“工具包 + manifest + capability policy”，不必一次性引入 Cordis 全部运行时。

### P4：保留领域真相，不把订单状态交给 Harness

无论借鉴 DSH 还是 Pi，都不应让通用 Agent loop 直接决定订单状态。订单状态仍应由：

```text
工具参数 -> 领域 service -> repository transaction -> event
```

LLM 负责识别用户意图、选择工具和解释事实；不负责伪造成交、取消、平仓结果。

## 10. 风险与验证边界

### DeepSeek Harness

- 官方标注为 developer preview，存在兼容性破坏风险。
- 官方 Safety Notice 明确说明没有经过安全审计，不应作为不可信任务的唯一安全控制。
- 插件和模型生成的命令仍可能访问文件、进程、网络、凭据；sandbox 和 approval 是降低风险，不是绝对隔离。

### Pi Agent

- 官方核心强调可扩展和自由，但默认不提供完整宿主权限控制。
- 第三方 extension/package 拥有较高权限，安装前必须审查代码或使用容器化。
- 会话压缩是有损的；原始 JSONL 仍是回溯依据。

### MarketAssAgent

- 当前金融写操作安全依赖具体 service/tool 的实现，尚未有跨工具统一 policy gate。
- Graph 状态不持久化，不能把 checkpointer 当作跨进程会话真相。
- MemoryAPI 默认 JSON/JSONL，PostgreSQL 主要承载分析快照和模拟交易结构化数据。
- 行情与研报等外部 I/O 的失败必须继续返回明确错误，不能让 LLM 以猜测补齐事实。

## 11. 最终结论

如果目标是把 MarketAssAgent 做成“金融版的通用 Agent 平台”，应优先吸收 DSH 的三个思想：

1. 工具 schema、执行、策略、结果展示分层。
2. 写操作统一经过可审计的 policy seam。
3. 结构化事件作为长期状态和投影的基础。

如果目标是把 MarketAssAgent 做成“更好用的交易分析 Agent”，应优先吸收 Pi 的三个思想：

1. 保持核心主链路小而明确。
2. 增加长任务 steering/follow-up 与工具执行模式。
3. 用扩展 seam 支持自定义提示词、工具和输出，而不把所有能力塞进主 prompt。

当前最合理的路线不是替换现有 LangGraph + ConversationService，而是：

```text
保留金融领域 service / repository / event 真相
  -> 增加写操作统一策略
  -> 统一记忆 projection
  -> 增加长任务控制与工具事件流
  -> 业务域变多后再引入轻量插件包
```

这条路线能获得两个 Harness 的关键优点，同时避免为了通用性牺牲金融订单状态的可审计性。
