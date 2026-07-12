"""Task lifecycle: schedule/start/pause/resume/stop, the one-active-per-
campaign conflict guard, stop_expired_tasks, the scheduler-tick helpers
(start_due_tasks / complete_finished_tasks), and an end-to-end pass through
the real engine validating campaign reuse. Follows test_sequencer.py's
db_session-fixture style — pure lifecycle-module tests with injected `now`,
no web client."""
from datetime import datetime, timedelta, timezone

from awkns_outreach.db.models import Campaign, MailSequence, Task
from awkns_outreach.send.mailer import SendResult
from awkns_outreach.sequencer import engine, lifecycle

UTC = timezone.utc
NOW = datetime(2026, 7, 6, 2, 0, tzinfo=UTC)  # Monday, business hours in Taipei
STEPS = [
    {"key": "intro", "delay_days": 0, "subject": "hi {company}", "body": "b0"},
]


def _campaign(session, **kw) -> Campaign:
    base = dict(
        name="c", target_titles=[], seed_companies=[],
        sender_identity={"postal_address": "1 Test St", "from": "s@mail.x.com",
                         "sender_name": "Steven"},
        warmup_start=datetime(2026, 1, 1, tzinfo=UTC),
    )
    base.update(kw)
    c = Campaign(**base)
    session.add(c)
    session.flush()
    return c


def _seq(session, **kw) -> MailSequence:
    base = dict(name="Seq", status="active", steps=list(STEPS))
    base.update(kw)
    seq = MailSequence(**base)
    session.add(seq)
    session.flush()
    return seq


def _task(session, campaign, **kw) -> Task:
    base = dict(name="Task", campaign_id=campaign.id, status="draft", sequences={})
    base.update(kw)
    task = Task(**base)
    session.add(task)
    session.flush()
    return task


def _lead(session, campaign, **kw):
    from awkns_outreach.db.models import Lead
    base = dict(campaign_id=campaign.id, email="k@toyota.co.jp", company="Toyota",
                contact_name="Kenji", status="active", step=0)
    base.update(kw)
    lead = Lead(**base)
    session.add(lead)
    session.flush()
    return lead


def _mock_ok(monkeypatch):
    monkeypatch.setattr(engine, "send_outreach_email",
                        lambda l, c, e, s, steps, dry_run: SendResult(ok=True, id=f"msg-{s}", subject="subj"))


# --- active_conflict --------------------------------------------------------

def test_active_conflict_none_when_only_draft_exists(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c, status="draft")
    assert lifecycle.active_conflict(db_session, c.id, exclude_id=task.id) is None


def test_active_conflict_finds_scheduled_running_or_paused(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c, status="draft")
    other = _task(db_session, c, name="Other", status="running")
    conflict = lifecycle.active_conflict(db_session, c.id, exclude_id=task.id)
    assert conflict is not None and conflict.id == other.id


def test_active_conflict_excludes_self(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c, status="scheduled")
    assert lifecycle.active_conflict(db_session, c.id, exclude_id=task.id) is None


# --- schedule_task / unschedule_task -----------------------------------------

def test_schedule_task_happy_path(db_session):
    c = _campaign(db_session)
    seq = _seq(db_session)
    task = _task(db_session, c, status="draft", sequences={"B": seq.id})
    when = NOW + timedelta(days=1)
    ok, msg = lifecycle.schedule_task(db_session, task, when)
    assert ok and msg == "Task scheduled."
    assert task.status == "scheduled"
    assert task.scheduled_start_at == when
    assert task.end_at is None


def test_schedule_task_with_end_at(db_session):
    c = _campaign(db_session)
    seq = _seq(db_session)
    task = _task(db_session, c, status="draft", sequences={"B": seq.id})
    when = NOW + timedelta(days=1)
    end = when + timedelta(days=7)
    ok, msg = lifecycle.schedule_task(db_session, task, when, end_at=end)
    assert ok
    assert task.end_at == end


