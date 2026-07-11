"""MailSequence lifecycle: schedule/start/pause/resume/stop, the one-active-
per-group conflict guard, the scheduler-tick helpers (start_due_sequences /
complete_finished_sequences), and an end-to-end pass through the real engine
validating Group reuse. Follows test_sequencer.py's db_session-fixture style
— pure lifecycle-module tests with injected `now`, no web client."""
from datetime import datetime, timedelta, timezone

from awkns_outreach.db.models import Campaign, MailSequence
from awkns_outreach.send.mailer import SendResult
from awkns_outreach.sequencer import engine, lifecycle

UTC = timezone.utc
NOW = datetime(2026, 7, 6, 2, 0, tzinfo=UTC)  # Monday, business hours in Taipei
STEPS = [
    {"key": "intro", "delay_days": 0, "subject": "hi {company}", "body": "b0"},
]


def _campaign(session, **kw) -> Campaign:
    base = dict(
        name="c", target_titles=[], seed_companies=[], sequence=[],
        sender_identity={"postal_address": "1 Test St", "from": "s@mail.x.com",
                         "sender_name": "Steven"},
        warmup_start=datetime(2026, 1, 1, tzinfo=UTC),
    )
    base.update(kw)
    c = Campaign(**base)
    session.add(c)
    session.flush()
    return c


def _sequence(session, campaign, **kw) -> MailSequence:
    base = dict(name="Seq", campaign_id=campaign.id, status="draft", steps=list(STEPS))
    base.update(kw)
    seq = MailSequence(**base)
    session.add(seq)
    session.flush()
    return seq


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
                        lambda l, c, e, s, dry_run: SendResult(ok=True, id=f"msg-{s}", subject="subj"))


# --- active_conflict --------------------------------------------------------

def test_active_conflict_none_when_only_draft_exists(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="draft")
    assert lifecycle.active_conflict(db_session, c.id, exclude_id=seq.id) is None


def test_active_conflict_finds_scheduled_running_or_paused(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="draft")
    other = _sequence(db_session, c, name="Other", status="running")
    conflict = lifecycle.active_conflict(db_session, c.id, exclude_id=seq.id)
    assert conflict is not None and conflict.id == other.id


def test_active_conflict_excludes_self(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="scheduled")
    assert lifecycle.active_conflict(db_session, c.id, exclude_id=seq.id) is None


# --- schedule_sequence / unschedule_sequence --------------------------------

def test_schedule_sequence_happy_path(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="draft")
    when = NOW + timedelta(days=1)
    ok, msg = lifecycle.schedule_sequence(db_session, seq, when)
    assert ok and msg == "Sequence scheduled."
    assert seq.status == "scheduled"
    assert seq.scheduled_start_at == when


def test_schedule_sequence_rejects_wrong_status(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="running")
    ok, msg = lifecycle.schedule_sequence(db_session, seq, NOW)
    assert not ok and seq.status == "running" and seq.scheduled_start_at is None
    assert msg == "Sequence isn't a draft."


def test_schedule_sequence_rejects_on_conflict(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="draft")
    other = _sequence(db_session, c, name="Other seq", status="scheduled")
    ok, msg = lifecycle.schedule_sequence(db_session, seq, NOW)
    assert not ok and seq.status == "draft" and seq.scheduled_start_at is None
    assert msg == "Other seq is already scheduled for this group."


def test_unschedule_sequence_happy_path(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="scheduled", scheduled_start_at=NOW)
    ok, msg = lifecycle.unschedule_sequence(db_session, seq)
    assert ok and msg == "Sequence unscheduled."
    assert seq.status == "draft" and seq.scheduled_start_at is None


def test_unschedule_sequence_rejects_wrong_status(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="draft")
    ok, msg = lifecycle.unschedule_sequence(db_session, seq)
    assert not ok and msg == "Sequence isn't scheduled."
    assert seq.status == "draft"


# --- start_sequence ----------------------------------------------------------

