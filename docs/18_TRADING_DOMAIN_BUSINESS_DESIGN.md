# 交易域业务设计（第一版）

**更新时间**: 2026-07-13  
**适用分支**: `main`  
**定位**: 先定业务边界，不着急落代码

> **当前结论**：本项目的交易域应采用“代码维护状态，LLM 按需解释与复盘”的设计。  
> LLM 不承担开单决策、不承担状态流转、不承担全量订单上下文记忆；代码在每次相关行情请求前先自动兑单，再把必要结果交给 LLM 做解释和复盘。  
> 一旦用户确认“此单进入跟踪”，数据库里就应立即生成一条委托单，而不是只留一份松散的 JSON 计划。
>
> **目的校正**：这套设计首先是为了：
> 1. 记录分析真正产出的成果，而不是只生成一段“看起来很有道理”的回复。
> 2. 给你自己的真实交易提供事前参考和事后回看依据。
> 3. 用最少但稳定的结构记录交易里程，例如进入跟踪、触发、调整、止盈止损、关闭。
>
> **反目标**：
> - 不是为了做一套看起来复杂、很会分析的重型交易系统。
> - 不是为了让 LLM 扛起整套交易状态机和大上下文记忆。
> - 不是第一版就追求高精度撮合、精细回测或完整账户引擎。

---

## 1. 本轮固定的设计原则

### 1.1 LLM 不负责开单和状态流转

LLM 在交易域里的职责应该收敛为：

- 解释当前行情
- 解释某笔单为什么开、为什么关
- 复盘哪些单值得看
- 组织自然语言输出

LLM **不负责**：

- 判断某笔单是否成交
- 判断是否止损/止盈
- 扫描全量持仓历史并自己维护状态
- 从 `reply_text` 文本里反推数据库写入动作

一句话：**交易状态是代码事实，不是 LLM 推理结果。**

### 1.2 状态流转尽量不让用户感知

用户看到的应该是：

- “这笔单现在还在”
- “这笔单刚刚已经止损/止盈”
- “这次没有触发”

而不是一串内部状态跳转过程。

所以状态流转要在代码里隐式完成，用户不需要先感知“系统正在同步订单状态”。  
同步应该发生在回答前，而不是回答后。

### 1.3 每次相关行情请求前先自动兑单

当用户问某个标的行情、追问某笔单、或者要求复盘时，系统应先做一轮代码侧同步：

1. 找出这个 `symbol + interval` 下仍活跃的交易对象
2. 读取最新 K 线
3. 用固定规则判断：
   - 是否触发开仓
   - 是否已触发止损/止盈
   - 是否过期失效
4. 更新数据库当前状态
5. 追加事件流
6. 再让 LLM 回答

这样 LLM 拿到的永远是“已同步后的事实”，而不是“陈旧持仓 + 口头猜测”。

### 1.4 行情分析和交易执行之间必须有中间层

不能把：

- 行情判断
- 交易预期
- 模拟开仓

直接混成一个动作。

必须拆成：

```text
analysis_snapshot
  -> trade_idea
  -> paper_order(pending_trigger)
  -> reconcile by kline
  -> filled / close
```

也就是：

- 行情分析回答“市场现在是什么结构”
- `trade_idea` 回答“如果出现什么条件，我准备怎么交易”
- `paper_order` 回答“系统当前正在跟踪什么委托”
- 自动兑单回答“现在是否已经触发或失效”

这里拆层的目的，不是为了把设计做复杂，而是为了把分析结论沉淀成可复用、可回看的交易参考。

### 1.5 默认不把交易域全量上下文塞给 LLM

LLM 不应在每次问行情时都收到：

- 全量历史单
- 全量事件流
- 全量持仓状态

默认策略应是：

1. 代码先自动同步
2. 再按当前问题只取相关单子
3. 只把紧凑摘要交给 LLM

复盘时，LLM 可以自主决定去取哪几笔单，但也是通过工具按需拉，不是首屏预注入全量账本。

---

## 2. 角色分工

### 2.1 代码负责什么

- 从结构化行情结果提炼 `trade_idea`
- 在用户确认跟踪时创建 `journal_idea + paper_order`
- 根据 K 线自动同步状态
- 维护当前状态字段
- 维护 append-only 事件流
- 按条件筛选“和当前问题最相关的单子”

