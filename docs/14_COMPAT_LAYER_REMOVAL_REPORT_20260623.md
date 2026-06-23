# Phase 16 兼容层彻底移除报告（2026-06-23）

## 摘要

移除最后遗留的 `interfaces/` 目录与 `domain/market/analysis.py` 聚合 re-export，全部改为新分层 canonical import。  
测试：**64 passed**；CI guard：**passed**。

---

## 删除

| 路径 | 说明 |
| --- | --- |
| `interfaces/` 整个目录 | 渲染器已实迁 |
| `domain/market/analysis.py` | 纯 re-export 兼容层，调用方直连子模块 |

## 实迁

| 旧路径 | 新路径 |
| --- | --- |
| `interfaces/renderers/feishu_renderer.py` | `infrastructure/adapters/renderers/feishu_renderer.py` |
| `interfaces/renderers/base.py` | `infrastructure/adapters/renderers/base.py` |
| `interfaces/presenters/web_presenter.py` | （Phase 15 已在）`application/presenters/web_presenter.py` |

## Import 变更

| 旧 import | 新 import |
| --- | --- |
| `interfaces.renderers.feishu_renderer` | `infrastructure.adapters.renderers.feishu_renderer` |
| `domain.market.analysis` | `domain.market.analysis_service` / `domain.market.structure` |
| `application.presenters.web_presenter` | `application.presenters`（包级 export） |

## CI Guard

- 新增禁止路径：`interfaces/`
- 新增禁止 import：`from interfaces.*`

## 当前 canonical 分层

```text
domain/          → 业务逻辑 + LangChain tools
application/     → ConversationService / envelope / WebPresenter
infrastructure/  → adapters + renderers + persistence + memory
app/             → factory + api routes（无 adapters 子目录）
tools/registry   → 直接 import domain.*
```
