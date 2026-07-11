"""Tasks page web routes: schedule/unschedule/start/pause/resume/stop a
MailSequence, and the Asia/Taipei -> UTC datetime-local conversion. Follows
test_sequences_web.py's engine/client/session fixture style."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, Lead, MailSequence
from awkns_outreach.db.session import Base, get_db
from awkns_outreach.web.app import app

AUTH = ("admin", "secret")
UTC = timezone.utc

STEPS = [{"key": "intro", "delay_days": 0, "subject": "hi {company}", "body": "b"}]


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


def _make_campaign(session, **kwargs) -> Campaign:
    c = Campaign(name=kwargs.pop("name", "Acme"), target_titles=[], seed_companies=[], **kwargs)
    session.add(c)
    session.commit()
    return c


def _make_sequence(session, campaign, **kwargs) -> MailSequence:
    base = dict(name="Seq", campaign_id=campaign.id, status="draft", steps=list(STEPS))
    base.update(kwargs)
    seq = MailSequence(**base)
    session.add(seq)
    session.commit()
    return seq


def _make_lead(session, campaign, **kwargs) -> Lead:
    base = dict(campaign_id=campaign.id, email="k@toyota.co.jp", company="Toyota", status="active", step=0)
    base.update(kwargs)
    lead = Lead(**base)
    session.add(lead)
    session.commit()
    return lead


def test_tasks_require_auth(client):
    assert client.get("/tasks").status_code == 401


def test_get_tasks_shows_sequences(client, session):
    c = _make_campaign(session, name="Widgets Co")
    _make_sequence(session, c, name="My sequence", status="draft")
    r = client.get("/tasks", auth=AUTH)
    assert r.status_code == 200
    assert "My sequence" in r.text
    assert "Widgets Co" in r.text


def test_schedule_converts_taipei_local_to_utc(client, session):
    c = _make_campaign(session)
    seq = _make_sequence(session, c, status="draft")
    r = client.post(
        f"/sequences/{seq.id}/schedule", auth=AUTH, follow_redirects=False,
        data={"scheduled_start_at": "2026-08-01T09:00"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks?msg=Sequence%20scheduled."
    session.refresh(seq)
    assert seq.status == "scheduled"
    # 09:00 Asia/Taipei (UTC+8, no DST) == 01:00 UTC same day.
    got = seq.scheduled_start_at
    if got.tzinfo is None:
        got = got.replace(tzinfo=UTC)
    assert got == datetime(2026, 8, 1, 1, 0, tzinfo=UTC)


def test_schedule_invalid_datetime_redirects_with_message(client, session):
    c = _make_campaign(session)
    seq = _make_sequence(session, c, status="draft")
    r = client.post(
        f"/sequences/{seq.id}/schedule", auth=AUTH, follow_redirects=False,
        data={"scheduled_start_at": "not-a-date"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks?msg=Invalid%20date/time."
    session.refresh(seq)
    assert seq.status == "draft"
    assert seq.scheduled_start_at is None


def test_schedule_rejected_when_group_already_has_active_sequence(client, session):
    c = _make_campaign(session)
    _make_sequence(session, c, name="Already running", status="running")
    seq = _make_sequence(session, c, name="New draft", status="draft")
    r = client.post(
        f"/sequences/{seq.id}/schedule", auth=AUTH, follow_redirects=False,
        data={"scheduled_start_at": "2026-08-01T09:00"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == (
        "/tasks?msg=Already%20running%20is%20already%20running%20for%20this%20group."
    )
    session.refresh(seq)
    assert seq.status == "draft"
    assert seq.scheduled_start_at is None


def test_unschedule(client, session):
    c = _make_campaign(session)
    seq = _make_sequence(
        session, c, status="scheduled", scheduled_start_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    r = client.post(f"/sequences/{seq.id}/unschedule", auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks?msg=Sequence%20unscheduled."
    session.refresh(seq)
    assert seq.status == "draft"
    assert seq.scheduled_start_at is None


def test_lifecycle_start_snapshots_into_campaign_and_flips_status(client, session):
    c = _make_campaign(session)
    seq = _make_sequence(session, c, status="draft", steps=list(STEPS))
    _make_lead(session, c)
    r = client.post(
        f"/sequences/{seq.id}/lifecycle", auth=AUTH, follow_redirects=False,
        data={"action": "start"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks?msg=Sequence%20started."
    session.refresh(seq)
    session.refresh(c)
    assert seq.status == "running"
    assert c.sequence == STEPS
    assert c.status == "active"


def test_lifecycle_pause_resume_stop(client, session):
    c = _make_campaign(session, status="active")
    seq = _make_sequence(session, c, status="running")

    r = client.post(
        f"/sequences/{seq.id}/lifecycle", auth=AUTH, follow_redirects=False,
        data={"action": "pause"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks?msg=Sequence%20paused."
    session.refresh(seq)
    session.refresh(c)
    assert seq.status == "paused" and c.status == "paused"

    r2 = client.post(
        f"/sequences/{seq.id}/lifecycle", auth=AUTH, follow_redirects=False,
        data={"action": "resume"},
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/tasks?msg=Sequence%20resumed."
    session.refresh(seq)
    session.refresh(c)
    assert seq.status == "running" and c.status == "active"

    r3 = client.post(
        f"/sequences/{seq.id}/lifecycle", auth=AUTH, follow_redirects=False,
        data={"action": "stop"},
    )
    assert r3.status_code == 303
    assert r3.headers["location"] == "/tasks?msg=Sequence%20stopped."
    session.refresh(seq)
    session.refresh(c)
    assert seq.status == "stopped" and c.status == "paused"


def test_lifecycle_unknown_action_returns_400(client, session):
    c = _make_campaign(session)
    seq = _make_sequence(session, c, status="draft")
    r = client.post(f"/sequences/{seq.id}/lifecycle", auth=AUTH, data={"action": "bogus"})
    assert r.status_code == 400