### 2.2 LLM 负责什么

- 对当前同步后的交易对象做解释
- 在复盘时决定要看哪几笔单
- 将交易事实和当前行情做对照分析
- 组织成自然语言回复

### 2.3 用户负责什么

第一版建议保留用户对“是否开始跟踪这笔交易预期”的控制。

也就是把“开单”拆成两个概念：

1. **纳入跟踪**
   - 用户明确说“按这个方案跟踪/模拟”
   - 或未来单独开一个自动跟踪开关
2. **实际开仓**
   - 一旦进入跟踪，数据库立即生成一条委托单
   - 后续是否触发成交由代码按 K 线自动判断

这样做可以避免：

- 每次分析都往数据库里塞一堆无效单
- 也避免把“分析建议”直接等于“已经建仓”

---

## 3. 什么时候开单

## 3.1 第一阶段结论

第一版建议采用 **两段式开单**：

```text
分析产出交易预期
  -> 用户确认纳入跟踪
  -> 创建 trade_idea + paper_order(pending_trigger)
  -> 代码根据 K 线触发实际开仓
```

### 3.2 不是所有分析都生成交易对象

只有当分析结果满足最小结构化条件时，才允许生成 `trade_idea`：

- 有 `symbol`
- 有 `interval`
- 有方向 `side`
- 有入场条件 `entry_rule`
- 有失效/止损条件 `stop_rule`
- 有退出逻辑 `target_rule` 或明确 `exit_rule`

缺少其中任一项时，只保留分析结果，不生成交易对象。

### 3.3 `trade_idea` 进入跟踪的门槛

建议门槛：

1. 分析结果满足结构化交易条件
2. 用户明确要求“跟踪/模拟”
3. 系统为该对象生成稳定 `idea_id`
4. 系统同步生成稳定 `order_id`

进入跟踪后：

- `journal_ideas` 初始状态通常是 `watch`
- `paper_orders` 初始状态通常是 `pending_trigger`

### 3.4 真正开仓的条件

进入 `watch` 后，是否开仓由代码按 K 线判断：

- 突破型：价格触达或越过触发位
- 回踩型：价格进入预设入场区间
- 区间反手型：到达预设边界并满足触发规则

第一版不做盘口级撮合，只做 **bar-based 模拟**。  
也就是说：只根据 K 线的 `open/high/low/close` 来判断是否触发。

---

## 4. 开单之后的状态流转

## 4.1 第一版核心状态

为了减少复杂度，第一版先分成两层状态：

### A. `journal_ideas.state`

- `watch`
  - 已纳入跟踪，等待触发
- `open`
  - 已模拟开仓
- `closed`
  - 已结束
- `cancelled`
  - 人工取消或策略取消
- `expired`
  - 超过有效期，未触发

注意：

- `reviewed` 不建议作为主状态
- 复盘只是一个事件，不应该污染交易生命周期

### B. `paper_orders.status`

- `pending_trigger`
  - 已创建委托，等待 K 线触发
- `filled`
  - 已触发成交
- `cancelled`
  - 人工取消或策略取消
- `expired`
  - 到期未触发
- `closed`
  - 已完成退出

### 4.2 状态流转图

```text
journal_ideas.watch
  -> journal_ideas.open
  -> journal_ideas.expired
  -> journal_ideas.cancelled

paper_orders.pending_trigger
  -> paper_orders.filled
  -> paper_orders.expired
  -> paper_orders.cancelled

paper_orders.filled
  -> paper_orders.closed(tp)
  -> paper_orders.closed(sl)
  -> paper_orders.closed(invalidation)
  -> paper_orders.closed(manual)
  -> paper_orders.closed(timeout)
```

### 4.3 用户无感知的同步时机

状态同步最好在以下时机自动执行：

1. 用户问当前标的行情前
2. 用户追问“这单现在怎么样”前
3. 用户发起复盘前

默认不在闲聊或无关问题时触发同步，避免无效 I/O。

---

## 5. 自动兑单规则

这是第一版最重要的业务设计点，因为一旦决定用 K 线自动兑单，就必须先固定歧义处理规则。

## 5.1 基本原则

- 只使用结构化行情数据，不使用 LLM 推断
- 同步对象不是单表，而是：
  - 活跃 `journal_ideas`
  - 对应的 `paper_orders(status in pending_trigger, filled)`
