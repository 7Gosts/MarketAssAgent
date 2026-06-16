# 目录聚类迁移三阶段执行清单（已完成）

**日期**: 2026-06-16  
**目标**: 从“顶层扁平 + shim 并存”迁移到“清晰分层 + 单一路径”。  
**当前状态**: 阶段 1、2、3 均已落地，文档保留为执行归档。

---

## 阶段 1：统一 import 到 canonical 路径（低风险）

### 要做什么

- 全仓替换旧路径 import：
  - `adapters.*` -> `app.adapters.*`
  - `renderers.*` -> `interfaces.renderers.*`
  - `presenters.*` -> `interfaces.presenters.*`
- 保留 shim 文件不删，仅做兼容。
- 给 shim 增加 deprecation 警告，避免新增引用继续走旧路径。

### 执行命令

```bash
# 1) 盘点旧 import
rg -n "from (adapters|renderers|presenters)\.|import (adapters|renderers|presenters)\." -g "*.py"

# 2) 盘点 canonical 使用情况（对比迁移进度）
rg -n "from (app\.adapters|interfaces\.renderers|interfaces\.presenters)\.|import (app\.adapters|interfaces\.renderers|interfaces\.presenters)\." -g "*.py"

# 3) 回归测试
python3 -m pytest -q
```

### 重点改动文件（按当前仓库）

- `cli/feishu_bot.py`
- `adapters/feishu_longconn.py`
- `scripts/smoke_feishu_renderer.py`
- 其他 `rg` 命中的旧路径引用文件

### 阶段验收条件

- 业务代码中不再直接引用 `adapters.*` / `renderers.*` / `presenters.*`（shim 自身除外）。
- `python3 -m pytest -q` 全量通过。
- README 与架构文档没有新增旧路径示例。

---

## 阶段 2：聚类顶层目录（中风险）

### 要做什么

- 收拢入口层目录：
  - `api/` -> `app/api/`
  - `app_factory.py` -> `app/factory.py`
- 保留短期兼容入口（例如根目录 `app_factory.py` 仅转发导入）。
- 对 `cli/*`、`scripts/*`、`tests/*` 做路径更新。

### 执行命令

```bash
# 1) 盘点对旧入口的依赖
rg -n "from app_factory import|import app_factory|from api\.|import api\." -g "*.py"

# 2) 迁移后检查新路径使用
rg -n "from app\.factory import|from app\.api\.|import app\.api\." -g "*.py"

# 3) 启动链路检查
python3 cli/api_server.py --help || true
python3 cli/feishu_bot.py --help || true

# 4) 回归测试
python3 -m pytest -q
```

### 重点改动文件

- `cli/api_server.py`
- `cli/feishu_bot.py`
- `scripts/web_dev.sh`
- `scripts/feishu_dev.sh`
- `tests/*` 中涉及 `app_factory` / `api.routes` 的文件
- `app/__init__.py`、新建 `app/factory.py`、`app/api/*`

### 阶段验收条件

- 顶层入口路径已收敛到 `app/*`。
- 根目录只保留向后兼容桥接（如有）且有删除计划。
- 两条启动链路可正常起服务；全量测试通过。

---

## 阶段 3：删除 shim 与遗留目录（中高风险）

### 要做什么

- 删除兼容目录与旧实现：
  - `adapters/`（仅保留必要兼容时可暂留 `feishu_longconn.py`，并尽快迁移）
  - `renderers/`
  - `presenters/`
  - 评估后删除 `memory/feishu_memory.py`
- 清理文档中所有旧路径。
- 增加 CI 检查，防止旧 import 回流。

### 执行命令

```bash
# 1) 删除前最后检查（必须为空）
rg -n "from (adapters|renderers|presenters)\.|import (adapters|renderers|presenters)\." -g "*.py"

# 2) 删除目录后检查导入错误
python3 -m pytest -q

# 3) 可选：加防回流检查（pre-commit/CI）
rg -n "from (adapters|renderers|presenters)\.|import (adapters|renderers|presenters)\." -g "*.py" && exit 1 || true
```

### 重点改动文件

- 删除目录：`adapters/`、`renderers/`、`presenters/`
- `pyproject.toml`（若需加 lint/CI 钩子）
- `.github/workflows/*`（若已有 CI，可增加 grep 守卫）
- `docs/00_PROJECT_ARCHITECTURE.md`、`README.md`

### 阶段验收条件

- 仓库不再存在 shim 目录。
- 全仓旧 import 为 0。
- 全量测试通过；启动链路通过。
- 文档示例全部为 canonical 路径。

---

## 结果归档

本计划按 1 -> 2 -> 3 顺序执行完毕。最终状态：

1. 旧 import 已全量替换为 canonical 路径。
2. `api/` 与 `app_factory.py` 已收敛到 `app/api/*` 与 `app/factory.py`。
3. shim 与旧兼容目录已删除。

历史建议提交消息格式（归档）：

- `refactor(step1): replace legacy imports with canonical paths`
- `refactor(step2): move api/factory under app package`
- `refactor(step3): remove shim packages and legacy memory adapter`
