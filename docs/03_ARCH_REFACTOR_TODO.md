# 架构重构待办清单

**日期**: 2026-06-16  
**状态**: 阶段 1/2/3 已完成（兼容层已清除）

## 已完成（目录收敛）

- 仅保留 canonical 路径：
  - `app/adapters/*`
  - `app/api/*`
  - `app/factory.py`
  - `interfaces/renderers/*`
  - `interfaces/presenters/*`
- 兼容层已删除：
  - `adapters/`
  - `renderers/`
  - `presenters/`
  - `api/routes.py`（旧路径）
  - `app_factory.py`（旧路径）
- 旧记忆兼容文件已删除：`memory/feishu_memory.py`

## 当前待办（新阶段）

1. 持续优化目录语义
   - 评估 `core/services/memory/tools` 的边界，是否进一步收敛为 `domain/infra`。
2. 渲染与投递标准化
   - 引入统一 `DeliveryGateway`，降低多渠道扩展成本。
3. 调试与回放能力
   - 增加请求链路 ID，支持从 `debug/llm_raw_outputs.jsonl` 一键重放。
4. 运行产物治理
   - 增加 `scripts/clean_runtime_data.sh`，支持一键清理本地运行数据。

## 记忆系统进度（Phase D/E）

- 已接入 `MemoryAPI` + `FactStore`，并在 `ConversationService` 中双写 `recent_message` 与 `tool_observation`。
- 新增 provenance 输出能力：当用户追问“依据/来源/怎么知道”时，回复会附带 `依据来源` 摘要。
- 新增灰度开关：
  - `feature_flags.memory_new_api`
  - `feature_flags.memory_api_only_mode`

## 验收基线（持续约束）

- 禁止新增旧路径 import：
  - `adapters.*` / `renderers.*` / `presenters.*`
  - `api.routes` / `app_factory`
- 全量测试必须通过后再合并。
- README 与架构文档必须与目录一致。
