"""Web layer: unsubscribe (GET + one-click POST), Resend webhook, and admin
auth/flow. Uses a shared in-memory SQLite via a get_db dependency override."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach.compliance import make_unsub_token
from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, Event, Lead, Suppression
from awkns_outreach.db.session import Base, get_db
from awkns_outreach.web.app import app


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


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_unsubscribe_get_suppresses(client, session):
    token = make_unsub_token("a@b.com")
    r = client.get(f"/outreach/unsubscribe?token={token}")
    assert r.status_code == 200 and "unsubscribed" in r.text.lower()
    assert session.get(Suppression, "a@b.com") is not None


def test_unsubscribe_one_click_post(client, session):
    token = make_unsub_token("click@b.com")
    r = client.post(f"/outreach/unsubscribe?token={token}")
    assert r.status_code == 200
    assert session.get(Suppression, "click@b.com") is not None


def test_unsubscribe_invalid_token(client, session):
    r = client.get("/outreach/unsubscribe?token=garbage")
    assert r.status_code == 400
    assert session.query(Suppression).count() == 0


def test_webhook_bounce_suppresses_and_logs(client, session):
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.flush()
    session.add(Lead(campaign_id=c.id, email="bounce@x.com", company="X", status="active"))
    session.commit()

    r = client.post("/webhooks/resend", json={
        "type": "email.bounced",
        "data": {"to": ["bounce@x.com"], "email_id": "e1"},
    })
    assert r.status_code == 200
    assert session.get(Suppression, "bounce@x.com").reason == "bounce"
    assert session.query(Event).filter_by(type="bounce").count() == 1


def test_webhook_ignores_unknown_recipient(client):
    r = client.post("/webhooks/resend", json={"type": "email.opened", "data": {}})
    assert r.status_code == 200 and r.json().get("ignored")


def test_admin_requires_auth(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("admin", "wrong")).status_code == 401
    assert client.get("/", auth=("admin", "secret")).status_code == 200


def test_admin_create_and_view_campaign(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    r = client.post("/campaigns", auth=auth, data={
        "name": "JP studios", "titles": "creative director, head of content",
        "seed_text": '[{"name": "Toyota", "website": "https://www.toyota.co.jp", "priority": "A"}]',
        "angle_prompt": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    c = session.query(Campaign).one()
    assert c.name == "JP studios"
    assert c.target_titles == ["creative director", "head of content"]
    assert c.seed_companies == [{"name": "Toyota", "website": "toyota.co.jp", "priority": "A"}]

    detail = client.get(f"/campaigns/{c.id}", auth=auth)
    assert detail.status_code == 200 and "JP studios" in detail.text


def test_sequence_editor_saves_steps(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[], sequence=[], sender_identity={})
    session.add(c)
    session.commit()

    # Editor page renders (with the placeholder cheatsheet).
    page = client.get(f"/campaigns/{c.id}/sequence", auth=auth)
    assert page.status_code == 200 and "{angle}" in page.text

    # Save two steps; a fully-blank third row is dropped.
    r = client.post(f"/campaigns/{c.id}/sequence", auth=auth, follow_redirects=False, data={
        "step_key": ["intro", "bump", ""],
        "delay_days": ["0", "3", "0"],
        "subject": ["quick idea for {company}", "re: quick idea", ""],
        "body": ["Hi {first_name}, {angle}", "floating back up", ""],
    })
    assert r.status_code == 303
    session.refresh(c)
    assert len(c.sequence) == 2
    assert c.sequence[0] == {"key": "intro", "delay_days": 0,
                             "subject": "quick idea for {company}", "body": "Hi {first_name}, {angle}"}
    assert c.sequence[1]["key"] == "bump" and c.sequence[1]["delay_days"] == 3


def test_sequence_editor_clearing_all_steps(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[],
                 sequence=[{"key": "x", "delay_days": 0, "subject": "s", "body": "b"}],
                 sender_identity={})
    session.add(c)
    session.commit()
    r = client.post(f"/campaigns/{c.id}/sequence", auth=("admin", "secret"),
                    follow_redirects=False, data={"step_key": [], "delay_days": [],
                                                  "subject": [], "body": []})
    assert r.status_code == 303
    session.refresh(c)
    assert c.sequence == []
