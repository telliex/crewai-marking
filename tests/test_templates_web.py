"""Template library web: CRUD, preview against the hard-coded example
contact, and test-send (Resend fallback, or a connected Gmail mailbox)."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach.config import settings
from awkns_outreach.db.models import EmailTemplate, Mailbox
from awkns_outreach.db.session import Base, get_db
from awkns_outreach.web.app import app

AUTH = ("admin", "secret")


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def client(engine):
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def session(engine):
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = TestSession()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _admin_password(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")


def test_create_edit_delete_template(client, session):
    r = client.post("/templates", auth=AUTH, follow_redirects=False, data={
        "name": "Intro", "subject": "quick idea for {company}", "body": "Hi {first_name}, {angle}",
    })
    assert r.status_code == 303
    t = session.query(EmailTemplate).one()
    assert t.name == "Intro"

    edit_page = client.get(f"/templates/{t.id}/edit", auth=AUTH)
    assert edit_page.status_code == 200 and "Intro" in edit_page.text

    r2 = client.post(f"/templates/{t.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "Intro v2", "subject": "s2", "body": "b2",
    })
    assert r2.status_code == 303
    session.refresh(t)
    assert t.name == "Intro v2" and t.subject == "s2" and t.body == "b2"

    list_page = client.get("/templates", auth=AUTH)
    assert "Intro v2" in list_page.text

    r3 = client.post(f"/templates/{t.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "delete", "name": "x", "subject": "x", "body": "x",
    })
    assert r3.status_code == 303
    assert session.query(EmailTemplate).count() == 0


def test_new_template_defaults_to_active_status(client, session):
    r = client.post("/templates", auth=AUTH, follow_redirects=False, data={
        "name": "Intro", "subject": "s", "body": "b",
    })
    assert r.status_code == 303
    t = session.query(EmailTemplate).one()
    assert t.status == "active"


def test_preview_fragment_renders_example_contact_without_saved_template(client, session):
    r = client.post("/templates/preview-fragment", auth=AUTH, data={
        "subject": "hi {company}", "body": "Hi {first_name}, {angle}",
    })
    assert r.status_code == 200
    assert "hi Acme Studios" in r.text
    assert "Hi Jamie," in r.text
    assert session.query(EmailTemplate).count() == 0  # no template was created/required