def test_start_sequence_snapshots_steps_into_campaign(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="draft", steps=list(STEPS))
    ok, msg = lifecycle.start_sequence(db_session, seq, NOW)
    assert ok and msg == "Sequence started."
    assert c.sequence == STEPS
    assert c.status == "active"
    assert seq.status == "running" and seq.started_at == NOW

    # Mutating the sequence's OWN steps afterward must not retroactively
    # change what was already committed to campaign.sequence (deep-enough copy).
    seq.steps[0]["subject"] = "mutated"
    assert c.sequence[0]["subject"] == "hi {company}"


def test_start_sequence_resets_completed_lead_and_spares_replied_lead(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="draft")
    completed_lead = _lead(
        db_session, c, email="done@x.com", status="completed", step=3,
        next_action_at=NOW, thread_ref="thread-1", last_message_id="msg-1",
    )
    replied_lead = _lead(
        db_session, c, email="replied@x.com", status="replied", step=1,
        next_action_at=NOW, thread_ref="thread-2", last_message_id="msg-2",
    )
    ok, _ = lifecycle.start_sequence(db_session, seq, NOW)
    assert ok
    db_session.refresh(completed_lead)
    db_session.refresh(replied_lead)

    assert completed_lead.step == 0
    assert completed_lead.status == "active"
    assert completed_lead.next_action_at is None
    assert completed_lead.thread_ref is None
    assert completed_lead.last_message_id is None

    # replied lead is untouched (not in the resettable status set)
    assert replied_lead.step == 1
    assert replied_lead.status == "replied"
    assert replied_lead.thread_ref == "thread-2"
    assert replied_lead.last_message_id == "msg-2"


def test_start_sequence_rejects_on_conflict(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="draft")
    other = _sequence(db_session, c, name="Other seq", status="paused")
    ok, msg = lifecycle.start_sequence(db_session, seq, NOW)
    assert not ok and seq.status == "draft"
    assert msg == "Other seq is already paused for this group."
    assert c.sequence == []


def test_start_sequence_rejects_empty_steps(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="draft", steps=[])
    ok, msg = lifecycle.start_sequence(db_session, seq, NOW)
    assert not ok and msg == "Sequence has no steps."
    assert seq.status == "draft"


def test_start_sequence_rejects_wrong_status(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="completed")
    ok, msg = lifecycle.start_sequence(db_session, seq, NOW)
    assert not ok
    assert msg == "Sequence can't be started from its current status."


# --- pause_sequence / resume_sequence / stop_sequence ------------------------

def test_pause_sequence_happy_path(db_session):
    c = _campaign(db_session, status="active")
    seq = _sequence(db_session, c, status="running")
    ok, msg = lifecycle.pause_sequence(db_session, seq)
    assert ok and msg == "Sequence paused."
    assert seq.status == "paused" and c.status == "paused"


def test_pause_sequence_rejects_wrong_status(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="draft")
    ok, msg = lifecycle.pause_sequence(db_session, seq)
    assert not ok and msg == "Sequence isn't running."
    assert seq.status == "draft"


def test_resume_sequence_happy_path(db_session):
    c = _campaign(db_session, status="paused")
    seq = _sequence(db_session, c, status="paused")
    ok, msg = lifecycle.resume_sequence(db_session, seq)
    assert ok and msg == "Sequence resumed."
    assert seq.status == "running" and c.status == "active"


def test_resume_sequence_rejects_wrong_status(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="stopped")
    ok, msg = lifecycle.resume_sequence(db_session, seq)
    assert not ok and msg == "Sequence isn't paused."


def test_resume_sequence_rejects_on_conflict(db_session):
    c = _campaign(db_session, status="paused")
    seq = _sequence(db_session, c, status="paused")
    other = _sequence(db_session, c, name="Started meanwhile", status="running")
    ok, msg = lifecycle.resume_sequence(db_session, seq)
    assert not ok and seq.status == "paused"
    assert msg == "Started meanwhile is already running for this group."


def test_stop_sequence_from_running(db_session):
    c = _campaign(db_session, status="active")
    seq = _sequence(db_session, c, status="running")
    ok, msg = lifecycle.stop_sequence(db_session, seq)
    assert ok and msg == "Sequence stopped."
    assert seq.status == "stopped" and c.status == "paused"


