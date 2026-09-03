# 去除 LangGraph 与 LangChain 改造方案及测试守护矩阵

**状态**：实施中（本地代码迁移和 DeepSeek 冒烟完成；待干净环境与 HCT 端点修复后验证）

**范围**：同时移除 `langgraph`、`langchain-core`、`langchain-openai`

**适用代码**：`src/core`、`src/tools`、`src/domain`、`tests`、`scripts`

**原则**：先替换编排和协议，再删除依赖；不改动行情分析、记忆和模拟交易领域规则。

## 1. 结论

本项目不需要继续依赖 LangGraph 或 LangChain 来完成当前 Agent 流程。当前实际使用的图只有三个节点：

```text
reason -> act -> reason -> supervisor
```

它实际提供的能力主要是：

1. 调用一次 LLM；
2. 读取模型返回的 tool calls；
3. 过滤不允许的工具；
4. 执行工具并把结果回灌给模型；
5. 没有 tool call 时结束并生成最终回复。

这部分可以由项目内约 4 个轻量模块替代，不需要再引入新的 Agent 框架。

推荐最终架构：

```text
ConversationService
        |
        v
MarketReActAgent
        |
        v
NativeAgentLoop
   +----+-----+
   |          |
   v          v
LLMClient  ToolExecutor
                |
                v
        Domain Service / Repository
```

LLM 只负责选择工具和解释工具结果；订单状态、会话归属、取消规则、数据库事务和事件记录仍由领域服务负责。

## 2. 当前调用链和耦合点

### 2.1 真实调用链

```text
Web / Feishu
    -> ConversationService.run()
    -> build_light_agent_input()
    -> MarketReActAgent.invoke()
    -> StateGraph.ainvoke()
    -> reason
    -> ToolNode
    -> reason
    -> supervisor
    -> ConversationEnvelope
    -> Web / Feishu renderer
```

关键入口是 `ConversationService` 和 `MarketReActAgent.invoke(...)`。迁移时应保持这两个入口的调用方式不变，避免 transport 层、消息渲染层和领域层同时改动。

### 2.2 LangGraph 直接耦合

| 文件 | 当前耦合 | 改造方向 |
| --- | --- | --- |
| `src/core/graph.py` | `StateGraph`、`ToolNode`、`Runtime`、`RunnableConfig`、`AIMessage` | 拆为 `NativeAgentLoop`、`ToolExecutor`、自有消息协议 |
| `src/core/state.py` | `AgentState`、`add_messages`、`BaseMessage` | 使用普通 `TypedDict` 和自有 `Message` |
| `src/core/agent.py` | `ChatOpenAI`、`HumanMessage`、`AIMessage`、`graph.ainvoke` | 使用 `LLMClient` 和 `NativeAgentLoop` |
| `src/tools/context_memory.py` | `InjectedState` | 使用显式 `ToolContext` |
| `src/domain/market/analysis_service.py` | `InjectedState` | 使用显式 `ToolContext` |
| 测试 | `ToolNode`、LangChain 消息对象 | 改测自有执行器和消息协议 |

### 2.3 LangChain 直接耦合

| 用途 | 位置 | 替换方式 |
| --- | --- | --- |
| `ChatOpenAI` | `src/core/agent.py`、`src/core/asset_discovery.py`、`scripts/real_tool_calling_check.py` | 基于已有 `httpx` 实现 OpenAI-compatible client |
| 消息对象 | `src/core/agent.py`、`src/core/state.py`、测试 | 自有 `Message` dataclass 或 dict |
| `@tool` | `src/tools/*.py`、`src/domain/market/analysis_service.py`、`src/domain/profile/user_profile.py` | 普通函数 + `ToolSpec` |
| `BaseTool` | `src/tools/registry.py` | `list[ToolSpec]` |
| `ChatPromptTemplate` | `src/core/prompt.py` | 普通 system prompt 字符串 |
| `RunnableConfig` | `src/core/graph.py` | 显式 `ToolContext` |

当前 `requirements.txt` 中需要最终删除：

```text
langgraph>=0.2.0
langchain-core>=0.3.0
langchain-openai>=0.2.0
```

`httpx` 已存在，可以直接作为新的 LLM HTTP 调用基础，不需要新增 OpenAI SDK。

## 3. 不变的业务契约

以下内容应作为迁移的硬性验收标准：

### 3.1 Agent 外部接口不变

```python
await agent.invoke(
    user_input,
    session_id=session_id,
    request_id=request_id,
    history=history,
    allowed_tools=allowed_tools,
)
```

`ConversationService`、Web、飞书和 HTTP 入口不应感知底层是否使用 LangGraph。

### 3.2 工具名称不变

以下工具名称不能因为移除装饰器而变化：

- `analyze_market`
- `get_key_levels`
- `evaluate_structure`
- `analyze_fibonacci`
- `fetch_market_data`
- `search_research_reports`
- `get_response_guidance`
- `get_user_profile`
- `update_user_profile`
- `get_last_snapshot`
- `get_previous_analysis_snapshot`
- `get_recent_tool_observations`
- `search_conversation_summaries`
- `prepare_simulated_order`
- `simulate_open_position`
- `cancel_paper_order`
- `reconcile_paper_orders`
- `get_journal_status`

### 3.3 工具返回结构不变

工具返回的业务 dict 不应改成框架专属对象。工具结果进入 LLM 消息前才做 JSON 序列化，领域层和现有测试仍使用 dict。

### 3.4 订单领域行为不变

`cancel_paper_order` 迁移后仍必须：

- 只接受精确 `order_id`；
- 校验订单属于当前 `session_id`；
- 只允许取消 `pending_trigger`；
- 将状态变为 `cancelled`，而不是物理删除；
- 保留 `order_cancelled` 事件；
- 重复取消保持幂等；
- 已成交持仓不能用取消代替平仓。

## 4. 目标内部协议

### 4.1 消息协议

新增 `src/core/message_protocol.py`：

```python
@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None
```

模型请求使用 OpenAI-compatible dict：

```python
{
    "role": "assistant",
    "content": "",
    "tool_calls": [
        {
            "id": "call_123",
            "type": "function",
            "function": {
                "name": "cancel_paper_order",
                "arguments": "{\"order_id\": \"ord_x\"}",
            },
        }
    ],
}
```

工具结果使用：

```python
{
    "role": "tool",
    "tool_call_id": "call_123",
    "name": "cancel_paper_order",
    "content": "{\"status\": \"success\"}",
}
```

### 4.2 LLM 客户端

新增 `src/core/llm_client.py`，统一封装：

```text
POST {base_url}/chat/completions
```

请求至少支持：

- `model`；
- `messages`；
- `tools`；
- `tool_choice=auto`；
- `temperature`；
- timeout；
- HTTP 错误和 JSON 错误转换。