- 同步结果由代码计算并落库
- 状态变化时，同一事务内同时：
  - `UPDATE journal_ideas`
  - `UPDATE paper_orders`
  - `INSERT journal_events`
- 没有状态变化就不写库

### 5.2 `paper_orders.pending_trigger -> filled`

对于 `paper_orders.status='pending_trigger'` 的委托，代码只读取显式列判断是否触发，例如：

- `order_type='breakout_stop'`
  - 价格越过 `trigger_price`
- `order_type='pullback_limit'`
  - 价格进入 `entry_zone_low ~ entry_zone_high`
- `order_type='zone_reclaim_close'`
  - 价格先回踩入区
  - 随后某根 bar `close >= confirm_close_above`

触发后：

- `paper_orders.status -> filled`
- 写入 `filled_at`
- 写入 `filled_price`
- `journal_ideas.state -> open`
- 同步写入 `opened_at / opened_price`
- 追加 `journal_events(event_type='order_filled')`

第一版成交价口径建议固定为：

1. 有 `limit_price` 时，优先记 `limit_price`
2. 否则取 `trigger_price`
3. 仍为空时，取 `confirm_close_above`

这意味着：

- `planned_entry_price` 不能继续只是 JSON 里的一个 key
- 它必须落成 `limit_price`、`trigger_price` 或其他显式列

### 5.3 `paper_orders.filled -> closed`

对于 `paper_orders.status='filled'` 的委托：

- 如果满足止损条件：
  - `paper_orders.status -> closed`
  - `paper_orders.close_reason='sl'`
  - `journal_ideas.state -> closed`
- 如果满足止盈条件：
  - `paper_orders.status -> closed`
  - `paper_orders.close_reason='tp'`
  - `journal_ideas.state -> closed`
- 如果满足失效条件：
  - `paper_orders.status -> closed`
  - `paper_orders.close_reason='invalidation'`
  - `journal_ideas.state -> closed`
- 如果超时：
  - `paper_orders.status -> closed`
  - `paper_orders.close_reason='timeout'`
  - `journal_ideas.state -> closed`

关闭时同步写入：

- `closed_at`
- `closed_price`
- `close_reason`
- 可选 `realized_pnl_pct`
- `journal_events(event_type='order_closed_*')`

### 5.4 同一根 K 线同时命中止盈和止损怎么办

这是 K 线回测/模拟里最容易反复争论的点，第一版必须明确规则。

建议第一版采用 **保守规则**：

- 同一根 bar 同时命中止盈和止损时，按更不利结果处理
- 多单优先记为 `sl`
- 空单优先记为 `sl`

原因：

- 简单
- 稳定
- 不会把模拟结果做得过于乐观

后续如果真的需要更细，可以再引入：

- 按 bar 内路径假设
- 按更低周期 K 线复核

但第一版先不要复杂化。

### 5.5 bar-based 模拟的已知边界

第一版必须接受这些边界：

- 无法知道 bar 内真实先后顺序
- 无法知道滑点和撮合细节
- 无法模拟部分成交

所以第一版的定位应该是：

- **策略跟踪与复盘**
- 不是精确撮合引擎
- 更像“分析成果的后续跟踪器”和“交易里程记录器”

---

## 6. 行情判断如何转换成交易预期

## 6.1 转换必须基于结构化字段

转换来源应该是结构化结果，例如当前项目已有的：

- `analysis_snapshot`
- `actionability`
- `trigger_conditions`
- `invalidation_conditions`
- `levels_v2`
- `key_levels`

不允许：

- 从 `reply_text` 里用正则反推
- 从自然语言摘要里猜 entry / stop / tp

### 6.2 推荐的中间对象：`trade_idea`

`trade_idea` 不是“订单”，而是“可被系统持续跟踪的交易预期”。

建议最少包含：

- `idea_id`
- `session_id`
- `symbol`
- `interval`
- `side`
- `setup_type`
- `entry_rule`
- `stop_rule`
- `target_rule`
- `expiry_rule`
- `state`
- `source_request_id`
- `source_snapshot_ref`

### 6.3 `trade_idea` 不是委托，`paper_order` 才是委托

这里必须把两层语义拆开：

- `trade_idea`
  - 表示“我准备怎么交易”
  - 对应 `journal_ideas`
