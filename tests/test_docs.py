"""Docs viewer: lists top-level docs/*.md and renders one to HTML."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach.config import settings
from awkns_outreach.db.session import Base, get_db
from awkns_outreach.web.app import app

AUTH = ("admin", "secret")


@pytest.fixture
def client():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TestSession = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

    def override_get_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    eng.dispose()


@pytest.fixture(autouse=True)
def _admin_password(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")


def test_docs_index_lists_and_renders(client):
    r = client.get("/docs", auth=AUTH)
    assert r.status_code == 200
    assert "/docs/send-limits" in r.text      # the doc is listed
    assert "<table>" in r.text                # markdown tables render


def test_specific_doc_renders_markdown(client):
    r = client.get("/docs/send-limits", auth=AUTH)
    assert r.status_code == 200
    assert "寄送額度與節奏規則" in r.text        # original Chinese heading kept


def test_missing_doc_404(client):
    assert client.get("/docs/nope", auth=AUTH).status_code == 404


def test_requires_admin(client):
    assert client.get("/docs").status_code == 401
