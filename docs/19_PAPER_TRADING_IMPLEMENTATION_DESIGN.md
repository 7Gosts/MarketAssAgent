# 模拟开单与状态流转开发设计

**更新时间**: 2026-07-16
**状态**: Step 1-6 已完成最小可用实现；直接开单触发已改为“LLM 草稿 -> 正式 symbol 准备 -> 写库”三段式
**定位**: 交易域数据库正式落地与第一阶段施工文档
**前置阅读**: `07_DATABASE_UNIFICATION_PLAN.md`、`18_TRADING_DOMAIN_BUSINESS_DESIGN.md`

本文把模拟开单从现有 `journals` 过渡表升级为正式交易域模型。第一版目标不是完整撮合和账户引擎，而是稳定支持：

1. 用户确认后创建可跟踪的模拟委托。
2. 代码根据 K 线推进 `pending_trigger -> filled -> closed`。
3. 所有状态变化进入 append-only 事件流。
4. 复盘时能回答“为什么开、怎么触发、为什么结束”。

当前已落代码：

- 模型: `src/infrastructure/persistence/models.py`
- 迁移: `ops/alembic/versions/journal_002_create_paper_trading_core.py`
- 仓储: `src/infrastructure/persistence/paper_trading_repository.py`
- 状态流转: `src/domain/trading/reconciliation.py`
- 服务: `src/domain/trading/paper_trading_service.py`
- 工具: `src/tools/sim_account.py`
- smoke: `scripts/smoke_paper_trading.py`

## 1. 当前代码基线

当前仓库已有能力：

- PostgreSQL 连接: `src/infrastructure/persistence/db.py`
- SQLAlchemy 模型: `src/infrastructure/persistence/models.py`
- 过渡台账表: `journals`
- 过渡仓储: `src/infrastructure/persistence/journal_repository.py`
- 显式模拟开仓工具: `src/tools/sim_account.py:simulate_open_position`
- 持仓查询工具: `src/tools/sim_account.py:get_journal_status`
- 分析快照正式表: `analysis_snapshots`

当前限制：

- 旧 `journals` 只有 `symbol / direction / entry_price / stop_loss / take_profit / status`，只作为过渡表保留，不再作为正式模拟交易真相。
- 当前自动兑单已支持显式 `reconcile_paper_orders`，但尚未自动接入所有行情请求前置同步。
- 交易记录可保存 `source_snapshot_id`，但“直接开单”场景允许为空，复盘证据弱于分析后开单。
- 最近一周/时间范围复盘还未抽成专用工具，目前状态查询以最近记录和事件流为主。

## 2. 目标边界

第一版只落 `idea / order / event` 三层：

```text
analysis_snapshots
  -> journal_ideas
  -> paper_orders
  -> journal_events
```

暂不落完整账户系统：

- 不做真实资金余额。
- 不做保证金。
- 不做部分成交。
- 不做多次加减仓。
- 不做盘口级撮合。

如后续需要账户收益曲线，再新增 `paper_fills / account_positions / account_ledger`，其中账户流水必须采用 append-only ledger。

## 3. 状态机

### 3.1 `journal_ideas.state`

`journal_ideas` 表示一条交易想法的生命周期。

| 状态 | 含义 | 可进入状态 |
| --- | --- | --- |
| `watch` | 用户已确认跟踪，等待触发 | `open` / `expired` / `cancelled` |
| `open` | 对应模拟委托已触发成交 | `closed` |
| `closed` | 已止盈、止损、失效或手动关闭 | 终态 |
| `expired` | 有效期内未触发 | 终态 |
| `cancelled` | 用户或系统主动取消 | 终态 |

### 3.2 `paper_orders.status`

`paper_orders` 表示真正被自动兑单服务处理的模拟委托。

| 状态 | 含义 | 可进入状态 |
| --- | --- | --- |
| `pending_trigger` | 已创建，等待 K 线触发 | `filled` / `expired` / `cancelled` |
| `filled` | 已模拟成交，等待退出 | `closed` |
| `closed` | 已完成退出 | 终态 |
| `expired` | 触发前过期 | 终态 |
| `cancelled` | 触发前取消 | 终态 |

### 3.3 流转规则

```text
journal_ideas.watch
  -> paper_orders.pending_trigger
  -> paper_orders.filled + journal_ideas.open
  -> paper_orders.closed + journal_ideas.closed
```

关闭原因统一写入 `close_reason`：

- `tp`: 止盈
- `sl`: 止损
- `invalidation`: 结构失效
- `timeout`: 超时
- `manual`: 用户手动关闭

