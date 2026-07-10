"""Mailboxes web: list rendering, the connect/callback OAuth round-trip,
disconnect, and the campaign edit page's mailbox_id persistence. Same
TestClient + dependency_overrides + monkeypatch admin_password idiom as
test_web.py (duplicated here rather than shared, to keep each test module
self-contained)."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, Mailbox
from awkns_outreach.db.session import Base, get_db
from awkns_outreach.gmail.oauth import make_oauth_state
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


def test_mailboxes_list_renders_statuses(client, session):
    session.add_all([
        Mailbox(email="a@gmail.com", status="connected"),
        Mailbox(email="b@gmail.com", status="needs_reconnect"),
    ])
    session.commit()
    r = client.get("/mailboxes", auth=AUTH)
    assert r.status_code == 200
    assert "a@gmail.com" in r.text and "b@gmail.com" in r.text
    assert "needs reconnect" in r.text


def test_connect_redirects_to_google(client):
    r = client.get("/mailboxes/connect", auth=AUTH, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth")


@respx.mock
def test_callback_valid_state_creates_mailbox_row(client, session):
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "at1", "refresh_token": "rt1", "expires_in": 3600, "scope": "gmail.send",
        })
    )
    respx.get("https://gmail.googleapis.com/gmail/v1/users/me/profile").mock(
        return_value=httpx.Response(200, json={"emailAddress": "steven@gmail.com"})
    )
    state = make_oauth_state()
    r = client.get(
        f"/oauth/google/callback?code=authcode&state={state}", auth=AUTH, follow_redirects=False
    )
    assert r.status_code == 303
    mb = session.query(Mailbox).filter_by(email="steven@gmail.com").one()
    assert mb.status == "connected"
    assert mb.refresh_token == "rt1"


def test_callback_bad_state_400(client):
    r = client.get("/oauth/google/callback?code=x&state=garbage", auth=AUTH)
    assert r.status_code == 400


def test_disconnect_clears_tokens(client, session):
    mb = Mailbox(
        email="a@gmail.com", access_token="at", refresh_token="rt",
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1), status="connected",
    )
    session.add(mb)
    session.commit()

    with respx.mock:
        respx.post("https://oauth2.googleapis.com/revoke").mock(return_value=httpx.Response(200))
        r = client.post(f"/mailboxes/{mb.id}/disconnect", auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    session.refresh(mb)
    assert mb.status == "disconnected"
    assert mb.access_token is None and mb.refresh_token is None


def test_campaign_edit_persists_mailbox_id(client, session):
    mb = Mailbox(email="a@gmail.com", status="connected")
    session.add(mb)
    c = Campaign(name="c", target_titles=[], seed_companies=[], status="active")
    session.add(c)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/edit", auth=AUTH, follow_redirects=False, data={
        "name": "c", "description": "", "titles": "", "angle_prompt": "", "mailbox_id": mb.id,
    })
    assert r.status_code == 303
    session.refresh(c)
    assert c.mailbox_id == mb.id
