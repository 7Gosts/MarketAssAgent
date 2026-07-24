from sqlalchemy import BigInteger, Column, Integer, String, Float, DateTime, Text, Index, JSON, Numeric, CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, timezone

Base = declarative_base()
JSON_VARIANT = JSON().with_variant(JSONB, "postgresql")
ID_VARIANT = Integer().with_variant(BigInteger, "postgresql")


class Journal(Base):
    """交易台账记录"""
    __tablename__ = "journals"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, index=True)
    symbol = Column(String, nullable=False)
    direction = Column(String)          # long / short
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    status = Column(String, default="open")   # open / closed / stopped
    created_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text)


class AnalysisSnapshot(Base):
    """分析快照证据表：为同标的/同周期历史对比与后续交易关联提供稳定引用。"""

    __tablename__ = "analysis_snapshots"
    __table_args__ = (
        Index(
            "uq_analysis_snapshots_snapshot_id",
            "snapshot_id",
            unique=True,
        ),
        Index(
            "idx_analysis_snapshots_session_symbol_interval_time",
            "session_id",
            "symbol_key",
            "interval",
            "snapshot_time",
        ),
    )

    id = Column(ID_VARIANT, primary_key=True, autoincrement=True)
    snapshot_id = Column(String(64), nullable=False)
    session_id = Column(String(128), nullable=False)
    source_request_id = Column(String(128), nullable=False, default="", server_default="")
    symbol = Column(String(64), nullable=False)
    symbol_key = Column(String(64), nullable=False)
    market = Column(String(32))
    provider = Column(String(32), nullable=False, default="marketassagent", server_default="marketassagent")
    interval = Column(String(16), nullable=False)
    snapshot_time = Column(DateTime(timezone=True), nullable=False, index=True)
    current_price = Column(Numeric(20, 8, asdecimal=False), nullable=False)
    trend = Column(String(24), nullable=False)
    stance = Column(String(24))
    support_json = Column(JSON_VARIANT)
    resistance_json = Column(JSON_VARIANT)
    payload_json = Column(JSON_VARIANT)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class JournalIdea(Base):
    """正式交易想法表：记录为什么跟踪这笔模拟交易。"""

    __tablename__ = "journal_ideas"
    __table_args__ = (
        Index("uq_journal_ideas_idea_id", "idea_id", unique=True),
        Index("idx_journal_ideas_session_state", "session_id", "state", "updated_at"),
        Index("idx_journal_ideas_symbol_interval_state", "symbol_key", "interval", "state"),
        Index("idx_journal_ideas_source_snapshot", "source_snapshot_id"),
        CheckConstraint("side IN ('long', 'short')", name="ck_journal_ideas_side"),
        CheckConstraint(
            "state IN ('watch', 'open', 'closed', 'expired', 'cancelled')",
            name="ck_journal_ideas_state",
        ),
    )

    id = Column(ID_VARIANT, primary_key=True, autoincrement=True)
    idea_id = Column(String(64), nullable=False)
    session_id = Column(String(128), nullable=False)
    source_request_id = Column(String(128), nullable=False, default="", server_default="")
    source_snapshot_id = Column(String(64))
    current_order_id = Column(String(64))

    symbol = Column(String(64), nullable=False)
    symbol_key = Column(String(64), nullable=False)
    market = Column(String(32))
    provider = Column(String(32), nullable=False, default="marketassagent", server_default="marketassagent")
    interval = Column(String(16), nullable=False)
    side = Column(String(16), nullable=False)
    setup_type = Column(String(32), nullable=False, default="manual", server_default="manual")

    state = Column(String(24), nullable=False, default="watch", server_default="watch")
    entry_zone_low = Column(Numeric(20, 8, asdecimal=False))
    entry_zone_high = Column(Numeric(20, 8, asdecimal=False))
    stop_loss = Column(Numeric(20, 8, asdecimal=False))
    tp1 = Column(Numeric(20, 8, asdecimal=False))
    tp2 = Column(Numeric(20, 8, asdecimal=False))
    final_target = Column(Numeric(20, 8, asdecimal=False))
    valid_until = Column(DateTime(timezone=True))

    opened_at = Column(DateTime(timezone=True))
    opened_price = Column(Numeric(20, 8, asdecimal=False))
    closed_at = Column(DateTime(timezone=True))
    closed_price = Column(Numeric(20, 8, asdecimal=False))
    close_reason = Column(String(32))
    pnl_pct = Column(Numeric(12, 6, asdecimal=False))

    strategy_reason = Column(Text)
    meta_json = Column(JSON_VARIANT)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class PaperOrder(Base):
    """正式模拟委托表：记录等待触发、成交、关闭等执行真相。"""

    __tablename__ = "paper_orders"
    __table_args__ = (
        Index("uq_paper_orders_order_id", "order_id", unique=True),
        Index("idx_paper_orders_idea_id", "idea_id"),
        Index("idx_paper_orders_status_symbol", "status", "symbol_key", "interval"),
        Index("idx_paper_orders_valid_until", "valid_until"),
        CheckConstraint("side IN ('long', 'short')", name="ck_paper_orders_side"),
        CheckConstraint(
            "status IN ('pending_trigger', 'filled', 'closed', 'expired', 'cancelled')",
            name="ck_paper_orders_status",
        ),
        CheckConstraint(
            "order_type IN ('breakout_stop', 'pullback_limit', 'zone_reclaim_close')",
            name="ck_paper_orders_order_type",
        ),
    )

    id = Column(ID_VARIANT, primary_key=True, autoincrement=True)
    order_id = Column(String(64), nullable=False)
    idea_id = Column(String(64), nullable=False)

    symbol = Column(String(64), nullable=False)
    symbol_key = Column(String(64), nullable=False)
    market = Column(String(32))
    provider = Column(String(32), nullable=False, default="marketassagent", server_default="marketassagent")
    interval = Column(String(16), nullable=False)
    side = Column(String(16), nullable=False)
    order_type = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="pending_trigger", server_default="pending_trigger")

    entry_zone_low = Column(Numeric(20, 8, asdecimal=False))
    entry_zone_high = Column(Numeric(20, 8, asdecimal=False))
    position_size = Column(Numeric(20, 8, asdecimal=False))
    trigger_price = Column(Numeric(20, 8, asdecimal=False))
    confirm_close_above = Column(Numeric(20, 8, asdecimal=False))
    confirm_close_below = Column(Numeric(20, 8, asdecimal=False))
    limit_price = Column(Numeric(20, 8, asdecimal=False))

    stop_loss = Column(Numeric(20, 8, asdecimal=False))
    tp1 = Column(Numeric(20, 8, asdecimal=False))
    tp2 = Column(Numeric(20, 8, asdecimal=False))
    final_target = Column(Numeric(20, 8, asdecimal=False))
    valid_until = Column(DateTime(timezone=True))
    timeout_bars = Column(Integer)

    filled_at = Column(DateTime(timezone=True))
    filled_price = Column(Numeric(20, 8, asdecimal=False))
    closed_at = Column(DateTime(timezone=True))
    closed_price = Column(Numeric(20, 8, asdecimal=False))
    close_reason = Column(String(32))
    realized_pnl_pct = Column(Numeric(12, 6, asdecimal=False))

    simulation_rule_json = Column(JSON_VARIANT)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class JournalEvent(Base):
    """交易事件流：所有状态变化都以 append-only 方式记录。"""

    __tablename__ = "journal_events"
    __table_args__ = (
        Index("uq_journal_events_event_id", "event_id", unique=True),
        Index("idx_journal_events_idea_time", "idea_id", "event_time"),
        Index("idx_journal_events_session_time", "session_id", "event_time"),
        Index("idx_journal_events_order_time", "order_id", "event_time"),
    )

    id = Column(ID_VARIANT, primary_key=True, autoincrement=True)
    event_id = Column(String(64), nullable=False)
    idea_id = Column(String(64), nullable=False)
    order_id = Column(String(64))
    session_id = Column(String(128), nullable=False)

    event_type = Column(String(48), nullable=False)
    old_idea_state = Column(String(24))
    new_idea_state = Column(String(24))
    old_order_status = Column(String(32))
    new_order_status = Column(String(32))

    event_time = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    event_price = Column(Numeric(20, 8, asdecimal=False))
    source = Column(String(32), nullable=False, default="system", server_default="system")
    request_id = Column(String(128), nullable=False, default="", server_default="")
    payload_json = Column(JSON_VARIANT)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