响应统一解析：

- `message.content`；
- `message.tool_calls`；
- `finish_reason`；
- `usage.prompt_tokens`；
- `usage.completion_tokens`；
- `usage.total_tokens`。

DeepSeek 等 provider 经常把 `function.arguments` 返回为 JSON 字符串，解析必须集中在 client 或协议层，不能散落在 Agent Loop 中。

暂不实现流式输出。当前主链路使用完整响应，先保持行为一致，后续再单独设计 streaming protocol。

### 4.3 工具协议

新增 `src/core/tool_protocol.py`：

```python
@dataclass(frozen=True)
class ToolContext:
    session_id: str
    request_id: str
    storage: Any | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[..., Any]
    side_effect: Literal["read", "write"]
```

`ToolSpec.parameters` 只描述模型可以填写的业务参数。以下字段不应暴露给模型：

- `session_id`；
- `request_id`；
- 数据库连接；
- Memory API；
- runtime/store 对象。

### 4.4 工具执行器

新增 `src/core/tool_executor.py`，负责：

1. 按名称查找 `ToolSpec`；
2. 校验工具是否在允许集合中；
3. 解析 JSON 参数；
4. 校验必填字段和基础类型；
5. 注入 `ToolContext`；
6. 捕获异常并转为结构化工具结果；
7. 记录 tool call 和 tool result；
8. 维护 `tool_call_id` 关联。

读工具可以并行执行；写工具必须串行执行。订单创建、取消、成交同步不能因为模型一次返回多个 call 而发生未定义的并行写入。

## 5. Agent Loop 设计

新增 `src/core/agent_loop.py`，替换 `src/core/graph.py` 的图编排：

```python
async def run_agent_loop(state: AgentState) -> AgentState:
    for step in range(max_steps):
        response = await llm_client.complete(
            messages=state["messages"],
            tools=tool_registry.schemas(
                allowed_tools=state["allowed_tools"]
            ),
        )

        state["messages"].append(response.message)

        if not response.tool_calls:
            return finalize(state)

        results = await executor.execute_many(
            response.tool_calls,
            context=ToolContext(
                session_id=state["session_id"],
                request_id=state["request_id"],
            ),
        )
        state["messages"].extend(results)

    return loop_limit_result(state)
```

必须从原 `graph.py` 保留的行为：

- 工具名称过滤；
- 重复工具调用检测；
- 工具调用数量告警；
- token usage 记录；
- debug trace；
- 最终 recommendation/disclaimer；
- 最大步数保护。

### `allowed_tools` 兼容问题

当前代码中 `allowed_tools=[]` 的含义是“未指定，因此使用全部工具”，而不是“禁止全部工具”。`ConversationService` 当前正是传入空列表，因此第一版迁移必须保持这一语义。

建议先增加测试锁定旧行为，后续再考虑改为：

```text
allowed_tools=None  -> 使用全部工具
allowed_tools=[]    -> 不允许工具
```

如果要改变语义，必须同步修改 `ConversationService` 和所有调用方，不能只在新 Loop 中单独改变。

## 6. 文件迁移清单

### 6.1 新增文件

| 文件 | 职责 |
| --- | --- |
| `src/core/message_protocol.py` | 消息、工具调用、工具结果协议 |
| `src/core/llm_client.py` | OpenAI-compatible HTTP 调用 |
| `src/core/tool_protocol.py` | `ToolSpec`、`ToolContext` |
| `src/core/tool_executor.py` | 参数校验、上下文注入、工具执行 |
| `src/core/agent_loop.py` | 自有 ReAct / tool-calling 循环 |
| `tests/test_native_agent_loop.py` | Agent Loop 行为测试 |
| `tests/test_tool_executor.py` | 工具执行器和安全边界测试 |
| `tests/test_llm_client.py` | HTTP 请求和响应解析测试 |

### 6.2 修改文件

| 文件 | 修改内容 |
| --- | --- |
| `src/core/agent.py` | 去掉 `ChatOpenAI`、LangChain Message 和 Graph 调用 |
| `src/core/state.py` | 去掉 `add_messages`、`BaseMessage`、LangGraph 注释和类型 |
| `src/core/prompt.py` | 去掉 `ChatPromptTemplate`，保留 system prompt 文本 |
| `src/core/supervisor.py` | 从 graph node 改为普通 finalize 函数 |
| `src/tools/registry.py` | 返回 `ToolSpec`，不再依赖 `BaseTool` |
| `src/tools/*.py` | 去掉 `@tool`，将运行时参数改为 `ToolContext` |
| `src/domain/market/analysis_service.py` | 去掉 `InjectedState` |
| `src/domain/profile/user_profile.py` | 去掉 `@tool` |
| `src/core/asset_discovery.py` | 使用统一 `LLMClient` |
| `scripts/real_tool_calling_check.py` | 使用统一 `LLMClient` |
| `pyproject.toml` | 去掉 LangGraph 相关项目描述 |
| `requirements.txt` | 删除三个 LangChain/LangGraph 依赖 |
| `docs/INDEX.md` | 增加本文档入口 |

### 6.3 最终删除或收敛文件

`src/core/graph.py` 不应继续作为第二套 Agent 实现保留。推荐在迁移完成后删除；如果必须保留文件名，应只保留极薄的兼容导出，不得保留 LangGraph import。

`checkpointer` 和 `store` 是 LangGraph 专属参数。当前生产代码没有发现实际调用方，迁移时应移除；如果外部调用仍传非空值，应给出明确废弃错误，不能静默忽略持久化参数。

## 7. 现有测试守护能力对照

测试迁移的原则是：保留业务行为测试，替换框架实现测试。不能因为删除 `ToolNode` 就删除订单、记忆或分析能力的验证。

### 7.1 应继续保留、基本不改的测试

