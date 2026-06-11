# 飞书卡片模式移除 + 行情分析日志增强

**日期**: 2026-06-04  
**涉及文件**:
- `app/feishu_adapter.py`
- `app/formatters/feishu.py`
- `app/agent_graph.py`
- `scripts/dev.sh` (新增)

## 1. 移除 FEISHU_CARD_MODE 环境变量及相关逻辑

### 背景
之前通过 `FEISHU_CARD_MODE=1` 控制是否启用飞书交互式卡片回复。默认值为 "0"（关闭），导致非 chat 类型的回复直接返回纯文本提示：
> "当前未启用飞书卡片模式，请开启 FEISHU_CARD_MODE。"

### 改动
- 删除 `_feishu_card_mode_enabled()` 函数及所有 `os.getenv("FEISHU_CARD_MODE", ...)` 检查。
- `build_feishu_delivery()` 移除 `card_mode` 参数，**默认总是尝试构建卡片**（chat 类型仍走纯文本）。
- `send_feishu_reply()` 和 `send_reply_or_fallback()` 移除 `card_mode` 参数及所有调用点传递。
- 删除常量 `FEISHU_CARD_MODE_DISABLED_MSG`。
- `scripts/dev.sh` 中无需再导出该变量。

### 效果
现在飞书机器人（除闲聊外）**始终使用卡片模式**，无需任何环境变量或条件判断，简化配置。

## 2. 为 analyze_multi / market_analysis 增加细粒度错误日志

### 背景
真实用户消息 "看看科大讯飞 的行情" 触发 `analyze_multi` 路径，耗时 32s 后抛出：
> RuntimeError('任务执行完成但无有效结果')

导致 unified_graph 失败，降级为普通 chat 回复。顶层日志不够定位是 fetch 还是 merge 阶段的问题。

### 改动（`app/agent_graph.py`）
- `_capability_multi_analysis()`：
  - 记录 `market_analysis start`（symbols + interval）
  - `fetch_market_snapshots` 增加 start / fetch_done / fetch_failed（含异常类型和消息）
  - `merge_snapshot_facts_bundle` 增加 merge_done / merge_failed
- `capability_node` 中的 `analyze_multi` 分支：
  - 外层 try/except 记录 `capability market_analysis success`（facts keys）或 `failed`（err + payloads）
  - 所有异常重新抛出，保证上层 `unified_graph_error` 仍能捕获

### 效果
当 `AGENT_PIPELINE_LOG=1`（`dev.sh` 默认开启）时，日志会清晰显示：
- fetch_market_snapshots 是否成功
- merge 是否产出 facts
- 具体在哪个子步骤失败

便于后续排查 A 股 / tickflow provider 等问题。

## 提交信息
```
feat: remove FEISHU_CARD_MODE and enhance market_analysis logging

- Always use Feishu card mode for non-chat replies
- Add detailed pipeline logs in analyze_multi / _capability_multi_analysis
- Create docs/ for change records
```

---

# 核心 Agent 重构与工具集成（Core Agent Hardening）

**日期**: 2026-06-04  
**涉及文件**:
- `core/state.py`（优化版）
- `core/graph.py`（填充 TODO）
- `core/agent.py`
- `tools/registry.py`（完善）
- `memory/snapshot.py`（新增 save/load）
- `adapters/feishu_adapter.py`（新建）
- `adapters/web_adapter.py`（新建）
- `README.md`（架构图 + 目录树更新）

## 1. 旧架构残留清理确认
- 确认 `app/`, `analysis/`, `intel/`, `scripts/`, `sql/` 已彻底删除
- 项目目录结构精简为 `core/`, `tools/`, `memory/`, `persistence/`, `adapters/`, `config/`, `cli/`, `tests/`, `docs/`

## 2. Core 模块硬化
- `core/state.py`：采用优化版 `AnalysisSnapshot` + `AgentState`（含 session_id、next、error、metadata）
- `core/graph.py`：清理 SYSTEM_PROMPT 导入，填充 reason/act 节点注释与策略说明
- `core/agent.py`：`MarketReActAgent.invoke()` 入口清晰可用

## 3. 工具注册中心
- `tools/registry.py`：实现 `make_tool_list`（安全占位加载）与 `get_tool_by_name`
- 后续可轻松对接 `market_data`、`sim_account`、`research` 等工具

## 4. Snapshot 持久化增强
- `memory/snapshot.py`：新增 `save_snapshot` / `load_snapshot`（JSON 文件存储）
- 支持追问时恢复上次 `AnalysisSnapshot`，解决上下文不稳定问题

