# 数据库现状与统一治理计划

**更新时间**: 2026-07-10
**适用分支**: `main`

> **当前结论**：数据库尚未作为项目主运行链路的必需组件。会话与默认 MemoryAPI 使用本地 JSON/JSONL；PostgreSQL 相关的 journal、account 和可选 FactStore 代码暂按实验性能力保留，后续统一设计完成前不作为部署验收项。

### 已发现但暂缓处理的迁移问题

- 仓库目前只有 `ops/alembic/versions/journal_001_create_journals.py`。
- 2026-07-10 检查本机 PostgreSQL 时，`alembic_version` 指向 `journal_005`，但仓库中没有 `journal_002`～`journal_005`。
- 该状态可能来自历史项目、未合入迁移或本地实验库，目前无法仅凭现有代码判断正确迁移链。
- 在迁移来源核清前，不执行 `upgrade`、`downgrade` 或 `stamp`，也不补造空 revision；本问题不阻塞默认 JSON/JSONL 主链路和本次目录重构。

---

## 1. 当前数据库使用现状

### 1.1 PostgreSQL（可选、尚未完成接入验收）

- 现有代码意图承载：
  - 交易台账（`src/infrastructure/persistence/models.py`）
  - 纸交易/状态查询（`src/tools/sim_account.py` 依赖仓储层）
- 配置来源：
  - `runtime/config/analysis_defaults.yaml` 的 `database.postgres.dsn`
  - 运行时读取：`runtime/config/runtime_config.py:get_postgres_dsn()`
- 启动行为：
  - `runtime/app/factory.py` 中以 best-effort 方式调用 `init_db()`；失败只记录日志，不阻塞 Agent 启动
- 当前限制：
  - 尚未完成迁移链、部署流程和真实数据库集成验收
  - CI 默认排除 `postgres` marker，不代表 PostgreSQL 链路已经可投入使用

### 1.2 JSON/JSONL（当前默认持久化）

- 主要承载：
  - 会话历史与状态
  - `Fact`（`recent_message`, `tool_observation`, `user_profile`）
  - `Checkpoint`（`last_snapshot`）
- 入口：
  - `src/core/memory_api.py:create_default_memory_api()`
  - `src/infrastructure/memory/*`
- 默认位置：`~/.marketassagent/` 下的 sessions、facts 和 checkpoints 文件
- SQLite memory backend 已移除；遗留 `memory_store.sqlite3` 不属于当前运行路径

### 1.3 InMemory（LangGraph 线程态）

- 当前 `checkpointer/store` 默认是 `MemorySaver + InMemoryStore`
- 作用域：进程内短期状态（重启会丢失）
- 开关：`feature_flags.memory_api_only_mode`（MemoryAPI 默认启用，无需 memory_new_api）

---

## 2. 当前问题（为什么需要统一）

1. 存储分散：JSON/JSONL + 可选 PostgreSQL + InMemory 并存，运维和排障成本高。
2. 观测分裂：一条请求的数据散落在多个后端，链路追踪不完整。
3. 一致性弱：跨模块无法天然做事务边界与统一备份策略。
4. 扩展受限：多实例部署时，本地 JSON/JSONL 和 InMemory 都不适合作为长期共享状态。

---

## 3. 目标架构（统一管理）

目标：将“业务数据 + 记忆数据 + Graph 持久化”统一到 PostgreSQL，保留接口不变。

### 3.1 目标原则

1. API 不变：上层继续使用 `MemoryAPI`，仅替换底层 store。
2. 配置单一：统一走 `database.postgres.dsn`，移除并行 DB 入口。
3. 可灰度可回滚：先补齐迁移链与集成测试，再设计双写/读切换，避免一次性切换风险。
4. 可审计：用户画像审计、tool provenance、checkpoint 均可持久追溯。

### 3.2 目标落点

- `src/infrastructure/persistence` 继续承载业务台账
- 复核并完善现有 `PostgresFactStore`，用于承载 facts/checkpoints/profile
- LangGraph `checkpointer/store` 切到可持久后端（PostgreSQL 或兼容实现）

---

## 4. 分阶段实施计划与待办

## Phase 0（当前状态固化）

- [x] 文档固化“默认 JSON/JSONL、PostgreSQL 暂未验收”的现状（本文件）
- [ ] 核清 `journal_002`～`journal_005` 的来源与完整迁移历史
- [ ] 决定保留现有本地库、重建开发库或导入历史迁移，形成书面决策
- [ ] 增加运行时启动日志：打印 memory backend、graph backend
- [ ] 增加健康检查输出：当前 memory backend 类型

**验收标准**
- 启动日志可明确看到 `memory_backend` 与 `graph_state_backend`

## Phase 1（迁移基线恢复后再启动）

- [ ] 先完成 Alembic 迁移链一致性验证和空库升级测试
- [ ] 验证现有 `src/core/postgres_fact_store.py` 是否满足 FactStore 契约
- [ ] 复用 `FactStore` 接口：`write_fact/recall/get_latest_fact/set_checkpoint/get_checkpoint`
- [x] 已有配置项：`memory.backend = json|postgres`
- [ ] 保留 JSON/JSONL 作为回滚路径
- [ ] 增加迁移脚本：JSON/JSONL facts/checkpoints -> PostgreSQL

**验收标准**
- `memory.backend=postgres` 时，`tests/test_memory_api.py` 全通过
- `ConversationService` 行为与 JSON 模式一致（回归测试通过）

## Phase 2（Graph 持久化统一，2~4 天）

- [ ] 将 `MemorySaver/InMemoryStore` 切为持久化后端
- [ ] `thread_id=session_id` 下，跨进程重启可恢复图状态
- [ ] 增加 thread 级状态 TTL/清理策略

**验收标准**
- 重启服务后，多轮对话 thread 状态可恢复

## Phase 3（收口与治理，1~2 天）

- [ ] 在 PostgreSQL 验收后决定是否下线 JSON 默认路径（保留明确回滚方案）
- [ ] 合并重复配置（`runtime/config/settings.py` 的 `DATABASE_URL` 与 runtime config 收敛）
- [ ] 增加统一备份/恢复脚本
- [ ] 增加监控：recall 延迟、写入失败率、profile 更新审计量

**验收标准**
- 生产默认仅使用 PostgreSQL
- CI 增加守卫，禁止新引入未受控的本地持久化后端

---

## 5. 风险与回滚策略

1. 风险：PostgreSQL 压力增加，记忆写入放大。
   - 对策：批量写、索引优化、限流与采样。
2. 风险：迁移期事实丢失或重复。
   - 对策：双写窗口 + 对账脚本 + 幂等键（`id`/`thread_id+timestamp`）。
3. 风险：统一后故障面扩大。
   - 对策：读写超时、降级开关、JSON/JSONL 紧急只读兜底（短期保留）。

---

## 6. 本周优先级建议

1. 先核清 `journal_005` 来源并恢复完整迁移基线，不对现有数据库做写操作。
2. 再验证 PostgreSQL journal/account 与 FactStore 的真实集成链路。
3. 最后决定是否推进 Graph 持久化与生产数据库统一。
