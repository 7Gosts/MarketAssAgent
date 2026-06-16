# 架构演进与防回流清单

**日期**: 2026-06-17  
**主文档**: [`00_PROJECT_ARCHITECTURE.md`](00_PROJECT_ARCHITECTURE.md)（目录分层、流程图、配置契约）

---

## 已完成

### 目录收敛（2026-06 前）

- Canonical 路径：`app/adapters/*`、`app/api/*`、`app/factory.py`、`interfaces/renderers/*`、`interfaces/presenters/web_presenter.py`
- 已删除顶层 shim：`adapters/`、`renderers/`、`presenters/`、`api/routes.py`（旧）、`app_factory.py`

### 记忆系统（Phase D/E）

- MemoryAPI + FactStore 默认启用（`app/factory.py`）
- `ConversationService` 双写 `recent_message` / `tool_observation`
- provenance 追问回复
- 详见 [`06_AGENT_MEMORY_ARCHITECTURE.md`](06_AGENT_MEMORY_ARCHITECTURE.md)

### 用户画像（2026-06-17）

- `profile_update` task_type + Orchestrator 路径 + Prompt storage_key 注入
- 测试：`test_prompts_storage_key.py`、`test_response_planner.py`、`test_orchestrator_tool_filter.py`

### 死代码清理（2026-06-17）

| 类别 | 已删除 |
| --- | --- |
| 无效配置 | YAML `agent.*`、`feishu.memory/llm_router/narrative`、`session.compact_*`、`market_config.json` |
| 废弃模块 | `FeishuPresenter`、`services/response_planner.py`（re-export）、`schemas/response_plan.py` |
| Session 路由层 | `pending_intent`、`update_from_route`、`resolve_followup`、compact 未接线 API |
| Orchestrator 冗余 | 未使用的 `envelope_builder`、不可达 `_handle_default` |
| Planner | `error_reason` 只写字段 |
| 其他 | `get_agent()`、`polished_text` 提取、顶层空 `adapters/` 目录 |

### LLM 工具自主性

- 弱化 `required_tools` 强制过滤；Prompt 层明确工具策略
- 详见 [`04_LLM_TOOL_AUTONOMY_PLAN.md`](04_LLM_TOOL_AUTONOMY_PLAN.md)

---

## 防新旧架构并存 — 硬性约束

### 1. 禁止复活的路径 / 模块

CI 脚本 `scripts/guard_no_legacy_memory_path.py` 会拦截：

| 禁止项 | 原因 |
| --- | --- |
| 顶层 `adapters/`、`renderers/`、`presenters/`、`formatters/` | 已迁至 `app/`、`interfaces/` |
| `app_factory.py`、`api/routes.py`（根目录） | 已迁至 `app/factory.py`、`app/api/routes.py` |
| `memory/feishu_memory.py` | 已统一为 MarketSessionManager + MemoryAPI |
| `core/router.py`、`core/writer.py` | 旧 intent 路由 / narrative writer |
| import `from adapters.` / `from presenters.` 等 | 旧包名 |

### 2. 禁止的模式（代码审查）

| 反模式 | 正确做法 |
| --- | --- |
| Adapter 内 `agent.invoke()` + 自管历史 | 只调 `ConversationService.run()` |
| YAML 加配置但不写 `runtime_config` 读取 | 配置与读取同 PR 合入 |
| Planner 增加 `*_hint` 字段但不注入 Prompt | 规则写进 `prompt.py` / `prompts.py` |
| 新建 `XxxPresenter` 做第二套输出 | 扩展 `envelope_builder` 或 Renderer |
| `services/foo.py` 仅 `from core.foo import *` | 直接 import `core.foo` |
| 双 Prompt 源各写一套周期规则 | System + Human 两处同步维护 |

### 3. PR 合并门槛

```bash
python3 scripts/guard_no_legacy_memory_path.py
python3 -m pytest tests/ -q
```

---

## 待办（按优先级）

### P1 — 记忆单轨

- [ ] `memory_api_only_mode` 默认改 `true`，稳定后删除 legacy JSON session 双写
- [ ] `snapshot_manager`（进程内单例）收敛到 MemoryAPI checkpoint，去掉 `"default"` 硬编码 session

### P2 — 输出与调试

- [ ] 精简 `ConversationEnvelope`：评估是否移除恒空的 `blocks` / `DeliveryHint`（需 Web 客户端确认）
- [ ] 请求链路 ID + `debug/llm_raw_outputs.jsonl` 一键重放

### P3 — 运维

- [ ] `scripts/clean_runtime_data.sh` 清理 `~/.marketassagent/*`
- [ ] 评估 `core/` vs `services/` vs `memory/` 是否重命名为 `domain/infra`（低优先级）

---

## 记忆存储速查

| 数据 | 后端 | 位置 |
| --- | --- | --- |
| 短期对话 | JSON | `~/.marketassagent/sessions/` |
| facts / checkpoint / profile | JSON（默认） | `~/.marketassagent/output/` |
| journal / account | PostgreSQL | `database.postgres.dsn` |

`memory.backend: postgres` 为可选 FactStore；SQLite memory 已移除。
