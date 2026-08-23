from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from utils.logging_utils import get_logger


logger = get_logger(__name__)

_ANALYSIS_SNAPSHOT_TARGET_COLUMNS = {
    "id",
    "snapshot_id",
    "session_id",
    "source_request_id",
    "symbol",
    "symbol_key",
    "market",
    "provider",
    "interval",
    "snapshot_time",
    "current_price",
    "trend",
    "stance",
    "support_json",
    "resistance_json",
    "payload_json",
    "created_at",
}

_ANALYSIS_SNAPSHOT_COMPAT_COLUMNS = {
    "idea_id",
    "last_price",
    "fib_zone",
    "risk_flags",
    "fixed_template",
    "raw_stats",
    "source_session_dir",
}

# 正式三表的目标列（与 models.py 中 JournalIdea / PaperOrder / JournalEvent 一致）。
# 旧库（如 Stock_Analysis 的 journal_001~journal_005 链）同名表缺这些列时，由
# ensure_paper_trading_schema 幂等补齐，存量兼容列保留不删，避免破坏兄弟项目写入。
_JOURNAL_IDEAS_TARGET_COLUMNS = {
    "idea_id",
    "session_id",
    "source_request_id",
    "source_snapshot_id",
    "current_order_id",
    "symbol",
    "symbol_key",
    "market",
    "provider",
    "interval",
    "side",
    "setup_type",
    "state",
    "entry_zone_low",
    "entry_zone_high",
    "stop_loss",
    "tp1",
    "tp2",
    "final_target",
    "valid_until",
    "opened_at",
    "opened_price",
    "closed_at",
    "closed_price",
    "close_reason",
    "pnl_pct",
    "strategy_reason",
    "meta_json",
    "created_at",
    "updated_at",
}

_PAPER_ORDERS_TARGET_COLUMNS = {
    "order_id",
    "idea_id",
    "symbol",
    "symbol_key",
    "market",
    "provider",
    "interval",
    "side",
    "order_type",
    "status",
    "entry_zone_low",
    "entry_zone_high",
    "position_size",
    "trigger_price",
    "confirm_close_above",
    "confirm_close_below",
    "limit_price",
    "stop_loss",
    "tp1",
    "tp2",
    "final_target",
    "valid_until",
    "timeout_bars",
    "filled_at",
    "filled_price",
    "closed_at",
    "closed_price",
    "close_reason",
    "realized_pnl_pct",
    "simulation_rule_json",
    "created_at",
    "updated_at",
}

_JOURNAL_EVENTS_TARGET_COLUMNS = {
    "event_id",
    "idea_id",
    "order_id",
    "session_id",
    "event_type",
    "old_idea_state",
    "new_idea_state",
    "old_order_status",
    "new_order_status",
    "event_time",
    "event_price",
    "source",
    "request_id",
    "payload_json",
    "created_at",
}

_SYMBOL_KEY_EXPR = "UPPER(REPLACE(REPLACE(COALESCE(symbol, ''), '_', ''), '-', ''))"


def ensure_runtime_schema(engine: Engine) -> None:
    ensure_analysis_snapshot_schema(engine)
    ensure_paper_trading_schema(engine)


