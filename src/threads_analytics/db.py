"""Database engine, session factory, and schema bootstrap."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from .account_scope import get_or_create_default_account
from .config import get_settings
from .models import Base, Run

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(settings.database_url, future=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db() -> None:
    """Create all tables if they don't already exist."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    with session_scope() as session:
        get_or_create_default_account(session)
    recover_orphaned_runs()


def recover_orphaned_runs() -> None:
    """Mark long-running jobs as failed on startup (crash recovery)."""
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        stmt = select(Run).where(Run.status == "running", Run.started_at < cutoff)
        for run in session.scalars(stmt):
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.notes = (run.notes or "") + " [recovered as failed on startup]"
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope around a series of operations."""
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
