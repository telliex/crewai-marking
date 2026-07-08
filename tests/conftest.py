import os

# Set required env vars before any project modules are imported
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("SERPER_API_KEY", "test-key")
os.environ.setdefault("GOOGLE_SHEETS_ID", "test-sheet-id")
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_JSON", "./fake_service_account.json")
os.environ.setdefault("APOLLO_API_KEY", "test-apollo-key")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def db_session() -> Session:
    """A throwaway in-memory SQLite DB with the full schema, one session.

    Portable model types (JSONType/StrArray) map to JSON here; Postgres-only
    behaviour (true concurrent CAS) is exercised separately where a real
    Postgres URL is provided.
    """
    from awkns_outreach.db.session import Base
    from awkns_outreach.db import models  # noqa: F401  (register tables)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
