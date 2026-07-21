"""Tasks page web routes: create/edit/delete a Task, the campaign-summary
HTMX fragment, and schedule/unschedule/start/pause/resume/stop — including
the Asia/Taipei -> UTC datetime-local conversion for both `scheduled_start_at`
and `end_at`. Follows test_sequences_web.py's engine/client/session fixture
style."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, Lead, MailSequence, Task
from awkns_outreach.db.session import Base, get_db
from awkns_outreach.sequencer.engine import RunSummary
from awkns_outreach.web.app import app
from awkns_outreach.web.routes import tasks

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


def _make_sequence(session, **kwargs) -> MailSequence:
    base = dict(name="Seq", status="active", steps=list(STEPS))
    base.update(kwargs)
    seq = MailSequence(**base)
    session.add(seq)
    session.commit()
    return seq


def _make_task(session, campaign, **kwargs) -> Task:
    base = dict(name="Task", campaign_id=campaign.id, status="draft", sequences={})
    base.update(kwargs)
    task = Task(**base)
    session.add(task)
    session.commit()
    return task


def _make_lead(session, campaign, **kwargs) -> Lead:
    base = dict(campaign_id=campaign.id, email="k@toyota.co.jp", company="Toyota", status="active", step=0)
    base.update(kwargs)
    lead = Lead(**base)
    session.add(lead)
    session.commit()
    return lead


def test_tasks_require_auth(client):
    assert client.get("/tasks").status_code == 401


def test_get_tasks_shows_tasks(client, session):
    c = _make_campaign(session, name="Widgets Co")
    _make_task(session, c, name="My task", status="draft")
    r = client.get("/tasks", auth=AUTH)
    assert r.status_code == 200
    assert "My task" in r.text
    assert "Widgets Co" in r.text


# --- create ------------------------------------------------------------------

def test_new_task_form_renders(client, session):
    _make_campaign(session, name="Widgets Co")
    _make_sequence(session, name="Seq A")
    r = client.get("/tasks/new", auth=AUTH)
    assert r.status_code == 200
    assert "Widgets Co" in r.text
    assert "Seq A" in r.text


def test_create_task_happy_path(client, session):
    c = _make_campaign(session)
    seq_a = _make_sequence(session, name="Seq A")
    seq_b = _make_sequence(session, name="Seq B")
    r = client.post("/tasks", auth=AUTH, follow_redirects=False, data={
        "name": "Q3 send", "campaign_id": c.id, "seq_A": seq_a.id, "seq_B": seq_b.id, "seq_C": "",
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks?msg=Task%20created."
    task = session.query(Task).one()
    assert task.name == "Q3 send"
    assert task.campaign_id == c.id
    assert task.sequences == {"A": seq_a.id, "B": seq_b.id}


def test_create_task_missing_name_errors_without_persisting(client, session):
    c = _make_campaign(session)
    seq = _make_sequence(session)
    r = client.post("/tasks", auth=AUTH, data={
        "name": "  ", "campaign_id": c.id, "seq_A": seq.id, "seq_B": "", "seq_C": "",
    })
    assert r.status_code == 200  # redirect followed back to the form
    assert r.request.url.path == "/tasks/new"
    assert "Name is required." in r.text
    assert session.query(Task).count() == 0


def test_create_task_unknown_campaign_errors_without_persisting(client, session):
    seq = _make_sequence(session)
    r = client.post("/tasks", auth=AUTH, data={
        "name": "Orphan", "campaign_id": "does-not-exist", "seq_A": seq.id, "seq_B": "", "seq_C": "",
    })
    assert r.status_code == 200
    assert r.request.url.path == "/tasks/new"
    assert "Select a valid campaign." in r.text
    assert session.query(Task).count() == 0


def test_create_task_no_tier_assigned_errors_without_persisting(client, session):
    c = _make_campaign(session)
    r = client.post("/tasks", auth=AUTH, data={
        "name": "Empty", "campaign_id": c.id, "seq_A": "", "seq_B": "", "seq_C": "",
    })
    assert r.status_code == 200
    assert r.request.url.path == "/tasks/new"
    assert "Assign at least one tier" in r.text
    assert session.query(Task).count() == 0


def test_create_task_invalid_sequence_id_errors_without_persisting(client, session):
    c = _make_campaign(session)
    r = client.post("/tasks", auth=AUTH, data={
        "name": "Bad seq", "campaign_id": c.id, "seq_A": "does-not-exist", "seq_B": "", "seq_C": "",
    })
    assert r.status_code == 200
    assert r.request.url.path == "/tasks/new"
    assert "Tier A" in r.text
    assert session.query(Task).count() == 0


# --- campaign-summary fragment ------------------------------------------------

def test_campaign_summary_fragment_counts_tiers_and_breaks_out_unclassified(client, session):
    c = _make_campaign(session)
    session.add_all([
        Lead(campaign_id=c.id, email="a@x.com", company="X", status="active", tier="A"),
        Lead(campaign_id=c.id, email="b@x.com", company="X", status="active", tier="B"),
        Lead(campaign_id=c.id, email="c@x.com", company="X", status="active", tier="C"),
        Lead(campaign_id=c.id, email="n1@x.com", company="X", status="active", tier=None),
        Lead(campaign_id=c.id, email="n2@x.com", company="X", status="active", tier=None),
    ])
    session.commit()

    r = client.get(f"/tasks/campaign-summary?campaign_id={c.id}", auth=AUTH)
    assert r.status_code == 200
    assert "A: 1" in r.text
    assert "B: 3" in r.text  # 1 explicit B + 2 unclassified
    assert "incl. 2 unclassified" in r.text
    assert "C: 1" in r.text


def test_campaign_summary_fragment_no_campaign_selected(client, session):
    r = client.get("/tasks/campaign-summary", auth=AUTH)
    assert r.status_code == 200
    assert "Select a campaign" in r.text


# --- edit / delete -------------------------------------------------------------

def test_edit_task_form_prefills(client, session):
    c = _make_campaign(session, name="Widgets Co")
    seq = _make_sequence(session, name="Seq A")
    task = _make_task(session, c, name="Prefill me", sequences={"A": seq.id})
    r = client.get(f"/tasks/{task.id}/edit", auth=AUTH)
    assert r.status_code == 200
    assert "Prefill me" in r.text
    assert "Widgets Co" in r.text


def test_save_edit_updates_name_campaign_and_assignments(client, session):
    c1 = _make_campaign(session, name="Campaign A")
    c2 = _make_campaign(session, name="Campaign B")
    seq = _make_sequence(session, name="Seq A")
    task = _make_task(session, c1, name="Original", sequences={})

    r = client.post(f"/tasks/{task.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "Updated", "campaign_id": c2.id,
        "seq_A": seq.id, "seq_B": "", "seq_C": "",
    })
    assert r.status_code == 303
    session.refresh(task)
    assert task.name == "Updated"
    assert task.campaign_id == c2.id
    assert task.sequences == {"A": seq.id}


def test_save_edit_rejects_campaign_reassignment_when_target_has_active_task(client, session):
    c1 = _make_campaign(session, name="Campaign A")
    c2 = _make_campaign(session, name="Campaign B")
    seq = _make_sequence(session)
    task = _make_task(session, c1, name="Scheduled task", status="scheduled", sequences={"A": seq.id})
    other = _make_task(session, c2, name="Already running", status="running")

    r = client.post(f"/tasks/{task.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "Scheduled task", "campaign_id": c2.id,
        "seq_A": seq.id, "seq_B": "", "seq_C": "",
    })
    assert r.status_code == 303
    assert r.headers["location"] == (
        f"/tasks/{task.id}/edit?msg=Already%20running%20is%20already%20running%20for%20this%20campaign."
    )
    session.refresh(task)
    assert task.campaign_id == c1.id  # unchanged — reassignment rejected


def test_edit_and_delete_blocked_while_running(client, session):
    c = _make_campaign(session)
    task = _make_task(session, c, name="Live task", status="running")

    get_r = client.get(f"/tasks/{task.id}/edit", auth=AUTH, follow_redirects=False)
    assert get_r.status_code == 303
    assert get_r.headers["location"].startswith("/tasks?msg=")

    post_r = client.post(f"/tasks/{task.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "Should not apply", "campaign_id": c.id,
        "seq_A": "", "seq_B": "", "seq_C": "",
    })
    assert post_r.status_code == 303
    assert post_r.headers["location"].startswith("/tasks?msg=")
    session.refresh(task)
    assert task.name == "Live task"  # unchanged

    delete_r = client.post(f"/tasks/{task.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "delete",
    })
    assert delete_r.status_code == 303
    assert delete_r.headers["location"].startswith("/tasks?msg=")
    assert session.query(Task).count() == 1  # still present


def test_delete_draft_task_removes_row(client, session):
    c = _make_campaign(session)
    task = _make_task(session, c, name="Throwaway")

    r = client.post(f"/tasks/{task.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "delete",
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks?msg=Task%20deleted."
    assert session.query(Task).count() == 0


def test_post_edit_unknown_action_returns_400(client, session):
    c = _make_campaign(session)
    task = _make_task(session, c)
    r = client.post(f"/tasks/{task.id}/edit", auth=AUTH, data={"action": "bogus"})
    assert r.status_code == 400


def test_unknown_task_id_returns_404(client, session):
    r = client.get("/tasks/does-not-exist/edit", auth=AUTH)
    assert r.status_code == 404


# --- schedule / unschedule -----------------------------------------------------

def test_schedule_converts_taipei_local_to_utc(client, session):
    c = _make_campaign(session)
    seq = _make_sequence(session)
    task = _make_task(session, c, status="draft", sequences={"B": seq.id})
    r = client.post(
        f"/tasks/{task.id}/schedule", auth=AUTH, follow_redirects=False,
        data={"scheduled_start_at": "2026-08-01T09:00"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks?msg=Task%20scheduled."
    session.refresh(task)
    assert task.status == "scheduled"
    # 09:00 Asia/Taipei (UTC+8, no DST) == 01:00 UTC same day.
    got = task.scheduled_start_at
    if got.tzinfo is None:
        got = got.replace(tzinfo=UTC)
    assert got == datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
    assert task.end_at is None


def test_schedule_with_end_at_converts_both_taipei_local_to_utc(client, session):
    c = _make_campaign(session)
    seq = _make_sequence(session)
    task = _make_task(session, c, status="draft", sequences={"B": seq.id})
    r = client.post(
        f"/tasks/{task.id}/schedule", auth=AUTH, follow_redirects=False,
        data={"scheduled_start_at": "2026-08-01T09:00", "end_at": "2026-08-08T09:00"},
    )
    assert r.status_code == 303
    session.refresh(task)
    assert task.status == "scheduled"
    got_end = task.end_at
    if got_end.tzinfo is None:
        got_end = got_end.replace(tzinfo=UTC)
    assert got_end == datetime(2026, 8, 8, 1, 0, tzinfo=UTC)


def test_schedule_invalid_datetime_redirects_with_message(client, session):
    c = _make_campaign(session)
    seq = _make_sequence(session)
    task = _make_task(session, c, status="draft", sequences={"B": seq.id})
    r = client.post(
        f"/tasks/{task.id}/schedule", auth=AUTH, follow_redirects=False,
        data={"scheduled_start_at": "not-a-date"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks?msg=Invalid%20date/time."
    session.refresh(task)
    assert task.status == "draft"
    assert task.scheduled_start_at is None


def test_schedule_rejected_when_campaign_already_has_active_task(client, session):
    c = _make_campaign(session)
    seq = _make_sequence(session)
    _make_task(session, c, name="Already running", status="running")
    task = _make_task(session, c, name="New draft", status="draft", sequences={"B": seq.id})
    r = client.post(
        f"/tasks/{task.id}/schedule", auth=AUTH, follow_redirects=False,
        data={"scheduled_start_at": "2026-08-01T09:00"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == (
        "/tasks?msg=Already%20running%20is%20already%20running%20for%20this%20campaign."
    )
    session.refresh(task)
    assert task.status == "draft"
    assert task.scheduled_start_at is None


def test_unschedule(client, session):
    c = _make_campaign(session)
    task = _make_task(
        session, c, status="scheduled", scheduled_start_at=datetime(2026, 8, 1, tzinfo=UTC),
        end_at=datetime(2026, 8, 8, tzinfo=UTC),
    )
    r = client.post(f"/tasks/{task.id}/unschedule", auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks?msg=Task%20unscheduled."
    session.refresh(task)
    assert task.status == "draft"
    assert task.scheduled_start_at is None
    assert task.end_at is None


# --- lifecycle -----------------------------------------------------------------

def test_lifecycle_start_snapshots_steps_by_tier_and_flips_status(client, session):
    c = _make_campaign(session)
    seq = _make_sequence(session, status="active", steps=list(STEPS))
    task = _make_task(session, c, status="draft", sequences={"B": seq.id})
    _make_lead(session, c, tier="B")
    r = client.post(
        f"/tasks/{task.id}/lifecycle", auth=AUTH, follow_redirects=False,
        data={"action": "start"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks?msg=Task%20started."
    session.refresh(task)
    session.refresh(c)
    assert task.status == "running"
    assert task.steps_by_tier == {"B": STEPS}
    assert c.status == "active"


def test_lifecycle_pause_resume_stop(client, session):
    c = _make_campaign(session, status="active")
    task = _make_task(session, c, status="running", steps_by_tier={"B": list(STEPS)})

    r = client.post(
        f"/tasks/{task.id}/lifecycle", auth=AUTH, follow_redirects=False,
        data={"action": "pause"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks?msg=Task%20paused."
    session.refresh(task)
    session.refresh(c)
    assert task.status == "paused" and c.status == "paused"

    r2 = client.post(
        f"/tasks/{task.id}/lifecycle", auth=AUTH, follow_redirects=False,
        data={"action": "resume"},
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/tasks?msg=Task%20resumed."
    session.refresh(task)
    session.refresh(c)
    assert task.status == "running" and c.status == "active"

    r3 = client.post(
        f"/tasks/{task.id}/lifecycle", auth=AUTH, follow_redirects=False,
        data={"action": "stop"},
    )
    assert r3.status_code == 303
    assert r3.headers["location"] == "/tasks?msg=Task%20stopped."
    session.refresh(task)
    session.refresh(c)
    assert task.status == "stopped" and c.status == "paused"
    assert task.steps_by_tier == {}


def test_tasks_page_shows_drift_warning_when_running_but_campaign_not_active(client, session):
    c = _make_campaign(session, name="Drifted Co", status="paused")
    _make_task(session, c, name="Drifted task", status="running")
    r = client.get("/tasks", auth=AUTH)
    assert r.status_code == 200
    assert "drift" in r.text.lower()
    assert "paused" in r.text.lower()


def test_tasks_page_no_drift_warning_when_running_and_campaign_active(client, session):
    c = _make_campaign(session, name="Healthy Co", status="active")
    _make_task(session, c, name="Healthy task", status="running")
    r = client.get("/tasks", auth=AUTH)
    assert r.status_code == 200
    assert "drift" not in r.text.lower()


# --- run: dry-run / send-for-real console, moved here from the campaign page --


def test_run_task_blocks_when_not_running(client, session, monkeypatch):
    c = _make_campaign(session)
    task = _make_task(session, c, status="draft")

    def boom(*a, **kw):
        raise AssertionError("process_campaign must not be called for a non-running Task")

    monkeypatch.setattr(tasks, "process_campaign", boom)

    r = client.post(f"/tasks/{task.id}/run", auth=AUTH, data={}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks?msg=Task%20isn't%20running."


def test_run_task_runs_when_status_running(client, session, monkeypatch):
    c = _make_campaign(session)
    steps_by_tier = {"A": [{"subject": "Hi {{first_name}}", "body": "Hello"}]}
    task = _make_task(session, c, status="running", steps_by_tier=steps_by_tier)
    _make_lead(session, c, tier="A")

    canned = RunSummary(
        dry_run=True, considered=1, sent=1, skipped=0, suppressed=0, errors=0,
        cap=5, sent_last_24h=0, daily_remaining=4,
    )
    calls = []

    def fake_process_campaign(db, campaign, steps, **kw):
        calls.append((campaign.id, steps, kw))
        return canned

    monkeypatch.setattr(tasks, "process_campaign", fake_process_campaign)

    r = client.post(f"/tasks/{task.id}/run", auth=AUTH, data={}, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    location = unquote(r.headers["location"])
    assert location.startswith("/tasks?msg=")
    assert "DRY-RUN: sent 1, skipped 0, suppressed 0, errors 0 (cap 5, remaining 4)." in location

    assert len(calls) == 1
    called_campaign_id, called_steps, called_kwargs = calls[0]
    assert called_campaign_id == c.id
    assert called_steps == steps_by_tier
    assert called_kwargs["dry_run"] is True


def test_lifecycle_unknown_action_returns_400(client, session):
    c = _make_campaign(session)
    task = _make_task(session, c, status="draft")
    r = client.post(f"/tasks/{task.id}/lifecycle", auth=AUTH, data={"action": "bogus"})
    assert r.status_code == 400