同一根 K 线同时命中止盈和止损时，第一版按保守规则处理为 `sl`。

## 4. 正式数据库设计

### 4.1 `journal_ideas`

```sql
CREATE TABLE journal_ideas (
    id BIGSERIAL PRIMARY KEY,
    idea_id VARCHAR(64) NOT NULL UNIQUE,
    session_id VARCHAR(128) NOT NULL,
    source_request_id VARCHAR(128) NOT NULL DEFAULT '',
    source_snapshot_id VARCHAR(64),
    current_order_id VARCHAR(64),

    symbol VARCHAR(64) NOT NULL,
    symbol_key VARCHAR(64) NOT NULL,
    market VARCHAR(32),
    provider VARCHAR(32) NOT NULL DEFAULT 'marketassagent',
    interval VARCHAR(16) NOT NULL,
    side VARCHAR(16) NOT NULL,
    setup_type VARCHAR(32) NOT NULL DEFAULT 'unspecified',

    state VARCHAR(24) NOT NULL DEFAULT 'watch',
    entry_zone_low NUMERIC(20, 8),
    entry_zone_high NUMERIC(20, 8),
    stop_loss NUMERIC(20, 8),
    tp1 NUMERIC(20, 8),
    tp2 NUMERIC(20, 8),
    final_target NUMERIC(20, 8),
    valid_until TIMESTAMPTZ,

    opened_at TIMESTAMPTZ,
    opened_price NUMERIC(20, 8),
    closed_at TIMESTAMPTZ,
    closed_price NUMERIC(20, 8),
    close_reason VARCHAR(32),
    pnl_pct NUMERIC(12, 6),

    strategy_reason TEXT,
    meta_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_journal_ideas_side CHECK (side IN ('long', 'short')),
    CONSTRAINT ck_journal_ideas_state CHECK (state IN ('watch', 'open', 'closed', 'expired', 'cancelled'))
);

CREATE INDEX idx_journal_ideas_session_state ON journal_ideas (session_id, state, updated_at DESC);
CREATE INDEX idx_journal_ideas_symbol_interval_state ON journal_ideas (symbol_key, interval, state);
CREATE INDEX idx_journal_ideas_source_snapshot ON journal_ideas (source_snapshot_id);
```

说明：

- `idea_id` 是业务幂等键，不能依赖自增主键做业务关联。
- `source_snapshot_id` 关联 `analysis_snapshots.snapshot_id`，第一版可不加数据库外键，避免历史数据和修复流程被强约束卡住。
- 执行关键字段必须是显式列，不能只放在 `meta_json`。

### 4.2 `paper_orders`

```sql
CREATE TABLE paper_orders (
    id BIGSERIAL PRIMARY KEY,
    order_id VARCHAR(64) NOT NULL UNIQUE,
    idea_id VARCHAR(64) NOT NULL,

    symbol VARCHAR(64) NOT NULL,
    symbol_key VARCHAR(64) NOT NULL,
    market VARCHAR(32),
    provider VARCHAR(32) NOT NULL DEFAULT 'marketassagent',
    interval VARCHAR(16) NOT NULL,
    side VARCHAR(16) NOT NULL,
    order_type VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending_trigger',

    entry_zone_low NUMERIC(20, 8),
    entry_zone_high NUMERIC(20, 8),
    trigger_price NUMERIC(20, 8),
    confirm_close_above NUMERIC(20, 8),
    confirm_close_below NUMERIC(20, 8),
    limit_price NUMERIC(20, 8),

    stop_loss NUMERIC(20, 8),
    tp1 NUMERIC(20, 8),
    tp2 NUMERIC(20, 8),
    final_target NUMERIC(20, 8),
    valid_until TIMESTAMPTZ,
    timeout_bars INTEGER,

    filled_at TIMESTAMPTZ,
    filled_price NUMERIC(20, 8),
    closed_at TIMESTAMPTZ,
    closed_price NUMERIC(20, 8),
    close_reason VARCHAR(32),
    realized_pnl_pct NUMERIC(12, 6),

    simulation_rule_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_paper_orders_side CHECK (side IN ('long', 'short')),
    CONSTRAINT ck_paper_orders_status CHECK (status IN ('pending_trigger', 'filled', 'closed', 'expired', 'cancelled')),
    CONSTRAINT ck_paper_orders_order_type CHECK (
        order_type IN ('breakout_stop', 'pullback_limit', 'zone_reclaim_close')
    )
);

CREATE INDEX idx_paper_orders_idea_id ON paper_orders (idea_id);
CREATE INDEX idx_paper_orders_status_symbol ON paper_orders (status, symbol_key, interval);
CREATE INDEX idx_paper_orders_valid_until ON paper_orders (valid_until);
```

