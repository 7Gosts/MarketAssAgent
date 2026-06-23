# 文档索引

这里是工程设计、架构演进、迁移记录的索引；项目安装、启动和使用入口见根目录 `README.md`。

| 文档 | 何时阅读 |
| --- | --- |
| [**00_PROJECT_ARCHITECTURE.md**](00_PROJECT_ARCHITECTURE.md) | **首选** — 总架构、流程图、核心/辅助/测试/脚本分层、有效配置清单、PR 自检 |
| [03_ARCH_REFACTOR_TODO.md](03_ARCH_REFACTOR_TODO.md) | 演进待办、已完成项、CI 防回流约束 |
| [06_AGENT_MEMORY_ARCHITECTURE.md](06_AGENT_MEMORY_ARCHITECTURE.md) | MemoryAPI、JSON session 双轨、用户画像 |
| [04_LLM_TOOL_AUTONOMY_PLAN.md](04_LLM_TOOL_AUTONOMY_PLAN.md) | LLM 工具调用策略演进 |
| [02_FRONTEND_TRANSPORT_PLAN.md](02_FRONTEND_TRANSPORT_PLAN.md) | Web 作为 transport 的约定 |
| [07_DATABASE_UNIFICATION_PLAN.md](07_DATABASE_UNIFICATION_PLAN.md) | PG journal/account 治理 |
| [08_AGENT_DIRECT_CONTEXT_PLAN.md](08_AGENT_DIRECT_CONTEXT_PLAN.md) | Direct Context 迁移施工记录（含已完成阶段） |
| [09_LLM_INPUT_OUTPUT_TUNING_PLAN.md](09_LLM_INPUT_OUTPUT_TUNING_PLAN.md) | Direct Context 定型后的 Prompt / 工具输出 / 可观测性调优 |
| [10_CODEX_STYLE_MEMORY_EXECUTION_PLAN.md](10_CODEX_STYLE_MEMORY_EXECUTION_PLAN.md) | Codex 风格追问记忆改造执行计划（分阶段落地） |
| [11_DOMAIN_STRUCTURE_REFACTOR_REPORT_20260623.md](11_DOMAIN_STRUCTURE_REFACTOR_REPORT_20260623.md) | 本次目录分层重构执行报告（树状图 / 迁移对照 / 测试结果） |
| [12_FINAL_CLEANUP_REPORT_20260623.md](12_FINAL_CLEANUP_REPORT_20260623.md) | Phase 14 兼容层移除与 domain 拆分 |
| [13_PHASE15_DEAD_CODE_CLEANUP.md](13_PHASE15_DEAD_CODE_CLEANUP.md) | Phase 15 死代码与未接线模块清理 |
| [14_COMPAT_LAYER_REMOVAL_REPORT_20260623.md](14_COMPAT_LAYER_REMOVAL_REPORT_20260623.md) | Phase 16 兼容层彻底移除 |
| [15_SLIM_SCHEMA_FEISHU_FIB_REPORT_20260623.md](15_SLIM_SCHEMA_FEISHU_FIB_REPORT_20260623.md) | Phase 17 Schema/Feishu/斐波那契瘦身 |
| [16_FEISHU_ETH_SOL_4H_CHECKLIST.md](16_FEISHU_ETH_SOL_4H_CHECKLIST.md) | 飞书 ETH/SOL 4h 实测检查清单（日志链路 + 判定标准） |
| [05_DIRECTORY_MIGRATION_PLAYBOOK.md](05_DIRECTORY_MIGRATION_PLAYBOOK.md) | 历史目录迁移记录（只读参考） |
| [01_AGENT_ARCH_UPDATE_LOG.md](01_AGENT_ARCH_UPDATE_LOG.md) | 变更日志（只增不改旧条目） |

**改代码前**：先确认改动属于哪一层（见 00 文档 §3），避免在传输层或废弃路径加业务逻辑。
