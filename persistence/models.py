from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()


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
