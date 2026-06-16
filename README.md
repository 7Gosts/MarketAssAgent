# MarketReActAgent

基于 LangGraph + ReAct 架构的金融市场智能 Agent，支持股票、加密货币、黄金的技术分析、多轮对话、条件化建议和纸账户模拟。

## 核心特性

- LangGraph 状态机驱动的多轮 ReAct 流程（支持真正的 Tool Calling）
- ConversationService + MarketSessionManager 统一会话记忆（Web / 飞书共用同一编排链）
- RuntimeServices 单例化装配（`app/factory.py` 为唯一运行时装配点）
- AnalysisSnapshot 机制（保存分析快照，辅助追问上下文）
- 条件化交易建议 + 严格免责声明
- 支持真实研报搜索（基于 yanbaoke）
- 支持 PostgreSQL + Alembic 数据库持久化
- 支持飞书长连接 + Web `/chat` 多入口
- 支持 A 股 / 美股 / 港股、加密货币、沪金连续 AU0 等多市场行情

## 环境要求

- Python >= 3.11
- Node.js >= 18（研报搜索功能必需）
- PostgreSQL（可选，用于持久化交易记录）
- OpenAI / DeepSeek API Key

## 快速开始

### 1. 克隆项目并安装依赖

```bash
git clone https://github.com/7Gosts/MarketAssAgent.git
cd MarketAssAgent

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量（重点）

复制环境变量模板：

```bash
cp .env.example .env
```

编辑 `.env` 文件，**至少配置 LLM Provider、模型名和 API Key**：

```env
# 示例 1：OpenAI-compatible Provider
LLM_PROVIDER=openai
LLM_MODEL=your-model-name
OPENAI_API_KEY=sk-your-api-key

# 示例 2：DeepSeek
# LLM_PROVIDER=deepseek
# LLM_MODEL=your-model-name
# DEEPSEEK_API_KEY=sk-your-api-key

# 数据库配置（可选）
DATABASE_URL=postgresql+psycopg://stock_user:111@127.0.0.1:5432/stock_analysis
```

> **说明**：项目运行时会从 `analysis_defaults.yaml` 和环境变量读取 LLM 配置；请显式设置 `LLM_MODEL` 或对应 Provider 的 `*_MODEL`。如需手动注入模型实例，可在创建 `MarketReActAgent(...)` 时传入。

### 3. 初始化数据库（推荐）

```bash
# 创建表结构
alembic upgrade head
```

### 4. 安装 Node.js（研报搜索功能必需）

```bash
# Ubuntu/Debian
sudo apt install -y nodejs npm
```

### 5. 启动项目

```bash
python cli/api_server.py
```

访问 `http://localhost:8000/chat` 使用 Web 聊天页，或访问 `http://localhost:8000` 检查服务状态。

开发环境也可以使用脚本启动：

```bash
bash scripts/web_dev.sh
```

## 部署指南

### Docker 部署（推荐）

```bash
# 构建镜像
docker build -t marketreagent .

# 运行容器
docker run -p 8000:8000 --env-file .env marketreagent
```

### 生产环境建议

- 使用 `gunicorn` 或 `uvicorn` 配合 systemd / supervisor 管理进程
- 配置 PostgreSQL 并执行 `alembic upgrade head`
- 通过环境变量管理所有密钥（不要提交到 Git）

## 飞书接入

如果你没有公网 `HTTPS` 回调地址，可以直接使用飞书长连接模式：

```bash
python3 cli/feishu_bot.py
```

或：

```bash
bash scripts/feishu_dev.sh
```

当前项目的飞书接入只保留长连接模式。它会主动连接飞书服务器收消息，不依赖公网回调地址。前提是已在 `config/analysis_defaults.yaml` 或环境变量中配置 `feishu.app_id` / `feishu.app_secret`，并安装了 `lark-oapi` 依赖。

飞书和 Web 共用同一套会话记忆编排：入口只负责协议适配与 `session_id` 映射，消息保存、历史读取、Agent 调用、回复保存统一由 `ConversationService` 处理。

## API 使用示例

```bash
curl -X POST http://localhost:8000/api/agent/run \
  -H "Content-Type: application/json" \
  -d '{"text": "BTC_USDT 4h 行情分析", "session_id": "test"}'
```

