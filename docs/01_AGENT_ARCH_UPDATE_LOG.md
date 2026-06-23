# Agent 架构更新日志（按日期降序）

---

# 2026-06-23（Phase 17）

## Schema 瘦身 + FeishuAdapter 瘦身 + 斐波那契工具恢复

- `ConversationEnvelope` 移除 `blocks` / `DeliveryHint` 及 meta 中恒空 rich 字段
- `FeishuAdapter` 移除未使用的 `agent` 构造参数
- `analyze_fibonacci` 重新注册为 LangChain 工具
- 架构文档与 README API 示例同步

---

# 2026-06-23（Phase 16）

## 兼容层彻底移除

- 删除 `interfaces/` 目录；飞书渲染器迁入 `infrastructure/adapters/renderers/`
- 删除 `domain/market/analysis.py` re-export 层；`tools/registry` 与测试直连 `analysis_service` / `structure`
- 更新 `docs/00_PROJECT_ARCHITECTURE.md` 目录表，移除全部旧路径/兼容层描述
- CI guard 禁止 `interfaces/` 复活

---

# 2026-06-23

## 目录分层重构（Domain / Application / Infrastructure）

- 新增顶层分层目录：
  - `domain/market`、`domain/profile`、`domain/trading`
  - `application/services`、`application/presenters`
  - `infrastructure/adapters`、`infrastructure/persistence`、`infrastructure/memory`
- `tools` 收敛为 Facade：
  - `tools/technical_analysis.py` 转发到 `domain/market/analysis.py`
  - `tools/user_profile.py` 转发到 `domain/profile/user_profile.py`
- 主链路 import 同步：
  - `app/factory.py` 改为引用 `infrastructure.adapters.*` 与 `application.services.*`
  - `cli/feishu_bot.py` 改为引用 `infrastructure.adapters.feishu_longconn`
- 保留旧路径兼容层，确保可回滚。

## Wyckoff v2 深化 + 阶段转换

- `market_structure_v2` 增强字段：
  - `wyckoff_phase`
  - `wyckoff_phase_transition`
  - `wyckoff_signals`
  - `wyckoff_confidence`
  - `spring_upthrust_detected`
- Spring / Upthrust / 价量背离证据链强化，并进入 evidence 输出。
- `multi_pattern_overlap` 维持结构化排序：`pattern/confidence/reason`。

## 回归验证

- 子集回归：`tests/test_analysis_output_sanitize.py` + 记忆链路相关测试通过（21 passed）。
- 全量回归：`python3 -m pytest -q` 在当前环境 62 passed / 1 skipped / 2 failed（均为 `psycopg` 缺失导致的 PostgreSQL 依赖问题）。

## market_structure_v1 + pattern_detection_v1 落地（v1）

- `tools/technical_analysis.py` 新增结构识别与形态识别：
  - `market_structure_v1`
  - `pattern_detection_v1`
- `analyze_market` 输出升级：
  - `analysis.market_structure_v1`
  - `analysis.pattern_detection_v1`
- `compact_summary_v1` 同步新增关键字段，供 LLM 直接消费：
  - `structure_label`
  - `pattern_name`
  - `pattern_confidence`
  - `range_width_pct`
  - `top_evidence`

## Prompt 约束增强（避免形态“脑补”）

- `core/prompt.py` 更新事实边界约束：
  - 有 `market_structure_v1 / pattern_detection_v1` 时优先引用其 evidence。
  - 证据不足时不输出“明确形态结论”，改为“盘整/区间震荡”等保守表达。
  - 若使用“三角收敛/矩形盘整”描述，要求同时给出结构证据。

## 验证结果

- `python3 -m pytest -q tests/test_analysis_output_sanitize.py` -> `4 passed`
- `python3 -m pytest -q tests/test_direct_agent_context_flow.py tests/test_phase_c_memory_flow.py` -> `9 passed`
- 语法检查：`python3 -m py_compile core/prompt.py tools/technical_analysis.py` 通过

---

# 2026-06-18

## Direct Context + 输出压缩（Phase B/C/D）

- 落地 Direct Context 预算控制：
  - `agent_context.max_chars`
  - `agent_context.max_recent_sources`
  - `agent_context.max_conclusion_chars`