说明：

- `paper_orders` 是自动兑单服务的真相表。
- `trigger_price / limit_price / stop_loss / tp1 / tp2` 等字段必须显式列化。
- `simulation_rule_json` 只保存非关键扩展信息，例如 matched bar、debug context、post tp1 rule。

### 4.3 `journal_events`

```sql
CREATE TABLE journal_events (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL UNIQUE,
    idea_id VARCHAR(64) NOT NULL,
    order_id VARCHAR(64),
    session_id VARCHAR(128) NOT NULL,

    event_type VARCHAR(48) NOT NULL,
    old_idea_state VARCHAR(24),
    new_idea_state VARCHAR(24),
    old_order_status VARCHAR(32),
    new_order_status VARCHAR(32),

    event_time TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_price NUMERIC(20, 8),
    source VARCHAR(32) NOT NULL DEFAULT 'system',
    request_id VARCHAR(128) NOT NULL DEFAULT '',
    payload_json JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_journal_events_idea_time ON journal_events (idea_id, event_time DESC);
CREATE INDEX idx_journal_events_session_time ON journal_events (session_id, event_time DESC);
CREATE INDEX idx_journal_events_order_time ON journal_events (order_id, event_time DESC);
```

建议事件类型：

- `idea_created`
- `order_created`
- `order_filled`
- `order_closed_tp`
- `order_closed_sl`
- `order_closed_invalidation`
- `order_closed_timeout`
- `order_cancelled`
- `order_expired`
- `manual_note`
- `review_added`

## 5. SQLAlchemy 模型与迁移策略

新增迁移：

```text
ops/alembic/versions/journal_002_create_paper_trading_core.py
```

同步修改：

- `src/infrastructure/persistence/models.py`
  - 新增 `JournalIdea`
  - 新增 `PaperOrder`
  - 新增 `JournalEvent`
- `src/infrastructure/persistence/schema_repair.py`
  - 只做运行时轻量补列或兼容旧表，不替代 Alembic 正式迁移

注意：

- 第一版不删除 `journals`，它作为过渡表保留。
- 新代码不再向 `journals` 写正式模拟单。
- 如需兼容旧数据，单独写迁移脚本把 `journals.status='open'` 的记录转成 `journal_ideas + paper_orders + journal_events`，不要在运行时隐式迁移。

## 6. 代码模块设计

### 6.1 仓储层

新增：

```text
src/infrastructure/persistence/paper_trading_repository.py
```

接口建议：

```python
class PaperTradingRepository:
    def create_tracked_order(self, command: CreateTrackedOrderCommand) -> TrackedOrderResult: ...
    def list_active_orders(self, *, session_id: str | None, symbol_key: str | None, interval: str | None) -> list[PaperOrder]: ...
    def get_order_bundle(self, *, order_id: str) -> OrderBundle | None: ...
    def apply_transition(self, transition: OrderTransition) -> OrderBundle: ...
    def list_recent_events(self, *, session_id: str, limit: int = 50) -> list[JournalEvent]: ...
```

仓储约束：

- `create_tracked_order` 必须一个事务内插入 `journal_ideas / paper_orders / journal_events`。
- `apply_transition` 必须一个事务内更新 idea、order 并插入 event。
- 幂等基于 `idea_id / order_id / event_id`，捕获唯一键冲突后读取既有记录返回。

### 6.2 领域服务

新增：

```text
src/domain/trading/paper_trading_service.py
src/domain/trading/reconciliation.py
src/domain/trading/types.py
```

职责：

- `paper_trading_service.py`
  - 创建模拟跟踪单。
  - 查询当前活跃单。
  - 查询复盘上下文。
- `reconciliation.py`
  - 读取活跃订单和 K 线。
  - 计算状态流转。
  - 生成 `OrderTransition`。
- `types.py`
  - 放命令对象、结果对象、状态枚举。

### 6.3 工具层

改造 `src/tools/sim_account.py`：

- 新增 `prepare_simulated_order`，用于直接开单前的标的解析与价格校验，不写库。
- `simulate_open_position` 保留名称，但语义升级为“创建模拟跟踪单”，默认写入 `pending_trigger`。
- 新增 `reconcile_paper_orders`，用于显式同步指定 session/symbol/interval 的活跃单。
- `get_journal_status` 改为从新三表读取，返回 open/pending/closed 摘要。
- 过渡期可保留 `legacy_journals` 统计字段，但不能把旧表作为状态真相。

