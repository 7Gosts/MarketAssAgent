# 行情工具 LLM 展示层中文替换方案

## 状态

第一阶段已落地。

`MarketFacts` 只负责行情工具结果的顶层结构约束；中文替换是另一件事，不能混进行情计算、快照写入或历史快照读取。

## 需求收敛

要解决的问题是：LLM 最终回复里不要出现 `break_up`、`break_down`、`inside` 这类工具内部枚举词。

这不是行情工具全字段中文化，也不是行情结果 schema 重建。目标只有一个：工具结果被包装成 LLM 可见的 tool message 前，把少量明确展示枚举替换成中文。

## 正确分层

行情工具内部结果保持原样：

- 计算逻辑继续使用现有字段和值。
- 快照、数据库、历史快照和内部上下文摘要继续使用原始事实。
- 类化约束只约束结构和类型，不负责中文展示。

LLM 展示层单独处理：

- 只处理传给 LLM 的副本。
- 只处理明确字段。
- 未知值原样保留，不能抛错导致工具失败。

## 第一阶段已落地替换

只处理 `analyze_market` 单标的结果中的三个明确路径：

```python
analysis["ma_regime"]
analysis["ma_alignment"]
analysis["recent_candles"][i]["event"]
```

其他字段先不动。`source`、`resolution.source`、`direction`、`volume_tag`、`role`、`strength` 都不在第一阶段处理范围内，除非再次确认它们确实稳定出现在最终回复且需要由工具侧替换。

## 建议落点

中文替换放在 tool message 包装边界，而不是 `analyze_market` 的领域计算结果里。

当前代码中，工具结果转成 LLM 可见字符串的位置是 `src/core/message_protocol.py` 的 `tool_message()`。第一阶段没有新增通用展示系统，只在 `name == "analyze_market"` 且结果为 `dict` 时调用 `_present_analyze_market_result()`。

## 非目标

- 不新增通用中文化系统。
- 不递归扫描所有同名字段。
- 不为多请求比较结果做额外展示改写。
- 不新增嵌套 Pydantic schema。
- 不翻译 `source` 这类跨业务复用字段。
- 不让展示层转换影响工具执行状态。
- 不用测试锁死中文文案或提示词。

## 验收

- `analyze_market` 原始返回结构和快照写入逻辑不因展示替换改变。
- LLM 可见 tool message 中，`analysis.ma_regime`、`analysis.ma_alignment` 与 `recent_candles[*].event` 使用中文展示值。
- 如果出现未知 event 值，原样传给 LLM，不抛异常。
- 不再出现 `resolution.source=catalog_alias` 导致 `analyze_market` 失败的问题。
