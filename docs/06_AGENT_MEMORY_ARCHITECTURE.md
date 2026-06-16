# Agent 记忆架构设计（当前实现）

**更新时间**: 2026-06-16  
**适用版本**: 当前 `main` 分支

---

## 1. 设计目标

当前项目的记忆系统目标是：

1. 所有调用链（Web / Feishu / chat / analyze）统一使用同一记忆接口。
2. 对话短期记忆由 LangGraph `thread_id` 贯穿，避免“分支失忆”。
3. 事实记忆（facts）可结构化存储，并支持“你怎么知道”的来源追溯。
4. 支持灰度切换与回滚，避免一次性替换导致线上不稳定。

---

## 2. 总体分层

当前记忆层由三部分组成：

1. **Thread 级执行记忆（LangGraph）**
   - 在 `core/agent.py` 调用 graph 时统一注入：
     - `config={"configurable": {"thread_id": session_id}}`
   - Graph 可接 `checkpointer/store`（Phase B）。
   - 代码：
     - `core/graph.py`
     - `core/agent.py`
     - `app/factory.py`

2. **统一 Memory API（应用记忆接口）**
   - 统一 API：`recall / write_fact / snapshot / checkpoint / get_checkpoint`
   - 默认实现：`DefaultMemoryAPI + SQLiteFactStore`
   - 代码：
     - `core/memory_api.py`
     - `core/fact_store.py`

3. **会话编排层（ConversationService）**
   - 统一记忆读写入口（当前唯一业务入口）。
   - 负责：
     - 写入 `recent_message` facts
     - 从 MemoryAPI 读历史
     - 写入 `tool_observation` facts
     - 更新 `last_snapshot` checkpoint
     - 追问来源时拼接 provenance block
   - 代码：
     - `services/conversation_service.py`

4. **长期用户画像层（UserProfile）**
   - 画像模型：`core/profile.py`
   - 存储方式：以 `Fact(type="user_profile")` 持久化到 `thread_id=user_profile_{user_id}`
   - 读取时机：`Orchestrator._build_context()` 且 `plan.user_context_needed=true`
   - 更新时机：`ConversationService` 在用户输入中识别风格/风险/常用标的/仓位偏好后更新

---

## 3. 数据模型

### 3.1 Fact（结构化事实）

Fact 字段：

- `id`: UUID
- `thread_id`: 会话线程标识（当前使用 `session_id`）
- `source`: 来源（如 `conversation_service` / `analyze_market`）
- `timestamp`: ISO 时间戳
- `type`: 事实类型（如 `recent_message` / `tool_observation`）
- `payload`: 结构化内容
- `provenance`: 来源追踪（`request_id` / `tool_call_id`）
- `tags`: 标签

实现见 `core/fact_store.py` 中 `Fact` dataclass。

### 3.2 Checkpoint（线程级状态）

用于保存关键短期状态（目前主要是 `last_snapshot`）：

- 表：`checkpoints`
- 主键：`(thread_id, ck_key)`
- 值：`value_json`

### 3.3 UserProfile（长期画像）

字段定义见 `core/profile.py`，核心字段：

- `preferred_style`: `left_side/right_side/swing/scalping/unknown`
- `risk_profile`: `conservative/balanced/aggressive/unknown`
- `favorite_symbols`: 常用标的列表
- `max_position_ratio`: 单仓偏好上限
- `preferred_timeframes`: 偏好周期
- `notes`: 自然语言偏好备注

---

## 4. 读写链路（请求生命周期）

一次请求中记忆相关流程如下：

1. `ConversationService.run()`
2. 写入用户消息：
   - legacy 模式：写 `session_manager`
   - 新模式：写 `recent_message` fact
3. 读取历史：
   - `memory_api_only_mode=true`：仅从 MemoryAPI recall
   - 否则：MemoryAPI 与 legacy 兼容读取
4. Planner 生成 plan（含 `required_provenance` 判定）
   - 若命中“我的仓位/我偏好/我习惯”等模式，会标记 `user_context_needed=true`
5. Orchestrator 执行（chat/analyze 均带历史）
   - `user_context_needed=true` 时注入 `user_profile` 到 prompt 上下文