def ensure_paper_trading_schema(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "journal_ideas" in tables:
        _ensure_journal_ideas_schema(engine, inspector)
    if "paper_orders" in tables:
        _ensure_paper_orders_schema(engine, inspector)
    if "journal_events" in tables:
        _ensure_journal_events_schema(engine, inspector)


def _ensure_journal_ideas_schema(engine: Engine, inspector) -> None:
    columns = {column["name"]: column for column in inspector.get_columns("journal_ideas")}
    if not _JOURNAL_IDEAS_TARGET_COLUMNS.issubset(columns):
        with engine.begin() as conn:
            _repair_journal_ideas_table(conn, columns)
        logger.info("[db-schema] journal_ideas repaired mode=compat_to_formal")
    with engine.begin() as conn:
        # 旧库兼容列（plan_type/direction/status）为 NOT NULL，而正式写入不提供这三列；
        # 放宽约束（幂等），保留列本身供兄弟项目继续写入。
        for stmt in (
            "ALTER TABLE journal_ideas ALTER COLUMN plan_type DROP NOT NULL",
            "ALTER TABLE journal_ideas ALTER COLUMN direction DROP NOT NULL",
            "ALTER TABLE journal_ideas ALTER COLUMN status DROP NOT NULL",
        ):
            conn.execute(text(stmt))
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_journal_ideas_session_state
                ON journal_ideas (session_id, state, updated_at)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_journal_ideas_symbol_interval_state
                ON journal_ideas (symbol_key, interval, state)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_journal_ideas_source_snapshot
                ON journal_ideas (source_snapshot_id)
                """
            )
        )


def _repair_journal_ideas_table(conn: Connection, columns: dict[str, dict]) -> None:
    for stmt in (
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS session_id VARCHAR(128) NOT NULL DEFAULT ''",
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS source_request_id VARCHAR(128) NOT NULL DEFAULT ''",
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS source_snapshot_id VARCHAR(64)",
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS current_order_id VARCHAR(64)",
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS symbol_key VARCHAR(64) NOT NULL DEFAULT ''",
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS side VARCHAR(16) NOT NULL DEFAULT ''",
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS setup_type VARCHAR(32) NOT NULL DEFAULT 'manual'",
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS state VARCHAR(24) NOT NULL DEFAULT 'watch'",
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS final_target NUMERIC(20, 8)",
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS opened_at TIMESTAMPTZ",
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS opened_price NUMERIC(20, 8)",
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS close_reason VARCHAR(32)",
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS pnl_pct NUMERIC(12, 6)",
        "ALTER TABLE journal_ideas ADD COLUMN IF NOT EXISTS meta_json JSONB",
    ):
        conn.execute(text(stmt))

    if {"direction", "status"}.intersection(columns):
        conn.execute(
            text(
                f"""
                UPDATE journal_ideas
                SET
                  symbol_key = COALESCE(NULLIF(symbol_key, ''), {_SYMBOL_KEY_EXPR}),
                  side = COALESCE(NULLIF(side, ''), LOWER(COALESCE(direction, ''))),
                  state = CASE
                    WHEN status = 'open' THEN 'open'
                    WHEN status = 'filled' THEN 'open'
                    WHEN status = 'closed' THEN 'closed'
                    WHEN status = 'expired' THEN 'expired'
                    WHEN status = 'cancelled' THEN 'cancelled'
                    ELSE state
                  END
                """
            )
        )


def _ensure_paper_orders_schema(engine: Engine, inspector) -> None:
    columns = {column["name"]: column for column in inspector.get_columns("paper_orders")}
    if not _PAPER_ORDERS_TARGET_COLUMNS.issubset(columns):
        with engine.begin() as conn:
            _repair_paper_orders_table(conn, columns)
        logger.info("[db-schema] paper_orders repaired mode=compat_to_formal")
    with engine.begin() as conn:
        # 旧库 order_type 为 VARCHAR(16)，正式模型允许 zone_reclaim_close(18 字符)；
        # 加宽避免写入超长失败（幂等）。
        conn.execute(text("ALTER TABLE paper_orders ALTER COLUMN order_type TYPE VARCHAR(32)"))
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_paper_orders_status_symbol
                ON paper_orders (status, symbol_key, interval)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_paper_orders_valid_until
                ON paper_orders (valid_until)
                """
            )
        )


