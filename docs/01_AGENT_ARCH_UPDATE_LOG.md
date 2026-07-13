# Agent 架构更新日志（按日期降序）

---

# 2026-07-10

## 项目目录重构收口

- 业务源码统一迁入 `src/`：
  - `application / core / domain / infrastructure / schemas / tools / utils`
- 运行时资源统一迁入 `runtime/`：
  - `app / cli / config / web`
- 部署资源统一迁入 `ops/`：
  - `Dockerfile / docker-compose.yml / alembic`
- 开发、启动和 smoke 脚本统一保留在根目录 `scripts/`，不再保留重复的 `ops/scripts`。
- 新增 `sitecustomize.py`、入口路径初始化和测试 `conftest.py`，兼容现有顶层 import，同时保持 Web、飞书、脚本和测试可运行。

## 配置、入口与部署修复

- 修复默认市场配置路径，`market_config.json` 统一从 `runtime/config/` 读取，并增加回归测试。
- `runtime/config/analysis_defaults.yaml` 同时加入 Git 与 Docker 忽略规则，避免本地 API Key 被提交或打入镜像。
- Web / 飞书启动脚本统一注入 `runtime / src / repo root`，并验证脚本可从仓库外执行。
- 修复 CI legacy path guard 的新目录 allowlist。
- 修复 Compose 构建上下文、Dockerfile 路径及配置文件只读挂载；Docker CLI 未安装，因此仅完成静态路径验证，未实际构建镜像。
- README、项目架构文档和文档索引同步为 `src / runtime / ops / scripts` 当前基线。

## Codex 原生项目 Skills

- 将通用 skills 收敛为项目内 `.agents/skills/marketass-*`：
  - `marketass-architecture`
  - `marketass-debug`
  - `marketass-implement`
  - `marketass-review`
  - `marketass-tdd`
- `skills-lock.json` 仅保留上述项目工作流，`.vscode/` 与其他本地 Agent 数据保持忽略。

## 数据库状态澄清（暂缓接入）

- 当前主运行链路默认使用 JSON/JSONL，不以 PostgreSQL 作为启动或部署前置条件。
- 检查发现本机 PostgreSQL 的 `alembic_version` 为 `journal_005`，仓库仅保留 `journal_001`；`journal_002`～`journal_005` 来源尚未核清。
- 在迁移链恢复前不执行 `upgrade / downgrade / stamp`，不补造空 revision；配置模板默认关闭 PostgreSQL。
- 详细待办记录在 `docs/07_DATABASE_UNIFICATION_PLAN.md`，不阻塞本次目录重构提交。

## 验证结果

- CI guard：通过。
- `python -m pytest -q -m "not postgres"`：`78 passed`。
- Response style mock smoke：`5/5 passed`。
- Web 根路由、`/chat` 和 `/api/agent/run` 注册验证通过。
- Python 编译、Shell 语法、迁移文件清单、Compose 路径检查和 `git diff --check` 通过。

---

# 2026-07-07

## 交易策略响应契约文档化

- `docs/16_RESPONSE_CONTRACT_ARCHITECTURE_PLAN.md` 增加“交易策略响应契约”。
- 明确 Agent 的行情能力定位：
  - 支持交易解读、结构判断、关键位识别、条件化策略、复盘与风控建议。
  - 不承诺确定性预测，不替代真实盘口深度、逐笔成交与突发消息监控。
- 明确压缩行情信息与逐根 K 线的边界：
  - 压缩信息足够支持方向、位置、关键位、盈亏比和策略骨架。
  - 短线择时、真假突破、回踩有效性、长影线与量价节奏等场景，应按需补原始 K 线。
- 复盘类问题建议输出“回踩确认 / 突破确认 / 结构反转确认 / 失效条件 / 风控纪律”的交易员式框架。

---

# 2026-06-27

## 风险提示口径调整（Prompt）

- `core/prompt.py` 的文末约束由固定免责声明改为：
  - 文末补充风险提示，并结合市场情绪，从专业交易员角度劝诫用户保持交易纪律与自律。
- 目标：保留风险边界，同时让结尾提示更贴近交易执行语境（纪律、情绪、仓位自控）。

---

# 2026-06-25（Phase 18）

## 行情主链路切换到 AKShare（去除 TickFlow）

- `tools/market_data.py` 股票数据主路径改为 AKShare：
  - A 股：`stock_zh_a_daily`
  - 美股：`stock_us_daily`
  - 港股：`stock_hk_daily`
- 保留原分工：
  - 加密货币：`gate.io`
  - 黄金：AKShare `AU0`（日线/60m）
- `config/runtime_config.py` 移除 `get_tickflow_api_key()`，不再依赖 TickFlow key。
- `README.md` 行情数据源描述同步改为 AKShare。

## 标的解析与发现链路（Catalog + Discovery）

- 新增 `config/market_config.json` 作为可交易标的配置源（含 CN/US/HK/CRYPTO/PM 示例，补充港股样例小米 `01810.HK`）。
- 新增 `core/asset_catalog.py`：
  - 加载/缓存 `market_config.json`
  - 构建 symbol/alias 索引
  - 安全归一化（如 `NVDA.US -> NVDA`、`000625 -> 000625.SZ`、`1810 -> 01810.HK`）
  - 支持发现结果回写注册 `register_discovered_asset()`
- 新增 `core/asset_discovery.py`：
  - 使用运行时 LLM 生成候选（`symbol/name/market/research_keyword/aliases/confidence`）
  - 严格 JSON 数组解析，失败时返回空候选
- `tools/market_data.py` 新增内部解析主链：
  - 先 `catalog` / `catalog_alias`
  - 未命中再 `discovery`
  - discovery 候选必须先经 AKShare 验活

## 自动注册规则收敛（3 条硬规则）

- 规则收敛为：
  1. 配置命中直接通过。
  2. 未命中且 discovery 唯一候选时，仅在“近似等名 + 置信度达标”下自动注册。
  3. 其余全部返回 `clarify` / `not_found`。
- 移除未命中时“按代码格式 raw_symbol 直接放行”的路径，降低误判与误注册风险。
- 语义守卫实现已瘦身为小规则集合（前后缀归一 + token 匹配），避免大词表膨胀。

## 分析输出与 Prompt 协同

- `domain/market/analysis_service.py` 改为优先使用 `resolved_symbol`：
  - 输出 `analysis.symbol` 与 `message` 使用解析后代码
  - 当用户输入与解析代码不同，附带 `requested_symbol`
  - 透传 `resolution` 到分析结果，便于后续追问解释
- `core/prompt.py` 更新策略：
  - 美股示例改为 `NVDA/AAPL/TSLA`
  - 鼓励已知标的直接补全代码后调用 `analyze_market`
  - 不确定时直接调用主工具，内部解析处理歧义

## 回归验证

- 新增 `tests/test_market_symbol_resolution.py`，覆盖：
  - catalog 命中与别名命中
  - 常见符号归一化
  - discovery 单候选自动注册
  - 语义不一致阻断自动注册
  - discovery 为空返回 `not_found`
  - 多候选返回 `clarify`
  - `analyze_market` 使用 `resolved_symbol`
- 已执行：
  - `python3 -m pytest -q tests/test_market_symbol_resolution.py tests/test_analysis_output_sanitize.py`
  - 结果：`16 passed`

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

- `analyze_multi`（后续收敛到 `analyze_market` 统一入口）支持混合周期请求列表：
  - `[{"symbol":"ETHUSDT","interval":"4h"},{"symbol":"SOLUSDT","interval":"4h"},{"symbol":"AU9999","interval":"1d"}]`

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
