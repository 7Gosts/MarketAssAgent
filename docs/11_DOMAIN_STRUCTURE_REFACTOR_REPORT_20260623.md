# 目录重构执行报告（2026-06-23）

## 1. 新目录结构（核心部分）

```text
MarketAssAgent/
├── core/
├── domain/
│   ├── market/
│   │   ├── analysis.py
│   │   ├── structure.py
│   │   ├── patterns.py
│   │   └── indicators.py
│   ├── profile/
│   │   └── user_profile.py
│   └── trading/
│       ├── trade_plan.py
│       └── position_review.py
├── application/
│   ├── services/
│   │   ├── conversation_service.py
│   │   └── envelope_builder.py
│   └── presenters/
│       └── web_presenter.py
├── infrastructure/
│   ├── adapters/
│   │   ├── feishu_adapter.py
│   │   ├── feishu_longconn.py
│   │   └── web_adapter.py
│   ├── persistence/
│   │   ├── db.py
│   │   ├── journal_repository.py
│   │   └── models.py
│   └── memory/
│       ├── json_persistence.py
│       ├── session_manager.py
│       ├── session_state.py
│       ├── session_store.py
│       └── snapshot.py
├── tools/
│   ├── technical_analysis.py
│   └── user_profile.py
└── app/
```

## 2. 主要迁移对照

| 迁移前 | 迁移后 | 说明 |
| --- | --- | --- |
| `tools/technical_analysis.py` | `domain/market/analysis.py` | 业务实现迁移；`tools` 保留 Facade 兼容入口 |
| `tools/user_profile.py` | `domain/profile/user_profile.py` | 业务实现迁移；`tools` 保留 Facade 兼容入口 |
| `services/conversation_service.py` | `application/services/conversation_service.py` | 新路径为兼容封装层（当前仍复用原实现） |
| `services/envelope_builder.py` | `application/services/envelope_builder.py` | 新路径为兼容封装层（当前仍复用原实现） |
| `app/adapters/*` | `infrastructure/adapters/*` | 新路径兼容封装，主链路 import 已切到新路径 |
| `persistence/*` | `infrastructure/persistence/*` | 新路径兼容封装 |
| `memory/*` | `infrastructure/memory/*` | 新路径兼容封装 |

## 3. 兼容策略

- 保留旧路径导入能力，避免一次性重构导致调用方全部失效。
- `tools/technical_analysis.py` 与 `tools/user_profile.py` 作为稳定门面层，保证外部 API 与测试 patch 点可用。
- 主链路入口已切换到新层级 import：
  - `app/factory.py`
  - `cli/feishu_bot.py`
  - `app/adapters/feishu_adapter.py`
  - `app/adapters/web_adapter.py`

## 4. 测试结果

### 4.1 重点回归（通过）

- `python3 -m pytest -q tests/test_analysis_output_sanitize.py tests/test_user_profile_tools_injection.py tests/test_direct_agent_context_flow.py tests/test_phase_c_memory_flow.py`
- 结果：`21 passed`

### 4.2 全量回归（环境依赖导致 2 失败）

- `python3 -m pytest -q`
- 结果：`62 passed, 1 skipped, 2 failed`
- 失败原因：
  - 缺少 PostgreSQL 驱动 `psycopg`（`ModuleNotFoundError: No module named 'psycopg'`）
  - 对应失败用例：`tests/test_journal_repository.py`、`tests/test_memory_api_backend_selection.py`（postgres backend 分支）

## 5. 遗留问题

1. 本次为低风险迁移，`application/*` 与 `infrastructure/*` 当前以兼容层为主，后续可逐步“反向实迁”移除旧路径依赖。
2. `domain/market/analysis.py` 仍是大文件，后续建议继续拆分纯函数到 `structure.py / patterns.py / indicators.py` 并让 `analysis.py` 只做编排。
3. 若要让全量测试在本地全绿，需要安装 `psycopg` 或在 CI 中隔离 postgres 相关用例环境。

