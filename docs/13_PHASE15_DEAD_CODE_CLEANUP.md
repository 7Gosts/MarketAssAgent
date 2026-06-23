# Phase 15 死代码清理报告（2026-06-23）

## 摘要

在 Phase 14 分层落地后，本轮移除**未接入主链路**的代码与占位模块。  
测试：**64 passed**；CI guard：**passed**。

---

## 删除项

| 路径 | 原因 |
| --- | --- |
| `infrastructure/adapters/web_adapter.py` | 已装配但 `routes.py` 从未调用；Web 直连 `ConversationService` |
| `interfaces/renderers/web_renderer.py` | 恒等 `return content`，仅被已删 WebAdapter 使用 |
| `interfaces/presenters/` | 已实迁至 `application/presenters/web_presenter.py` |
| `domain/trading/` | 占位 stub，全仓库零 import |
| `tests/test_real_tool_calling.py` | 全文件 skip 的手动脚本 → `scripts/real_tool_calling_check.py` |
| `tests/test_au0_akshare.py` | 非 pytest 用例 → `scripts/fetch_au0_daily.py` |

## 移除的死代码（方法/函数）

| 位置 | 符号 | 原因 |
| --- | --- | --- |
| `application/services/envelope_builder.py` | `EnvelopeBuilder.build_from_text()` | 零调用 |
| `infrastructure/memory/snapshot.py` | `SnapshotManager.clear_snapshot()` | 零调用 |
| `domain/market/analysis_service.py` | `analyze_fibonacci()`、`get_technical_tools()` | 未注册进 `get_all_tools()`，Agent 不可达 |
| `domain/market/analysis_service.py` | 重复 `_calculate_fib_levels()` | 已在 `indicators.py` |
| `tools/registry.py` | `get_technical_tools()` | 零外部调用 |

## 迁移/合并

- `WebPresenter`：`interfaces/presenters/` → `application/presenters/web_presenter.py`
- `app/factory.py`：移除 `WebAdapter` 字段与装配
- `app/api/routes.py`：import 改为 `application.presenters`

## CI Guard 更新

- 禁止 `interfaces/presenters/` 目录复活
- 禁止 `from interfaces.presenters.* import`

---

## 有意保留（暂未删）

| 项 | 原因 |
| --- | --- |
| `schemas/conversation.py` 中 `ConversationBlock` / `blocks` | API 向前兼容；Web 客户端不消费但契约仍在 |
| `interfaces/renderers/feishu_renderer.py` | 飞书主链路在用 |
| `core/profile.py` | MemoryAPI 用户画像模型，与 `domain/profile` 职责不同 |

## 后续可选

1. 简化 `ConversationEnvelope` schema（需确认 API 消费者）
2. 更新 `docs/00_PROJECT_ARCHITECTURE.md` 中仍引用旧路径/已删测试的表格
3. `FeishuAdapter` 仍持有 `agent` 参数（部分为历史遗留），可单独瘦身
