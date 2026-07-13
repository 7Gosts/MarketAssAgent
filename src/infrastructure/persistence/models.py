from sqlalchemy import BigInteger, Column, Integer, String, Float, DateTime, Text, Index, JSON, Numeric
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
