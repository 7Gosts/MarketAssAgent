# MarketAssAgent — 独立 AI 行情分析 Agent

基于 LangGraph ReAct 架构的金融行情分析助手，支持加密货币、A股、美股、贵金属等多市场技术分析。

## 快速开始

### 1. 配置密钥

```bash
cp config/analysis_defaults.example.yaml config/analysis_defaults.yaml
# 编辑 config/analysis_defaults.yaml，填入 LLM API 密钥、飞书凭证等
```

或使用环境变量（见 `.env.example`）：

```bash
cp .env.example .env
# 编辑 .env，填入密钥
```

### 2. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
```

### 3. 运行

```bash
# 交互模式
python cli/run.py

# 单轮模式
python cli/run.py "BTC_USDT 行情分析"
```

### 4. VS Code 调试

在 VS Code 中打开 MarketAssAgent/ 目录，按 F5 即可启动 REPL 调试。

可选配置：
- `MarketAssAgent REPL` — 交互模式
- `MarketAssAgent 单轮` — 单次查询
- `行情分析 CLI` — 行情分析工具
- `飞书 Bot` — 飞书机器人

## 项目结构

```
MarketAssAgent/
├── config/          # 配置文件（YAML、JSON、runtime_config.py）
├── app/             # 业务逻辑（capabilities、executors、session）
├── analysis/        # K 线分析、指标计算
├── persistence/     # 数据库交互（PostgreSQL）
├── intel/           # 研报检索（需 Node.js）
├── sql/             # SQL 查询文件
├── tools/           # LLM 客户端、飞书、行情数据源
├── cli/             # 命令行入口
├── core/            # LangGraph Agent 核心（state、graph、prompt、supervisor）
├── memory/          # Session 管理、Snapshot 提取
└── .vscode/         # VS Code 调试配置
```

## 核心配置

| 配置项 | 文件 | 说明 |
|--------|------|------|
| LLM 密钥 | `config/analysis_defaults.yaml` → `llm.providers` | API key、base_url、model |
| 飞书凭证 | 同上 → `feishu` | app_id、app_secret |
| 资产 catalog | `config/market_config.json` | symbol → provider 映射 |
| PostgreSQL | 同上 → `database.postgres` | DSN、连接池 |

**环境变量覆盖**：`LLM_API_KEY`、`LLM_PROVIDER`、`LLM_MODEL` 等可覆盖 YAML 配置。

## 可选依赖

- **PostgreSQL**：模拟账户功能需要，无数据库时 sim_account 工具将返回空数据
- **Node.js**：研报检索功能需要（`tools/yanbaoke/scripts/`），无 Node.js 时 research 工具将返回空数据

## 合规声明

仅供技术分析与程序化演示，不构成投资建议。# MarketAssAgent
# MarketAssAgent