def test_schedule_task_rejects_end_at_before_or_equal_start(db_session):
    c = _campaign(db_session)
    seq = _seq(db_session)
    task = _task(db_session, c, status="draft", sequences={"B": seq.id})
    when = NOW + timedelta(days=1)
    ok, msg = lifecycle.schedule_task(db_session, task, when, end_at=when)
    assert not ok and msg == "End time must be after the start time."
    assert task.status == "draft"

    ok2, msg2 = lifecycle.schedule_task(db_session, task, when, end_at=when - timedelta(hours=1))
    assert not ok2 and msg2 == "End time must be after the start time."


def test_schedule_task_rejects_wrong_status(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c, status="running")
    ok, msg = lifecycle.schedule_task(db_session, task, NOW)
    assert not ok and task.status == "running" and task.scheduled_start_at is None
    assert msg == "Task isn't a draft."


def test_schedule_task_rejects_on_conflict(db_session):
    c = _campaign(db_session)
    seq = _seq(db_session)
    task = _task(db_session, c, status="draft", sequences={"B": seq.id})
    other = _task(db_session, c, name="Other task", status="scheduled")
    ok, msg = lifecycle.schedule_task(db_session, task, NOW)
    assert not ok and task.status == "draft" and task.scheduled_start_at is None
    assert msg == "Other task is already scheduled for this campaign."


def test_schedule_task_rejects_no_tiers_assigned(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c, status="draft", sequences={})
    ok, msg = lifecycle.schedule_task(db_session, task, NOW)
    assert not ok and msg == "Assign at least one tier's sequence first."
    assert task.status == "draft"


def test_schedule_task_rejects_missing_sequence(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c, status="draft", sequences={"A": "does-not-exist"})
    ok, msg = lifecycle.schedule_task(db_session, task, NOW)
    assert not ok and "Tier A" in msg


def test_schedule_task_rejects_archived_sequence(db_session):
    c = _campaign(db_session)
    seq = _seq(db_session, status="archived")
    task = _task(db_session, c, status="draft", sequences={"C": seq.id})
    ok, msg = lifecycle.schedule_task(db_session, task, NOW)
    assert not ok and "Tier C" in msg and "archived" in msg


def test_schedule_task_rejects_empty_steps_sequence(db_session):
    c = _campaign(db_session)
    seq = _seq(db_session, steps=[])
    task = _task(db_session, c, status="draft", sequences={"B": seq.id})
    ok, msg = lifecycle.schedule_task(db_session, task, NOW)
    assert not ok and "Tier B" in msg and "no steps" in msg


def test_unschedule_task_happy_path(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c, status="scheduled", scheduled_start_at=NOW, end_at=NOW + timedelta(days=1))
    ok, msg = lifecycle.unschedule_task(db_session, task)
    assert ok and msg == "Task unscheduled."
    assert task.status == "draft" and task.scheduled_start_at is None and task.end_at is None


def test_unschedule_task_rejects_wrong_status(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c, status="draft")
    ok, msg = lifecycle.unschedule_task(db_session, task)
    assert not ok and msg == "Task isn't scheduled."
    assert task.status == "draft"


# --- start_task ---------------------------------------------------------------

def test_start_task_snapshots_steps_by_tier(db_session):
    c = _campaign(db_session)
    seq_a = _seq(db_session, name="A seq", steps=[{"key": "a0", "delay_days": 0, "subject": "sa", "body": "ba"}])
    seq_b = _seq(db_session, name="B seq", steps=[{"key": "b0", "delay_days": 0, "subject": "sb", "body": "bb"}])
    task = _task(db_session, c, status="draft", sequences={"A": seq_a.id, "B": seq_b.id})
    ok, msg = lifecycle.start_task(db_session, task, NOW)
    assert ok and msg == "Task started."
    assert task.steps_by_tier == {
        "A": [{"key": "a0", "delay_days": 0, "subject": "sa", "body": "ba"}],
        "B": [{"key": "b0", "delay_days": 0, "subject": "sb", "body": "bb"}],
    }
    assert c.status == "active"
    assert task.status == "running" and task.started_at == NOW

    # Mutating the sequence's OWN steps afterward must not retroactively
    # change what was already snapshotted (deep-enough copy).
    seq_a.steps[0]["subject"] = "mutated"
    assert task.steps_by_tier["A"][0]["subject"] == "sa"