接口返回统一 envelope 根对象：

```json
{
  "envelope": {
    "version": "1.0",
    "reply_text": "主文本回复",
    "blocks": [],
    "meta": {},
    "raw": {},
    "delivery_hint": {}
  }
}
```

同一个 `session_id` 会读取最近对话历史，支持连续追问。

可用本地脚本验证 Web 真实入口记忆：

```bash
python scripts/verify_web_memory.py
```

## 运行时与会话记忆

项目的运行时对象统一由 `app/factory.py` 装配：

- `RuntimeServices` 持有唯一的 `MarketReActAgent`
- `RuntimeServices` 持有唯一的 `MarketSessionManager`
- `RuntimeServices` 持有唯一的 `ConversationService`
- `app/api/routes.py`、`FeishuAdapter`、`WebAdapter` 均通过依赖注入使用这些服务

会话链路统一为：

```text
入口(Web / Feishu)
  -> ConversationService
  -> MarketSessionManager 读取最近历史
  -> MarketReActAgent / chat invoke_fn
  -> ConversationService 提取回复并保存
```

`FeishuMemory` 旧实现已移除，主路径仅保留 `MarketSessionManager` 统一会话管理。

**记忆后端说明（当前默认）**：

- **短期会话**：JSON/JSONL（`~/.marketassagent/sessions/`），无需 PostgreSQL
- **长期记忆 / 用户画像**：本地 JSON（`memory.backend: json`，**MemoryAPI 默认启用**）
  - 文件：`memory_facts.jsonl`、`memory_checkpoints.json`
- **PostgreSQL**：用于 journal/account 等原有 persistence；`memory.backend: postgres` 为可选 FactStore 后端
- **SQLite memory backend 已移除**；遗留的 `memory_store.sqlite3` 可安全删除（无迁移）

详细记忆架构说明见：`docs/06_AGENT_MEMORY_ARCHITECTURE.md`  
文档总索引见：`docs/README.md`

## 运行产物目录

默认情况下，运行产物会写入用户目录而不是仓库目录，避免项目根目录越跑越乱：

- `~/.marketassagent/sessions`
- `~/.marketassagent/debug`
- `~/.marketassagent/output`

可通过环境变量覆盖根目录：

```bash
export MARKETASSAGENT_DATA_DIR=/your/runtime/data/dir
```

## 目录结构

完整分层说明、流程图与「核心 / 辅助 / 测试 / 脚本」分类见 **[`docs/00_PROJECT_ARCHITECTURE.md`](docs/00_PROJECT_ARCHITECTURE.md)**。

```
MarketAssAgent/
├── app/               # ★ 运行时装配 + 传输适配（factory / adapters / api）
├── services/          # ★ 会话编排（ConversationService）+ envelope 组装
├── core/              # ★ Agent 核心（graph / agent / planner / orchestrator / memory_api）
├── tools/             # ★ LangChain 工具（分析 / 行情 / 研报 / 画像）
├── memory/            # ○ 短期 JSON 会话持久化
├── interfaces/        # △ 渠道渲染（FeishuRenderer / WebPresenter）
├── persistence/       # ○ PostgreSQL Journal / Account
├── config/            # ○ 配置（runtime_config 为唯一读取入口）
├── cli/               # △ 进程入口（api_server / feishu_bot）
├── web/               # △ 静态聊天页
├── tests/             # 自动化测试（非生产）
├── scripts/           # 开发脚本 + CI guard（非生产）
└── docs/              # 架构文档
```

图例：★ 核心 · ○ 辅助 · △ 传输层（薄，不含业务决策）

## 行情数据源

- A 股 / 美股 / 港股：TickFlow
- 加密货币：Gate.io REST API
- 国内黄金：AKShare 新浪期货接口，默认使用沪金连续 `AU0`

## 注意事项

- 研报搜索功能需要 Node.js 环境
- 数据库持久化功能需要正确配置 PostgreSQL DSN
- LLM Provider、模型名、base_url、api_key 可通过 `analysis_defaults.yaml` 或环境变量切换
- Web 记忆回归脚本需要先启动 API 服务

## 合规声明

仅供技术分析与程序化演示，不构成投资建议。