def test_stop_sequence_from_paused(db_session):
    c = _campaign(db_session, status="paused")
    seq = _sequence(db_session, c, status="paused")
    ok, msg = lifecycle.stop_sequence(db_session, seq)
    assert ok and seq.status == "stopped" and c.status == "paused"


def test_stop_sequence_rejects_wrong_status(db_session):
    c = _campaign(db_session)
    seq = _sequence(db_session, c, status="draft")
    ok, msg = lifecycle.stop_sequence(db_session, seq)
    assert not ok
    assert msg == "Sequence can't be stopped from its current status."
    assert seq.status == "draft"


# --- start_due_sequences -----------------------------------------------------

def test_start_due_sequences_starts_due_leaves_future_alone_skips_conflicts(db_session):
    c1 = _campaign(db_session)
    due = _sequence(db_session, c1, name="Due", status="scheduled", scheduled_start_at=NOW - timedelta(minutes=1))

    c3 = _campaign(db_session)
    future = _sequence(db_session, c3, name="Future", status="scheduled", scheduled_start_at=NOW + timedelta(days=1))

    c2 = _campaign(db_session)
    conflicted = _sequence(db_session, c2, name="Conflicted", status="scheduled", scheduled_start_at=NOW)
    _sequence(db_session, c2, name="Already running", status="running")

    started = lifecycle.start_due_sequences(db_session, NOW)

    assert [s.id for s in started] == [due.id]
    assert due.status == "running"
    assert future.status == "scheduled"  # untouched, not due yet
    assert conflicted.status == "scheduled"  # skipped, not crashed


# --- complete_finished_sequences ---------------------------------------------

def test_complete_finished_sequences_completes_when_no_active_leads(db_session):
    c = _campaign(db_session, status="active")
    seq = _sequence(db_session, c, status="running")
    _lead(db_session, c, status="replied")  # not active/sending
    completed = lifecycle.complete_finished_sequences(db_session, NOW)
    assert [s.id for s in completed] == [seq.id]
    assert seq.status == "completed" and seq.completed_at == NOW
    assert c.status == "active"  # unchanged — not "paused"


def test_complete_finished_sequences_leaves_running_with_active_lead(db_session):
    c = _campaign(db_session, status="active")
    seq = _sequence(db_session, c, status="running")
    _lead(db_session, c, status="active")
    completed = lifecycle.complete_finished_sequences(db_session, NOW)
    assert completed == []
    assert seq.status == "running"


# --- end-to-end: start -> send -> complete -> reuse Group with 2nd sequence --

def test_end_to_end_start_send_complete_and_reuse_group(db_session, monkeypatch):
    _mock_ok(monkeypatch)
    c = _campaign(db_session)
    seq1 = _sequence(db_session, c, name="First run", status="draft", steps=list(STEPS))
    lead = _lead(db_session, c, email="k@toyota.co.jp")

    ok, _ = lifecycle.start_sequence(db_session, seq1, NOW)
    assert ok and c.sequence == STEPS and c.status == "active"

    s = engine.process_campaign(db_session, c, dry_run=False, now=NOW, ignore_hours=True, gap_ms=0)
    assert s.sent == 1
    db_session.refresh(lead)
    assert lead.step == 1 and lead.status == "completed"  # single-step sequence

    completed = lifecycle.complete_finished_sequences(db_session, NOW)
    assert [x.id for x in completed] == [seq1.id]
    assert seq1.status == "completed"

    # Group is now idle (no scheduled/running/paused sequence) — start a
    # second sequence on the SAME campaign and confirm the lead resets.
    seq2 = _sequence(db_session, c, name="Second run", status="draft", steps=list(STEPS))
    ok2, _ = lifecycle.start_sequence(db_session, seq2, NOW + timedelta(days=1))
    assert ok2
    db_session.refresh(lead)
    assert lead.step == 0 and lead.status == "active"

    s2 = engine.process_campaign(
        db_session, c, dry_run=False, now=NOW + timedelta(days=1), ignore_hours=True, gap_ms=0
    )
    assert s2.sent == 1
    db_session.refresh(lead)
    assert lead.step == 1 and lead.status == "completed"
