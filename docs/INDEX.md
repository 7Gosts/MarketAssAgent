# 文档索引

这里是工程设计、架构演进、迁移记录的索引；项目安装、启动和使用入口见根目录 `README.md`。

## 当前有效文档（实施优先）

| 文档 | 何时阅读 |
| --- | --- |
| [**00_PROJECT_ARCHITECTURE.md**](00_PROJECT_ARCHITECTURE.md) | 当前真实主链路、代码分层、稳定会话/分析承接机制 |
| [**16_RESPONSE_CONTRACT_ARCHITECTURE_PLAN.md**](16_RESPONSE_CONTRACT_ARCHITECTURE_PLAN.md) | **首选** — 当前 light-only 主链路、工具按需补证、实施步骤与替换记录 |
| [17_ANALYSIS_SNAPSHOT_MEMORY_PLAN.md](17_ANALYSIS_SNAPSHOT_MEMORY_PLAN.md) | 行情分析轻量快照的已实施最小版；下一阶段快照入库衔接见 `07 + 18` |
| [03_ARCH_REFACTOR_TODO.md](03_ARCH_REFACTOR_TODO.md) | 演进待办、已完成项、CI 防回流约束 |
| [04_LLM_TOOL_AUTONOMY_PLAN.md](04_LLM_TOOL_AUTONOMY_PLAN.md) | LLM 工具调用策略演进 |
| [02_FRONTEND_TRANSPORT_PLAN.md](02_FRONTEND_TRANSPORT_PLAN.md) | Web 作为 transport 的约定 |
| [07_DATABASE_UNIFICATION_PLAN.md](07_DATABASE_UNIFICATION_PLAN.md) | 数据库下一阶段路线：先做快照入库和本地 DB smoke，再推进模拟开单/复盘；含当前持久化路径的保留/废弃/待迁移清单 |
| [18_TRADING_DOMAIN_BUSINESS_DESIGN.md](18_TRADING_DOMAIN_BUSINESS_DESIGN.md) | 交易域业务设计：LLM 边界、自动兑单、三表正式目标模型 |
| [19_PAPER_TRADING_IMPLEMENTATION_DESIGN.md](19_PAPER_TRADING_IMPLEMENTATION_DESIGN.md) | 模拟开单与状态流转实施设计：正式 DDL、模块拆分、自动兑单规则、开工步骤 |
| [20_DATABASE_SETUP.md](20_DATABASE_SETUP.md) | 新电脑克隆后的 PostgreSQL 配置、建表与验证步骤 |
| [01_AGENT_ARCH_UPDATE_LOG.md](01_AGENT_ARCH_UPDATE_LOG.md) | 变更日志（只增不改旧条目） |

## 保留的历史文档

| 文档 | 说明 |
| --- | --- |
| [06_AGENT_MEMORY_ARCHITECTURE.md](06_AGENT_MEMORY_ARCHITECTURE.md) | 旧记忆架构说明（含 Direct Context 旧描述） |

已删除的 2026-06 历史迁移/清理报告不再作为当前施工依据，也不再保留在仓库中。

**改代码前**：先确认改动属于哪一层。会话/分析主链路先看 `00 + 16 + 17`，数据库与模拟交易相关改动先看 `07 + 18`。