def test_start_task_resets_assigned_tier_leads_and_parks_unassigned_and_spares_replied(db_session):
    c = _campaign(db_session)
    seq_b = _seq(db_session, name="B seq")
    task = _task(db_session, c, status="draft", sequences={"B": seq_b.id})

    reset_lead = _lead(
        db_session, c, email="reset@x.com", tier="B", status="completed", step=3,
        next_action_at=NOW, thread_ref="thread-1", last_message_id="msg-1",
    )
    unassigned_lead = _lead(
        db_session, c, email="parked@x.com", tier="A", status="active", step=0,
    )
    replied_lead = _lead(
        db_session, c, email="replied@x.com", tier="B", status="replied", step=1,
        next_action_at=NOW, thread_ref="thread-2", last_message_id="msg-2",
    )
    null_tier_lead = _lead(
        db_session, c, email="null@x.com", tier=None, status="active", step=0,
    )

    ok, _ = lifecycle.start_task(db_session, task, NOW)
    assert ok
    db_session.refresh(reset_lead)
    db_session.refresh(unassigned_lead)
    db_session.refresh(replied_lead)
    db_session.refresh(null_tier_lead)

    # Assigned tier ("B"), resettable status -> clean slate.
    assert reset_lead.step == 0
    assert reset_lead.status == "active"
    assert reset_lead.next_action_at is None
    assert reset_lead.thread_ref is None
    assert reset_lead.last_message_id is None

    # NULL tier counts as "B" (assigned) -> also reset/kept active.
    assert null_tier_lead.status == "active"

    # Unassigned tier ("A") -> parked as paused, not reset.
    assert unassigned_lead.status == "paused"

    # replied lead is untouched (not in the resettable status set), even
    # though its tier ("B") is assigned.
    assert replied_lead.step == 1
    assert replied_lead.status == "replied"
    assert replied_lead.thread_ref == "thread-2"
    assert replied_lead.last_message_id == "msg-2"


def test_start_task_rejects_on_conflict(db_session):
    c = _campaign(db_session)
    seq = _seq(db_session)
    task = _task(db_session, c, status="draft", sequences={"B": seq.id})
    other = _task(db_session, c, name="Other task", status="paused")
    ok, msg = lifecycle.start_task(db_session, task, NOW)
    assert not ok and task.status == "draft"
    assert msg == "Other task is already paused for this campaign."
    assert task.steps_by_tier == {}


def test_start_task_rejects_no_tiers_assigned(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c, status="draft", sequences={})
    ok, msg = lifecycle.start_task(db_session, task, NOW)
    assert not ok and msg == "Assign at least one tier's sequence first."
    assert task.status == "draft"


def test_start_task_rejects_when_assigned_sequence_archived_since_schedule(db_session):
    """A sequence may have been archived/emptied after schedule_task already
    validated it — start_task must re-validate."""
    c = _campaign(db_session)
    seq = _seq(db_session)
    task = _task(db_session, c, status="scheduled", sequences={"A": seq.id})
    seq.status = "archived"
    db_session.flush()
    ok, msg = lifecycle.start_task(db_session, task, NOW)
    assert not ok and "Tier A" in msg and "archived" in msg
    assert task.status == "scheduled"


def test_start_task_rejects_wrong_status(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c, status="completed")
    ok, msg = lifecycle.start_task(db_session, task, NOW)
    assert not ok
    assert msg == "Task can't be started from its current status."


# --- pause_task / resume_task / stop_task ------------------------------------

def test_pause_task_happy_path(db_session):
    c = _campaign(db_session, status="active")
    task = _task(db_session, c, status="running")
    ok, msg = lifecycle.pause_task(db_session, task)
    assert ok and msg == "Task paused."
    assert task.status == "paused" and c.status == "paused"


def test_pause_task_rejects_wrong_status(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c, status="draft")
    ok, msg = lifecycle.pause_task(db_session, task)
    assert not ok and msg == "Task isn't running."
    assert task.status == "draft"


def test_resume_task_happy_path(db_session):
    c = _campaign(db_session, status="paused")
    task = _task(db_session, c, status="paused")
    ok, msg = lifecycle.resume_task(db_session, task)
    assert ok and msg == "Task resumed."
    assert task.status == "running" and c.status == "active"


