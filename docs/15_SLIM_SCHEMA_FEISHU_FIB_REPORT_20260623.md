# Phase 17 瘦身报告（2026-06-23）

## 1. Schema 瘦身

`ConversationEnvelope` 现为 Markdown-first 最小契约：

```python
class ConversationEnvelope(BaseModel):
    version: str = "1.2"
    reply_text: str
    meta: dict[str, Any]
    raw: dict[str, Any]
```

已删除：`ConversationBlock`、`BlockType`、`DeliveryHint`、`blocks`、`delivery_hint`、`meta.has_rich_content`、`meta.block_summary`。

Web 前端（`web/app.js`）仅使用 `envelope.reply_text`，无 breaking change。

## 2. FeishuAdapter 瘦身

- 构造函数移除未使用的 `agent: MarketReActAgent` 参数
- 现为：`FeishuAdapter(conversation_service=ConversationService, fallback_to_template=True)`
- `app/factory.py` 同步更新

## 3. 斐波那契工具恢复

- `domain/market/analysis_service.py` 重新添加 `@tool analyze_fibonacci`
- 已注册进 `tools/registry.get_all_tools()`
- `core/prompt.py` 补充「仅斐波那契专用请求时调用」规则
- K 线解析改为 dict 格式（与 `market_data` 一致）

## 4. 文档同步

- `docs/00_PROJECT_ARCHITECTURE.md`：测试表更新为 `scripts/fetch_au0_daily.py`、`scripts/real_tool_calling_check.py`；Markdown-first 描述更新
- `docs/03_ARCH_REFACTOR_TODO.md`：Schema 瘦身项标记完成
- `README.md`：API 响应示例更新

## 5. 测试

`64 passed`；guard passed。
