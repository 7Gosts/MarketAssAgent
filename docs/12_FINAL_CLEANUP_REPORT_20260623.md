# Phase 14 最终清理报告（2026-06-23）

## 1. 执行摘要

分支：`final-cleanup-20260616`  
目标：移除兼容层、修复测试、精简 domain 结构。  
结果：**64 passed, 1 skipped, 0 failed**；CI guard 通过。

---

## 2. 删除的文件/目录

| 路径 | 说明 |
| --- | --- |
| `tools/technical_analysis.py` | Facade 已删除，逻辑在 `domain/market/` |
| `tools/user_profile.py` | Facade 已删除，逻辑在 `domain/profile/user_profile.py` |
| `services/` | 实迁至 `application/services/` |
| `persistence/` | 实迁至 `infrastructure/persistence/` |
| `memory/` | 实迁至 `infrastructure/memory/` |
| `app/adapters/` | 实迁至 `infrastructure/adapters/` |
| `application/presenters/web_presenter.py` | 冗余 wrapper（主路径用 `interfaces/presenters/`） |

> `formatters/` 目录在仓库中本不存在，无需删除。

---

## 3. 实迁与重构

### 3.1 Application 层

- `application/services/conversation_service.py` — 完整实现（原 `services/`）
- `application/services/envelope_builder.py` — 完整实现

### 3.2 Infrastructure 层

- `infrastructure/adapters/*` — Feishu/Web 适配器完整实现
- `infrastructure/persistence/*` — db / models / journal_repository
- `infrastructure/memory/*` — session / snapshot / json 持久化

### 3.3 Domain 层拆分

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `domain/market/indicators.py` | ~258 | MA、关键位、斐波那契、量价结构 |
| `domain/market/structure.py` | ~593 | Swing/Wyckoff/市场结构 v2 |
| `domain/market/patterns.py` | ~41 | 形态识别 v2 |
| `domain/market/analysis_service.py` | ~690 | 编排 + LangChain `@tool` 入口 |
| `domain/market/analysis.py` | ~38 | 公共 API 聚合导出 |

原单文件 `analysis.py`（1537 行）已拆分为上述模块。

---

## 4. Import 路径变更（调用方须使用新路径）

| 旧路径 | 新路径 |
| --- | --- |
| `tools.technical_analysis` | `domain.market.analysis` 或 `domain.market.analysis_service` |
| `tools.user_profile` | `domain.profile.user_profile` |
| `services.conversation_service` | `application.services.conversation_service` |
| `services.envelope_builder` | `application.services.envelope_builder` |
| `persistence.*` | `infrastructure.persistence.*` |
| `memory.*` | `infrastructure.memory.*` |
| `app.adapters.*` | `infrastructure.adapters.*` |

---

## 5. 依赖与测试

- `requirements.txt` 新增 `psycopg[binary]>=3.1.0`，修复 PostgreSQL 相关 2 个失败用例
- 全量：`python3 -m pytest -q` → **64 passed, 1 skipped**
- Guard：`python3 scripts/guard_no_legacy_memory_path.py` → **passed**
  - 新增禁止旧路径 import（`services/`、`persistence/`、`memory/`、`app/adapters/`、`tools/*` facade）

---

## 6. 修改的测试文件

- `tests/test_analysis_output_sanitize.py` — import/patch 指向 `domain.market.*`
- `tests/test_user_profile_tools_injection.py` — import 指向 `domain.profile.*`
- `tests/test_runtime_memory_api_default.py` — 同上
- `tests/test_memory_api_backend_selection.py` — 同上
- `tests/test_agent_journal.py` — patch 指向 `infrastructure.persistence.*`
- 其余测试 import 批量更新至 `application.*` / `infrastructure.*`

---

## 7. 保留项（有意未动）

- `schemas/conversation.py` — `ConversationEnvelope` 仍为对外契约；`blocks` 字段保留但恒空（Markdown-first）
- `interfaces/presenters/`、`interfaces/renderers/` — 传输层渲染仍在此目录
- `application/presenters/__init__.py` — 仅 re-export `interfaces`，可后续合并

---

## 8. 后续可选优化

1. 将 `interfaces/presenters` 合并进 `application/presenters` 并删除 wrapper
2. 更新 `docs/00_PROJECT_ARCHITECTURE.md` 中仍引用旧路径的表格行
3. Prompt 迭代 / Trace 持久化（独立于本次清理）