def test_resume_task_rejects_wrong_status(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c, status="stopped")
    ok, msg = lifecycle.resume_task(db_session, task)
    assert not ok and msg == "Task isn't paused."


def test_resume_task_rejects_on_conflict(db_session):
    c = _campaign(db_session, status="paused")
    task = _task(db_session, c, status="paused")
    other = _task(db_session, c, name="Started meanwhile", status="running")
    ok, msg = lifecycle.resume_task(db_session, task)
    assert not ok and task.status == "paused"
    assert msg == "Started meanwhile is already running for this campaign."


def test_stop_task_from_running(db_session):
    c = _campaign(db_session, status="active")
    task = _task(db_session, c, status="running", steps_by_tier={"B": list(STEPS)})
    ok, msg = lifecycle.stop_task(db_session, task)
    assert ok and msg == "Task stopped."
    assert task.status == "stopped" and c.status == "paused"
    # Snapshot cleared so a later dashboard resume can't resurrect the
    # stopped send (engine.py's empty-steps guard makes it a no-op instead).
    assert task.steps_by_tier == {}


def test_stop_task_from_paused(db_session):
    c = _campaign(db_session, status="paused")
    task = _task(db_session, c, status="paused", steps_by_tier={"B": list(STEPS)})
    ok, msg = lifecycle.stop_task(db_session, task)
    assert ok and task.status == "stopped" and c.status == "paused"
    assert task.steps_by_tier == {}


def test_stop_then_dashboard_resume_does_not_resend(db_session, monkeypatch):
    """Regression: stopping a task and then resuming its Campaign from the
    dashboard (flipping campaign.status back to "active" with no new Task
    started) must NOT resurrect the stopped send."""
    _mock_ok(monkeypatch)
    c = _campaign(db_session, status="active")
    seq = _seq(db_session)
    task = _task(db_session, c, status="running", sequences={"B": seq.id},
                steps_by_tier={"B": list(STEPS)})
    _lead(db_session, c, email="k@toyota.co.jp", tier="B")

    ok, _ = lifecycle.stop_task(db_session, task)
    assert ok
    assert task.steps_by_tier == {}

    # Simulate the dashboard's Campaign "resume" action: only campaign.status
    # flips, no Task is re-activated (a "stopped" task isn't matched by
    # admin.py's _TASK_MIRROR resume rule).
    c.status = "active"
    db_session.commit()

    summary = engine.process_campaign(
        db_session, c, task.steps_by_tier, dry_run=False, now=NOW, ignore_hours=True, gap_ms=0,
    )
    assert summary.blocked == "no steps"
    assert summary.sent == 0


def test_stop_task_rejects_wrong_status(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c, status="draft")
    ok, msg = lifecycle.stop_task(db_session, task)
    assert not ok
    assert msg == "Task can't be stopped from its current status."
    assert task.status == "draft"


# --- start_due_tasks ----------------------------------------------------------

def test_start_due_tasks_starts_due_leaves_future_alone_skips_conflicts(db_session):
    seq = _seq(db_session)
    c1 = _campaign(db_session)
    due = _task(db_session, c1, name="Due", status="scheduled",
               scheduled_start_at=NOW - timedelta(minutes=1), sequences={"B": seq.id})

    c3 = _campaign(db_session)
    future = _task(db_session, c3, name="Future", status="scheduled",
                  scheduled_start_at=NOW + timedelta(days=1), sequences={"B": seq.id})

    c2 = _campaign(db_session)
    conflicted = _task(db_session, c2, name="Conflicted", status="scheduled",
                      scheduled_start_at=NOW, sequences={"B": seq.id})
    _task(db_session, c2, name="Already running", status="running")

    started = lifecycle.start_due_tasks(db_session, NOW)

    assert [t.id for t in started] == [due.id]
    assert due.status == "running"
    assert future.status == "scheduled"  # untouched, not due yet
    assert conflicted.status == "scheduled"  # skipped, not crashed


# --- stop_expired_tasks --------------------------------------------------------