| 测试文件 | 当前守护能力 | 对本次迁移的价值 |
| --- | --- | --- |
| `tests/test_analysis_output_sanitize.py` 中分析 schema 测试 | 分析结果字段、脱敏和多标的结构 | 守护 LLM 收到的工具结果契约不变 |
| `tests/test_paper_trading_reconciliation.py` | pending → filled → closed 状态机和事件 | 守护 Agent Loop 不破坏交易域状态流转 |
| `tests/test_paper_trading_repository.py` | 订单和事件持久化 | 守护写入事务和数据结构 |
| `tests/test_journal_status.py` | 账本查询结果 | 守护查询工具事实来源 |
| `tests/test_sim_account_tools.py` | 模拟开单、自然语言标的拦截、取消、幂等 | 守护最重要的金融写操作边界 |
| `tests/test_context_memory_tools.py` 中 JSON/DB roundtrip | 快照、工具观察、会话摘要读取 | 守护记忆工具行为 |
| `tests/test_direct_agent_context_flow.py` | light input、session、request_id、summary | 守护 ConversationService 到 Agent 的外部契约 |
| `tests/test_phase_c_memory_flow.py` | turn summary、历史快照承接 | 守护上下文拼装和持久化 |
| `tests/test_user_profile_memory.py` | 用户画像读写 | 守护 profile 工具的业务行为 |
| `tests/test_user_profile_tools_injection.py` | 画像上下文注入 | 迁移后应继续验证显式 `ToolContext` |
| `tests/test_prompt_contract.py` | system prompt 关键规则 | 守护交易取消、周期选择、输出契约不回退 |
| `tests/test_supervisor.py` | 最终回复和免责声明 | 守护 graph supervisor 改为 finalize 后的输出 |
| `tests/test_conversation_envelope.py` | 对外响应 envelope | 守护 transport 不受底层替换影响 |
| `tests/test_feishu_renderer.py` | 飞书渲染 | 守护最终文本交付 |
| `tests/test_api_delivered_summary.py` | API 返回和交付摘要 | 守护接口层兼容 |
| `tests/test_market_symbol_resolution.py` | 标的解析 | 守护 asset discovery 和工具参数准备 |

这些测试不应该因为“框架已删除”而降低断言。它们测试的是项目能力，不是 LangChain 类型。

### 7.2 必须重写的框架耦合测试

| 当前测试 | 当前绑定 | 重写后的守护目标 |
| --- | --- | --- |
| `tests/test_graph_tool_guardrails.py` | `AIMessage`、`ToolNode`、`graph.py` 私有函数 | `NativeAgentLoop` 的工具过滤、签名去重、告警和最大步数 |
| `tests/test_context_memory_tools.py` 中 `ToolNode(...)._func` | LangGraph 注入机制 | `ToolExecutor` 把 `ToolContext.request_id` 注入快照工具 |
| `tests/test_analysis_output_sanitize.py` 中 `ToolNode` 测试 | LangGraph `InjectedState` | 直接通过 `ToolExecutor` 执行 `analyze_market`，验证 session/request 进入持久化 |
| `tests/test_agent_thread_id.py` | Graph `configurable.thread_id`、`checkpointer` | `session_id/request_id` 进入 Agent Loop 和 ToolContext |
| `tests/test_sim_account_tools.py` 中 `.invoke(...)` | LangChain `StructuredTool.invoke` | 改为直接调用业务函数或 `ToolExecutor.execute` |
| `tests/test_context_memory_tools.py` 中 `.invoke(...)` | LangChain tool wrapper | 改为直接函数调用或 `ToolExecutor.execute` |
| `tests/test_agent.py` | Dummy LangChain LLM/Graph | 使用 fake `LLMClient`，验证无工具和有工具两条路径 |

### 7.3 现有覆盖与目标覆盖

| 功能 | 当前已有测试 | 迁移后必须具备 |
| --- | --- | --- |
| 无工具直接回复 | `test_agent.py` 部分覆盖 | `test_native_agent_loop_returns_final_answer` |
| 单次工具调用 | Graph 间接覆盖 | `test_native_agent_loop_executes_one_tool` |
| 多轮工具调用 | 缺少稳定的 Loop 契约测试 | `test_native_agent_loop_reenters_llm_after_tool_result` |
| 多个 tool calls | 缺少顺序/并行契约 | `test_executor_serializes_write_tools` |
| 非法工具过滤 | `test_graph_tool_guardrails.py` | `test_loop_rejects_unknown_or_disallowed_tool` |
| 重复工具调用 | `test_graph_tool_guardrails.py` | `test_loop_detects_duplicate_tool_signature` |
| tool call id 配对 | 当前不完整 | `test_tool_result_preserves_tool_call_id` |
| JSON 参数错误 | 当前不完整 | `test_executor_returns_structured_argument_error` |
| 工具抛异常 | 当前不完整 | `test_executor_converts_exception_to_tool_result` |
| 最大步数 | 当前不完整 | `test_loop_stops_at_max_steps` |
| session/request 注入 | `test_agent_thread_id.py`、分析测试 | `test_executor_injects_context_without_model_visibility` |
| `allowed_tools=[]` 兼容语义 | `test_direct_agent_context_flow.py` 只验证传入 | 新增 active tool selection 测试 |
| LLM HTTP 请求 | 没有 | `tests/test_llm_client.py` |
| provider 错误 | 没有 | timeout、4xx、5xx、非法 JSON 测试 |
| token usage | Graph 内部已有逻辑 | client response normalization 测试 |
| 取消订单 | `test_sim_account_tools.py` | 保留原测试 + 通过 ToolExecutor 的集成路径 |

## 8. 建议新增测试明细

### 8.1 `tests/test_native_agent_loop.py`

使用 fake LLM，不连接网络，按预设序列返回响应：

1. 第一轮直接返回文本，Agent 正常结束；
2. 第一轮返回 `get_journal_status`，第二轮根据 tool result 返回文本；
3. 第一轮返回两个只读工具，结果全部回灌；
4. 模型返回未注册工具，工具不执行；
5. 模型重复返回相同工具和参数，产生告警或结构化拒绝；
6. 连续返回 tool call，超过 `max_steps` 后结束；
7. 最终结果包含 `recommendation.text` 和 disclaimer；
8. token usage 被标准化并写入 debug 记录。

### 8.2 `tests/test_tool_executor.py`

重点测试：

1. 工具名称查找；
2. allowed tool 过滤；
3. 缺少必填参数；
4. 参数 JSON 非法；
5. 工具异常转换为错误结果；
6. `session_id/request_id` 注入；
7. 注入字段不出现在模型 schema；
8. write 工具串行执行；
9. read 工具可以并行执行；
10. `tool_call_id` 保持不变。

### 8.3 `tests/test_llm_client.py`

使用 `httpx.MockTransport` 或等价 fake transport，测试：

1. 正确发送 model、messages、tools、temperature；
2. DeepSeek/OpenAI-compatible tool call 解析；
3. arguments JSON 字符串解析；
4. 无 tool call 的普通回复；
5. usage 字段解析；
6. timeout；
7. 4xx/5xx；
8. provider 返回非法 JSON；
9. 缺少 choices/message 时返回清晰错误。

## 9. 交易写操作的专门守护

删除 LangGraph/LangChain 后，订单工具不能退化为“普通函数直接执行”。调用链必须保持：

```text
LLM tool call
    -> tool name allowlist
    -> schema validation
    -> ToolContext 注入
    -> 订单 service policy
    -> repository transaction
    -> journal event
    -> tool observation
```

至少需要保留以下测试：