- `paper_order`
  - 表示“系统当前真的在跟踪哪一条委托”
  - 对应 `paper_orders`

当用户只是问行情时：

- 允许只有 `analysis_snapshot`
- 允许只有 `trade_idea`
- 但不创建 `paper_order`

只有当用户明确说“跟踪这单 / 模拟这单”时：

- 才插入 `journal_ideas`
- 同时插入 `paper_orders`
- 并追加 `journal_events`

### 6.4 推荐转换逻辑

```text
analysis_snapshot
  -> rule-based extractor
  -> trade_idea
  -> user confirms tracking
  -> paper_order
```

转换器应由代码实现，作用是：

1. 判断这次分析是否足够形成交易预期
2. 如果足够，提炼出统一规则字段
3. 如果不够，则不生成 `trade_idea`
4. 如果用户未确认跟踪，则不生成 `paper_order`

### 6.5 第一版不要求每个分析都转成 idea

有些分析只是：

- 市场解释
- 风险提示
- 方向判断

并不一定适合形成交易对象。

所以第一版应允许：

- 有 `analysis_snapshot`
- 但没有 `trade_idea`

### 6.6 哪些字段必须是显式列，哪些才允许放 JSON

这里要把原则说透：

- 不是一切 JSON 都不允许
- 而是**不能把执行关键字段藏在 JSON**

第一版必须是显式列的字段：

- `symbol`
- `interval`
- `side`
- `order_type`
- `status`
- `entry_zone_low`
- `entry_zone_high`
- `trigger_price`
- `confirm_close_above`
- `limit_price`
- `stop_loss`
- `tp1`
- `tp2`
- `final_target`
- `valid_until`
- `timeout_bars`
- `filled_price`
- `closed_price`
- `close_reason`

可以先放 JSON 的字段，只能是“非第一版自动执行真相”，例如：

- `strategy_reason`
- `risk_note`
- `extra_targets`
- `post_tp1_rule`
- `matched_bar_ohlc`
- `debug_context`

结论就是：

- `planned_entry_price` 不应该继续作为 JSON key 存在
- 如果它表达的是预计入场价，就应直接落成 `paper_orders.limit_price`
- 如果它表达的是触发价，就应直接落成 `paper_orders.trigger_price`

---

## 7. 最少需要哪些表

## 7.1 第一版最小表模型

在当前原则下，**第一版最少需要 3 张核心表**：

### 表 1：`journal_ideas`

作用：

- 保存 `trade_idea` 的策略对象
- 保存高层当前状态
- 作为“这笔交易为什么存在”的主记录
- 作为你后续真实交易前后回看时的分析成果记录
- 不承担委托撮合细节真相

建议字段方向：

- 主键与引用
  - `idea_id`
  - `session_id`
  - `source_request_id`
  - `source_snapshot_ref`
  - `current_order_id`
- 标的与周期
  - `symbol`
  - `market`
  - `provider`
  - `interval`
  - `side`
  - `setup_type`
- 策略摘要
  - `entry_zone_low`
  - `entry_zone_high`
  - `stop_loss`
  - `tp1`
  - `tp2`
  - `final_target`
  - `strategy_reason`
  - `valid_until`
- 当前状态
  - `state`
  - `opened_at`
  - `opened_price`
  - `closed_at`
  - `closed_price`
  - `close_reason`
  - `pnl_pct`
  - `created_at`
  - `updated_at`
  - `meta_json`

### 表 2：`paper_orders`

作用：

- 保存真正的“委托对象”
- 作为执行规则和执行结果的真相表
- 作为实际交易前后的执行参考记录
- 在用户确认跟踪时立即创建

建议字段方向：

- 主键与引用
  - `order_id`
  - `idea_id`
- 标的与委托
  - `symbol`
  - `market`
  - `provider`
  - `interval`
  - `side`
  - `order_type`
- 触发与入场
  - `entry_zone_low`
  - `entry_zone_high`
  - `trigger_price`
  - `confirm_close_above`
  - `limit_price`
- 风控与退出
  - `stop_loss`
  - `tp1`
  - `tp2`
  - `final_target`
  - `valid_until`
  - `timeout_bars`
- 执行状态
  - `status`
  - `status_reason`
  - `requested_qty`
  - `requested_notional`
  - `created_at`
  - `updated_at`
  - `filled_at`
  - `filled_price`
  - `closed_at`
  - `closed_price`
  - `close_reason`
