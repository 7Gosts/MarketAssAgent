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


def ensure_runtime_schema(engine: Engine) -> None:
    ensure_analysis_snapshot_schema(engine)
    ensure_paper_trading_schema(engine)


def ensure_paper_trading_schema(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return

    if "paper_orders" not in inspect(engine).get_table_names():
        return

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS position_size NUMERIC(20, 8)"))

    logger.info("[db-schema] paper_orders ensured position_size column")


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