| 场景 | 预期 |
| --- | --- |
| 精确取消 pending 订单 | 成功，状态为 `cancelled` |
| 其他 session 取消 | 失败，不改变订单 |
| 重复取消 | 成功幂等，不重复写事件 |
| 取消 filled 订单 | 失败，提示应使用平仓流程 |
| 按 symbol 模糊取消 | 不允许执行 |
| ToolExecutor 缺失 context | 写操作拒绝执行 |
| LLM 伪造 session_id | 不采信模型字段，使用 ToolContext |

其中最后两项是移除 `InjectedState` 后必须补上的安全回归测试。

## 10. 迁移阶段和回滚点

### 阶段 1：协议层

新增消息、LLM、工具协议，不删除旧依赖。先完成 fake 测试。

**回滚点**：只新增文件，不影响现有 Agent。

### 阶段 2：工具执行层

把工具注册从 `BaseTool` 改为 `ToolSpec`，将隐式状态改为 `ToolContext`。

**回滚点**：保留旧工具函数的业务逻辑，出现问题时只回退 registry/executor。

### 阶段 3：Agent Loop

由 `MarketReActAgent` 切换到 `NativeAgentLoop`，保持 `invoke(...)` 接口不变。

**回滚点**：切换前保留一个独立提交；不要把旧 Graph 和新 Loop 混在一个提交里。

### 阶段 4：测试和脚本迁移

替换 `ToolNode`、LangChain Message、`ChatOpenAI` 的测试和诊断脚本。

### 阶段 5：删除依赖

确认源码和测试无 import 后，删除 requirements 中三个依赖，再创建干净环境验证。

## 11. 验证命令和通过标准

### 静态检查

```bash
rg -n "langgraph|langchain" src tests scripts requirements.txt pyproject.toml
```

最终结果应为空，文档可以保留迁移历史中的关键词，但生产源码、测试和依赖文件不应再引用它们。

### 语法和测试

```bash
python -m compileall src
python -m pytest tests/ -q
```

### 干净环境

在全新虚拟环境中安装 requirements，验证：

1. 应用可以导入和启动；
2. Agent 可以使用 fake provider 完成测试；
3. 真实 DeepSeek/OpenAI-compatible provider 可以完成一次 tool call smoke test；
4. 模拟订单创建、取消、查询和 reconcile 回归通过。

### 性能检查

工具执行本身的扫描复杂度不因框架替换而改变：

- 读工具、订单查询和数据库访问瓶颈仍主要在网络/数据库 I/O；
- Agent Loop 每轮只增加一次 LLM 请求；
- 多个只读工具可以并行，减少总等待时间；
- 写工具串行执行，优先保证订单状态一致性；
- 重复调用检测使用工具签名集合，单轮检查为 `O(k)`，不会对完整消息历史反复扫描。

## 12. 最终验收清单

- [x] `MarketReActAgent.invoke(...)` 接口不变；
- [x] `ConversationService`、Web、Feishu 无框架 import；
- [x] 所有工具名称和业务返回 dict 不变；
- [x] `ToolContext` 替代 `InjectedState`；
- [x] 模型不可伪造 session/request；
- [x] `cancel_paper_order` 仍是软取消、精确订单、幂等；
- [x] `StateGraph`、`ToolNode`、`BaseTool`、`@tool` 全部移除；
- [x] `ChatOpenAI`、LangChain Message 全部移除；
- [x] `asset_discovery` 使用统一 LLM client；
- [x] 原有领域和 transport 测试全部通过；
- [x] 新增 Agent Loop、ToolExecutor、LLM client 测试通过；
- [ ] 干净环境不安装三个旧依赖仍可启动；
- [x] `rg` 静态扫描无生产代码和测试残留 import。

## 13. 建议实施顺序

建议按以下提交拆分：

```text
1. add native message/tool/llm protocols
2. migrate tool registry and context injection
3. replace LangGraph agent loop
4. migrate tests and diagnostic scripts
5. remove LangGraph and LangChain dependencies
```

不建议把“协议重写、工具迁移、领域逻辑调整、依赖删除”放进一个提交。这样可以在每个阶段运行已有测试，并在发现 provider 或工具调用兼容问题时快速定位回滚点。

### 13.1 本轮实施记录（2026-09-04）

本轮已完成本地代码迁移：

- 新增原生消息协议、OpenAI-compatible HTTP 客户端、工具协议、执行器和 Agent Loop；
- 工具改为普通 Python 函数，由 `ToolRegistry` 手写并控制模型可见 schema；
- `session_id/request_id` 由 `ToolContext` 注入，订单写工具不采信模型提供的运行时字段；
- `asset_discovery` 和真实 tool-calling 检查脚本改用统一 HTTP 客户端；
- 删除旧 `src/core/graph.py`，并从 `requirements.txt` 删除三个框架依赖；
- 原有框架耦合测试已迁移为原生协议、执行器和循环测试。

本地结果：`python3 -m pytest -q tests/` 为 `118 passed`；静态扫描在源码、运行时、测试、脚本和依赖文件中无旧框架引用。DeepSeek 已完成真实 tool-calling 冒烟；HCT 因原配置域名停止服务返回 HTTP 400。HCT 新端点验证与全新虚拟环境安装仍是部署前验收项，因此文档状态暂不标记为“已完成”。

## 14. 具体实施手册

本节是实际施工顺序，按提交执行即可。默认不修改行情算法、Repository、MemoryAPI 和订单状态机；只替换 LLM 适配、消息协议、工具注册和 Agent 编排。

### 14.1 开始前的基线

先建立独立分支或保存一个可回滚提交：

```bash
git status --short
git switch -c remove-langchain-native-agent
python3 -m pytest tests/ -q
```

当前工作区可能存在与本任务无关的修改，不能使用 `git reset --hard`、`git checkout --` 或批量清理来建立基线。迁移提交只允许包含本次涉及的源码、测试、依赖和文档。

在开始删除依赖前，先记录基线：

```bash
python3 -m pip freeze | rg 'langgraph|langchain|httpx'
python3 -m pytest -q \
  tests/test_direct_agent_context_flow.py \
  tests/test_context_memory_tools.py \
  tests/test_analysis_output_sanitize.py \
  tests/test_sim_account_tools.py \
  tests/test_paper_trading_reconciliation.py
```

如果基线中已经有失败，必须记录测试名、失败原因和是否属于本次迁移。不能把迁移后的失败全部归因于新实现。

当前已知的一项基线风险是模拟订单测试中的 `recent_events` 顺序断言；该问题应单独处理或在迁移记录中标记，不能在替换 Agent 编排的提交中顺便修改订单事件排序。

### 14.2 提交拆分和完成条件