直接开单触发规则：

```text
用户自然语言
  -> LLM 抽取开单草稿(asset_text / direction / entry / stop / tp)
  -> prepare_simulated_order 解析 asset_text 为 market_config 正式 symbol
  -> ready 时用 simulate_args 调用 simulate_open_position
  -> clarify / invalid 时只追问或说明冲突，不写库
```

关键边界：

- `asset_text` 可以是“以太坊”“比特币”“英伟达”等自然语言。
- 写库时的 `symbol` 必须是 `market_config.json` 中的正式代码，例如 `ETH_USDT`。
- `simulate_open_position` 即使收到“以太坊”，也会先走同一套准备逻辑，不能把自然语言标的原样写入数据库。
- `prepare_simulated_order` 只使用本地 `market_config` 做标的解析，不在开单准备阶段隐式新增市场配置。
- 多单校验 `stop_loss < entry_price < take_profit`；空单校验 `take_profit < entry_price < stop_loss`。

建议工具返回：

```json
{
  "status": "ready",
  "asset_text": "以太坊",
  "symbol": "ETH_USDT",
  "session_id": "feishu_xxx",
  "direction": "long",
  "entry_price": 1786,
  "stop_loss": 1754,
  "take_profit": 1854,
  "simulate_args": {
    "symbol": "ETH_USDT",
    "direction": "long",
    "entry_price": 1786,
    "stop_loss": 1754,
    "take_profit": 1854
  },
  "message": "已解析为 ETH_USDT，参数校验通过，可创建模拟跟踪单。"
}
```

### 6.4 调用链接入

第一阶段先不让所有行情请求自动同步，避免扩大行为面。推荐顺序：

1. 用户明确要求模拟/跟踪/记录一笔单，且给出入场、止损、止盈。
2. LLM 调用 `prepare_simulated_order`，把自然语言标的转为正式 symbol。
3. `prepare_simulated_order.status == ready` 时，LLM 使用 `simulate_args` 调用 `simulate_open_position`。
4. 用户问“这单怎么样/同步一下/复盘”时，LLM 可调用 `reconcile_paper_orders`。
5. 待行为稳定后，再在行情类请求前由应用层自动调用同步服务。

自动同步最终接入点应在应用服务或工具编排层，而不是 prompt 文本里：

- 优先候选: `src/application/services/conversation_service.py`
- 不建议: 在 `core/prompt.py` 中要求 LLM 自己维护状态

## 7. 自动兑单规则

### 7.1 触发入场

`pending_trigger -> filled`：

- `breakout_stop`
  - 多单: `bar.high >= trigger_price`
  - 空单: `bar.low <= trigger_price`
- `pullback_limit`
  - 多单: `bar.low <= entry_zone_high AND bar.high >= entry_zone_low`
  - 空单: 同样以 K 线区间和入场区间重叠判定
- `zone_reclaim_close`
  - 多单: `bar.low` 进入区间后，`bar.close >= confirm_close_above`
  - 空单: `bar.high` 进入区间后，`bar.close <= confirm_close_below`

成交价优先级：

1. `limit_price`
2. `trigger_price`
3. `confirm_close_above / confirm_close_below`
4. 当前 bar `close`

### 7.2 退出

`filled -> closed`：

- 多单止损: `bar.low <= stop_loss`
- 多单止盈: `bar.high >= tp1/final_target`
- 空单止损: `bar.high >= stop_loss`
- 空单止盈: `bar.low <= tp1/final_target`

第一版只关闭整单，不做分批止盈。若命中 `tp1`，可先按 `tp1` 关闭；后续再扩展 `tp2/final_target` 和部分成交。

### 7.3 过期

- `pending_trigger` 超过 `valid_until` 后转 `expired`。
- `filled` 超过策略持仓有效期时转 `closed(timeout)`，第一版可不启用。

### 7.4 无变化

如果本轮 K 线没有触发任何状态变化：

- 不写 `journal_events`。
- 返回 `status='unchanged'`。
- 保留最近一次检查时间可放入 `paper_orders.simulation_rule_json.last_checked_at`，但第一版不是必须。

## 8. 失败模式与兜底