## 5. 适配器对接新核心
- 新建 `adapters/feishu_adapter.py`：`handle_feishu_message` 调用 `MarketReActAgent.invoke()`
- 新建 `adapters/web_adapter.py`：`run_agent` 返回完整 state，供 FastAPI 使用

## 6. 文档与架构说明
- `README.md`：添加 Mermaid 架构流程图 + 精简目录树 + 核心模块说明

## 提交信息
```
refactor: complete core ReAct skeleton + tool wiring

- Confirmed old dirs removed
- Hardened core/state, graph (filled TODOs), agent
- Completed tools/registry.py with safe loading
- Enhanced memory/snapshot.py with save/load for follow-ups
- Added adapters/feishu_adapter.py & web_adapter.py calling new agent
- Updated README with mermaid architecture + clean directory tree
```

---

## 重构过程中遇到的不符合预期情况（Issue Log）

**日期**: 2026-06-04

### 1. 循环导入（Circular Import）问题反复出现
- **现象**：`core/agent.py` ↔ `core/graph.py` ↔ `tools/registry.py` 之间多次出现 `ImportError: cannot import name 'xxx' from partially initialized module`。
- **原因**：
  - `graph.py` 需要 `call_model`（定义在 `agent.py`）
  - `agent.py` 需要 `build_graph` 和 `get_all_tools`
  - `registry.py` 在导入时尝试加载 `technical_analysis` 等子模块
- **处理方式**：
  - 使用 `TYPE_CHECKING` + 函数内延迟导入
  - 在 `registry.py` 中添加 `try/except` 安全导入
  - 多次迭代后才稳定

### 2. 部分工具函数缺失导致 `get_all_tools()` 返回空列表
- **现象**：`tools/research.py` 和 `tools/sim_account.py` 中缺少 `search_research_reports`、`simulate_open_position`、`get_journal_status` 等函数。
- **影响**：`get_all_tools()` 实际返回 0 个工具，LangGraph 的 `ToolNode` 无法正常工作。
- **当前状态**：已做安全处理（返回已实现的工具），待后续补齐。

### 3. `AgentState` 与 `call_model` 设计不一致
- **问题**：`core/graph.py` 中的 `call_model` 尝试访问 `state.get("llm")`，但 `AgentState` TypedDict 中从未定义 `llm` 字段。
- **影响**：当前 `call_model` 无法真正调用 LLM，仅为占位逻辑。
- **后续建议**：在 `AgentState` 中增加 `llm: Any` 字段，或在 `agent.py` 初始化时注入 LLM 实例。

### 4. 旧代码残留清理不彻底
- 即使执行了 `rm -rf app/ analysis/ ...`，仍有大量旧文件引用已删除的 `app/` 模块：
  - `tools/market_data.py`
  - `persistence/journal_repository_pg.py`
  - `cli/feishu_bot.py` 等
- **影响**：如果直接导入这些文件会导致 `ModuleNotFoundError`。
- **处理**：目前仅在 `registry.py` 中做了安全导入保护。

### 5. 非 Python 文件语法检查误操作
- 尝试对 `requirements.txt` 执行 `python -m py_compile`，导致 `SyntaxError`（预期行为，但属于小插曲）。

### 6. Snapshot 持久化机制偏简单
- `memory/snapshot.py` 目前使用内存 dict + JSON 文件，适合开发调试，但生产环境缺少：
  - 真正的数据库持久化
  - 按 session_id + symbol 的复合键管理
  - 过期清理机制

### 总结
本次重构过程中，**循环导入**和**工具函数缺失**是两个最反复出现的问题，消耗了较多迭代时间。最终通过“安全导入 + 延迟导入”策略解决了稳定性问题，但核心的 LLM 注入和真实工具实现仍需后续补齐。

---

## 下一阶段：核心调用链修复 + 工具补齐 + 测试验证（v4.1）

**日期**: 2026-06-04  
**涉及文件**:
- `core/agent.py`
- `core/graph.py`
- `tools/registry.py`
- `tools/research.py`（新建）
- `tools/sim_account.py`（新建）
- `tools/market_data.py`（新建）
- `main.py`
- `tests/test_agent.py`（新建）
- `.env.example`

### 主要改动

1. **核心调用链彻底修复**
   - 移除 `core/graph.py` 中的重复 `call_model` 定义
   - 新增 `make_call_model(llm)` 工厂函数，实现 LLM 闭包绑定
   - `MarketReActAgent.__init__` 支持 `llm` 参数注入，默认使用 `ChatOpenAI`
   - `build_graph(llm)` 正确接收并使用 LLM 实例
   - 彻底解决循环导入问题