- 扩展信息
  - `simulation_rule_json`
  - `meta_json`

### 表 3：`journal_events`

作用：

- append-only 记录状态变化和交易里程
- 支持复盘
- 支持调试“为什么会变成现在这样”

建议字段方向：

- `id`
- `idea_id`
- `order_id`
- `event_type`
- `event_time`
- `old_idea_state`
- `new_idea_state`
- `old_order_status`
- `new_order_status`
- `event_price`
- `payload_json`

### 为什么第一版先停在 3 张表

因为当前还没有这些硬需求：

- 部分成交
- 分批入场/分批止盈
- 真实订单簿撮合
- 多账户资金约束

在这些需求没出现前，不必急着引入：

- `paper_fills`
- `account_ledger`
- `account_positions`

## 7.2 什么时候再拆更多表

只有满足以下条件之一，再考虑继续拆表：

1. 一笔 `idea` 需要多次开仓/减仓
2. 一笔委托需要多次成交
3. 需要账户余额、保证金、可用资金
4. 需要部分成交和滑点口径

到了这一步，再增加：

- `paper_fills`
- `account_ledger`
- `account_positions`

## 7.3 三张表分别在什么时机记录

这是第一版非常关键的口径：

- `journal_ideas`：保存 **策略对象 + 高层当前状态**
- `paper_orders`：保存 **委托对象 + 执行真相**
- `journal_events`：记录 **状态变化过程**

三者不是重复写同一份数据。

### A. `journal_ideas` 的记录时机

`journal_ideas` **不是每次分析都写**。

第一版建议只在“交易预期进入系统跟踪范围”时创建：

1. 本轮分析已经提炼出结构化 `trade_idea`
2. 用户明确要求“跟踪/模拟这笔”
3. 或未来单独开启“自动跟踪模式”

满足以上条件后：

- `INSERT 1` 条 `journal_ideas`
- 初始状态通常写成 `watch`

也就是说：

- 普通问行情
- 普通结构解释
- 还没确认要跟踪的建议

这些都**不写** `journal_ideas`。

### B. `paper_orders` 的记录时机

`paper_orders` 是“用户确认跟踪后立即落地的委托单”。

满足跟踪条件后，同一事务内应同时：

1. `INSERT journal_ideas(state=watch)`
2. `INSERT paper_orders(status=pending_trigger)`
3. `INSERT journal_events(event_type='order_created')`

也就是说：

- 只要用户确认“此单进入跟踪”
- 数据库里就必须已经有一条 `paper_orders`
- 而不是只在某个 `meta_json` 里留一份计划文本

### C. `journal_ideas` / `paper_orders` 的更新时机

这两张表在创建后，后续只在“当前状态真的发生变化”时更新，例如：

- `watch -> open` / `pending_trigger -> filled`
- `open -> closed` / `filled -> closed`
- `watch -> expired` / `pending_trigger -> expired`
- `watch -> cancelled` / `pending_trigger -> cancelled`
- 用户手动修改止损、目标、有效期

更新时是 **原地更新当前行**，因为这两张表的职责就是保存“这笔策略对象 / 委托对象现在是什么状态”。

### D. `journal_events` 的记录时机

`journal_events` 是 append-only。

只有发生**有意义的状态变化或人工动作**时才追加一条，例如：

- `idea_created`
- `order_created`
- `order_filled`
- `closed_tp`
- `closed_sl`
- `closed_invalidation`
- `expired`
- `cancelled`
- `rule_updated`

默认 **不记录**：

- 普通查询
- 普通读取
- 自动同步后发现“没有变化”

否则事件流会被大量无效噪音淹没。

### E. 自动兑单时怎么写

每次相关行情请求前，代码会先自动兑单：

1. 读取活跃的 `journal_ideas`
2. 读取对应的活跃 `paper_orders`
3. 拉最新 K 线
4. 判断是否触发开仓 / 止盈 / 止损 / 失效
5. 如果状态变化：
   - 更新 `journal_ideas`
   - 更新 `paper_orders`
   - 追加 1 条 `journal_events`
6. 如果状态没变化：
   - 什么都不写

所以自动兑单是“**先算，后决定要不要写**”，不是每次同步都落一条日志。

### F. 推荐的事务口径

