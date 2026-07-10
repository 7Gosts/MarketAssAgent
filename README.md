# MarketReActAgent

基于 LangGraph + ReAct 架构的金融市场智能 Agent，支持股票、加密货币、黄金的技术分析、多轮对话、条件化建议和纸账户模拟。

## 核心特性

- LangGraph 状态机驱动的多轮 ReAct 流程（支持真正的 Tool Calling）
- ConversationService + MarketSessionManager 统一会话记忆（Web / 飞书共用同一编排链）
- RuntimeServices 单例化装配（`runtime/app/factory.py` 为唯一运行时装配点）
- AnalysisSnapshot 机制（保存分析快照，辅助追问上下文）
- 条件化交易建议 + 严格免责声明
- 支持真实研报搜索（基于 yanbaoke）
- 预留 PostgreSQL + Alembic 持久化能力（当前非主运行链路）
- 支持飞书长连接 + Web `/chat` 多入口
- 支持 A 股 / 美股 / 港股、加密货币、沪金连续 AU0 等多市场行情

## 环境要求

- Python >= 3.11
- Node.js >= 18（研报搜索功能必需）
- PostgreSQL（暂缓接入；默认运行不需要）
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

### 2. 配置文件与环境变量

复制环境变量模板：

```bash
cp .env.example .env
```

复制配置模板并编辑本地配置：

```bash
cp runtime/config/analysis_defaults.example.yaml runtime/config/analysis_defaults.yaml
```

在 `runtime/config/analysis_defaults.yaml` 的 `llm` 段配置默认 provider 和对应 provider 的 `model/base_url/api_key`。该文件已被 Git 和 Docker 构建上下文忽略。

```yaml
llm:
  default_provider: "openai"
  providers:
    openai:
      base_url: "https://api.openai.com/v1"
      model: "gpt-4.1-mini"
      api_key: "sk-..."
```

`.env` 仍用于飞书等运行参数；数据库接入暂缓：

```env
FEISHU_APP_ID=your_app_id
FEISHU_APP_SECRET=your_app_secret
```

> **说明**：当前 LLM 运行参数统一来自 `analysis_defaults.yaml`。如需手动注入模型实例，可在创建 `MarketReActAgent(...)` 时直接传入 `llm`。

### 3. 数据库状态

当前默认使用 JSON/JSONL 保存会话与长期记忆，不需要数据库。PostgreSQL 迁移链尚待核对，现阶段不要直接执行 Alembic `upgrade/downgrade/stamp`；具体问题和后续方案见 [`docs/07_DATABASE_UNIFICATION_PLAN.md`](docs/07_DATABASE_UNIFICATION_PLAN.md)。

### 4. 安装 Node.js（研报搜索功能必需）

```bash
# Ubuntu/Debian
sudo apt install -y nodejs npm
```

### 5. 启动项目

```bash
bash scripts/web_dev.sh
```

访问 `http://localhost:8000/chat` 使用 Web 聊天页，或访问 `http://localhost:8000` 检查服务状态。

也可以直接启动 Python 入口：

```bash
PYTHONPATH="$PWD/runtime:$PWD/src:$PWD" python -m cli.api_server
```

## 部署指南

### Docker 部署（推荐）

```bash
# 构建镜像
docker build -f ops/Dockerfile -t marketreagent .

# 运行容器
docker run -p 8000:8000 --env-file .env \
  -e STOCK_ANALYSIS_CRYPTO_CONFIG=/run/marketass/analysis_defaults.yaml \
  -v "$PWD/runtime/config/analysis_defaults.yaml:/run/marketass/analysis_defaults.yaml:ro" \
  marketreagent
```

或使用 Compose（同样会只读挂载本地 `analysis_defaults.yaml`）：

```bash
docker compose -f ops/docker-compose.yml up --build
```

### 生产环境建议

- 使用 `gunicorn` 或 `uvicorn` 配合 systemd / supervisor 管理进程
- PostgreSQL 接入暂不作为部署前置条件，待迁移链核清后再启用
- 通过环境变量管理所有密钥（不要提交到 Git）

## 飞书接入

如果你没有公网 `HTTPS` 回调地址，可以直接使用飞书长连接模式：

```bash
PYTHONPATH="$PWD/runtime:$PWD/src:$PWD" python3 runtime/cli/feishu_bot.py
```

或：

```bash
bash scripts/feishu_dev.sh
```

当前项目的飞书接入只保留长连接模式。它会主动连接飞书服务器收消息，不依赖公网回调地址。前提是已在 `runtime/config/analysis_defaults.yaml` 或环境变量中配置 `feishu.app_id` / `feishu.app_secret`，并安装了 `lark-oapi` 依赖。

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
    "version": "1.2",
    "reply_text": "主文本回复",
    "meta": {},
    "raw": {}
  }
}
```

同一个 `session_id` 会读取最近对话历史，支持连续追问。

可用本地脚本验证 Web 真实入口记忆：

```bash
python scripts/verify_web_memory.py
```

## 运行时与会话记忆

项目的运行时对象统一由 `runtime/app/factory.py` 装配：

- `RuntimeServices` 持有唯一的 `MarketReActAgent`
- `RuntimeServices` 持有唯一的 `MarketSessionManager`
- `RuntimeServices` 持有唯一的 `ConversationService`
- `runtime/app/api/routes.py`、`FeishuAdapter` 均通过依赖注入使用 `ConversationService`

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
- **PostgreSQL**：保留 journal/account 和可选 FactStore 实现，但当前尚未完成迁移链与部署验收
- **SQLite memory backend 已移除**；遗留的 `memory_store.sqlite3` 可安全删除（无迁移）

详细记忆架构说明见：`docs/06_AGENT_MEMORY_ARCHITECTURE.md`  
文档总索引见：`docs/INDEX.md`

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

完整分层说明见 **[`docs/00_PROJECT_ARCHITECTURE.md`](docs/00_PROJECT_ARCHITECTURE.md)**。  
当前目录按源码、运行时资源、部署资源和开发脚本分组：

```
MarketAssAgent/
├── src/                     # 业务源码（core/domain/application/infrastructure/tools/schemas）
├── runtime/                 # 应用装配、进程入口、配置与 Web 静态资源
├── ops/                     # Docker、Compose 与 Alembic
├── scripts/                 # 开发、验证和运维脚本
├── tests/                   # 自动化测试
└── docs/                    # 架构与演进文档
```

## 行情数据源

- A 股 / 美股 / 港股：AKShare
- 加密货币：Gate.io REST API
- 国内黄金：AKShare 新浪期货接口，默认使用沪金连续 `AU0`

## 注意事项

- 研报搜索功能需要 Node.js 环境
- 数据库持久化功能需要正确配置 PostgreSQL DSN
- LLM Provider、模型名、base_url、api_key 可通过 `analysis_defaults.yaml` 或环境变量切换
- Web 记忆回归脚本需要先启动 API 服务

## 合规声明

仅供技术分析与程序化演示，不构成投资建议。