| 提交 | 主要文件 | 完成条件 |
| --- | --- | --- |
| `native-protocol` | `src/core/message_protocol.py`、`tool_protocol.py`、协议测试 | 不依赖 LangChain，可以构造消息、工具调用和工具结果 |
| `native-llm-client` | `src/core/llm_client.py`、`asset_discovery.py`、LLM client 测试 | fake HTTP 下能解析普通回复、tool call、usage 和错误 |
| `native-tool-executor` | `tool_executor.py`、`registry.py`、工具适配 | 当前注册工具均有稳定 name/schema/execute，运行时字段不暴露给模型 |
| `native-agent-loop` | `agent_loop.py`、`agent.py`、`state.py`、`prompt.py`、`supervisor.py` | fake LLM 能完成无工具、单工具、多轮工具和最大步数路径 |
| `native-test-migration` | 迁移后的旧测试、新增 Loop/Executor 测试 | 测试不再导入 LangChain/LangGraph |
| `remove-framework-deps` | `requirements.txt`、`pyproject.toml`、旧实现删除 | 干净环境中无三个旧依赖仍可启动和测试 |

每个提交都应单独执行最小测试。最后一个提交才删除 `src/core/graph.py` 和三个依赖。

## 15. 目标代码接口（可直接照此实现）

### 15.1 `src/core/message_protocol.py`

建议使用 dataclass，不引入新的消息框架：

```python
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal


MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: MessageRole
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    tool_call_id: str = ""
    name: str = ""

    def to_openai_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
        }
        if self.role == "assistant" and self.tool_calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(
                            call.arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
                for call in self.tool_calls
            ]
        if self.role == "tool":
            result["tool_call_id"] = self.tool_call_id
            if self.name:
                result["name"] = self.name
        return result
```

实现时增加两个构造函数：

```python
def user_message(content: str) -> Message: ...

def tool_message(
    *,
    tool_call_id: str,
    name: str,
    result: Any,
) -> Message: ...
```

`tool_message` 统一使用 `json.dumps(..., default=str)`；如果工具返回字符串则原样保留，避免双重 JSON 编码。

### 15.2 `src/core/llm_client.py`

定义 provider 无关的返回对象：

```python
@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_prompt_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    message: Message
    finish_reason: str = ""
    usage: TokenUsage = TokenUsage()
    raw: dict[str, Any] | None = None


class LLMClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class OpenAICompatibleLLMClient:
    async def complete(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        ...
```

构造函数读取 `runtime/config/runtime_config.py` 暴露的统一配置：

```python
settings = get_llm_runtime_settings()
model = require_llm_model(settings, context="Agent")
base_url = settings["base_url"]
api_key = settings["api_key"]
temperature = resolve_llm_temperature(settings, fallback=0.2)
```

不要在代码、测试或文档中复制真实 API key；测试必须使用假 key 和 MockTransport。

URL 规则：

```python
def completion_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"
```

这样可以同时兼容不带版本路径、带 `/v1` 以及已经带 `/chat/completions` 的地址。

HTTP 请求规则：

```python
payload = {
    "model": model,
    "messages": [message.to_openai_dict() for message in messages],
    "tools": tools,
    "tool_choice": "auto",
    "temperature": temperature,
}

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}
```

建议默认连接超时 10 秒、读取超时 90 秒；只对 408、429、500、502、503、504 重试最多 2 次，退避 0.5 秒和 1 秒。400、401、403、422 不重试。

响应解析集中在 `_parse_response(payload)`：

```python
choice = payload.get("choices", [{}])[0]
raw_message = choice.get("message") or {}
raw_calls = raw_message.get("tool_calls") or []

calls = []
for raw_call in raw_calls:
    function = raw_call.get("function") or {}
    arguments = function.get("arguments") or "{}"
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict):
        raise LLMClientError("tool call arguments 必须是 JSON object")
    calls.append(
        ToolCall(
            id=str(raw_call.get("id") or ""),
            name=str(function.get("name") or ""),
            arguments=arguments,
        )
    )
```

如果 provider 返回没有 `id` 的 tool call，client 可以生成请求内唯一的 `call_{index}`，并在日志中标记 `generated_id=true`，不能用参数 hash 冒充真实调用 id。

### 15.3 `src/core/tool_protocol.py`

```python
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal


ToolExecutorFn = Callable[..., Any] | Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolContext:
    session_id: str
    request_id: str
    storage: Any | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    execute: ToolExecutorFn
    side_effect: Literal["read", "write"] = "read"
    requires_context: bool = False

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
```

`parameters` 使用 JSON Schema 子集：`type`、`properties`、`required`、`enum`、`items`、`description`。不要把 Python 函数的所有默认参数自动暴露给模型；手写 schema 更容易控制金融写操作边界。

### 15.4 `src/core/tool_executor.py`

第一版全部串行执行，先保证行为和数据库一致性：

```python
class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(
        self,
        call: ToolCall,
        *,
        context: ToolContext,
        allowed_names: set[str],
    ) -> Message:
        spec = self.registry.get(call.name)
        if spec is None:
            return tool_message(
                tool_call_id=call.id,
                name=call.name,
                result={"status": "error", "error": "unknown_tool"},
            )

        if call.name not in allowed_names:
            return tool_message(
                tool_call_id=call.id,
                name=call.name,
                result={"status": "error", "error": "tool_not_allowed"},
            )

        try:
            arguments = validate_arguments(spec.parameters, call.arguments)
            result = await invoke_tool(spec, arguments, context)
            return tool_message(
                tool_call_id=call.id,
                name=call.name,
                result=normalize_tool_result(result),
            )
        except Exception as exc:
            logger.exception("tool execution failed name=%s", call.name)
            return tool_message(
                tool_call_id=call.id,
                name=call.name,
                result={
                    "status": "error",
                    "error": "tool_execution_failed",
                    "message": str(exc),
                },
            )
```

当前工具大多是同步函数，而 Agent Loop 是 async，实际执行应使用 `asyncio.to_thread`，不要阻塞 event loop：

```python
async def invoke_tool(spec, arguments, context):
    result = await asyncio.to_thread(
        spec.execute,
        **arguments,
        context=context,
    )
    if inspect.isawaitable(result):
        return await result
    return result
```

后续加入只读并行时，只允许将 `side_effect="read"` 的调用提交到 `asyncio.gather`；同一批中存在写工具时全部降级为串行。

### 15.5 registry 的具体形态

`src/tools/registry.py` 不再导入 `BaseTool`，改为：

```python
class ToolRegistry:
    def __init__(self, specs: list[ToolSpec]) -> None:
        self._by_name = {spec.name: spec for spec in specs}

    def get(self, name: str) -> ToolSpec | None:
        return self._by_name.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._by_name.values())

    def schemas(self, allowed_names: set[str] | None = None) -> list[dict[str, Any]]:
        specs = self.all()
        if allowed_names is not None:
            specs = [spec for spec in specs if spec.name in allowed_names]
        return [spec.openai_schema() for spec in specs]
```

