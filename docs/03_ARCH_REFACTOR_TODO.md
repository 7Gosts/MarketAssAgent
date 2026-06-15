# 架构重构待办清单

**日期**: 2026-06-15  
**状态**: 进行中（中风险渐进迁移）

## 目标

- 完成目录收敛：`app/`、`interfaces/` 为 canonical 路径
- 清理兼容层技术债：逐步移除 `adapters/`、`renderers/`、`presenters/` shim
- 保持线上可用：每步迁移都可回滚、可测试、可观测

## Docs 待办总览

1. 同步架构文档
   - `docs/00_PROJECT_ARCHITECTURE.md` 保持与 `app/`、`interfaces/` 实际目录一致。
2. 同步使用文档
   - README 的“目录结构”和“运行产物目录”与代码保持一致。
3. 补充迁移指引
   - 新增“旧 import 到新 import 映射表”（供批量替换与 code review 使用）。
4. 记录删除窗口
   - 在本文件明确 shim 删除版本和日期，避免长期遗留。

## 当前已完成

- `app/adapters` 已落地，`adapters/*` 保留兼容 shim。
- `interfaces/renderers` 已落地，`renderers/*` 保留兼容 shim。
- `interfaces/presenters` 已落地，`presenters/*` 保留兼容 shim。
- 运行产物目录外置到 `~/.marketassagent`（可由 `MARKETASSAGENT_DATA_DIR` 覆盖）。
- `FeishuRenderer` 表格路径已统一为 `schema 2.0 + table`，旧表格模式已移除。

## P0（下一阶段必须完成）

1. 兼容层淘汰计划（deprecation）
   - 在 `adapters/*`、`renderers/*`、`presenters/*` shim 中加入 `DeprecationWarning`。
   - 统计仓内剩余旧 import 使用点，全部切换到 canonical 路径。
   - 设定删除窗口（建议 2 个小版本后删除 shim）。

2. 文档与架构图同步
   - 更新 `docs/00_PROJECT_ARCHITECTURE.md`，反映 canonical 路径已迁移。
   - 在 README 增加“目录迁移状态”表，避免新同学继续使用旧路径。

3. 启动与脚本入口统一
   - 检查 `cli/*`、`scripts/*`、`tests/*` 是否仍依赖旧路径。
   - 确保全部入口可在 shim 删除后继续运行。

## P1（建议本轮完成）

1. 去除已废弃目录残留
   - 删除 `formatters/` 目录（如果确认无引用）。
   - 清理未使用的 deprecated 文件与注释（例如旧飞书记忆实现）。

2. Debug 与运行产物治理
   - 提供 `scripts/clean_runtime_data.sh` 清理 `~/.marketassagent/{debug,sessions,output}`。
   - `debug` 文件按大小或时间做轮转（避免长期膨胀）。

3. Feishu 渲染质量优化
   - `table` 卡片列宽策略从静态规则升级为按内容长度自适应。
   - 对超长单元格增加截断标记和“展开查看”链接（如后端可提供）。

## P2（后续优化）

1. 目录继续收敛
   - 评估将 `api/` 纳入 `app/`（如 `app/api`），减少顶层目录数量。
   - 评估 `core/services/memory` 的边界重新命名（`domain/infra` 分层）。

2. 接口层标准化
   - 引入 Renderer Registry（按 channel 自动分发）。
   - 将 Feishu/Web 发送能力抽象为统一 `DeliveryGateway`。

3. 观测与回放
   - 调试输出增加 request_id / message_id 关联字段。
   - 提供本地回放工具：从 `debug/llm_raw_outputs.jsonl` 重放渲染结果。

## 风险与回滚策略

- 风险点：shim 删除过早导致脚本/测试导入失败。
- 回滚策略：
  1. 每次迁移只改一层（adapters/renderers/presenters 之一）。
  2. 保留 shim 至少一个版本周期。
  3. 回归测试必须全量通过后再 push。

## 验收标准

- 全仓无旧路径 import（或仅 shim 自身）。
- 全量测试通过。
- README + 架构文档与实际目录一致。
- 新增入口（脚本/API）默认使用 canonical 路径。
