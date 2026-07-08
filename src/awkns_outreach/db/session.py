"""Synchronous SQLAlchemy engine + session.

Sync (not async) on purpose: CrewAI is synchronous and the sequencer does
blocking, human-scale pacing sleeps, so an async stack would buy nothing but
foot-guns. FastAPI runs sync route handlers in a threadpool, so the web layer
stays responsive regardless.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from awkns_outreach.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency — yields a session, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