保留迁移期兼容函数：

```python
def get_tool_registry() -> ToolRegistry: ...

def get_all_tools() -> list[ToolSpec]:
    return get_tool_registry().all()
```

最终业务代码应改用 `get_tool_registry()`；`get_all_tools()` 只能作为短期兼容名称，不能继续带有“供 LangGraph 使用”的注释。

## 16. 工具逐项迁移表

迁移时先保留业务函数内部实现，再通过 wrapper 统一注入上下文。模型传入的参数和内部参数必须分离。

| 工具 | 模型可见参数 | 注入上下文 | side effect | 执行包装 |
| --- | --- | --- | --- | --- |
| `analyze_market` | `symbol`、`interval`、`force_refresh`、`requests` | `session_id`、`request_id` | write（写分析快照） | 原函数接收 context 注入的 session/request |
| `get_previous_analysis_snapshot` | `symbol`、`interval`、`exclude_request_id`、`limit` | `session_id`、当前 `request_id` | read | `exclude_request_id or context.request_id` |
| `get_last_snapshot` | `symbol`、`interval` | `session_id` | read | 从 context 传 session |
| `get_recent_tool_observations` | `limit`、`max_chars` | `session_id`、`request_id` | read | 当前请求排除逻辑由 wrapper 处理 |
| `search_conversation_summaries` | `query`、`limit`、`max_chars` | `session_id` | read | 从 context 传 session |
| `simulate_open_position` | symbol、direction、entry、SL、TP、仓位等业务字段 | `session_id`、`request_id` | write | 禁止采用模型提供的 session/request |
| `cancel_paper_order` | `order_id`、`reason` | `session_id`、`request_id` | write | 精确 order_id，领域服务二次校验 |
| `reconcile_paper_orders` | `symbol`、`interval` | `session_id` | write | 订单状态同步必须串行 |
| `get_journal_status` | `symbol`、`interval` | `session_id` | read | 从 context 传 session |
| `prepare_simulated_order` | asset、方向、价格、仓位等业务字段 | `session_id`、`request_id` | read | 不写库，只返回确认状态 |
| `get_user_profile` | `storage_key` | 无 | read | storage_key 仍是业务标识 |
| `update_user_profile` | `storage_key`、`updates`、`reason`、`confidence` | 无 | write | 保留现有 MemoryAPI 写入逻辑 |
| `fetch_market_data` | symbol、interval、limit 等 | 无 | read | 直接包装原函数 |
| `search_research_reports` | keyword、top_k | 无 | read | 直接包装原函数 |
| `get_response_guidance` | guidance_type | 无 | read | 直接包装原函数 |
| `get_key_levels` | symbol、interval 等 | 视当前签名 | read | schema 仅暴露实际业务参数 |
| `evaluate_structure` | symbol、interval 等 | 视当前签名 | read | schema 仅暴露实际业务参数 |
| `analyze_fibonacci` | symbol、interval 等 | 视当前签名 | read | schema 仅暴露实际业务参数 |

### 16.1 订单工具 wrapper 示例

不要让模型看见或覆盖 `session_id`、`request_id`：

```python
def _execute_cancel_paper_order(
    *,
    order_id: str,
    reason: str = "",
    context: ToolContext,
) -> dict[str, Any]:
    return cancel_paper_order(
        order_id=order_id,
        session_id=context.session_id,
        reason=reason,
        request_id=context.request_id,
    )
```

schema 只写：

```python
ToolSpec(
    name="cancel_paper_order",
    description="取消当前会话中指定的 pending_trigger 模拟订单；必须提供精确 order_id。",
    parameters={
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "minLength": 1},
            "reason": {"type": "string"},
        },
        "required": ["order_id"],
        "additionalProperties": False,
    },
    execute=_execute_cancel_paper_order,
    side_effect="write",
    requires_context=True,
)
```

迁移期可以让原函数暂时保留 `session_id` 和 `request_id` 参数，以减少业务改动；但 registry wrapper 必须强制用 context 覆盖它们，不能把模型参数直接展开到原函数。

### 16.2 分析工具的 InjectedState 替换

当前 `analyze_market` 的 `session_id`、`request_id` 来自 `InjectedState`。迁移后改为内部 context：

```python
def _execute_analyze_market(
    *,
    symbol: str | None = None,
    interval: str = "1d",
    force_refresh: bool = False,
    requests: list[dict[str, Any]] | None = None,
    context: ToolContext,
) -> dict[str, Any]:
    return analyze_market(
        symbol=symbol,
        interval=interval,
        force_refresh=force_refresh,
        requests=requests,
        session_id=context.session_id,
        request_id=context.request_id,
    )
```

这样可以继续保持分析快照的 `source_request_id` 和 `session_id`，同时让模型 schema 中不存在运行时状态字段。

## 17. `MarketReActAgent` 和状态迁移

### 17.1 `src/core/state.py`

删除：

```python
from typing import Annotated
from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage
```

改为：

```python
from typing import TypedDict
from .message_protocol import Message


class AgentState(TypedDict):
    messages: list[Message]
    session_id: str
    request_id: str
    current_symbol: str | None
    current_interval: str | None
    last_snapshot: AnalysisSnapshot | None
    analysis_result: dict | None
    risk_assessment: dict | None
    recommendation: dict | None
    intent: str | None
    metadata: dict | None
    error: str | None
    allowed_tools: list[str] | None
```

`next` 是图节点控制字段，Native Loop 不需要；如果外部代码仍读取它，最终状态可以保留 `next="end"` 作为兼容字段，但不再参与流程判断。

### 17.2 `src/core/prompt.py`

保留现有 `SYSTEM_PROMPT` 原文，只删除：

```python
from langchain_core.prompts import ChatPromptTemplate
```

并将 prompt 工厂改为：

```python
def get_system_prompt() -> str:
    return SYSTEM_PROMPT.strip()
```

初始化消息：

```python
messages = [Message(role="system", content=get_system_prompt())]
messages.extend(history_to_messages(history or []))
messages.append(Message(role="user", content=user_input))
```

历史输入仍兼容当前格式 `[{"role": "user", "text": "..."}]`；如果 role 不是 `user`，迁移期按 assistant 处理，避免破坏已有会话文件。

### 17.3 `src/core/agent.py`

`MarketReActAgent.__init__` 建议改为：

```python
class MarketReActAgent:
    def __init__(
        self,
        llm: OpenAICompatibleLLMClient | None = None,
        *,
        max_steps: int = 8,
    ) -> None:
        self.llm = llm or create_llm_client_from_config()
        self.registry = get_tool_registry()
        self.executor = ToolExecutor(self.registry)
        self.loop = NativeAgentLoop(
            llm=self.llm,
            registry=self.registry,
            executor=self.executor,
            max_steps=max_steps,
        )
```