def _repair_paper_orders_table(conn: Connection, columns: dict[str, dict]) -> None:
    for stmt in (
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS symbol_key VARCHAR(64) NOT NULL DEFAULT ''",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS entry_zone_low NUMERIC(20, 8)",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS entry_zone_high NUMERIC(20, 8)",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS confirm_close_above NUMERIC(20, 8)",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS confirm_close_below NUMERIC(20, 8)",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS stop_loss NUMERIC(20, 8)",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS tp1 NUMERIC(20, 8)",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS tp2 NUMERIC(20, 8)",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS final_target NUMERIC(20, 8)",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS timeout_bars INTEGER",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS filled_at TIMESTAMPTZ",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS filled_price NUMERIC(20, 8)",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS closed_price NUMERIC(20, 8)",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS close_reason VARCHAR(32)",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS realized_pnl_pct NUMERIC(12, 6)",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS simulation_rule_json JSONB",
    ):
        conn.execute(text(stmt))

    conn.execute(
        text(
            f"""
            UPDATE paper_orders
            SET symbol_key = COALESCE(NULLIF(symbol_key, ''), {_SYMBOL_KEY_EXPR})
            """
        )
    )


def _ensure_journal_events_schema(engine: Engine, inspector) -> None:
    columns = {column["name"]: column for column in inspector.get_columns("journal_events")}
    if not _JOURNAL_EVENTS_TARGET_COLUMNS.issubset(columns):
        with engine.begin() as conn:
            _repair_journal_events_table(conn)
        logger.info("[db-schema] journal_events repaired mode=compat_to_formal")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_journal_events_event_id
                ON journal_events (event_id)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_journal_events_idea_time
                ON journal_events (idea_id, event_time)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_journal_events_session_time
                ON journal_events (session_id, event_time)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_journal_events_order_time
                ON journal_events (order_id, event_time)
                """
            )
        )


def _repair_journal_events_table(conn: Connection) -> None:
    for stmt in (
        "ALTER TABLE journal_events ADD COLUMN IF NOT EXISTS event_id VARCHAR(64) NOT NULL DEFAULT ''",
        "ALTER TABLE journal_events ADD COLUMN IF NOT EXISTS order_id VARCHAR(64)",
        "ALTER TABLE journal_events ADD COLUMN IF NOT EXISTS session_id VARCHAR(128) NOT NULL DEFAULT ''",
        "ALTER TABLE journal_events ADD COLUMN IF NOT EXISTS old_idea_state VARCHAR(24)",
        "ALTER TABLE journal_events ADD COLUMN IF NOT EXISTS new_idea_state VARCHAR(24)",
        "ALTER TABLE journal_events ADD COLUMN IF NOT EXISTS old_order_status VARCHAR(32)",
        "ALTER TABLE journal_events ADD COLUMN IF NOT EXISTS new_order_status VARCHAR(32)",
        "ALTER TABLE journal_events ADD COLUMN IF NOT EXISTS event_price NUMERIC(20, 8)",
        "ALTER TABLE journal_events ADD COLUMN IF NOT EXISTS source VARCHAR(32) NOT NULL DEFAULT 'system'",
        "ALTER TABLE journal_events ADD COLUMN IF NOT EXISTS request_id VARCHAR(128) NOT NULL DEFAULT ''",
        "ALTER TABLE journal_events ADD COLUMN IF NOT EXISTS payload_json JSONB",
        "ALTER TABLE journal_events ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ):
        conn.execute(text(stmt))

    conn.execute(
        text(
            """
            UPDATE journal_events
            SET event_id = CONCAT('evt_', SUBSTRING(MD5(id::text) FROM 1 FOR 24))
            WHERE event_id = ''
            """
        )
    )


def ensure_analysis_snapshot_schema(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return

    inspector = inspect(engine)
    if "analysis_snapshots" not in inspector.get_table_names():
        return

    columns = {col["name"]: col for col in inspector.get_columns("analysis_snapshots")}
    if not _needs_analysis_snapshot_repair(columns):
        _ensure_analysis_snapshot_indexes(engine)
        return

    with engine.begin() as conn:
        _repair_analysis_snapshots_table(conn, columns)

    logger.info("[db-schema] analysis_snapshots repaired mode=compat_to_formal")


def _needs_analysis_snapshot_repair(columns: dict[str, dict]) -> bool:
    column_names = set(columns)
    if _ANALYSIS_SNAPSHOT_COMPAT_COLUMNS.intersection(column_names):
        return True
    if not _ANALYSIS_SNAPSHOT_TARGET_COLUMNS.issubset(column_names):
        return True

    snapshot_time = columns.get("snapshot_time") or {}
    snapshot_time_type = snapshot_time.get("type")
    if not bool(getattr(snapshot_time_type, "timezone", False)):
        return True
    return False


def _repair_analysis_snapshots_table(
    conn: Connection,
    original_columns: dict[str, dict],
) -> None:
    current_columns = set(original_columns)

    if "source_session_dir" in current_columns and "session_id" not in current_columns:
        conn.execute(text("ALTER TABLE analysis_snapshots RENAME COLUMN source_session_dir TO session_id"))
        current_columns.remove("source_session_dir")
        current_columns.add("session_id")
    elif "source_session_dir" in current_columns and "session_id" in current_columns:
        conn.execute(
            text(
                """
                UPDATE analysis_snapshots
                SET session_id = COALESCE(NULLIF(session_id, ''), NULLIF(source_session_dir, ''))
                """
            )
        )
        conn.execute(text("ALTER TABLE analysis_snapshots DROP COLUMN source_session_dir"))
        current_columns.remove("source_session_dir")

    if "last_price" in current_columns and "current_price" not in current_columns:
        conn.execute(text("ALTER TABLE analysis_snapshots RENAME COLUMN last_price TO current_price"))
        current_columns.remove("last_price")
        current_columns.add("current_price")
    elif "last_price" in current_columns and "current_price" in current_columns:
        conn.execute(
            text(
                """
                UPDATE analysis_snapshots
                SET current_price = COALESCE(current_price, last_price)
                """
            )
        )
        conn.execute(text("ALTER TABLE analysis_snapshots DROP COLUMN last_price"))
        current_columns.remove("last_price")

    if "raw_stats" in current_columns and "payload_json" not in current_columns:
        conn.execute(text("ALTER TABLE analysis_snapshots RENAME COLUMN raw_stats TO payload_json"))
        current_columns.remove("raw_stats")
        current_columns.add("payload_json")
    elif "raw_stats" in current_columns and "payload_json" in current_columns:
        conn.execute(
            text(
                """
                UPDATE analysis_snapshots
                SET payload_json = COALESCE(payload_json, raw_stats)
                """
            )
        )
        conn.execute(text("ALTER TABLE analysis_snapshots DROP COLUMN raw_stats"))
        current_columns.remove("raw_stats")

    add_column_sql = [
        "ALTER TABLE analysis_snapshots ADD COLUMN IF NOT EXISTS snapshot_id VARCHAR(64)",
        "ALTER TABLE analysis_snapshots ADD COLUMN IF NOT EXISTS source_request_id VARCHAR(128)",
        "ALTER TABLE analysis_snapshots ADD COLUMN IF NOT EXISTS symbol_key VARCHAR(64)",
        "ALTER TABLE analysis_snapshots ADD COLUMN IF NOT EXISTS market VARCHAR(32)",
        "ALTER TABLE analysis_snapshots ADD COLUMN IF NOT EXISTS stance VARCHAR(24)",
        "ALTER TABLE analysis_snapshots ADD COLUMN IF NOT EXISTS support_json JSONB",
        "ALTER TABLE analysis_snapshots ADD COLUMN IF NOT EXISTS resistance_json JSONB",
        "ALTER TABLE analysis_snapshots ADD COLUMN IF NOT EXISTS payload_json JSONB",
        "ALTER TABLE analysis_snapshots ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ",
    ]
    for stmt in add_column_sql:
        conn.execute(text(stmt))

    snapshot_time_type = (original_columns.get("snapshot_time") or {}).get("type")
    if "snapshot_time" in current_columns and not bool(getattr(snapshot_time_type, "timezone", False)):
        conn.execute(
            text(
                """
                ALTER TABLE analysis_snapshots
                ALTER COLUMN snapshot_time TYPE TIMESTAMPTZ
                USING snapshot_time AT TIME ZONE 'UTC'
                """
            )
        )

    alter_column_sql = [
        "ALTER TABLE analysis_snapshots ALTER COLUMN symbol TYPE VARCHAR(64)",
        "ALTER TABLE analysis_snapshots ALTER COLUMN provider TYPE VARCHAR(32)",
        "ALTER TABLE analysis_snapshots ALTER COLUMN interval TYPE VARCHAR(16)",
        "ALTER TABLE analysis_snapshots ALTER COLUMN trend TYPE VARCHAR(24)",
        "ALTER TABLE analysis_snapshots ALTER COLUMN session_id TYPE VARCHAR(128)",
        "ALTER TABLE analysis_snapshots ALTER COLUMN source_request_id TYPE VARCHAR(128)",
        "ALTER TABLE analysis_snapshots ALTER COLUMN symbol_key TYPE VARCHAR(64)",
        "ALTER TABLE analysis_snapshots ALTER COLUMN current_price TYPE NUMERIC(20, 8) USING current_price::numeric",
        "ALTER TABLE analysis_snapshots ALTER COLUMN provider SET DEFAULT 'marketassagent'",
        "ALTER TABLE analysis_snapshots ALTER COLUMN source_request_id SET DEFAULT ''",
        "ALTER TABLE analysis_snapshots ALTER COLUMN created_at SET DEFAULT NOW()",
    ]
    for stmt in alter_column_sql:
        conn.execute(text(stmt))

    fixed_expr = "COALESCE(fixed_template, '{}'::jsonb)" if "fixed_template" in current_columns else "'{}'::jsonb"
    payload_expr = "COALESCE(payload_json, '{}'::jsonb)"
    source_request_expr = (
        "COALESCE("
        "NULLIF(source_request_id, ''), "
        f"NULLIF(({fixed_expr} ->> 'request_id'), ''), "
        f"NULLIF(({payload_expr} ->> 'request_id'), ''), "
        "''"
        ")"
    )
    symbol_key_expr = (
        "COALESCE("
        "NULLIF(symbol_key, ''), "
        f"NULLIF(({payload_expr} ->> 'symbol_key'), ''), "
        "UPPER(REPLACE(REPLACE(COALESCE(symbol, ''), '_', ''), '-', ''))"
        ")"
    )
    snapshot_id_expr = (
        "COALESCE("
        "NULLIF(snapshot_id, ''), "
        f"NULLIF(({fixed_expr} ->> 'snapshot_id'), ''), "
        "CONCAT("
        "'snap_', "
        "SUBSTRING("
        "MD5("
        "CONCAT_WS("
        "'|', "
        "COALESCE(id::text, ''), "
        "COALESCE(session_id, ''), "
        "COALESCE(symbol, ''), "
        "COALESCE(interval, ''), "
        "COALESCE(snapshot_time::text, ''), "
        f"{source_request_expr}"
        ")"
        "), "
        "1, "
        "24"
        ")"
        ")"
        ")"
    )

    conn.execute(
        text(
            f"""
            UPDATE analysis_snapshots
            SET
              source_request_id = {source_request_expr},
              symbol_key = {symbol_key_expr},
              market = COALESCE(NULLIF(market, ''), NULLIF(({payload_expr} ->> 'market'), '')),
              provider = COALESCE(NULLIF(provider, ''), NULLIF(({payload_expr} ->> 'provider'), ''), 'marketassagent'),
              trend = COALESCE(NULLIF(trend, ''), NULLIF(({payload_expr} ->> 'trend'), ''), 'unknown'),
              current_price = COALESCE(
                current_price,
                NULLIF(({payload_expr} ->> 'current_price'), '')::numeric,
                NULLIF(({payload_expr} ->> 'price'), '')::numeric
              ),
              stance = COALESCE(
                NULLIF(stance, ''),
                NULLIF(({fixed_expr} ->> 'stance'), ''),
                NULLIF(({payload_expr} ->> 'stance'), '')
              ),
              support_json = COALESCE(
                support_json,
                CASE
                  WHEN jsonb_typeof({fixed_expr} -> 'support') = 'array' THEN {fixed_expr} -> 'support'
                  ELSE NULL
                END
              ),
              resistance_json = COALESCE(
                resistance_json,
                CASE
                  WHEN jsonb_typeof({fixed_expr} -> 'resistance') = 'array' THEN {fixed_expr} -> 'resistance'
                  ELSE NULL
                END
              ),
              payload_json = CASE
                WHEN payload_json IS NULL THEN {payload_expr}
                WHEN jsonb_typeof(payload_json) = 'object' THEN payload_json || jsonb_build_object(
                  'schema_version',
                  COALESCE(NULLIF(payload_json ->> 'schema_version', ''), NULLIF(({fixed_expr} ->> 'schema_version'), ''), 'analysis_snapshot.v1')
                )
                ELSE payload_json
              END,
              created_at = COALESCE(created_at, snapshot_time, NOW()),
              snapshot_id = {snapshot_id_expr}
            """
        )
    )

    conn.execute(
        text(
            """
            UPDATE analysis_snapshots
            SET payload_json = payload_json - 'request_id' - 'session_id' - 'symbol_key'
            WHERE payload_json IS NOT NULL
              AND jsonb_typeof(payload_json) = 'object'
            """
        )
    )

    not_null_sql = [
        "ALTER TABLE analysis_snapshots ALTER COLUMN snapshot_id SET NOT NULL",
        "ALTER TABLE analysis_snapshots ALTER COLUMN session_id SET NOT NULL",
        "ALTER TABLE analysis_snapshots ALTER COLUMN source_request_id SET NOT NULL",
        "ALTER TABLE analysis_snapshots ALTER COLUMN symbol SET NOT NULL",
        "ALTER TABLE analysis_snapshots ALTER COLUMN symbol_key SET NOT NULL",
        "ALTER TABLE analysis_snapshots ALTER COLUMN provider SET NOT NULL",
        "ALTER TABLE analysis_snapshots ALTER COLUMN interval SET NOT NULL",
        "ALTER TABLE analysis_snapshots ALTER COLUMN snapshot_time SET NOT NULL",
        "ALTER TABLE analysis_snapshots ALTER COLUMN current_price SET NOT NULL",
        "ALTER TABLE analysis_snapshots ALTER COLUMN trend SET NOT NULL",
        "ALTER TABLE analysis_snapshots ALTER COLUMN created_at SET NOT NULL",
    ]
    for stmt in not_null_sql:
        conn.execute(text(stmt))

    drop_compat_sql = [
        "ALTER TABLE analysis_snapshots DROP COLUMN IF EXISTS idea_id",
        "ALTER TABLE analysis_snapshots DROP COLUMN IF EXISTS fib_zone",
        "ALTER TABLE analysis_snapshots DROP COLUMN IF EXISTS risk_flags",
        "ALTER TABLE analysis_snapshots DROP COLUMN IF EXISTS fixed_template",
        "ALTER TABLE analysis_snapshots DROP COLUMN IF EXISTS raw_stats",
        "ALTER TABLE analysis_snapshots DROP COLUMN IF EXISTS source_session_dir",
        "ALTER TABLE analysis_snapshots DROP COLUMN IF EXISTS last_price",
    ]
    for stmt in drop_compat_sql:
        conn.execute(text(stmt))

    _ensure_analysis_snapshot_indexes(conn)


def _ensure_analysis_snapshot_indexes(bind: Engine | Connection) -> None:
    dialect_name = bind.dialect.name
    if dialect_name != "postgresql":
        return
    if isinstance(bind, Engine):
        with bind.begin() as conn:
            _ensure_analysis_snapshot_indexes(conn)
        return

    conn = bind
    conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_snapshots_snapshot_id
            ON analysis_snapshots (snapshot_id)
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_analysis_snapshots_session_symbol_interval_time
            ON analysis_snapshots (session_id, symbol_key, interval, snapshot_time DESC)
            """
        )
    )
