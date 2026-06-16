# 架构重构待办清单

**日期**: 2026-06-17  
**状态**: 记忆系统 JSON 化 + MemoryAPI 默认启用已完成

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
- MemoryAPI **默认启用**（`app/factory.py` 始终创建 `create_default_memory_api()`）。
- 保留灰度开关：`feature_flags.memory_api_only_mode`（控制是否只用 MemoryAPI 历史、停止写 legacy JSON session）。

## 验收基线（持续约束）

- 禁止新增旧路径 import：
  - `adapters.*` / `renderers.*` / `presenters.*`
  - `api.routes` / `app_factory`
- 全量测试必须通过后再合并。
- README 与架构文档必须与目录一致。

---

## 用户画像记忆系统（已完成，2026-06-17）

**目标**：让 LLM 在真实 ReAct 对话中知道当前用户的 `storage_key`，并在画像相关输入下有机会调用 `get_user_profile` / `update_user_profile`。

**核心改动**：

1. 新增 `profile_update` task_type（`core/planner.py`）
   - `_fallback_plan` 增加画像维护关键词检测（我偏好/我风险/我改成/记住/...）
   - 命中后返回 `task_type="profile_update"`

2. Orchestrator 支持 `profile_update` 路径（`core/orchestrator.py`）
   - `execute` 中新增分支，走 `_handle_agent_flow`
   - `_build_context` 注入 `storage_key`（优先 `user_id`，其次 `session_id`）
   - `TOOL_GROUP_MAP` 增加 `"profile"` 大类
   - `_KNOWN_TOOL_GROUPS` 增加 `"profile"`

3. Prompt 增强（`core/prompts.py` + `core/prompt.py`）
   - `get_full_prompt` 明确写入 `当前用户画像 storage_key: xxx`
   - 追加提示：“如需调用 get_user_profile / update_user_profile，必须使用该 storage_key”
   - `TASK_PROMPTS["profile_update"]` 增加说明
   - `core/prompt.py` 的【用户画像维护职责】补充 storage_key 使用要求

4. 保持 ConversationService 规则兜底（`_maybe_update_user_profile` 不删除）

5. 测试覆盖
   - `tests/test_prompts_storage_key.py`（storage_key 注入）
   - `tests/test_response_planner.py`（profile_update 识别）
   - `tests/test_orchestrator_tool_filter.py`（profile tools 过滤）
   - 全部通过：`pytest ... 17 passed`

**验证结论**：
- `get_all_tools()` 包含 profile tools
- `ResponsePlan(task_type="profile_update")` 能拿到 profile tools
- `get_full_prompt(...)` 能把 storage_key 写入 prompt
- 普通闲聊仍走 chat；画像维护输入走 profile_update → agent_flow → Tool Calling

**约束遵守**：
- 未重构 memory 架构
- 未引入新存储
- 未删除规则兜底
- profile tools 不在 `get_technical_tools()` 中
- 保持 feishu/web 通用（storage_key 仅用 user_id/session_id）

---

## 记忆系统（当前设计，2026-06-17）

完整说明见 [`docs/06_AGENT_MEMORY_ARCHITECTURE.md`](06_AGENT_MEMORY_ARCHITECTURE.md)。

| 数据类型 | 默认后端 | 存储位置 |
| --- | --- | --- |
| 短期对话 / session state | JSON | `~/.marketassagent/sessions/` |
| facts / checkpoint / user_profile | JSON | `~/.marketassagent/output/memory_facts.jsonl`、`memory_checkpoints.json` |
| journal / account | PostgreSQL | `database.postgres.dsn` |

要点：
- MemoryAPI **默认启用**（`app/factory.py`），无需 feature flag
- `memory.backend: json`（默认）；`postgres` 可选
- SQLite memory backend 已移除
- `memory_api_only_mode` 控制是否停止写 legacy JSON session
