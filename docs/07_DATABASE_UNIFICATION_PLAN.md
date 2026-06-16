# 数据库现状与统一治理计划

**更新时间**: 2026-06-16  
**适用分支**: `main`

---

## 1. 当前数据库使用现状

### 1.1 PostgreSQL（业务台账主库）

- 主要承载：
  - 交易台账（`persistence/models.py`）
  - 纸交易/状态查询（`tools/sim_account.py` 依赖仓储层）
- 配置来源：
  - `config/analysis_defaults.yaml` 的 `database.postgres.dsn`
  - 运行时读取：`config/runtime_config.py:get_postgres_dsn()`
- 启动行为：
  - `app/factory.py` 中调用 `init_db()` 尝试初始化

### 1.2 SQLite（记忆层默认持久化）

- 主要承载：
  - `Fact`（`recent_message`, `tool_observation`, `user_profile`）
  - `Checkpoint`（`last_snapshot`）
- 入口：
  - `core/memory_api.py:create_default_memory_api()`
  - 默认文件：`memory_facts.jsonl` / `memory_checkpoints.json`（遗留 `memory_store.sqlite3` 可删除）
- 设计动机：
  - Phase A/B 阶段快速落地、低耦合灰度

### 1.3 InMemory（LangGraph 线程态）

- 当前 `checkpointer/store` 默认是 `MemorySaver + InMemoryStore`
- 作用域：进程内短期状态（重启会丢失）
- 开关：`feature_flags.memory_api_only_mode`（MemoryAPI 默认启用，无需 memory_new_api）

---

## 2. 当前问题（为什么需要统一）

1. 存储分散：PostgreSQL + SQLite + InMemory 并存，运维和排障成本高。
2. 观测分裂：一条请求的数据散落在多个后端，链路追踪不完整。
3. 一致性弱：跨模块无法天然做事务边界与统一备份策略。
4. 扩展受限：多实例部署时，SQLite 和 InMemory 都不适合作为长期共享状态。

---

## 3. 目标架构（统一管理）

目标：将“业务数据 + 记忆数据 + Graph 持久化”统一到 PostgreSQL，保留接口不变。

### 3.1 目标原则

1. API 不变：上层继续使用 `MemoryAPI`，仅替换底层 store。
2. 配置单一：统一走 `database.postgres.dsn`，移除并行 DB 入口。
3. 可灰度可回滚：通过 feature flag 做双写/读切换，避免一次性切换风险。
4. 可审计：用户画像审计、tool provenance、checkpoint 均可持久追溯。

### 3.2 目标落点

- `persistence` 继续承载业务台账
- 新增 `PostgresFactStore` 承载 facts/checkpoints/profile
- LangGraph `checkpointer/store` 切到可持久后端（PostgreSQL 或兼容实现）

---

## 4. 分阶段实施计划与待办

## Phase 0（当前状态固化，1 天）

- [ ] 文档固化“多存储并存”现状（本文件）
- [ ] 增加运行时启动日志：打印 memory backend、graph backend
- [ ] 增加健康检查输出：当前 memory backend 类型

**验收标准**
- 启动日志可明确看到 `memory_backend` 与 `graph_state_backend`

## Phase 1（MemoryAPI 统一到 PostgreSQL，2~3 天）

- [ ] 新建 `core/fact_store_pg.py`（`PostgresFactStore`）
- [ ] 复用 `FactStore` 接口：`write_fact/recall/get_latest_fact/set_checkpoint/get_checkpoint`
- [ ] 增加配置项：`memory.backend = sqlite|postgres`
- [ ] 保留 SQLite 作为回滚路径
- [ ] 增加迁移脚本：SQLite facts/checkpoints -> PostgreSQL

**验收标准**
- `memory.backend=postgres` 时，`tests/test_memory_api.py` 全通过
- `ConversationService` 行为与 SQLite 模式一致（回归测试通过）

## Phase 2（Graph 持久化统一，2~4 天）

- [ ] 将 `MemorySaver/InMemoryStore` 切为持久化后端
- [ ] `thread_id=session_id` 下，跨进程重启可恢复图状态
- [ ] 增加 thread 级状态 TTL/清理策略

**验收标准**
- 重启服务后，多轮对话 thread 状态可恢复

## Phase 3（收口与治理，1~2 天）

- [ ] 下线 SQLite 默认路径（仅保留开发应急）
- [ ] 合并重复配置（`config/settings.py` 的 `DATABASE_URL` 与 runtime config 收敛）
- [ ] 增加统一备份/恢复脚本
- [ ] 增加监控：recall 延迟、写入失败率、profile 更新审计量

**验收标准**
- 生产默认仅使用 PostgreSQL
- CI 增加守卫，禁止新引入未受控本地 SQLite 持久化

---

## 5. 风险与回滚策略

1. 风险：PostgreSQL 压力增加，记忆写入放大。
   - 对策：批量写、索引优化、限流与采样。
2. 风险：迁移期事实丢失或重复。
   - 对策：双写窗口 + 对账脚本 + 幂等键（`id`/`thread_id+timestamp`）。
3. 风险：统一后故障面扩大。
   - 对策：读写超时、降级开关、SQLite 紧急只读兜底（短期保留）。

---

## 6. 本周优先级建议

1. 先完成 `PostgresFactStore` 与后端切换开关（Phase 1）。
2. 再做 Graph 持久化替换（Phase 2）。
3. 最后做配置与治理收口（Phase 3）。
