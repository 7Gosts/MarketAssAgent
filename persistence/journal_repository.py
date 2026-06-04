from __future__ import annotations

from typing import Optional, List
from sqlalchemy.orm import Session
from .db import get_session
from .models import Journal


class JournalRepository:
    def __init__(self, session: Optional[Session] = None):
        self.session = session or get_session()

    def create(self, **kwargs) -> Journal:
        journal = Journal(**kwargs)
        self.session.add(journal)
        self.session.commit()
        self.session.refresh(journal)
        return journal

    def get_by_id(self, journal_id: int) -> Optional[Journal]:
        return self.session.query(Journal).filter(Journal.id == journal_id).first()

    def list_by_session(self, session_id: str, limit: int = 50) -> List[Journal]:
        return (
            self.session.query(Journal)
            .filter(Journal.session_id == session_id)
            .order_by(Journal.created_at.desc())
            .limit(limit)
            .all()
        )

    def update_status(self, journal_id: int, status: str) -> Optional[Journal]:
        journal = self.get_by_id(journal_id)
        if journal:
            journal.status = status
            self.session.commit()
            self.session.refresh(journal)
        return journal