| 失败模式 | 触发条件 | 处理 |
| --- | --- | --- |
| 数据库未配置 | `database.postgres.dsn` 为空 | 工具返回 `status=error`，不影响聊天主链路 |
| 建表缺失 | 未执行 Alembic 或 schema 漂移 | 启动 `init_db` 可 best-effort create，正式环境仍要求跑迁移 |
| 重复创建 | 同一请求重试 | 依赖 `idea_id/order_id` 唯一键幂等返回既有记录 |
| 行情不可用 | 数据源异常或 K 线为空 | 同步返回 `status=error`，不修改状态 |
| 同 bar 命中 TP/SL | K 线高低点同时触达 | 保守记 `sl`，写入 event payload 说明 |
| 非法状态跳转 | closed 后又尝试 filled | 拒绝写入，返回 `invalid_transition` |
| 价格字段不足 | 缺少触发价/止损/目标 | 创建前校验失败，不入库 |

## 9. 开工步骤

### Step 1: 迁移和模型

交付：

- Alembic 新迁移 `journal_002_create_paper_trading_core.py`
- SQLAlchemy 三个新模型
- 最小模型测试

验证：

```bash
PYTHONPATH="$PWD/runtime:$PWD/src:$PWD" pytest tests/test_paper_trading_models.py
```

### Step 2: 仓储事务

交付：

- `PaperTradingRepository.create_tracked_order`
- `PaperTradingRepository.apply_transition`
- 幂等测试

验证：

```bash
PYTHONPATH="$PWD/runtime:$PWD/src:$PWD" pytest tests/test_paper_trading_repository.py
```

### Step 3: 显式模拟开单工具

交付：

- 改造 `simulate_open_position`
- 返回 `idea_id/order_id`
- 过渡期保留旧参数兼容，但内部写新三表

验证：

```bash
PYTHONPATH="$PWD/runtime:$PWD/src:$PWD" pytest tests/test_sim_account_tools.py
```

### Step 4: 自动兑单服务

交付：

- `reconciliation.py`
- 支持 `pending_trigger -> filled`
- 支持 `filled -> closed`
- 支持 `expired`
- 支持同 bar TP/SL 保守规则

验证：

```bash
PYTHONPATH="$PWD/runtime:$PWD/src:$PWD" pytest tests/test_paper_trading_reconciliation.py
```

### Step 5: 状态查询与复盘摘要

交付：

- `get_journal_status` 读取新三表
- 返回 `pending_orders / open_positions / recent_closed / recent_events`
- 支持按 `session_id/symbol/interval` 过滤

验证：

```bash
PYTHONPATH="$PWD/runtime:$PWD/src:$PWD" pytest tests/test_journal_status.py
```

### Step 6: 最小本地 smoke

交付：

- 新增脚本 `scripts/smoke_paper_trading.py`
- 流程: 建库检查 -> 创建模拟单 -> 喂入 K 线 -> 触发成交 -> 喂入止损/止盈 K 线 -> 查询事件流

验证：

```bash
PYTHONPATH="$PWD/runtime:$PWD/src:$PWD" python3 scripts/smoke_paper_trading.py
```

## 10. 验收标准

必须满足：

- 空库能通过迁移建出三张表。
- 同一模拟开单请求重试不重复插入。
- 一笔单能完整流转 `pending_trigger -> filled -> closed`。
- 每次状态变化都有 `journal_events` 记录。
- `get_journal_status` 不再依赖旧 `journals.status='open'` 判断真实持仓。
- 数据库不可用时工具返回结构化 error，不拖垮主聊天链路。

暂不要求：

- 账户余额变化。
- 部分成交。
- 多目标分批止盈。
- 自动在所有行情请求前同步。
- 旧 `journals` 数据全量迁移。

## 11. 复杂度与性能

自动兑单的主要成本通常在网络 I/O，即行情数据读取，而不是本地状态扫描。

第一版查询策略：

- 按 `session_id + symbol_key + interval + status` 取活跃单。
- 数据库索引命中后，本地只扫描活跃单，复杂度约 `O(active_orders * bars)`。
- 不从全量事件表反推当前状态，避免 `O(all_events)`。

如果后续出现同一 `symbol_key + interval` 下大量连续订单，应在同步服务中按分组维护游标：

```text
(symbol_key, interval) -> last_checked_bar_time
```

同步时先从游标之后读取 K 线；游标失效时再回退为“两段扫描”：游标到末尾，再从头到游标。这样可以避免每次都从第一根 K 线重新扫到最新。

## 12. 与既有文档的关系

- `18_TRADING_DOMAIN_BUSINESS_DESIGN.md` 定业务边界和原则。
- 本文定数据库 DDL、模块拆分、状态机和开工顺序。
- `07_DATABASE_UNIFICATION_PLAN.md` 继续作为数据库治理路线图。
- `journals` 是过渡表，不再作为正式模拟交易模型继续扩展。
