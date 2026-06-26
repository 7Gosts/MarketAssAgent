# 文档索引

这里是工程设计、架构演进、迁移记录的索引；项目安装、启动和使用入口见根目录 `README.md`。

## 当前有效文档（实施优先）

| 文档 | 何时阅读 |
| --- | --- |
| [**16_RESPONSE_CONTRACT_ARCHITECTURE_PLAN.md**](16_RESPONSE_CONTRACT_ARCHITECTURE_PLAN.md) | **首选** — 当前 light-only 主链路、工具按需补证、实施步骤与替换记录 |
| [03_ARCH_REFACTOR_TODO.md](03_ARCH_REFACTOR_TODO.md) | 演进待办、已完成项、CI 防回流约束 |
| [04_LLM_TOOL_AUTONOMY_PLAN.md](04_LLM_TOOL_AUTONOMY_PLAN.md) | LLM 工具调用策略演进 |
| [02_FRONTEND_TRANSPORT_PLAN.md](02_FRONTEND_TRANSPORT_PLAN.md) | Web 作为 transport 的约定 |
| [07_DATABASE_UNIFICATION_PLAN.md](07_DATABASE_UNIFICATION_PLAN.md) | PG journal/account 治理 |
| [01_AGENT_ARCH_UPDATE_LOG.md](01_AGENT_ARCH_UPDATE_LOG.md) | 变更日志（只增不改旧条目） |

## 历史归档（只读，不作为当前施工依据）

| 文档 | 说明 |
| --- | --- |
| [00_PROJECT_ARCHITECTURE.md](00_PROJECT_ARCHITECTURE.md) | 旧阶段总架构快照（含 Direct Context 描述） |
| [05_DIRECTORY_MIGRATION_PLAYBOOK.md](05_DIRECTORY_MIGRATION_PLAYBOOK.md) | 历史目录迁移记录 |
| [06_AGENT_MEMORY_ARCHITECTURE.md](06_AGENT_MEMORY_ARCHITECTURE.md) | 旧记忆架构说明（含 Direct Context 旧描述） |
| [11_DOMAIN_STRUCTURE_REFACTOR_REPORT_20260623.md](11_DOMAIN_STRUCTURE_REFACTOR_REPORT_20260623.md) | 目录分层重构执行报告 |
| [12_FINAL_CLEANUP_REPORT_20260623.md](12_FINAL_CLEANUP_REPORT_20260623.md) | Phase 14 清理报告 |
| [13_PHASE15_DEAD_CODE_CLEANUP.md](13_PHASE15_DEAD_CODE_CLEANUP.md) | Phase 15 清理报告 |
| [14_COMPAT_LAYER_REMOVAL_REPORT_20260623.md](14_COMPAT_LAYER_REMOVAL_REPORT_20260623.md) | Phase 16 清理报告 |
| [15_SLIM_SCHEMA_FEISHU_FIB_REPORT_20260623.md](15_SLIM_SCHEMA_FEISHU_FIB_REPORT_20260623.md) | Phase 17 清理报告 |

**改代码前**：先确认改动属于哪一层（以 16 文档和当前代码目录为准），避免在传输层或废弃路径加业务逻辑。
