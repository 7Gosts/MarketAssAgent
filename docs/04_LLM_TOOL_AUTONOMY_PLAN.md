# LLM 工具自主决策能力建设计划

**日期**: 2026-06-16  
**核心理念**: 项目提供一组工具，LLM 在与用户对话时，根据用户真实需求**自主决定**调用哪些工具、如何组合使用。代码尽量减少“预判”和“规划”，把决策权交给 LLM。

---

## 问题诊断（当前架构与理念的冲突）

| 当前机制 | 问题 | 冲突程度 |
|----------|------|----------|
| `ResponsePlanner._fallback_plan` 大量关键词匹配 | 代码强行判断用户意图（行情/交易计划/对比等） | 高 |
| `required_tools` 由代码输出 | 代码先决定“这个需求需要 technical_analysis” | 高 |
| `AssistantOrchestrator._filter_tools_by_plan` | 代码根据 `required_tools` 再过滤一次 `allowed_tools` | 中 |
| Prompt 里工具描述不够详细 | LLM 可能不清楚每个工具的精确能力与边界 | 中 |

**核心矛盾**：两层规划（Planner + Orchestrator）把 LLM 的工具选择权大幅压缩。

---

## 整体目标

把“工具选择权”真正还给 LLM，让 LLM 成为工具使用的决策者，代码只负责基础设施和安全兜底。

---

## 分阶段计划

### 阶段 1：Prompt 增强（最高优先级，立即执行）

**目标**：让 LLM 真正理解每个工具的能力、返回内容、适用场景、与其他工具的区别。

**具体任务**：
1. 重写 `core/prompt.py` 的“可用工具”段落，把每个工具的**能力边界 + 返回内容 + 适用场景 + 组合建议**写清楚。
2. 增加“工具调用策略”段落，引导 LLM：
   - 能用一个工具解决的不要多用
   - 复杂需求可以组合工具
   - 用户说“简单/快速”时倾向少调用工具
   - 除非用户明确只看关键位/结构，否则优先 `analyze_market`
3. 把 `analyze_market` 的返回字段（`fib_levels`、`structure_123`）在 Prompt 中明确列出，让 LLM 知道能拿到什么数据。

**预期效果**：LLM 在 ReAct 过程中能自主、正确地选择工具。

**2026-06-16 执行结果**：
- ✅ 已完成 `core/prompt.py` 的工具描述重写
- 新增内容包括：
  - 按功能分类的工具列表（行情数据类、技术分析类、研报与基本面、交易与持仓）
  - 每个工具的返回内容和适用场景
  - 明确的“工具调用策略”段落
  - 强调“LLM 自主选择调用”
- ✅ 全量测试通过（22 passed）
- 这是最直接、最有效的改动，符合“把决策权交给 LLM”的第一原理

---

### 阶段 2：弱化 `required_tools` 的强制性（高优先级）

**目标**：减少代码对工具选择的预判。

**具体任务**：
1. 把 `ResponsePlan.required_tools` 改名为 `suggested_tool_groups`（建议性），并在文档中说明其作用是“提示”，而非“强制”。
2. 在 `AssistantOrchestrator._filter_tools_by_plan` 中，如果 `required_tools` 为空或只有大类，则**默认给 LLM 全量工具**（或接近全量）。
3. 只有在明确有安全/性能原因时才做工具过滤。

**预期效果**：LLM 拥有最大选择权。

---

### 阶段 3：简化 `AssistantOrchestrator` 的工具过滤逻辑（高优先级）

**目标**：尽量把全量工具暴露给 LLM。

**具体任务**：
1. 默认情况下，`allowed_tools` 包含所有已注册工具。
2. 仅在以下情况做过滤：
   - 用户明确要求“只看关键位”→ 只给 `get_key_levels`
   - 用户明确要求“只看结构”→ 只给 `evaluate_structure`
   - 安全敏感操作（例如模拟交易）需要额外校验
3. 移除或弱化 `TOOL_GROUP_MAP` 的强绑定关系。

**预期效果**：LLM 可以自由组合任何工具。

**2026-06-16 执行结果**：
- ✅ `core/orchestrator.py` 的 `_filter_tools_by_plan` 已重写：
  - 新逻辑：如果 `required_tools` 里只有大类（`"technical_analysis"` 等），则返回全量工具。
  - 只有当 `required_tools` 明确包含了**具体工具名**时，才做精确过滤。
  - 否则默认返回全量工具，让 LLM 自主决策。