为了避免出现“当前状态更新了，但事件没写进去”这种脏状态，建议每次状态变化都按同一事务完成：

1. `UPDATE journal_ideas`
2. `UPDATE paper_orders`
3. `INSERT journal_events`

这三步要么都成功，要么都回滚。

### G. 一个完整例子

#### 场景 1：普通分析，不跟踪

用户问：`看看 ETH 4h`

- 生成 `analysis_snapshot`
- 不创建 `journal_ideas`
- 不创建 `paper_orders`
- 不创建 `journal_events`

#### 场景 2：用户确认跟踪

用户说：`按这个方案模拟跟踪`

- 创建 `journal_ideas(state=watch)`
- 创建 `paper_orders(status=pending_trigger)`
- 追加 `journal_events(event_type='order_created')`

#### 场景 3：后续问行情时触发开仓

用户下一次又问：`ETH 现在怎么样`

代码先自动兑单，发现入场条件满足：

- `journal_ideas.state` 更新为 `open`
- 写 `opened_at / opened_price`
- `paper_orders.status` 更新为 `filled`
- 写 `filled_at / filled_price`
- 追加 `journal_events(event_type='order_filled')`

然后再把同步后的结果交给 LLM 回答。

#### 场景 4：后来止损

再次问行情或复盘前，代码同步发现止损命中：

- `journal_ideas.state` 更新为 `closed`
- 写 `closed_at / closed_price / close_reason='sl'`
- `paper_orders.status` 更新为 `closed`
- 写 `closed_at / closed_price / close_reason='sl'`
- 追加 `journal_events(event_type='closed_sl')`

#### 场景 5：只是读，不变更

如果自动同步后发现状态还是 `open` / `filled`，且没有触发任何变化：

- 不更新 `journal_ideas`
- 不更新 `paper_orders`
- 不写 `journal_events`

### H. 用你给的 ETH 例子量化到数据库

假设用户最终确认：`按这个 ETH 4h 回踩确认多单进入跟踪`

那么第一时间就应落 3 类数据。

#### 1. `journal_ideas`

| 列 | 示例值 | 说明 |
| --- | --- | --- |
| `idea_id` | `idea_ethusdt_4h_20260713_145535_01` | 稳定业务 ID |
| `session_id` | `HCT-SHKL761W:1699` | 来源会话 |
| `source_request_id` | `req_20260713_145535` | 来源请求 |
| `symbol` | `ETH_USDT` | 标的 |
| `interval` | `4h` | 周期 |
| `side` | `long` | 方向 |
| `setup_type` | `pullback_confirmation` | setup 类型 |
| `entry_zone_low` | `1764.0` | 回踩区下沿 |
| `entry_zone_high` | `1771.2` | 回踩区上沿 |
| `stop_loss` | `1758.5` | 初始止损 |
| `tp1` | `1779.6` | 第一止盈 |
| `tp2` | `1789.6` | 第二止盈 |
| `final_target` | `1795.3` | 延伸目标 |
| `strategy_reason` | `4h 震荡偏强，等回踩确认再做多` | 策略摘要 |
| `state` | `watch` | 高层状态 |
| `current_order_id` | `ord_ethusdt_4h_20260713_145535_01` | 当前委托 |

#### 2. `paper_orders`

| 列 | 示例值 | 说明 |
| --- | --- | --- |
| `order_id` | `ord_ethusdt_4h_20260713_145535_01` | 委托 ID |
| `idea_id` | `idea_ethusdt_4h_20260713_145535_01` | 关联策略 |
| `symbol` | `ETH_USDT` | 标的 |
| `interval` | `4h` | 周期 |
| `side` | `buy` | 买入委托 |
| `order_type` | `zone_reclaim_close` | 先回踩入区，再收回确认 |
| `status` | `pending_trigger` | 等待触发 |
| `entry_zone_low` | `1764.0` | 回踩区下沿 |
| `entry_zone_high` | `1771.2` | 回踩区上沿 |
| `confirm_close_above` | `1771.2` | 4h 收回确认位 |
| `limit_price` | `1772.0` | 建议入场价 |
| `stop_loss` | `1758.5` | 初始止损 |
| `tp1` | `1779.6` | 第一止盈 |
| `tp2` | `1789.6` | 第二止盈 |
| `final_target` | `1795.3` | 延伸目标 |
| `timeout_bars` | `3` | 连续 3 根 4h 不延续则离场 |
| `simulation_rule_json` | `{"breakeven_after_tp1": true, "tp1_reduce_ratio": "0.3-0.5"}` | 仅保留第一版未结构化的扩展规则 |

