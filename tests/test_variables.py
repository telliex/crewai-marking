"""Variables settings: the config_store override layer, per-task identity
freezing, and the /settings/variables web page."""
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach import config_store as cs
from awkns_outreach.config import settings
from awkns_outreach.db.models import AppSetting, Campaign, Lead, MailSequence, Task
from awkns_outreach.db.session import Base, get_db
from awkns_outreach.sequencer import engine, lifecycle
from awkns_outreach.web.app import app

UTC = timezone.utc
NOW = datetime(2026, 7, 6, 2, 0, tzinfo=UTC)  # Monday, business hours in Taipei
AUTH = ("admin", "secret")


@pytest.fixture(autouse=True)
def _restore_config():
    """apply_overrides / saves mutate the global settings singleton and
    os.environ — snapshot and restore both so tests don't leak into each other."""
    saved_attr = {s.settings_attr: getattr(settings, s.settings_attr) for s in cs.REGISTRY}
    saved_env = {s.env_name: os.environ.get(s.env_name) for s in cs.REGISTRY}
    yield
    for attr, val in saved_attr.items():
        setattr(settings, attr, val)
    for name, val in saved_env.items():
        if val is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = val


# --- config_store unit tests -------------------------------------------------

def test_apply_overrides_sets_settings_and_environ(db_session):
    cs.set_override(db_session, "OUTREACH_COMPANY", "NewCo")
    cs.set_override(db_session, "SERPER_API_KEY", "sk-new")
    db_session.commit()
    cs.apply_overrides(db_session)
    assert settings.outreach_company == "NewCo"
    assert os.environ["OUTREACH_COMPANY"] == "NewCo"
    # crewai/litellm read SERPER straight from os.environ, so it must land there.
    assert settings.serper_api_key == "sk-new"
    assert os.environ["SERPER_API_KEY"] == "sk-new"


def test_clear_override_reverts_to_baseline(db_session):
    baseline = cs._BASELINE["OUTREACH_COMPANY"]
    cs.set_override(db_session, "OUTREACH_COMPANY", "NewCo")
    db_session.commit()
    cs.apply_overrides(db_session)
    assert settings.outreach_company == "NewCo"

    cs.clear_override(db_session, "OUTREACH_COMPANY")
    db_session.commit()
    cs.apply_overrides(db_session)
    assert settings.outreach_company == baseline
    assert os.environ["OUTREACH_COMPANY"] == baseline


def test_effective_and_overridden_keys(db_session):
    assert cs.overridden_keys(db_session) == set()
    cs.set_override(db_session, "CREW_MODEL", "vendor/model")
    db_session.commit()
    assert cs.overridden_keys(db_session) == {"CREW_MODEL"}
    assert cs.effective_values(db_session)["CREW_MODEL"] == "vendor/model"


def test_mask_secret():
    assert cs.mask_secret("") == "(not set)"
    assert cs.mask_secret("ab") == "••••"
    masked = cs.mask_secret("abcdefgh")
    assert masked.startswith("••••") and masked.endswith("efgh")
    assert "abcdefgh" not in masked  # the full secret never leaks


# --- per-task identity freeze ------------------------------------------------

def _campaign(session, **kw):
    base = dict(name="c", target_titles=[], seed_companies=[], sender_identity={},
                warmup_start=datetime(2026, 1, 1, tzinfo=UTC))
    base.update(kw)
    c = Campaign(**base)
    session.add(c)
    session.flush()
    return c


def _running_task(session, campaign, monkeypatch):
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


def test_start_task_freezes_identity(db_session, monkeypatch):
    monkeypatch.setattr(settings, "outreach_from", "old@x.com")
    monkeypatch.setattr(settings, "outreach_postal_address", "1 Test St")
    c = _campaign(db_session)  # sender_identity={} → falls back to settings
    task = _running_task(db_session, c, monkeypatch)

    assert task.identity_snapshot["from_email"] == "old@x.com"
    # A later global change must NOT rewrite an already-frozen snapshot.
    monkeypatch.setattr(settings, "outreach_from", "new@x.com")
    assert task.identity_snapshot["from_email"] == "old@x.com"


def test_process_campaign_uses_frozen_identity_not_live(db_session, monkeypatch):
    monkeypatch.setattr(settings, "outreach_from", "old@x.com")
    monkeypatch.setattr(settings, "outreach_postal_address", "1 Test St")
    c = _campaign(db_session)
    task = _running_task(db_session, c, monkeypatch)

    # Simulate an admin emptying the postal address AFTER the task started.
    monkeypatch.setattr(settings, "outreach_postal_address", "")

    # Frozen snapshot still carries the postal address → the legality gate passes.
    frozen = engine.process_campaign(
        db_session, c, task.steps_by_tier, dry_run=False,
        identity_snapshot=task.identity_snapshot, now=NOW,
    )
    assert not frozen.blocked

    # Live resolve (no snapshot) sees the now-empty postal address → blocked.
    live = engine.process_campaign(
        db_session, c, task.steps_by_tier, dry_run=False,
        identity_snapshot=None, now=NOW,
    )
    assert live.blocked and "postal" in live.blocked


def test_stop_task_clears_identity_snapshot(db_session, monkeypatch):
    monkeypatch.setattr(settings, "outreach_postal_address", "1 Test St")
    c = _campaign(db_session)
    task = _running_task(db_session, c, monkeypatch)
    assert task.identity_snapshot is not None
    lifecycle.stop_task(db_session, task)
    assert task.identity_snapshot is None


# --- web page ----------------------------------------------------------------

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


def test_get_variables_page_renders(client):
    r = client.get("/settings/variables", auth=AUTH)
    assert r.status_code == 200
    assert "OUTREACH_FROM" in r.text
    assert "Apollo API key" in r.text


def test_secret_value_is_masked(client):
    full = settings.apollo_api_key
    r = client.get("/settings/variables", auth=AUTH)
    assert r.status_code == 200
    if full:
        assert full not in r.text  # never render the raw secret
    assert "••••" in r.text


def test_post_saves_override_and_applies(client, session):
    r = client.post("/settings/variables", auth=AUTH, follow_redirects=False,
                    data={"OUTREACH_COMPANY": "SavedCo"})
    assert r.status_code == 303
    assert session.get(AppSetting, "OUTREACH_COMPANY").value == "SavedCo"
    assert settings.outreach_company == "SavedCo"  # applied to the live singleton


def test_blank_secret_kept_nonblank_saved(client, session):
    # Blank secret field → keep existing → no override row written.
    client.post("/settings/variables", auth=AUTH, follow_redirects=False,
                data={"APOLLO_API_KEY": ""})
    assert session.get(AppSetting, "APOLLO_API_KEY") is None

    # A real value → override saved and applied.
    client.post("/settings/variables", auth=AUTH, follow_redirects=False,
                data={"APOLLO_API_KEY": "sk-live-1234"})
    session.expire_all()
    assert session.get(AppSetting, "APOLLO_API_KEY").value == "sk-live-1234"
    assert settings.apollo_api_key == "sk-live-1234"


def test_reset_clears_override(client, session):
    client.post("/settings/variables", auth=AUTH, follow_redirects=False,
                data={"CREW_MODEL": "vendor/model"})
    assert session.get(AppSetting, "CREW_MODEL") is not None

    r = client.post("/settings/variables/reset", auth=AUTH, follow_redirects=False,
                    data={"key": "CREW_MODEL"})
    assert r.status_code == 303
    session.expire_all()
    assert session.get(AppSetting, "CREW_MODEL") is None
