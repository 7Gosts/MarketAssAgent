# 行情分析轻量快照记忆改造计划

**日期**: 2026-07-10  
**状态**: 最小版本已实施  
**目标**: 为每次行情分析保存一条轻量结构化快照，让 LLM 在需要“相比上次同标的同周期分析变化”时自主读取，而不是把历史快照每轮硬塞进 prompt。

---

## 0. 实施记录

### 2026-07-10 / 最小版本

已完成：

- `ConversationService` 在保存 `last_snapshot` 后追加写入 `analysis_snapshot` fact。
- `tools/context_memory.py` 新增 `get_previous_analysis_snapshot`，由 LLM 按需读取。
- `tools/registry.py` 注册新工具。
- `core/prompt.py` 增加“写相比上次前必须读取同标的同周期快照”的取证规则。
- 补充单元测试覆盖写入、读取、注册。

已验证：

```bash
python -m pytest -q tests/test_direct_agent_context_flow.py tests/test_context_memory_tools.py tests/test_json_fact_store.py
```

结果：`12 passed`。

---

## 1. 背景

当前 light-only 主链路已经可以通过 `turn_summary` 和 `last_snapshot` 承接上下文：

```text
用户消息
  -> ConversationService.run()
  -> 构造 light input（最近 turn_summary + last_snapshot hint）
  -> LLM 自主调用 analyze_market / context tools
  -> ConversationService 保存 tool_observation / last_snapshot / turn_summary
```

现有链路的问题是：

- `last_snapshot` 是每个 session 的最近一次快照，会被不同标的覆盖。
- `turn_summary` 是对话摘要，不是按 `symbol + interval` 索引的行情快照。
- LLM 可能从自然语言摘要里推断“上次价格”，短期可用，但不够稳定。

因此需要新增一种专门用于同标的同周期对比的轻量快照事实。

---

## 2. 目标与非目标

### 2.1 目标

- 每次 `analyze_market` 成功后，代码层自动保存一条 `analysis_snapshot` fact。
- 快照只保存下次对比必须使用的字段。
- 不在每轮 light input 中默认注入上一条同标的快照。
- LLM 需要写“相比上次同标的分析变化”时，自主调用工具读取历史快照。
- 读取必须按 `session_id + symbol + interval` 匹配，避免跨用户、跨标的、跨周期误用。

### 2.2 非目标

- 不替换 `last_snapshot`。它仍用于“刚才那个点位”这类模糊追问。
- 不替换 `turn_summary`。它仍用于 light input 的滚动摘要。
- 不新增数据库表。继续复用 `MemoryAPI.write_fact()` 和现有 JSON/Postgres FactStore。
- 不把完整 `analysis_result` 长期堆进 prompt。
- 不让 LLM 决定是否保存快照；保存是代码层的确定性归档行为。

---

## 3. 数据结构

新增 fact 类型：`analysis_snapshot`

payload 使用最小结构：

```json
{
  "schema_version": "analysis_snapshot.v1",
  "symbol": "ETH_USDT",
  "interval": "4h",
  "timestamp": "2026-07-10T15:00:51.714032",
  "price": 1768.95,
  "trend": "震荡",
  "stance": "wait",
  "support": [1759.2, 1758.0],
  "resistance": [1779.6, 1791.3]
}
```

字段含义：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `schema_version` | 是 | 固定为 `analysis_snapshot.v1` |
| `symbol` | 是 | 使用工具返回的标准化标的，如 `ETH_USDT` |
| `interval` | 是 | 周期，如 `4h` / `1d` |
| `timestamp` | 是 | `analyze_market` 返回的分析时间 |
| `price` | 是 | 当前分析价格，来自 `current_price` |
| `trend` | 是 | 趋势标签，如 `偏多` / `偏空` / `震荡` |
| `stance` | 否 | 来自 `actionability.bias`，如 `long` / `short` / `wait` |
| `support` | 否 | 最近 1-2 个支撑 |
| `resistance` | 否 | 最近 1-2 个阻力 |

Fact 外层：

```python
Fact(
    thread_id=thread_id,
    source="conversation_service",
    type="analysis_snapshot",
    payload=payload,
    provenance={"request_id": request_id},
    tags=[
        "analysis_snapshot",
        f"symbol:{symbol}",
        f"interval:{interval}",
    ],
)
```

---

## 4. 写入流程

写入位置：`application/services/conversation_service.py`

当前保存顺序：

```text
_extract_snapshot(result)
  -> _save_snapshot_checkpoint(thread_id, snapshot)
  -> _write_turn_summary_fact(...)
```

目标保存顺序：

```text
_extract_snapshot(result)
  -> _save_snapshot_checkpoint(thread_id, snapshot)
  -> _write_analysis_snapshot_fact(thread_id, snapshot, request_id)
  -> _write_turn_summary_fact(...)
```

写入触发条件：

- `snapshot` 必须是 meaningful snapshot。
- `symbol`、`interval`、`current_price`、`trend` 至少应存在。
- 无法构造最小 payload 时跳过写入，不影响回复。

失败模式：

- `MemoryAPI` 不存在：跳过写入。
- 写入异常：记录 warning，不影响本轮回答。
- `analyze_market` 失败：不写快照，避免污染历史。

