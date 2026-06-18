# Agent 记忆架构设计（当前实现）

**更新时间**: 2026-06-17  
**适用版本**: 当前 `main` 分支

---

## 1. 设计目标

1. Web / Feishu / chat / analyze 共用同一套记忆编排（`ConversationService`）。
2. 短期会话历史本地 JSON 持久化，开箱即用，不依赖 PostgreSQL。
3. 长期记忆（facts、checkpoint、用户画像）通过统一 `MemoryAPI` 读写，默认本地 JSON 文件。
4. 用户画像由 LLM 工具链主动维护，避免规则层二次判读。
5. PostgreSQL 仅用于 journal/account 等原有 persistence，与 MemoryAPI 默认路径分离。

---

## 2. 存储设计总览

| 数据类型 | 默认后端 | 存储位置 | 说明 |
| --- | --- | --- | --- |
| 短期对话历史 | JSON/JSONL | `~/.marketassagent/sessions/{session_id}/_history.jsonl` | `MarketSessionManager`，始终可用 |
| Session 状态 | JSON | `~/.marketassagent/sessions/{session_id}/{session_id}.json` | 标的、周期、last_facts_bundle 等 |
| recent_message facts | JSON | `~/.marketassagent/output/memory_facts.jsonl` | MemoryAPI 双写（与 session 并行） |
| tool_observation facts | JSON | 同上 | 工具调用观测，用于 provenance |
| user_profile | JSON | 同上（`thread_id=user_profile_{storage_key}`） | LLM 工具 + 规则兜底 |
| last_snapshot checkpoint | JSON | `~/.marketassagent/output/memory_checkpoints.json` | 上一轮关键上下文 |
| journal / account | PostgreSQL | `database.postgres.dsn` | 原有功能，未改动 |

**默认配置**（`config/analysis_defaults.yaml`）：

```yaml
memory:
  backend: "json"   # json | postgres

feature_flags:
  memory_api_only_mode: false   # true 时停止写 legacy JSON session 历史
```

**运行产物根目录**：`~/.marketassagent/`（可用 `MARKETASSAGENT_DATA_DIR` 覆盖）

---

## 3. 架构分层

```text
入口 (Web / Feishu)
  └─ ConversationService          ← 统一编排入口
       ├─ MarketSessionManager    ← 短期 JSON session（legacy，仍保留）
       ├─ MemoryAPI               ← 长期记忆（默认启用）
       │    └─ JsonFactStore      ← 默认 backend
       └─ MarketReActAgent        ← LangGraph ReAct（Direct Context 主链路）
```

### 3.1 短期会话（JSON Session）

- 代码：`memory/session_manager.py`、`memory/json_persistence.py`
- 职责：保存 user/assistant 消息、SessionState 元数据
- 与 MemoryAPI **并行**：默认 `memory_api_only_mode=false` 时双写历史
- **已移除**：历史压缩（compact）、旧 intent 路由字段（`pending_intent` 等）

> 进程内 `memory/snapshot.py` 的 `snapshot_manager` 仅被 `get_key_levels` 工具读取，**不是**主记忆源；分析快照以 MemoryAPI `last_snapshot` checkpoint 为准。目录分层见 [`00_PROJECT_ARCHITECTURE.md`](00_PROJECT_ARCHITECTURE.md)。

### 3.2 长期记忆（MemoryAPI + FactStore）

- 接口：`core/memory_api.py`（`DefaultMemoryAPI`）
- Protocol：`core/fact_store.py`（`FactStore`）
- 默认实现：`core/json_fact_store.py`（`JsonFactStore`）
- 可选实现：`core/postgres_fact_store.py`（显式 `memory.backend: postgres`）

**装配**（`app/factory.py`）：

- 启动时**始终**创建 `memory_api = create_default_memory_api(repo_root=repo_root)`
- 注入 `ConversationService`、`tools/user_profile`
- 不再需要 `memory_new_api` 开关（已移除）

