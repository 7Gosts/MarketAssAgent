# MarketReActAgent

基于 LangGraph + ReAct 架构的金融市场智能 Agent，支持股票、加密货币、黄金的技术分析、多轮对话、条件化建议和纸账户模拟。

## 核心特性

- LangGraph 状态机驱动的多轮 ReAct 流程（支持真正的 Tool Calling）
- AnalysisSnapshot 机制（解决追问上下文丢失）
- 条件化交易建议 + 严格免责声明
- 支持真实研报搜索（基于 yanbaoke）
- 支持 PostgreSQL + Alembic 数据库持久化
- 支持飞书 + Web 多入口

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

> **说明**：项目运行时会从 `analysis_defaults.yaml` 和环境变量读取 LLM 配置；请显式设置 `LLM_MODEL` 或对应 Provider 的 `*_MODEL`。如果你要手动注入模型实例，也可以在创建 `MarketReActAgent(...)`、`Router(...)`、`Writer(...)` 时自行传入。

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

访问 `http://localhost:8000` 测试服务。

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

## API 使用示例

```bash
curl -X POST http://localhost:8000/api/agent/run \
  -H "Content-Type: application/json" \
  -d '{"text": "BTC_USDT 4h 行情分析", "session_id": "test"}'
```

## 目录结构

```
MarketAssAgent/
├── core/              # LangGraph 核心（state, graph, agent）
├── tools/             # 工具层（technical_analysis, research, yanbaoke）
├── api/               # HTTP 接口
├── adapters/          # Feishu / Web 适配器
├── cli/               # CLI / 长连接 / HTTP 启动入口
├── persistence/       # 数据库模型 + Repository + Alembic
├── memory/            # Snapshot 管理
├── config/            # 配置
├── tests/             # 测试
├── alembic/           # 数据库迁移
└── app_factory.py
```

## 注意事项

- 研报搜索功能需要 Node.js 环境
- 数据库持久化功能需要正确配置 PostgreSQL DSN
- 默认使用 OpenAI 模型，可通过环境变量或代码切换

## 合规声明

仅供技术分析与程序化演示，不构成投资建议。