2. **工具层补齐**
   - 新建 `tools/research.py`：`search_research_reports`
   - 新建 `tools/sim_account.py`：`simulate_open_position`、`get_journal_status`
   - 新建 `tools/market_data.py`：`fetch_market_data`
   - `tools/registry.py` 采用安全导入 + 动态注册，目前已注册 **6 个工具**

3. **入口与测试**
   - `main.py` 初始化 `ChatOpenAI` 并注入 `MarketReActAgent`
   - 新建 `tests/test_agent.py`：基础 invoke 测试用例
   - 确保所有 package 目录包含 `__init__.py`

4. **配置完善**
   - 更新 `.env.example`：增加 `OPENAI_API_KEY`、`LLM_MODEL`

### 验证结果
- 所有模块导入成功
- `get_all_tools()` 返回 6 个工具
- `MarketReActAgent` 可正常实例化并调用 `invoke`

### 提交信息
```
feat: complete core call chain fix + tool completion + testing

- Fixed core/agent.py and core/graph.py: proper LLM injection, single call_model, build_graph(llm)
- Implemented tools/research.py, sim_account.py, market_data.py
- Updated tools/registry.py with safe imports (6 tools registered)
- Added LLM initialization in main.py
- Added tests/test_agent.py
- Ensured all __init__.py and updated .env.example
```

---

## 收尾验证：核心流程真正跑通

**日期**: 2026-06-04

### 验证内容
- 改进 `core/agent.py`：无 API Key 时给出清晰错误提示
- 修复 `core/graph.py`：`call_model` 始终返回 `AIMessage`，避免消息类型错误
- 增强 `core/supervisor.py`：自动生成带免责声明的 `recommendation`
- 更新 `tests/test_agent.py`：使用 Dummy LLM 成功跑通完整 ReAct 流程（reason → supervisor）

### 验证结果
- `python tests/test_agent.py` 输出：`✅ Dummy LLM test passed`
- 核心调用链（invoke → graph → supervisor → recommendation）已真正打通
- 项目具备可运行、可测试的基础

### 提交信息
```
fix: make core flow verifiable with dummy LLM + improve error messages

- core/agent.py: clear error when OPENAI_API_KEY missing
- core/graph.py: ensure AIMessage returned from call_model
- core/supervisor.py: generate recommendation with disclaimer
- tests/test_agent.py: runnable dummy LLM test that passes
```

---
## 数据库层搭建（Alembic + Persistence）

**日期**: 2026-06-04

### 完成内容

1. **Alembic 迁移系统**
   - 创建 `alembic/env.py`（参考 Stock_Analysis，使用 `get_postgres_dsn()`）
   - 创建 `alembic/script.py.mako`
   - 创建第一个迁移 `journal_001_create_journals.py`（基于 `persistence/models.py` 的 `Journal` 模型）

2. **Persistence 层完善**
   - 新建 `persistence/db.py`：`get_engine()`、`get_session()`、`init_db()`
   - 新建 `persistence/journal_repository.py`：`JournalRepository`（create、get_by_id、list_by_session、update_status）
   - 更新 `persistence/__init__.py`

3. **集成**
   - `main.py` 启动时自动调用 `init_db()`

### 使用方式

```bash
# 初始化数据库（首次）
alembic upgrade head

# 后续添加新表/字段时
alembic revision --autogenerate -m "add xxx"
alembic upgrade head
```

### 提交信息
```
feat: add Alembic + complete persistence database layer

- Created alembic/ with env.py, script.py.mako, journal_001
- Added persistence/db.py and journal_repository.py
- Integrated init_db() into main.py
```

---

## LLM 初始化重构（对齐 Stock_Analysis 配置体系）

**日期**: 2026-06-05

### 改动内容

- 重构 `core/agent.py`：
  - 移除硬编码的 `ChatOpenAI` 初始化
  - 使用 `config.runtime_config.get_llm_runtime_settings()` 获取配置
  - 支持通过 `analysis_defaults.yaml` 中的 `llm.providers` 配置多个提供商
  - 支持环境变量覆盖（`LLM_PROVIDER`、`LLM_API_KEY`、`LLM_MODEL` 等）

- 更新 `.env.example`，说明新的 LLM 配置方式

### 效果

现在可以方便地切换 LLM 提供商（OpenAI / DeepSeek / OpenRouter / HCT 等），与 Stock_Analysis 项目保持配置一致性。

### 提交信息
```
refactor: align LLM initialization with Stock_Analysis runtime_config

- core/agent.py now uses get_llm_runtime_settings()
- Supports multiple providers via analysis_defaults.yaml
- Environment variables can override LLM settings
```

