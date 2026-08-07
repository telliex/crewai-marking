"""Sender profiles: the profile → identity resolution + fallback, the Gmail
mailbox interaction, the running-task edit/delete lock, and the CRUD page."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach.config import settings
from awkns_outreach.db.models import (
    Campaign, Mailbox, MailSequence, SenderProfile, Task,
)
from awkns_outreach.db.session import Base, get_db
from awkns_outreach.identity import campaign_overrides, resolve_identity
from awkns_outreach.send.mailer import _apply_mailbox_identity
from awkns_outreach.sequencer import lifecycle
from awkns_outreach.web.app import app
from awkns_outreach.web.routes.senders import profile_locked

UTC = timezone.utc
NOW = datetime(2026, 7, 6, 2, 0, tzinfo=UTC)  # Monday, business hours in Taipei
AUTH = ("admin", "secret")


def _campaign(session, **kw):
    base = dict(name="c", target_titles=[], seed_companies=[], sender_identity={},
                warmup_start=datetime(2026, 1, 1, tzinfo=UTC))
    base.update(kw)
    c = Campaign(**base)
    session.add(c)
    session.flush()
    return c


def _profile(session, **kw):
    base = dict(name="P", from_email="", from_name="", reply_to="", sender_name="",
                company="", postal_address="", unsubscribe_mailto="", status="active")
    base.update(kw)
    p = SenderProfile(**base)
    session.add(p)
    session.flush()
    return p


# --- resolution & fallback ---------------------------------------------------

def test_campaign_overrides_none_profile_returns_sender_identity(db_session):
    c = _campaign(db_session, sender_identity={"from": "x@y.com"})
    assert campaign_overrides(c) == {"from": "x@y.com"}


def test_campaign_overrides_uses_profile(db_session):
    p = _profile(db_session, from_email="sales@co.com", from_name="Sales")
    c = _campaign(db_session, sender_profile_id=p.id)
    ov = campaign_overrides(c)
    assert ov["from"] == "sales@co.com"
    assert ov["from_name"] == "Sales"


def test_profile_blank_reply_to_falls_back_to_its_own_from(db_session):
    p = _profile(db_session, from_email="sales@co.com", reply_to="")
    assert p.as_overrides()["reply_to"] == "sales@co.com"


def test_profile_blank_fields_fall_back_to_global(db_session, monkeypatch):
    monkeypatch.setattr(settings, "outreach_from", "global@co.com")
    monkeypatch.setattr(settings, "outreach_company", "GlobalCo")
    p = _profile(db_session, from_name="Only Name")  # from_email/company blank
    c = _campaign(db_session, sender_profile_id=p.id)
    ident = resolve_identity(campaign_overrides(c))
    assert ident.from_name == "Only Name"       # from the profile
    assert ident.from_email == "global@co.com"  # blank → global fallback
    assert ident.company == "GlobalCo"


# --- Gmail mailbox interaction ----------------------------------------------

def test_apply_mailbox_identity_keeps_profile_from_name_and_reply_to(db_session):
    p = _profile(db_session, from_email="sales@co.com", from_name="Sales",
                 reply_to="reply@co.com")
    c = _campaign(db_session, sender_profile_id=p.id)
    mailbox = Mailbox(email="inbox@gmail.com", display_name="Inbox Bot", status="connected")
    ident = resolve_identity(campaign_overrides(c))
    _apply_mailbox_identity(ident, mailbox, c)
    # Gmail forces the From address to the mailbox...
    assert ident.from_email == "inbox@gmail.com"
    # ...but the profile's from_name / reply_to still win over the mailbox.
    assert ident.from_name == "Sales"
    assert ident.reply_to == "reply@co.com"


# --- running-task lock -------------------------------------------------------

def _running_task(session, campaign):
    seq = MailSequence(name="Seq", status="active",
                       steps=[{"key": "intro", "delay_days": 0, "subject": "hi", "body": "b"}])
    session.add(seq)
    session.flush()
    task = Task(name="T", campaign_id=campaign.id, status="draft", sequences={"B": seq.id})
    session.add(task)
    session.flush()
    ok, _ = lifecycle.start_task(session, task, NOW)
    assert ok
    return task


def test_profile_unlocked_without_running_task(db_session):
    p = _profile(db_session)
    c = _campaign(db_session, sender_profile_id=p.id)
    # draft task only → not locked
    seq = MailSequence(name="S", status="active", steps=[{"subject": "s", "body": "b"}])
    db_session.add(seq)
    db_session.flush()
    db_session.add(Task(name="d", campaign_id=c.id, status="draft", sequences={"B": seq.id}))
    db_session.flush()
    assert profile_locked(db_session, p.id) is False


def test_profile_locked_by_running_task_then_unlocked_on_stop(db_session):
    p = _profile(db_session, postal_address="1 Test St")
    c = _campaign(db_session, sender_profile_id=p.id)
    task = _running_task(db_session, c)
    assert profile_locked(db_session, p.id) is True

    lifecycle.stop_task(db_session, task)
    assert profile_locked(db_session, p.id) is False


# --- CRUD web page -----------------------------------------------------------

@pytest.fixture
def engine_db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def client(engine_db):
    TestSession = sessionmaker(bind=engine_db, autoflush=False, expire_on_commit=False)

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
def session(engine_db):
    TestSession = sessionmaker(bind=engine_db, autoflush=False, expire_on_commit=False)
    s = TestSession()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _admin_password(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")


def test_create_and_list_sender(client, session):
    r = client.post("/settings/senders", auth=AUTH, follow_redirects=False,
                    data={"name": "Sales Steven", "from_email": "sales@co.com"})
    assert r.status_code == 303
    p = session.scalars(select(SenderProfile)).first()
    assert p.name == "Sales Steven"

    r = client.get("/settings/senders", auth=AUTH)
    assert r.status_code == 200
    assert "Sales Steven" in r.text


def test_locked_profile_blocks_edit_and_delete_but_allows_archive(client, session):
    # Build a profile used by a campaign with a running task.
    p = _profile(session, name="Locked", postal_address="1 Test St")
    c = _campaign(session, sender_profile_id=p.id)
    _running_task(session, c)
    session.commit()

    # Save (edit) is blocked.
    r = client.post(f"/settings/senders/{p.id}/edit", auth=AUTH, follow_redirects=False,
                    data={"action": "save", "name": "Renamed"})
    assert r.status_code == 303 and "edited" in r.headers["location"]
    session.expire_all()
    assert session.get(SenderProfile, p.id).name == "Locked"

    # Delete is blocked.
    r = client.post(f"/settings/senders/{p.id}/edit", auth=AUTH, follow_redirects=False,
                    data={"action": "delete"})
    assert r.status_code == 303 and "deleted" in r.headers["location"]
    assert session.get(SenderProfile, p.id) is not None

    # Archive is still allowed.
    r = client.post(f"/settings/senders/{p.id}/edit", auth=AUTH, follow_redirects=False,
                    data={"action": "archive"})
    assert r.status_code == 303
    session.expire_all()
    assert session.get(SenderProfile, p.id).status == "archived"
