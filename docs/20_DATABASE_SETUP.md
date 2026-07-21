# PostgreSQL 初始化指南

本文用于在一台新电脑上克隆项目后，快速启用分析快照和新版模拟单三表：
`journal_ideas`、`paper_orders`、`journal_events`。

## 适用范围

- 使用全新的 PostgreSQL 数据库。
- 使用当前仓库的 SQLAlchemy 模型建表。
- 不适用于从旧版 `Stock_Analysis` 或其他来源复制过来的数据库。

旧库可能存在同名但不同列、约束或 Alembic 版本链。先备份，再单独制定迁移方案；不要直接对旧库执行下面的初始化命令。

## 1. 准备 PostgreSQL

本地已安装 PostgreSQL 时，创建数据库和应用账户：

```sql
CREATE USER marketass WITH PASSWORD 'change-me';
CREATE DATABASE marketass OWNER marketass;
```

也可直接使用项目的 Compose 配置仅启动数据库：

```bash
docker compose -f ops/docker-compose.yml up -d db
```

该 Compose 默认数据库为 `stock_analysis`，用户为 `stock_user`。生产环境请修改密码，并不要把包含真实凭证的配置提交到 Git。

## 2. 配置本地 DSN

复制模板：

```bash
cp runtime/config/analysis_defaults.example.yaml runtime/config/analysis_defaults.yaml
```

在 `runtime/config/analysis_defaults.yaml` 填写数据库连接。驱动名必须使用 `postgresql+psycopg`：

```yaml
database:
  postgres:
    dsn: "postgresql+psycopg://marketass:change-me@127.0.0.1:5432/marketass"
```

安装项目依赖后，`psycopg` 会由 `requirements.txt` 一并安装：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. 初始化当前模型

从仓库根目录执行：

```bash
PYTHONPATH="$PWD/runtime:$PWD/src:$PWD" python -c \
  "from infrastructure.persistence.db import init_db; init_db(); print('database initialized')"
```

`init_db()` 会按当前 SQLAlchemy 模型创建不存在的表和索引；可重复执行。Web 服务和飞书服务启动时也会尝试调用它，但首次部署建议显式运行一次，以便立刻发现 DSN、网络或权限问题。

不要对新环境执行 `alembic upgrade/downgrade/stamp`。仓库中的历史 Alembic 链不作为当前新库初始化入口。

## 4. 验证

执行下列命令确认关键表均可见：

```bash
PYTHONPATH="$PWD/runtime:$PWD/src:$PWD" python - <<'PY'
from sqlalchemy import inspect
from infrastructure.persistence.db import get_engine

required = {
    "analysis_snapshots",
    "journal_ideas",
    "paper_orders",
    "journal_events",
}
tables = set(inspect(get_engine()).get_table_names())
missing = sorted(required - tables)
if missing:
    raise SystemExit(f"missing tables: {missing}")
print("database ready")
PY
```

然后启动服务：

```bash
bash scripts/web_dev.sh
```

访问 `http://localhost:8000/chat`。当用户确认创建模拟单时，系统会在一个事务中写入一条 `journal_ideas`、一条 `paper_orders` 和一条 `journal_events`。

## 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `未配置 database.postgres.dsn` | 检查本地 `analysis_defaults.yaml` 是否存在，且 `database.postgres.dsn` 非空。 |
| 连接被拒绝 | 确认 PostgreSQL 已启动、端口和主机正确；Docker 场景检查 `docker compose ... ps`。 |
| 权限不足 | 应用账户至少需要目标数据库的建表、建索引和读写权限。 |
| 表存在但缺少列 | 这通常是旧库 schema 漂移。停止应用写入，备份后单独迁移；不要用新库初始化命令覆盖处理。 |
| 聊天仍可用但模拟单报错 | 数据库初始化在服务启动中是 best-effort；查看启动日志中的 `[DB] 初始化跳过` 原因。 |