---

## 运行时服务收口基线

**日期**：2026-06-12  
**提交**：`cbaccf9`

### 已完成

- `app_factory.py` 成为唯一运行时装配点
- `RuntimeServices` 持有唯一 `MarketSessionManager` 与 `ConversationService`
- `api/routes.py`、`FeishuAdapter`、`WebAdapter`、`Router` 均通过依赖注入使用服务
- Web `/api/agent/run` 真实链路记忆验证通过（`scripts/verify_web_memory.py`）
- `pytest -q` 通过（测试基线稳定）
- `FeishuMemory` 已退出主路径，仅保留 deprecated 兼容

### 当前边界

- Feishu analyze 路径已走 `ConversationService`
- Feishu chat 路径仍需进一步统一到同一记忆编排流程

### 下一阶段

- 用 `invoke_fn` / `run_with_callable` 收口 Feishu chat/analyze 记忆一致性
- 后续再做 history truncation、summary compaction、checkpointer

---

## 会话记忆统一重构（ConversationService 收口）

**日期**: 2026-06-12

### 背景问题

- 飞书与 Web 记忆链路长期分叉：飞书走专用 `FeishuMemory`，Web 完全无记忆
- 项目内已存在通用会话层（`SessionManager` / `SessionStore`），但未成为唯一真相源
- 入口层（`api/routes.py`、`FeishuAdapter`、`WebAdapter`）各自手写记忆编排，存在分叉风险

### 架构决策

- 新增 `services/conversation_service.py` 作为**唯一会话编排层**
- 所有真实入口（`api/routes.py`、`FeishuAdapter`、`WebAdapter`）统一委托 `ConversationService`
- `MarketSessionManager` 作为唯一真相源
- `FeishuMemory` 主路径依赖完全移除并标记 deprecated
- `LangGraph checkpointer` 暂不作为主记忆方案

### 主要变更

| 文件 | 变更要点 |
|------|----------|
| `services/conversation_service.py` | 新增，封装 `save_user_message → get_recent_messages → agent.invoke(history=...) → extract_reply_text → save_reply` |
| `api/routes.py` | Web 真入口改为调用 `ConversationService`，不再直接手写记忆逻辑 |
| `adapters/feishu_adapter.py` | 移除 `_handle_text_message` 中的直接 `save_message` / `load_history_window` 调用，改用 `ConversationService` |
| `adapters/web_adapter.py` | 改写为薄封装，内部仅委托 `ConversationService` |
| `app_factory.py` | `create_runtime_services()` 不再构造 `FeishuMemory`，`RuntimeServices.feishu_memory` 置 `None` |
| `core/router.py` | 历史加载改用 `MarketSessionManager`，删除临时 `new FeishuMemory(...)` |
| `memory/feishu_memory.py` | 添加 `@deprecated` 文档字符串 |
| `scripts/verify_web_memory.py` | 新增本地验证脚本（真实 HTTP 调用 `/api/agent/run`） |

### 验收结果

- ✅ Web `/chat` 同 `session_id` 连续两轮对话，第二轮正确引用第一轮内容
- ✅ 飞书同 `open_id`（映射为 `feishu_{open_id}`）连续对话记忆正常
- ✅ `Router` 可通过 `MarketSessionManager` 读取统一历史
- ✅ 全局搜索确认：主路径已无 `FeishuMemory(...)` 实例化
- ✅ 验证脚本 `scripts/verify_web_memory.py` 执行通过

### 提交信息

```
refactor: 新增 ConversationService 作为唯一会话编排层

- services/conversation_service.py: 唯一负责 save → load history → invoke → extract → save_reply
- api/routes.py、FeishuAdapter、WebAdapter 全部改为调用该 service
- 禁止在入口层重复实现记忆读写编排
- 保留 adapter 只做协议适配和 session_id 映射
```

---

## 部署支持：Dockerfile + docker-compose

**日期**: 2026-06-05

### 新增文件

- `Dockerfile`：基于 `python:3.11-slim`，内置 Node.js 20（支持 yanbaoke 研报搜索）
- `.dockerignore`：排除虚拟环境、缓存、日志、输出目录等
- `docker-compose.yml`：包含 `app` + `db`（PostgreSQL）服务，支持一键启动

### 使用方式

```bash
# 构建并启动
docker compose up --build

# 后台运行
docker compose up -d
```

### 提交信息
```
feat: add Dockerfile and docker-compose for deployment

- Dockerfile with Python 3.11 + Node.js 20
- .dockerignore for clean builds
- docker-compose.yml with app + postgres
```

---