迁移期如果外部仍传 `checkpointer` 或 `store`：

```python
if checkpointer is not None or store is not None:
    raise TypeError(
        "checkpointer/store 已随 LangGraph 移除，请改用 MemoryAPI 或 session manager"
    )
```

`runtime/app/factory.py` 中的：

```python
MarketReActAgent(checkpointer=None, store=None)
```

改为：

```python
MarketReActAgent()
```

### 17.4 `invoke` 初始状态

```python
async def invoke(
    self,
    user_input: str,
    session_id: str = "default",
    request_id: str = "",
    history: list[dict[str, str]] | None = None,
    allowed_tools: list[str] | None = None,
) -> dict[str, Any]:
    state: AgentState = {
        "messages": build_messages(
            system_prompt=get_system_prompt(),
            history=history or [],
            user_input=user_input,
        ),
        "session_id": str(session_id or "default"),
        "request_id": str(request_id or "").strip(),
        "current_symbol": None,
        "current_interval": None,
        "last_snapshot": None,
        "analysis_result": None,
        "risk_assessment": None,
        "recommendation": None,
        "intent": None,
        "metadata": {},
        "error": None,
        "allowed_tools": allowed_tools,
    }
    return await self.loop.run(state)
```

这里不再使用 `allowed_tools or []`，而是由一个集中函数决定兼容行为：

```python
def select_tool_names(
    requested: list[str] | None,
    all_names: set[str],
) -> set[str]:
    # 第一阶段兼容现有 ConversationService：None 和 [] 都表示全量。
    if not requested:
        return all_names
    return {name for name in requested if name in all_names}
```

## 18. Native Agent Loop 的逐步算法

### 18.1 第一版必须采用的顺序

第一版不要直接实现复杂并行。每一轮按以下顺序执行：

1. 组装 system、历史、当前用户消息；
2. 计算 active tool names；
3. 请求 LLM；
4. 记录 usage 和 `reason_start`；
5. 将 assistant 消息追加到 state；
6. 过滤未知/不允许工具；
7. 记录 tool call；
8. 逐个执行工具；
9. 将 tool messages 按调用顺序追加；
10. 返回第 3 步继续，直到没有可执行 tool call；
11. finalize recommendation；
12. 超过 `max_steps` 返回结构化错误。

### 18.2 参考实现骨架

```python
class NativeAgentLoop:
    def __init__(self, *, llm, registry, executor, max_steps=8):
        self.llm = llm
        self.registry = registry
        self.executor = executor
        self.max_steps = max(1, int(max_steps))

    async def run(self, state: AgentState) -> dict[str, Any]:
        all_names = {spec.name for spec in self.registry.all()}
        allowed_names = select_tool_names(
            state.get("allowed_tools"),
            all_names,
        )

        for step in range(self.max_steps):
            response = await self.llm.complete(
                messages=state["messages"],
                tools=self.registry.schemas(allowed_names),
            )
            record_usage(state, response.usage)
            state["messages"].append(response.message)

            calls = [
                call for call in response.message.tool_calls
                if call.name in allowed_names
            ]
            log_reason_result(state, step=step, response=response, calls=calls)

            if not calls:
                return finalize_state(state)

            context = ToolContext(
                session_id=state["session_id"],
                request_id=state["request_id"],
            )
            for call in calls:
                result_message = await self.executor.execute(
                    call,
                    context=context,
                    allowed_names=allowed_names,
                )
                state["messages"].append(result_message)
                log_tool_result(state, result_message)

        return loop_limit_state(state, self.max_steps)
```

### 18.3 重复工具调用策略

现有实现对重复调用只记录 warning，不阻止执行。第一版必须保持该行为：

```python
signature = f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"
```

使用 `set[str]` 统计本轮和历史重复，复杂度为 `O(k)`，其中 `k` 是本轮 tool call 数量。不要每次从头扫描全部消息并重新执行 JSON 序列化。

第二阶段才考虑对连续重复的同一写工具熔断，例如连续 3 次相同取消请求返回 `duplicate_tool_call`；这属于行为变更，需要单独开关和测试。

### 18.4 未知工具和不允许工具

第一版安全策略：

- 未注册工具不执行；
- 不在 `allowed_names` 的工具不执行；
- 记录 `tool_call_rejected` 事件；
- 给模型追加结构化 tool result，让模型知道工具没有执行；
- 如果同一 assistant 消息全部是非法工具调用，继续一次 LLM 轮次；
- 如果连续两轮全部非法，返回清晰错误，不执行任何副作用。

这比旧 Graph 直接丢弃非法 call 更容易观测，也避免模型以为订单已经取消。迁移测试必须明确断言数据库没有写入。

## 19. 最小测试实施代码

### 19.1 Fake LLM

新测试不要再创建 `AIMessage` 或模拟 `bind_tools`：

```python
class FakeLLM:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def complete(self, *, messages, tools):
        self.requests.append({"messages": messages, "tools": tools})
        return self.responses.pop(0)
```

构造工具调用：

```python
LLMResponse(
    message=Message(
        role="assistant",
        tool_calls=(
            ToolCall(
                id="call_status_1",
                name="get_journal_status",
                arguments={"symbol": "ETHUSDT"},
            ),
        ),
    ),
)
```

### 19.2 Agent Loop 必测断言

每个测试至少断言：

- fake LLM 调用次数；
- 每一轮发送的 tool schema；
- assistant/tool 消息顺序；
- tool call id 是否一致；
- 工具是否实际执行；
- 最终 `recommendation.text`；
- session/request 是否从 context 注入。

### 19.3 ToolExecutor 安全测试

以取消订单为例，测试不能只断言函数返回值，还要检查：

```python
assert "session_id" not in cancel_spec.parameters["properties"]
assert "request_id" not in cancel_spec.parameters["properties"]

result = await executor.execute(
    ToolCall(
        id="call_cancel_1",
        name="cancel_paper_order",
        arguments={"order_id": order_id},
    ),
    context=ToolContext(
        session_id="owner_session",
        request_id="req_cancel_1",
    ),
    allowed_names={"cancel_paper_order"},
)
```

然后检查数据库状态、事件数量和 `source_request_id`。测试模型传入伪造 `session_id` 时，schema 校验应拒绝额外字段，或者 wrapper 明确忽略而使用 context；不能使用模型提供的 session。

### 19.4 `ToolNode` 测试替换映射