- `ConversationService` 增加预算命中日志：
  - `truncated`
  - `dropped_chars`
  - `input_chars`
- `core/graph.py` 增加 token usage 记录与 debug 落盘：
  - `~/.marketassagent/debug/llm_token_usage.jsonl`
- 新增 `scripts/replay_debug_output.py`，支持按 `session_id` 聚合与 TopN 高耗轮次回放。

## 技术分析输出紧凑化（Phase B 深化）

- `analyze_market` 新增：
  - `compact_summary_v1`
  - `output_meta_v1`
- 多标的模式新增：
  - `comparison_brief_v1`
  - `output_meta_v1`
- `ConversationService` 写入 `tool_observation` 时优先 compact 内容，日志输出：
  - `raw_chars`
  - `compact_chars`
  - `compact_field_count`
  - `omitted_hint`

## 关键位与字段瘦身

- 修复关键位分类：按当前价重分 `support/resistance`（避免支撑/阻力反向）。
- 均线仅保留趋势定性，不再输出 `ma_values` 细节。
- 低价值字段降噪（减少给 LLM 的冗余内容）。
- 关键位进一步裁剪为“最近两档”：
  - `key_levels.support/resistance` 仅 2 档
  - `levels_v2.support_levels/resistance_levels` 仅 2 档

## 飞书标题显示模型来源

- `interactive/post` 标题统一显示 `env_prefix`，例如：
  - `市场助手回复（DEEPSEEK）`

---

# 2026-06-16

## 目录与主链路重构收口

- 目录 canonical 收敛到：
  - `app/adapters/*`
  - `app/api/*`
  - `app/factory.py`
  - `interfaces/*`
- 清理旧兼容路径（旧 router/orchestrator/planner 路径下线）。
- CI 守卫防止旧路径回流（`scripts/guard_no_legacy_memory_path.py`）。

## LLM 工具自主决策能力建设

- Prompt 工具策略改为 LLM-first，弱化代码硬编码路由。
- 旧 `required_tools` 强制链路降级为建议性约束。
- 清理冗余 fallback 与中过度过滤逻辑。

## 多标的混合周期能力

- `analyze_multi`（后续收敛到 `analyze_market` 统一入口）支持混合周期 map：
  - `{"ETHUSDT":"4h","SOLUSDT":"4h","AU9999":"1d"}`

## 记忆系统 Phase B~E + UserProfile 审计

- `thread_id=session_id` 贯穿 graph 调用。
- 统一 MemoryAPI 读写（`recent_message/tool_observation/checkpoint`）。
- UserProfile 增加审计日志与置信度链路。

---

# 2026-06-12

## 运行时服务收口基线

- `app/factory.py` 成为唯一运行时装配点。
- `RuntimeServices` 持有唯一 `MarketSessionManager` + `ConversationService`。
- Web / Feishu 路径统一通过会话服务编排，避免入口层重复记忆逻辑。

## ConversationService 成为唯一会话编排入口

- 统一流程：
  - `save user`
  - `load history`
  - `invoke agent`
  - `extract reply`
  - `save assistant`
- `FeishuMemory` 退出主路径，仅保留兼容标记。

---

# 2026-06-05

## LLM 初始化重构（对齐 runtime_config）

- `core/agent.py` 改为使用 `get_llm_runtime_settings()` 初始化。
- 支持多 provider 配置与统一切换（YAML 为主）。

## 部署支持（Docker）

- 新增：
  - `Dockerfile`
  - `.dockerignore`
  - `docker-compose.yml`

---

# 2026-06-04

## 核心 Agent 架构硬化

- 完成 core skeleton 打通：
  - `core/state.py`
  - `core/graph.py`
  - `core/agent.py`
- 工具注册中心完善：
  - `tools/registry.py`
- snapshot 能力接入：
  - `memory/snapshot.py`

## 数据库层与 Alembic

- 增加 Alembic 迁移框架与 journal 初始迁移。
- 完成 persistence 基础仓储：
  - `persistence/db.py`
  - `persistence/journal_repository.py`

## 历史问题记录（当日）

- 循环导入与工具缺失导致的初始化不稳定。
- 通过延迟导入 + 安全导入策略临时稳定。
- 后续版本逐步完成主链路清理与替换。