### 3.3 用户画像（UserProfile）

- 模型：`core/profile.py`
- 存储：Fact `type="user_profile"`，`thread_id=user_profile_{storage_key}`
- `storage_key`：优先 `user_id`，其次 `session_id`（feishu_* / web_* 通用）
- **主路径**：LLM 调用 `get_user_profile` / `update_user_profile`
- 工具**不**自己创建 store，只使用 factory 注入的 `memory_api`

### 3.4 LangGraph 执行记忆

- `MarketReActAgent` 默认 `checkpointer=None`、`store=None`
- 与长期 MemoryAPI **解耦**；进程内 graph 状态不持久化
- `thread_id` 仍通过 `config={"configurable": {"thread_id": session_id}}` 贯穿调用

### 3.5 PostgreSQL（独立用途）

- journal、account ledger、纸交易台账
- 配置：`database.postgres.dsn`
- **不参与** MemoryAPI 默认路径；可选作为 FactStore 后端

---

## 4. 数据模型

### 4.1 Fact

| 字段 | 说明 |
| --- | --- |
| `id` | UUID |
| `thread_id` | 会话/画像 key（如 `feishu_xxx` 或 `user_profile_feishu_xxx`） |
| `source` | 来源（`conversation_service` / `llm_inference` 等） |
| `timestamp` | ISO 时间戳 |
| `type` | `recent_message` / `tool_observation` / `user_profile` 等 |
| `payload` | 结构化内容 |
| `provenance` | 来源追踪（`request_id` / `tool_call_id`） |
| `tags` | 标签 |

JsonFactStore：`write_fact` append JSONL；`recall` 按 timestamp 新→旧；`get_latest_fact` 取指定 type 最新一条。

### 4.2 Checkpoint

- JsonFactStore：`memory_checkpoints.json`，key 为 `{thread_id}:{ck_key}`
- 当前主要用途：`last_snapshot`

### 4.3 UserProfile

核心字段见 `core/profile.py`：`preferred_style`、`risk_profile`、`market_bias`、`favorite_symbols`、`observations`、`style_history` 等。

---

## 5. 请求生命周期

1. `ConversationService.run()`
2. 写用户消息 → JSON session +（默认）MemoryAPI `recent_message` fact
3. 读历史 → `memory_api_only_mode` 决定只读 MemoryAPI 或与 JSON session 兼容
4. 构造 Direct Context（`storage_key`、`user_profile`、`last_snapshot`、`recent_sources`）
5. LLM ReAct 可调用画像/分析工具
6. 写 tool_observation facts、checkpoint、assistant 回复

---

## 6. 配置与开关

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `memory.backend` | `json` | `postgres` 为可选 FactStore |
| `feature_flags.memory_api_only_mode` | `false` | `true` 时历史只走 MemoryAPI，不写 JSON session |

环境变量：`MARKETASSAGENT_FEATURE_MEMORY_API_ONLY_MODE=true|false`

**已移除**：`memory_new_api`、`SQLiteFactStore`、`memory_store.sqlite3`

---

## 7. 相关测试

- `tests/test_memory_api.py` / `tests/test_json_fact_store.py`
- `tests/test_user_profile_memory.py` / `tests/test_user_profile_tools_injection.py`
- `tests/test_runtime_memory_api_default.py`
- `tests/test_phase_c_memory_flow.py` / `tests/test_session_json_persistence.py`

---

## 8. 关联文档

- **总架构与目录分层**：[`docs/00_PROJECT_ARCHITECTURE.md`](00_PROJECT_ARCHITECTURE.md)
- 架构待办与防回流：[`docs/03_ARCH_REFACTOR_TODO.md`](03_ARCH_REFACTOR_TODO.md)
- 数据库治理：[`docs/07_DATABASE_UNIFICATION_PLAN.md`](07_DATABASE_UNIFICATION_PLAN.md)
