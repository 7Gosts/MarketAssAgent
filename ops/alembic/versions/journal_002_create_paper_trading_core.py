"""Create paper trading core tables

Revision ID: journal_002
Revises: journal_001
Create Date: 2026-07-16

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "journal_002"
down_revision = "journal_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "journal_ideas",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("idea_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("source_request_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("source_snapshot_id", sa.String(length=64)),
        sa.Column("current_order_id", sa.String(length=64)),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("symbol_key", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=32)),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="marketassagent"),
        sa.Column("interval", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("setup_type", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="watch"),
        sa.Column("entry_zone_low", sa.Numeric(20, 8)),
        sa.Column("entry_zone_high", sa.Numeric(20, 8)),
        sa.Column("stop_loss", sa.Numeric(20, 8)),
        sa.Column("tp1", sa.Numeric(20, 8)),
        sa.Column("tp2", sa.Numeric(20, 8)),
        sa.Column("final_target", sa.Numeric(20, 8)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("opened_at", sa.DateTime(timezone=True)),
        sa.Column("opened_price", sa.Numeric(20, 8)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_price", sa.Numeric(20, 8)),
        sa.Column("close_reason", sa.String(length=32)),
        sa.Column("pnl_pct", sa.Numeric(12, 6)),
        sa.Column("strategy_reason", sa.Text()),
        sa.Column("meta_json", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("side IN ('long', 'short')", name="ck_journal_ideas_side"),
        sa.CheckConstraint(
            "state IN ('watch', 'open', 'closed', 'expired', 'cancelled')",
            name="ck_journal_ideas_state",
        ),
    )
    op.create_index("uq_journal_ideas_idea_id", "journal_ideas", ["idea_id"], unique=True)
    op.create_index(
        "idx_journal_ideas_session_state",
        "journal_ideas",
        ["session_id", "state", "updated_at"],
        unique=False,
    )
    op.create_index(
        "idx_journal_ideas_symbol_interval_state",
        "journal_ideas",
        ["symbol_key", "interval", "state"],
        unique=False,
    )
    op.create_index("idx_journal_ideas_source_snapshot", "journal_ideas", ["source_snapshot_id"], unique=False)

    op.create_table(
        "paper_orders",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("idea_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("symbol_key", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=32)),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="marketassagent"),
        sa.Column("interval", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("order_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending_trigger"),
        sa.Column("entry_zone_low", sa.Numeric(20, 8)),
        sa.Column("entry_zone_high", sa.Numeric(20, 8)),
        sa.Column("trigger_price", sa.Numeric(20, 8)),
        sa.Column("confirm_close_above", sa.Numeric(20, 8)),
        sa.Column("confirm_close_below", sa.Numeric(20, 8)),
        sa.Column("limit_price", sa.Numeric(20, 8)),
        sa.Column("stop_loss", sa.Numeric(20, 8)),
        sa.Column("tp1", sa.Numeric(20, 8)),
        sa.Column("tp2", sa.Numeric(20, 8)),
        sa.Column("final_target", sa.Numeric(20, 8)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("timeout_bars", sa.Integer()),
        sa.Column("filled_at", sa.DateTime(timezone=True)),
        sa.Column("filled_price", sa.Numeric(20, 8)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_price", sa.Numeric(20, 8)),
        sa.Column("close_reason", sa.String(length=32)),
        sa.Column("realized_pnl_pct", sa.Numeric(12, 6)),
        sa.Column("simulation_rule_json", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("side IN ('long', 'short')", name="ck_paper_orders_side"),
        sa.CheckConstraint(
            "status IN ('pending_trigger', 'filled', 'closed', 'expired', 'cancelled')",
            name="ck_paper_orders_status",
        ),
        sa.CheckConstraint(
            "order_type IN ('breakout_stop', 'pullback_limit', 'zone_reclaim_close')",
            name="ck_paper_orders_order_type",
        ),
    )
    op.create_index("uq_paper_orders_order_id", "paper_orders", ["order_id"], unique=True)
    op.create_index("idx_paper_orders_idea_id", "paper_orders", ["idea_id"], unique=False)
    op.create_index(
        "idx_paper_orders_status_symbol",
        "paper_orders",
        ["status", "symbol_key", "interval"],
        unique=False,
    )
    op.create_index("idx_paper_orders_valid_until", "paper_orders", ["valid_until"], unique=False)

    op.create_table(
        "journal_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("idea_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=64)),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("old_idea_state", sa.String(length=24)),
        sa.Column("new_idea_state", sa.String(length=24)),
        sa.Column("old_order_status", sa.String(length=32)),
        sa.Column("new_order_status", sa.String(length=32)),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("event_price", sa.Numeric(20, 8)),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="system"),
        sa.Column("request_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("uq_journal_events_event_id", "journal_events", ["event_id"], unique=True)
    op.create_index("idx_journal_events_idea_time", "journal_events", ["idea_id", "event_time"], unique=False)
    op.create_index("idx_journal_events_session_time", "journal_events", ["session_id", "event_time"], unique=False)
    op.create_index("idx_journal_events_order_time", "journal_events", ["order_id", "event_time"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_journal_events_order_time", table_name="journal_events")
    op.drop_index("idx_journal_events_session_time", table_name="journal_events")
    op.drop_index("idx_journal_events_idea_time", table_name="journal_events")
    op.drop_index("uq_journal_events_event_id", table_name="journal_events")
    op.drop_table("journal_events")

    op.drop_index("idx_paper_orders_valid_until", table_name="paper_orders")
    op.drop_index("idx_paper_orders_status_symbol", table_name="paper_orders")
    op.drop_index("idx_paper_orders_idea_id", table_name="paper_orders")
    op.drop_index("uq_paper_orders_order_id", table_name="paper_orders")
    op.drop_table("paper_orders")

    op.drop_index("idx_journal_ideas_source_snapshot", table_name="journal_ideas")
    op.drop_index("idx_journal_ideas_symbol_interval_state", table_name="journal_ideas")
    op.drop_index("idx_journal_ideas_session_state", table_name="journal_ideas")
    op.drop_index("uq_journal_ideas_idea_id", table_name="journal_ideas")
    op.drop_table("journal_ideas")