---

## 5. 读取流程

新增工具：`get_previous_analysis_snapshot`

位置：`tools/context_memory.py`

工具签名：

```python
get_previous_analysis_snapshot(
    session_id: str,
    symbol: str,
    interval: str,
    exclude_request_id: str = "",
) -> dict
```

返回成功：

```json
{
  "status": "success",
  "session_id": "feishu_xxx",
  "snapshot": {
    "schema_version": "analysis_snapshot.v1",
    "symbol": "ETH_USDT",
    "interval": "4h",
    "timestamp": "2026-07-10T10:49:00",
    "price": 1773.87,
    "trend": "震荡",
    "stance": "wait",
    "support": [1759.2],
    "resistance": [1791.3]
  }
}
```

未命中：

```json
{
  "status": "not_found",
  "session_id": "feishu_xxx",
  "snapshot": {}
}
```

匹配规则：

- 必须同 `session_id`。
- 必须同 `interval`。
- `symbol` 优先精确匹配。
- 可做弱兼容：`ETH_USDT` 与 `ETHUSDT` 视为同一候选。
- 返回最近一条匹配快照。
- 如果传入 `exclude_request_id`，跳过同一 request 写入的快照。

读取策略：

- 不在代码层每次预取。
- 不在 light input 中默认注入。
- LLM 根据任务需要自主调用。

---

## 6. Prompt 约束

在 `core/prompt.py` 增加规则：

```text
如果你要输出“相比上次同标的分析的变化”，必须先调用 get_previous_analysis_snapshot 查询同 symbol + interval 的上一条快照；未查到则说明没有可比快照。不要从自然语言历史摘要中编造上次价格、趋势或关键位。
```

使用顺序建议：

```text
需要当前行情
  -> 调 analyze_market
  -> 若要写“相比上次”
     -> 使用 analyze_market 返回的 symbol + interval 调 get_previous_analysis_snapshot
  -> 汇总当前分析与上一快照
```

---

## 7. 与现有记忆的关系

| 记忆类型 | 保存方式 | 职责 |
| --- | --- | --- |
| `last_snapshot` checkpoint | 覆盖写 | 最近一次上下文追问 |
| `turn_summary` fact | 追加写 | light input 滚动摘要 |
| `analysis_snapshot` fact | 追加写 | 同标的同周期历史对比 |
| `tool_observation` fact | 追加写 | 来源追问、工具证据摘要 |
| `_history.jsonl` / `recent_message` | 追加写 | 最近对话恢复 |

---

## 8. 实施步骤

### Step 1：写入 analysis_snapshot fact

改动文件：

- `application/services/conversation_service.py`
- `tests/test_direct_agent_context_flow.py`

验收：

- 有 `analysis_result` 时写入 `analysis_snapshot`。
- 从 `analyze_market` tool message 抽取 snapshot 时也写入。
- payload 只包含 v1 最小字段。
- 缺少关键字段时跳过。

### Step 2：新增读取工具

改动文件：

- `tools/context_memory.py`
- `tools/registry.py`
- `tests/test_context_memory_tools.py`

验收：

- 工具未注入 MemoryAPI 时返回 `error`。
- 无历史快照时返回 `not_found`。
- 多条快照时返回同 `symbol + interval` 的最近一条。
- 不同 interval 不混用。
- 工具出现在 `get_all_tools()` 里。

### Step 3：Prompt 增加取证规则

改动文件：

- `core/prompt.py`

验收：

- 规则明确要求写“相比上次”前调用 `get_previous_analysis_snapshot`。
- 不要求每轮默认读取快照。
- 不鼓励从自然语言历史摘要中猜上次价格。

### Step 4：最小回归

建议执行：

```bash
python -m pytest -q tests/test_direct_agent_context_flow.py tests/test_context_memory_tools.py tests/test_json_fact_store.py
```

如果涉及 prompt 行为，建议再跑一次飞书或 Web 最小手工验证：

```text
1. 看 ETH 的 4h 线
2. 等一段时间后再次问：看 ETH 的 4h 线
```

预期第二次回答中，“相比上次”来自 `get_previous_analysis_snapshot`，而不是从自然语言摘要猜测。

---

## 9. 性能与扩展

当前 JSON FactStore 的 `recall()` 会读取整个 `memory_facts.jsonl` 后过滤。新增快照每轮只多写一条小 fact，短期可接受。

后续如果历史文件很大，可优化为：

- 优先用 `tag="symbol:{symbol}"` 缩小候选集合。
- 增加 `analysis_snapshot` 专用索引文件。
- PostgreSQL backend 下增加 `(thread_id, type, timestamp)` 与 tag 查询优化。

复杂度：

- 当前 JSON 查询：`O(N)`，N 为该 memory facts 文件总行数。
- 使用 tag 过滤仍是 `O(N)`，但业务候选更少。
- 瓶颈主要是本地文件 I/O，不是网络 I/O。

---

## 10. 开发边界

- 不改 Feishu adapter。
- 不改 Web route。
- 不改 `analyze_market` 的业务分析逻辑。
- 不新增依赖。
- 不新增数据库迁移。
- 不删除现有 `last_snapshot` / `turn_summary` 行为。
