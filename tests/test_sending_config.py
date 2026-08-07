"""Operator-tunable sending limits: the settings-backed accessors in
sequencer/limits.py + engine.py, the new-campaign warmup stamping, and the
Sending section on the Variables page."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign
from awkns_outreach.db.session import Base, get_db
from awkns_outreach.sequencer import engine, limits
from awkns_outreach.web.app import app

UTC = timezone.utc
AUTH = ("admin", "secret")


# --- accessors read settings, with fallback on malformed values --------------

def test_daily_cap_and_ramp_override(monkeypatch):
    monkeypatch.setattr(settings, "warmup_daily_cap", "50")
    monkeypatch.setattr(settings, "warmup_ramp", "1,2,3")
    assert limits.daily_cap() == 50
    assert limits.ramp() == (1, 2, 3)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    assert limits.warmup_cap(None, start) == 1                      # ramp[0]
    assert limits.warmup_cap(start, start + timedelta(days=1)) == 2  # ramp[1]
    assert limits.warmup_cap(start, start + timedelta(days=9)) == 50  # past ramp → cap


def test_malformed_values_fall_back(monkeypatch):
    monkeypatch.setattr(settings, "warmup_daily_cap", "not-a-number")
    monkeypatch.setattr(settings, "warmup_ramp", "")
    assert limits.daily_cap() == limits.SEND.hard_daily_cap
    assert limits.ramp() == limits.SEND.warmup_ramp


def test_send_window_overrides(monkeypatch):
    monkeypatch.setattr(settings, "send_hours", "8-20")
    monkeypatch.setattr(settings, "send_days", "0,1")
    assert limits.send_window_hours() == (8, 20)
    assert limits.send_workdays() == (0, 1)
    # malformed → defaults
    monkeypatch.setattr(settings, "send_hours", "oops")
    assert limits.send_window_hours() == limits.SEND.send_hours


def test_pacing_and_retry_overrides(monkeypatch):
    monkeypatch.setattr(settings, "send_min_gap_ms", "1000")
    monkeypatch.setattr(settings, "send_jitter_ms", "2000")
    monkeypatch.setattr(settings, "max_send_errors", "5")
    monkeypatch.setattr(settings, "stale_claim_seconds", "42")
    assert limits.min_gap_ms() == 1000
    assert limits.jitter_ms() == 2000
    assert engine._max_send_errors() == 5
    assert engine._stale_claim_seconds() == 42


# --- new-campaign warmup stamping --------------------------------------------

def test_initial_warmup_start_modes(monkeypatch):
    now = datetime(2026, 6, 1, tzinfo=UTC)
    monkeypatch.setattr(settings, "warmup_default_mode", "warm")
    assert limits.initial_warmup_start(now) == now
    monkeypatch.setattr(settings, "warmup_default_mode", "none")
    assert limits.initial_warmup_start(now) is None
    monkeypatch.setattr(settings, "warmup_default_mode", "full")
    started = limits.initial_warmup_start(now)
    # "full" backdates far enough that the campaign is already at full speed.
    assert limits.warmup_cap(started, now) == limits.daily_cap()


# --- web: create stamps warmup_start, Variables shows Sending ----------------

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


def test_create_campaign_stamps_warmup_start(client, session, monkeypatch):
    monkeypatch.setattr(settings, "warmup_default_mode", "warm")
    r = client.post("/campaigns", auth=AUTH, follow_redirects=False,
                    data={"name": "Warmup Co"})
    assert r.status_code == 303
    c = session.scalars(select(Campaign)).first()
    # No longer silently NULL (which would trap the campaign at 5/day).
    assert c.warmup_start is not None


def test_variables_page_shows_sending_section(client):
    r = client.get("/settings/variables", auth=AUTH)
    assert r.status_code == 200
    assert "Sending / Warmup" in r.text
    assert "WARMUP_DAILY_CAP" in r.text
    assert "SEND_HOURS" in r.text