def test_stop_expired_tasks_stops_due_running(db_session):
    c = _campaign(db_session, status="active")
    task = _task(db_session, c, status="running", end_at=NOW - timedelta(minutes=1),
               steps_by_tier={"B": list(STEPS)})
    stopped = lifecycle.stop_expired_tasks(db_session, NOW)
    assert [t.id for t in stopped] == [task.id]
    assert task.status == "stopped"
    assert task.steps_by_tier == {}
    assert c.status == "paused"


def test_stop_expired_tasks_leaves_future_end_at_alone(db_session):
    c = _campaign(db_session, status="active")
    task = _task(db_session, c, status="running", end_at=NOW + timedelta(days=1))
    stopped = lifecycle.stop_expired_tasks(db_session, NOW)
    assert stopped == []
    assert task.status == "running"


def test_stop_expired_tasks_leaves_no_end_at_alone(db_session):
    c = _campaign(db_session, status="active")
    task = _task(db_session, c, status="running", end_at=None)
    stopped = lifecycle.stop_expired_tasks(db_session, NOW)
    assert stopped == []
    assert task.status == "running"


def test_stop_expired_tasks_stops_paused_with_expired_end(db_session):
    c = _campaign(db_session, status="paused")
    task = _task(db_session, c, status="paused", end_at=NOW - timedelta(minutes=1))
    stopped = lifecycle.stop_expired_tasks(db_session, NOW)
    assert [t.id for t in stopped] == [task.id]
    assert task.status == "stopped"


# --- complete_finished_tasks ---------------------------------------------------

def test_complete_finished_tasks_completes_when_no_active_leads(db_session):
    c = _campaign(db_session, status="active")
    task = _task(db_session, c, status="running")
    _lead(db_session, c, status="replied")  # not active/sending
    completed = lifecycle.complete_finished_tasks(db_session, NOW)
    assert [t.id for t in completed] == [task.id]
    assert task.status == "completed" and task.completed_at == NOW
    assert c.status == "active"  # unchanged — not "paused"


def test_complete_finished_tasks_leaves_running_with_active_lead(db_session):
    c = _campaign(db_session, status="active")
    task = _task(db_session, c, status="running")
    _lead(db_session, c, status="active")
    completed = lifecycle.complete_finished_tasks(db_session, NOW)
    assert completed == []
    assert task.status == "running"


# --- end-to-end: start -> send -> complete -> reuse Campaign with 2nd task ----

def test_end_to_end_start_send_complete_and_reuse_campaign(db_session, monkeypatch):
    _mock_ok(monkeypatch)
    c = _campaign(db_session)
    seq = _seq(db_session, name="First run", steps=list(STEPS))
    task1 = _task(db_session, c, name="First task", status="draft", sequences={"B": seq.id})
    lead = _lead(db_session, c, email="k@toyota.co.jp", tier="B")

    ok, _ = lifecycle.start_task(db_session, task1, NOW)
    assert ok and task1.steps_by_tier == {"B": STEPS} and c.status == "active"

    s = engine.process_campaign(db_session, c, task1.steps_by_tier, dry_run=False, now=NOW,
                                ignore_hours=True, gap_ms=0)
    assert s.sent == 1
    db_session.refresh(lead)
    assert lead.step == 1 and lead.status == "completed"  # single-step sequence

    completed = lifecycle.complete_finished_tasks(db_session, NOW)
    assert [t.id for t in completed] == [task1.id]
    assert task1.status == "completed"

    # Campaign is now idle (no scheduled/running/paused task) — start a
    # second task on the SAME campaign and confirm the lead resets.
    task2 = _task(db_session, c, name="Second task", status="draft", sequences={"B": seq.id})
    ok2, _ = lifecycle.start_task(db_session, task2, NOW + timedelta(days=1))
    assert ok2
    db_session.refresh(lead)
    assert lead.step == 0 and lead.status == "active"

    s2 = engine.process_campaign(
        db_session, c, task2.steps_by_tier, dry_run=False, now=NOW + timedelta(days=1),
        ignore_hours=True, gap_ms=0,
    )
    assert s2.sent == 1
    db_session.refresh(lead)
    assert lead.step == 1 and lead.status == "completed"