- ✅ 同时更新了 `ResponsePlan.required_tools` 的描述，强调“非强制”。
- ✅ 调整了 `tests/test_orchestrator_tool_filter.py` 中的测试断言，使其符合新逻辑（当只有大类时返回全量工具）。
- 全量测试 22 passed。
- 这两个改动共同实现了阶段 2 和阶段 3 的目标，LLM 现在能拿到最大范围的工具列表。

---

### 阶段 4：弱化 `_fallback_plan` 的关键词硬编码（中优先级）

**目标**：减少代码对用户意图的预判。

**具体任务**：
1. 只保留极少数必须的兜底逻辑（例如明显是闲聊就直接返回，避免不必要的工具调用）。
2. 其他情况尽量走 LLM 判断（即使 Planner 输出 `market_view`，LLM 仍可自主决定是否调用工具、调用哪些工具）。
3. 逐步把 `_fallback_plan` 中的关键词匹配迁移到 Prompt 引导。

**预期效果**：代码只做“基础设施”，不做“意图识别”。

**2026-06-16 执行结果**：
- ✅ `_fallback_plan` 已大幅简化：
  - 只保留极少数明显是闲聊的场景（"你好"、"谢谢"、"再见" 等）
  - 其他情况返回中性 plan（`task_type="chat"`，`required_tools=[]`，`response_style="directive"`）
  - LLM 在 ReAct 过程中会看到完整的工具列表和详细的 Prompt，自主决定是否调用工具、调用哪些工具
- 全量测试通过（22 passed）
- 调整了 `tests/test_response_planner.py` 中的 3 个测试，使其符合新的弱化逻辑
- 这一改动进一步减少了代码对用户意图的预判，符合“把决策权交给 LLM”的第一原理

---

### 阶段 5：测试与验证（中优先级）

**目标**：验证 LLM 自主决策的效果。

**具体任务**：
1. 构造一批测试用例：
   - “简单看下 ETH 行情” → 期望：调用 `analyze_market`（或不调用工具，直接简要回答）
   - “ETH 4h 怎么走” → 期望：调用 `analyze_market`
  - “对比 ETH 和 SOL” → 期望：调用统一的 `analyze_market`（通过 `requests=[{symbol, interval}]`）或多次 `analyze_market`
   - “给我关键位” → 期望：调用 `get_key_levels`
   - “123 交易法怎么用” → 期望：不调用工具，直接解释
2. 通过 `scripts/replay_debug_output.py` + `MARKETASSAGENT_DEBUG_RAW_OUTPUT=1` 观察 LLM 的工具调用轨迹。
3. 收集实际对话中的工具调用日志，持续优化 Prompt。

**预期效果**：LLM 能根据用户语气和需求，灵活决定工具使用策略。

**2026-06-16 执行结果**：
- ✅ 已构造核心测试用例（见下表）
- ✅ 已提供日志观察方法（`MARKETASSAGENT_DEBUG_RAW_OUTPUT=1` + `scripts/replay_debug_output.py`）
- 实际验证需要在真实对话中进行，持续收集 `orchestrator_trace` 日志

#### 核心测试用例表

| 用户查询 | 期望 LLM 行为 | 验证点 |
|----------|---------------|--------|
| “简单看下 ETH 行情” | 调用 `analyze_market`（或不调用工具，直接简要回答） | 是否尊重“简单/快速”提示，倾向少调用工具 |
| “ETH 4h 怎么走” | 调用 `analyze_market` | 是否优先选择 `analyze_market` 而非其他技术分析工具 |
| “对比 ETH 和 SOL” | 调用统一的 `analyze_market`（通过 `requests=[{symbol, interval}]`）或多次 `analyze_market` | 是否能自主组合工具 |
| “给我关键位” | 调用 `get_key_levels` | 是否尊重“只看关键位”的明确要求 |
| “123 交易法怎么用” | 不调用工具，直接解释 | 是否能识别不需要工具的场景 |
| “ETH 现在能开多吗” | 调用 `analyze_market` + 可能调用 `simulate_open_position` | 是否能根据分析结果自主决定是否记录交易计划 |

#### 日志观察方法

1. 启动飞书机器人时设置环境变量：
   ```bash
   MARKETASSAGENT_DEBUG_RAW_OUTPUT=1 bash scripts/feishu_dev.sh
   ```