| 旧测试写法 | 新测试写法 |
| --- | --- |
| `AIMessage(...)` | `Message(role="assistant", tool_calls=(ToolCall(...),))` |
| `ToolNode([tool])._func(state, ...)` | `ToolExecutor.execute(call, context=..., allowed_names=...)` |
| `tool.invoke({...})` | 直接业务函数，或通过 `ToolExecutor` 验证 schema/注入 |
| `response.content` | `response.message.content` |
| `response.tool_calls` | `response.message.tool_calls` |
| `configurable.thread_id` | `ToolContext.session_id` |

## 20. 配置、日志和错误处理

### 20.1 配置兼容

不新增第二套环境变量。继续使用 `runtime/config/runtime_config.py` 的：

- `get_llm_runtime_settings()`；
- `require_llm_model()`；
- `resolve_llm_temperature()`。

只在必要时增加以下配置，并同步 `runtime/config/analysis_defaults.example.yaml`：

```yaml
llm:
  request_timeout_seconds: 90
  connect_timeout_seconds: 10
agent:
  max_steps: 8
```

如果暂时不修改 runtime_config，则先使用代码默认值，避免把配置迁移和框架迁移混在一起。

### 20.2 日志事件

沿用现有 `MARKETASSAGENT_DEBUG_AGENT_LOOP` 和 `MARKETASSAGENT_DEBUG_TOKEN_USAGE`，将事件名称统一为：

```text
reason_start
llm_response
tool_call
tool_call_rejected
tool_result
final_answer_ready
loop_limit
llm_error
```

每条事件至少带：

- `session_id`；
- `request_id`（如果有）；
- `step`；
- `tool_name`（适用时）；
- 脱敏后的参数预览；
- `tool_call_id`；
- 结果预览；
- 不记录 API key；
- 默认不记录完整用户上下文。

### 20.3 错误分层

| 错误 | 对模型/用户的处理 | 是否重试 |
| --- | --- | --- |
| LLM timeout | 返回“模型服务超时，请稍后重试” | HTTP client 最多 2 次 |
| LLM 401/403 | 配置错误 | 不重试，日志告警 |
| LLM 429/5xx | provider 暂时不可用 | 最多 2 次退避 |
| tool 参数 JSON 错误 | tool result 返回结构化错误，让模型纠正 | 不重试工具 |
| tool 未授权 | tool result 返回拒绝原因 | 不执行 |
| 订单 service 异常 | tool result 保留业务错误 | 不自动重复写操作 |
| 超过最大步数 | finalize 为明确失败状态 | 不继续循环 |

特别注意：LLM HTTP 重试只重复“读模型”，不能重复工具执行。工具执行发生在 LLM 响应成功之后，不能因为下一次 LLM 请求失败而重新执行上一轮工具。

## 21. 具体命令清单

### 21.1 协议阶段

```bash
python3 -m pytest -q tests/test_native_protocol.py
```

源码文件使用补丁提交，不使用 shell 重定向直接写入。

### 21.2 依赖扫描

每个阶段都执行：

```bash
rg -n '^from langchain|^import langchain|^from langgraph|^import langgraph' \
  src runtime tests scripts
```

在删除依赖前，允许旧 import 只存在于尚未迁移的文件；删除依赖提交前结果必须为空。

### 21.3 单元测试和编译

```bash
python3 -m compileall src runtime
python3 -m pytest -q tests/test_native_agent_loop.py
python3 -m pytest -q tests/test_tool_executor.py
python3 -m pytest -q tests/test_llm_client.py
```

### 21.4 真实 provider smoke test

使用 `scripts/real_tool_calling_check.py`，要求：

1. 从 runtime config 读取 provider；
2. 只发一个无副作用工具，例如 `get_response_guidance`；
3. 打印 tool name、参数和解析结果；
4. 不创建订单、不修改画像、不写 PostgreSQL；
5. API key 只从配置读取，不在日志输出。

命令：

```bash
python3 scripts/real_tool_calling_check.py
```

DeepSeek、OpenAI-compatible provider 和 HCT 类 provider 至少各验证一次；provider 不可用时只记录验证缺口，不能用生产订单工具代替 smoke test。

## 22. 性能和并发实施边界

### 22.1 第一版复杂度

假设一次请求最多 `S` 个 LLM step，每轮最多 `K` 个 tool call：

- LLM 请求次数：`O(S)`；
- 工具执行次数：`O(S*K)`；
- 重复签名检查：每轮 `O(K)`，使用 set；
- tool schema 生成：每轮 `O(T)`，`T` 为允许工具数。

目前瓶颈是 LLM 网络 I/O、行情数据网络 I/O 和数据库 I/O，不是 Python 循环。第一版工具串行执行的优先级是状态一致性和可观测性。

### 22.2 第二版只读并行

满足以下条件后才开启：

- `ToolExecutor` 测试已完整；
- 所有工具正确标注 `side_effect`；
- 写工具的数据库事务测试通过；
- 同一 session 的 tool observation 顺序有明确设计。

并行策略：

```text
全是 read  -> asyncio.gather
包含 write -> 全部串行
```

同一个请求中即使只读工具并行，写入观察日志也必须按原始 tool call 顺序落库，避免后续记忆工具读到不稳定顺序。

## 23. 最终文件状态

迁移完成后，下面这些文件应满足要求：

```text
src/core/agent.py                 无 LangChain/LangGraph import
src/core/state.py                 只使用项目自有 Message/TypedDict
src/core/prompt.py                只保留纯文本 prompt
src/core/graph.py                 删除，或不再包含旧框架实现
src/core/llm_client.py            唯一模型 HTTP 入口
src/core/agent_loop.py            唯一 Agent 编排入口
src/core/tool_executor.py         唯一工具执行入口
src/tools/registry.py             唯一工具 schema/name 注册入口
runtime/app/factory.py            不传 checkpointer/store
requirements.txt                  不包含三个旧依赖
```

最终静态检查：

```bash
rg -n 'langgraph|langchain' \
  src runtime tests scripts requirements.txt pyproject.toml
```

结果必须为空。本文档和其他迁移历史文档可以保留这些名称，但生产代码、测试、脚本和依赖清单不能残留旧框架引用。

## 24. 实施完成判定

只有同时满足以下条件，才可以把状态从“方案设计”改为“已完成”：

1. `MarketReActAgent.invoke(...)` 外部接口保持兼容；
2. DeepSeek 和至少一个 OpenAI-compatible provider 能完成真实 tool call；
3. 所有工具均通过 `ToolSpec` 注册；
4. session/request 不再从模型参数读取；
5. 取消订单的 ownership、状态、幂等和事件测试通过；
6. Agent Loop 的无工具、单工具、多轮、非法工具、工具异常、最大步数测试通过；
7. 原有记忆、分析、模拟交易和 transport 测试通过；
8. 新环境不安装 LangGraph/LangChain 仍能 import、启动和运行测试；
9. 静态扫描没有旧框架 import；
10. 没有把旧框架类型换成同名 shim 后继续隐藏依赖。
