# 文档索引

| 文档 | 何时阅读 |
| --- | --- |
| [**00_PROJECT_ARCHITECTURE.md**](00_PROJECT_ARCHITECTURE.md) | **首选** — 总架构、流程图、核心/辅助/测试/脚本分层、有效配置清单、PR 自检 |
| [03_ARCH_REFACTOR_TODO.md](03_ARCH_REFACTOR_TODO.md) | 演进待办、已完成项、CI 防回流约束 |
| [06_AGENT_MEMORY_ARCHITECTURE.md](06_AGENT_MEMORY_ARCHITECTURE.md) | MemoryAPI、JSON session 双轨、用户画像 |
| [04_LLM_TOOL_AUTONOMY_PLAN.md](04_LLM_TOOL_AUTONOMY_PLAN.md) | LLM 工具调用策略演进 |
| [02_FRONTEND_TRANSPORT_PLAN.md](02_FRONTEND_TRANSPORT_PLAN.md) | Web 作为 transport 的约定 |
| [07_DATABASE_UNIFICATION_PLAN.md](07_DATABASE_UNIFICATION_PLAN.md) | PG journal/account 治理 |
| [05_DIRECTORY_MIGRATION_PLAYBOOK.md](05_DIRECTORY_MIGRATION_PLAYBOOK.md) | 历史目录迁移记录（只读参考） |
| [01_AGENT_ARCH_UPDATE_LOG.md](01_AGENT_ARCH_UPDATE_LOG.md) | 变更日志（只增不改旧条目） |

**改代码前**：先确认改动属于哪一层（见 00 文档 §3），避免在传输层或废弃路径加业务逻辑。