这里最关键的是：

- `1772` 不能只写成 JSON 里的 `planned_entry_price`
- 它应该是 `paper_orders.limit_price`
- `1771.2` 的确认条件也不能藏在 JSON 里，应该是 `confirm_close_above`

#### 3. `journal_events`

用户一确认跟踪，就至少追加 1 条事件：

| 列 | 示例值 |
| --- | --- |
| `idea_id` | `idea_ethusdt_4h_20260713_145535_01` |
| `order_id` | `ord_ethusdt_4h_20260713_145535_01` |
| `event_type` | `order_created` |
| `old_idea_state` | `NULL` |
| `new_idea_state` | `watch` |
| `old_order_status` | `NULL` |
| `new_order_status` | `pending_trigger` |
| `event_time` | `2026-07-13 14:55:35+08:00` |

后续如果真的触发成交，再追加：

- `event_type='order_filled'`

如果后来止损，再追加：

- `event_type='closed_sl'`

### H.1 这套记录为什么能服务你的真实交易

- 你回看时，能看到当时分析最终到底沉淀了什么结论，而不是只剩聊天文本。
- 你能看到这笔计划有没有进入跟踪、什么时候触发、后来是怎么结束的。
- 即使真实交易是在外部手动完成，这套记录仍然能作为你的交易参考和交易里程。

### I. 第一版最重要的边界

第一版的写入原则可以压缩成一句话：

- **普通分析只写 `analysis_snapshot`**
- **用户确认跟踪时同时写 `journal_ideas + paper_orders`**
- **状态变化时更新 `journal_ideas + paper_orders` 并追加 `journal_events`**
- **没有变化时不写**

---

## 8. LLM 的取数方式

## 8.1 默认不首屏注入交易账本

默认回答行情问题时：

- 先代码同步
- 再只给 LLM 当前标的最相关的交易摘要

### 8.2 复盘时按需拉取

当用户问：

- “复盘一下这笔单”
- “最近 ETH 的模拟单做得怎么样”
- “为什么这次止损了”

LLM 可以自主决定去拿：

- 当前标的最近关闭的单
- 最近活跃的单
- 指定会话内的相关事件流

但方式应该是：

- 调工具按条件过滤
- 不是把全量订单历史直接塞进 prompt

### 8.3 推荐的工具方向

后续如果实现工具，建议是这类受控读取能力：

- `sync_trade_ideas(symbol, interval)`  
  代码侧同步，通常不暴露给用户
- `get_relevant_trade_ideas(symbol, status, limit)`
- `get_trade_timeline(idea_id)`
- `get_trade_review_candidates(symbol, session_id, limit)`

---

## 9. 当前业务设计的结论

直接回答最初那 4 个问题：

### 1. 什么时候开单

不是分析完就开。  
第一版应拆成：

- 先形成 `trade_idea`
- 用户确认纳入跟踪
- 立即创建 `paper_order(pending_trigger)`
- 后续由代码按 K 线自动触发真正开仓

### 2. 开单之后状态流转

第一版核心状态：

- `watch`
- `open`
- `closed`
- `cancelled`
- `expired`

状态变化由代码隐式完成，并写入事件流，不要求用户显式感知。

### 3. 行情判断与下单之间的预期怎么转换

通过代码把结构化行情结果转成 `trade_idea`。  
用户确认跟踪后，再把 `trade_idea` 显式转换成 `paper_order`。  
不允许从 LLM 文本里反推。

### 4. 最少需要哪些表

第一版最少 3 张核心表：

- `journal_ideas`
- `paper_orders`
- `journal_events`

只有出现部分成交、分批入场、账户系统需求后，再继续拆 `paper_fills / ledger / positions`。

这一版的核心目的，是把“分析成果 + 跟踪过程 + 交易里程”稳定记下来，不是先把交易系统做重。

---

## 10. 下一步建议

下一步仍然先做业务设计，不改代码的话，最值得继续定的是：

1. `trade_idea` 的字段清单
2. `paper_orders` 的显式列清单
3. `journal_events` 的事件类型枚举
4. K 线自动兑单的精确规则表
