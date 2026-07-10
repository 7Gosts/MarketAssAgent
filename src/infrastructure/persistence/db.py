from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from config.runtime_config import get_postgres_dsn

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        dsn = get_postgres_dsn()
        if not dsn:
            raise RuntimeError("未配置 database.postgres.dsn（见 runtime/config/analysis_defaults.yaml）")
        _engine = create_engine(dsn, pool_pre_ping=True)
    return _engine


def get_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal()


def init_db():
    """初始化数据库（创建所有表）"""
    from .models import Base
    Base.metadata.create_all(bind=get_engine())
