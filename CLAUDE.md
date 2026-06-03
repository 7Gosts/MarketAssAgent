# MarketAssAgent

LangGraph ReAct 架构的多市场技术分析 Agent（加密货币、A股、美股、贵金属）。

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                    cli/ (入口层)                         │
│  agent_run.py ──→ app/agent_core (旧路径)               │
│  core_run.py  ──→ core/graph    (新路径, 并行测试中)     │
│  run.py       ──→ core/graph    (REPL 入口)             │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│                  core/ (LangGraph 主干)                   │
│                                                          │
│  graph.py:  状态机构建                                    │
│  nodes.py:  全部图节点                                    │
│  state.py:  MarketAgentState 定义                         │
│  prompt.py: SYSTEM_PROMPT + 上下文构建                    │
│  guardrails.py: FORBIDDEN_CLAIMS (纯数据)                │
│  supervisor.py: 输出守卫                                  │
│  agent.py:  re-export (向后兼容)                          │
└───────┬─────────────────────────┬───────────────────────┘
        │                         │
┌───────▼─────────┐   ┌──────────▼──────────┐
│   tools/         │   │   memory/            │
│   registry.py    │   │   snapshot.py        │
│   market_data.py │   │   session_manager.py │
│   sim_account.py │   │   __init__.py        │
│   research.py    │   └──────────┬───────────┘
│   legacy_bridge.py──┐           │
└───────┬────────────┘│  ┌───────▼───────────┐
        │             │  │ app/session_state  │
  ┌─────▼─────────────▼──▼──────────────────┐
  │           app/ (底层实现, 逐步迁移中)      │
  │  executors/, capabilities/, market_data/ │
  │  guardrails.py ← core/guardrails         │
  └─────────────────────────────────────────┘
```

## LangGraph 完整流程

```
START → restore_session → init_context → reason ─→ [tools → observe → reason]* ─→ supervisor → persist_snapshot → END
```

- **restore_session**: 从 session 恢复上一轮 last_snapshot / current_symbol 等
- **init_context**: 注入 SYSTEM_PROMPT + 上下文 + 强制注入上一轮 snapshot
- **reason**: LLM 推理，决定调工具还是直接回答
- **tools → observe**: 工具调用 → 从结果提取 snapshot
- **supervisor**: 输出守卫（禁止口径 + 条件语气 + 免责声明）
- **persist_snapshot**: 持久化 snapshot 和回复到 session

## 模块依赖规则

| 模块 | 依赖 app/? | 说明 |
|------|-----------|------|
| `core/` | ❌ 零依赖 | 独立的 LangGraph 状态机 |
| `tools/` | 仅通过 `legacy_bridge.py` | 过渡层，标注 `TODO(legacy)` |
| `memory/` | 仅通过 `session_manager.py` | 过渡层，标注 `TODO(legacy)` |

**原则**：`core/` 永不 import `app/`。`tools/` 和 `memory/` 只通过明确的过渡层桥接。

## Snapshot 机制

1. **保存**：`persist_snapshot_node` 在每轮分析后自动将 `last_snapshot` 持久化到 session
2. **恢复**：`restore_session_node` 在每轮开始时从 session 恢复上一轮 snapshot
3. **注入**：`init_context_node` 强制注入 `[系统注入] 上一轮分析上下文` 确保追问时 LLM 能"看到"上一轮结果
4. **格式**：使用 `snapshot_to_context_str()` 生成可读文本 + 关键数值结构化补充

## 关键入口

| 入口 | 路径 | 说明 |
|------|------|------|
| CLI 对话 | `python cli/agent_run.py` | 旧路径 (app/agent_core) |
| Core 入口 | `python cli/core_run.py` | 新路径 (core/graph)，并行测试中 |
| REPL | `python cli/run.py` | core/graph 路径 |
| 飞书 Bot | `python cli/feishu_bot.py` | 飞书集成 |

## 配置

- `config/analysis_defaults.yaml` — LLM providers、MA 系统、RR 阈值、数据库 DSN
- `config/market_config.json` — 22 个资产定义（symbol, name, market, data_symbol, research_keyword）
- `.env` — 敏感配置（API keys），参考 `.env.example`

## 测试

```bash
pytest tests/ -v
```

覆盖：state 契约、snapshot 提取/可读化、guardrails 守卫、图节点函数、多轮对话。

## Legacy 标注

代码中的 `# TODO(legacy)` 标记表示过渡层调用，最终目标是将 `app/` 中的功能迁移到 `tools/` 或 `core/` 原生实现后删除这些调用。搜索 `TODO(legacy)` 可查看所有待迁移点。

## 输出守卫

`supervisor_node` (core/supervisor.py) 确保所有输出：
1. 不含禁止口径（`FORBIDDEN_CLAIMS`）→ 替换为 `[已移除不当表述:...]`
2. 不含绝对表述 → 替换为条件语气（如 "应该买入" → "可考虑逢低关注"）
3. 追加免责声明（"仅供技术分析与程序化演示，不构成投资建议。"）