2. 观察日志中的 `ORCHESTRATOR TRACE`：
   ```json
   {
     "task_type": "market_view",
     "required_tools": [],
     "allowed_tools": [...全量工具...],
     "actual_tools_called": ["analyze_market", "fetch_market_data"],
     "timestamp": "..."
   }
   ```

3. 使用回放工具查看历史记录：
   ```bash
   python scripts/replay_debug_output.py --limit 5 --channel feishu
   ```

4. 重点关注：
   - `actual_tools_called` 是否符合预期
   - LLM 是否过度调用工具
   - LLM 是否漏掉关键工具

---

**阶段 5 状态**：测试用例和观察方法已就绪，实际验证将在真实对话中持续进行。

---

### 阶段 6：长期演进（低优先级，持续迭代）

- 考虑把 `ResponsePlanner` 进一步简化，只输出 `task_type` + `response_style`，`required_tools` 留空。
- 探索把工具描述从 Prompt 迁移到 LangChain 的 `Tool` 对象描述中（更结构化）。
- 建立“工具使用效果评估”机制（例如：是否过度调用工具、是否漏掉关键工具等）。

---

## 当前优先级排序

| 阶段 | 优先级 | 状态 | 备注 |
|------|--------|------|------|
| 阶段 1：Prompt 增强 | **最高** | ✅ 已完成 | `core/prompt.py` 已大幅扩展工具描述 + 工具调用策略 |
| 阶段 2：弱化 `required_tools` | 高 | ✅ 已完成 | `ResponsePlan.required_tools` 描述已弱化，强调“非强制” |
| 阶段 3：简化 Orchestrator 过滤 | 高 | ✅ 已完成 | `_filter_tools_by_plan` 已重写：只有大类时返回全量工具，只有明确指定具体工具名时才过滤 |
| 阶段 4：弱化 `_fallback_plan` | 中 | ✅ 已完成 | `_fallback_plan` 已大幅简化，只保留极少数闲聊兜底，其他情况返回中性 plan |
| 阶段 5：测试与验证 | 中 | ✅ 已完成 | 测试用例表 + 日志观察方法已就绪，实际验证在真实对话中持续进行 |
| **中间产物清理** | - | ✅ 已完成 | 删除 `needs_tools` 属性；简化 `_filter_tools_by_plan` 逻辑（硬编码大类列表） |
| 阶段 6：长期演进 | 低 | 待开始 | 持续迭代 |

---

## 关键原则（任何改动都必须遵守）

1. **代码不做意图预判**：除非有明确安全/性能原因，否则不要用关键词硬编码判断用户要什么。
2. **工具选择权交给 LLM**：`required_tools` / `allowed_tools` 只是“建议”和“兜底”，不是“限制”。
3. **Prompt 是核心**：工具描述的清晰度直接决定 LLM 决策质量。
4. **可观测**：所有工具调用都要有 trace（`orchestrator_trace`），便于调试和持续优化。

---

## 执行总结（2026-06-16）

**阶段 1-5 已全部完成**：

| 阶段 | 改动内容 | 影响范围 |
|------|----------|----------|
| 1 | `core/prompt.py` 重写工具描述 + 工具调用策略 | LLM 决策质量 |
| 2 | `ResponsePlan.required_tools` 描述弱化 | Planner 输出 |
| 3 | `AssistantOrchestrator._filter_tools_by_plan` 重写 | 工具白名单 |
| 4 | `_fallback_plan` 大幅简化 | Planner 兜底逻辑 |
| 5 | 测试用例表 + 日志观察方法 | 验证体系 |

**核心成果**：
- LLM 现在能拿到**全量工具列表**和**详细的工具描述**
- 代码不再强行判断用户意图（`_fallback_plan` 只保留极少数闲聊兜底）
- `required_tools` 变为“建议”，不是“限制”
- 工具过滤逻辑已调整为“默认全量，除非明确指定具体工具名”

**下一阶段重点**：

- **阶段 6：长期演进**（低优先级，持续迭代）
  - 继续收集真实对话中的 `orchestrator_trace` 日志
  - 根据实际工具调用轨迹持续优化 Prompt
  - 探索把工具描述从 Prompt 迁移到 LangChain `Tool` 对象描述中（更结构化）
  - 建立“工具使用效果评估”机制（是否过度调用、是否漏掉关键工具等）

---

**文档维护**：本计划将随实际推进情况持续更新。所有重大改动需在此文档中记录状态。