6. 将 tool 消息写入 `tool_observation` facts
7. 如果有 `analysis_result/last_snapshot`，写 checkpoint `last_snapshot`
8. 若 `required_provenance=true`，自动追加 `依据来源` 区块
9. 写 assistant 回复（legacy + recent_message fact）

---

## 5. 关键开关（灰度/回滚）

配置位于 `feature_flags`：

1. `memory_new_api`
   - 开启后装配 `MemoryAPI` 与 Graph 的 `checkpointer/store`。

2. `memory_api_only_mode`
   - 开启后，历史读写只走 MemoryAPI；
   - legacy `session_manager` 历史读写不再参与主链路（仅异常降级）。

环境变量覆盖规则（`config/runtime_config.py`）：

- `MARKETASSAGENT_FEATURE_MEMORY_NEW_API=true|false`
- `MARKETASSAGENT_FEATURE_MEMORY_API_ONLY_MODE=true|false`

---

## 6. “你怎么知道”机制

当用户问题命中来源追问意图（如“怎么知道/依据/来源”）：

1. Planner 将 `required_provenance=true`
2. ConversationService 读取最近 `tool_observation` facts
3. 回复末尾追加来源摘要：
   - 工具名
   - 时间
   - 摘要
   - `tool_call_id`（若有）

该机制避免模型凭空解释，改为引用记忆层中的事实来源。

---

## 7. UserProfile 自动学习机制

`ConversationService` 在每轮用户输入中做轻量规则提取（不依赖额外模型）：

1. 风格提取：
   - “右侧/左侧/波段/短线” -> `preferred_style`
2. 风险偏好提取：
   - “保守/稳健/平衡/激进” -> `risk_profile`
3. 常用标的提取：
   - “常看/偏好/喜欢 + BTC/ETH/AU0/... ” -> `favorite_symbols`
4. 周期提取：
   - “1h/4h/日线/15m” -> `preferred_timeframes`
5. 仓位上限提取：
   - “单仓 20%” -> `max_position_ratio=0.2`
6. 偏好备注：
   - “不追高/不喜欢...” -> `notes`

提取结果会通过 `memory_api.update_user_profile()` 写回长期画像。

---

## 8. 当前边界与已知限制

1. Graph 的 `checkpointer/store` 目前使用内存实现（`MemorySaver/InMemoryStore`）。
   - 进程重启后会丢失 thread 内部状态。
2. MemoryAPI facts 使用 SQLite，适合当前单机部署；分布式需替换后端。
3. 仍保留少量 legacy 兼容逻辑（由 feature flag 控制，便于回滚）。
4. `session_manager` 历史分支仅用于灰度兼容；长期将收敛为 `memory_api_only_mode` 主路径。

---

## 9. 回归与守卫

### 8.1 测试

- `tests/test_memory_api.py`
- `tests/test_agent_thread_id.py`
- `tests/test_phase_c_memory_flow.py`
- `tests/test_user_profile_memory.py`

重点覆盖：

- thread_id 贯穿
- memory-only 模式下不走 legacy history IO
- 两轮对话 provenance 追溯

### 8.2 CI 守卫

新增守卫脚本：

- `scripts/guard_no_legacy_memory_path.py`

守卫内容：

1. 禁止旧路径回流（`app_factory.py`、`api/routes.py`、`adapters/` 等）
2. 禁止旧 import 回流（`from adapters...` 等）
3. 限制 legacy session 直接访问点（允许列表外即失败）

GitHub Actions：

- `.github/workflows/ci.yml`

---

## 10. 运维建议

建议默认灰度顺序：

1. `memory_new_api=true`, `memory_api_only_mode=false`
2. 验证稳定后切 `memory_api_only_mode=true`
3. 连续观察后再考虑彻底删除 legacy session 历史读写逻辑

监控建议（后续可加）：

- `planner_fallback_rate`
- `memory_recall_latency`
- `tool_observation_write_rate`
- `provenance_append_rate`

---

## 11. 关联文档

- 数据库现状与统一治理计划：
  - `docs/07_DATABASE_UNIFICATION_PLAN.md`
