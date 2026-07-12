"""Sequencer: warmup cap, business hours, dry-run, real advance, suppression,
legal gate, retry cap, and the compare-and-swap claim. All deterministic via an
injected `now` and a mocked mailer."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from awkns_outreach.db.models import Campaign, Event, Lead
from awkns_outreach.send.mailer import SendResult
from awkns_outreach.sequencer import engine
from awkns_outreach.sequencer.limits import (
    SEND,
    in_business_hours,
    tz_for,
    warmup_cap,
)

UTC = timezone.utc
_SEQ = [
    {"key": "intro", "delay_days": 0, "subject": "hi {company}", "body": "b0 {first_name}"},
    {"key": "bump", "delay_days": 3, "subject": "re: hi", "body": "b1"},
]


# --- pure-logic units ------------------------------------------------------

def test_warmup_cap_by_day():
    start = datetime(2026, 6, 1, tzinfo=UTC)
    assert warmup_cap(None, start) == SEND.warmup_ramp[0]        # unset ⇒ conservative
    assert warmup_cap(start, start) == SEND.warmup_ramp[0]       # day 0
    assert warmup_cap(start, start + timedelta(days=3)) == SEND.warmup_ramp[3]
    assert warmup_cap(start, start + timedelta(days=999)) == SEND.hard_daily_cap
    assert warmup_cap(start, start - timedelta(days=1)) == 0     # future start


def test_tz_for_and_business_hours():
    assert tz_for("JP") == "Asia/Tokyo"
    assert tz_for(None) == "Asia/Taipei"
    # 2026-07-04 is a Saturday → outside Mon–Fri everywhere.
    sat = datetime(2026, 7, 4, 3, 0, tzinfo=UTC)  # 11:00 Sat in Taipei
    assert not in_business_hours(sat, "TW")
    # 2026-07-06 is a Monday; 02:00 UTC = 10:00 Taipei → inside window.
    mon = datetime(2026, 7, 6, 2, 0, tzinfo=UTC)
    assert in_business_hours(mon, "TW")
    # Same instant is 03:00 in Tokyo (UTC+9)? 11:00 → still, use night check:
    night = datetime(2026, 7, 5, 20, 0, tzinfo=UTC)  # 04:00 Mon Taipei
    assert not in_business_hours(night, "TW")


# --- engine fixtures -------------------------------------------------------

def _campaign(session, **identity):
    ident = {"postal_address": "1 Test St", "from": "s@mail.x.com", "sender_name": "Steven"}
    ident.update(identity)
    c = Campaign(
        name="c", target_titles=[], seed_companies=[], sequence=_SEQ,
        sender_identity=ident, warmup_start=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session.add(c)
    session.flush()
    return c


def _lead(session, c, **kw):
    base = dict(campaign_id=c.id, email="k@toyota.co.jp", company="Toyota",
                contact_name="Kenji", status="active", step=0)
    base.update(kw)
    lead = Lead(**base)
    session.add(lead)
    session.flush()
    return lead


def _mock_ok(monkeypatch):
    monkeypatch.setattr(engine, "send_outreach_email",
                        lambda l, c, e, s, dry_run: SendResult(ok=True, id=f"msg-{s}", subject="subj"))


NOW = datetime(2026, 7, 6, 2, 0, tzinfo=UTC)  # Monday, business hours in Taipei


def test_dry_run_sends_nothing_and_does_not_advance(db_session):
    c = _campaign(db_session)
    lead = _lead(db_session, c)
    s = engine.process_campaign(db_session, c, dry_run=True, now=NOW, ignore_hours=True)
    assert s.sent == 1 and s.dry_run
    db_session.refresh(lead)
    assert lead.step == 0 and lead.status == "active"  # unchanged
    assert db_session.query(Event).count() == 0


def test_real_send_advances_step_and_logs(db_session, monkeypatch):
    _mock_ok(monkeypatch)
    c = _campaign(db_session)
    lead = _lead(db_session, c)
    s = engine.process_campaign(db_session, c, dry_run=False, now=NOW, ignore_hours=True, gap_ms=0)
    assert s.sent == 1
    db_session.refresh(lead)
    assert lead.step == 1 and lead.status == "active"
    # SQLite drops tzinfo; compare naive wall-clock (Postgres keeps it aware).
    got = lead.next_action_at.replace(tzinfo=None)
    assert got == (NOW + timedelta(days=3)).replace(tzinfo=None)  # step-1 delay
    assert lead.thread_ref == "msg-0"
    ev = db_session.query(Event).one()
    assert ev.type == "sent" and ev.step == 0 and ev.detail == "msg-0"


def test_final_step_completes(db_session, monkeypatch):
    _mock_ok(monkeypatch)
    c = _campaign(db_session)
    lead = _lead(db_session, c, step=1)  # last step
    engine.process_campaign(db_session, c, dry_run=False, now=NOW, ignore_hours=True, gap_ms=0)
    db_session.refresh(lead)
    assert lead.step == 2 and lead.status == "completed" and lead.next_action_at is None


def test_suppressed_lead_flipped(db_session, monkeypatch):
    """Engine's suppression guard: a global suppression exists (e.g. from another
    campaign) while THIS lead is still active — the engine must flip it, not send."""
    _mock_ok(monkeypatch)
    from awkns_outreach.db.models import Suppression
    c = _campaign(db_session)
    lead = _lead(db_session, c)
    # Insert the suppression directly so this lead stays "active" (suppress()
    # would have flipped it already — that path is covered in test_compliance).
    db_session.add(Suppression(email="k@toyota.co.jp", reason="unsubscribe"))
    db_session.commit()
    s = engine.process_campaign(db_session, c, dry_run=False, now=NOW, ignore_hours=True, gap_ms=0)
    assert s.suppressed == 1 and s.sent == 0
    db_session.refresh(lead)
    assert lead.status == "suppressed"


def test_business_hours_skip(db_session, monkeypatch):
    _mock_ok(monkeypatch)
    c = _campaign(db_session)
    _lead(db_session, c, country="TW")
    sat = datetime(2026, 7, 4, 3, 0, tzinfo=UTC)  # Saturday in Taipei
    s = engine.process_campaign(db_session, c, dry_run=False, now=sat, gap_ms=0)
    assert s.sent == 0 and s.skipped == 1


def test_legal_gate_blocks_real_send(db_session, monkeypatch):
    # Hermetic: force the global fallback empty so the gate depends only on the
    # (empty) campaign identity, not on the developer's .env.
    from awkns_outreach.config import settings
    monkeypatch.setattr(settings, "outreach_postal_address", "")
    c = _campaign(db_session, postal_address="")  # no address
    _lead(db_session, c)
    s = engine.process_campaign(db_session, c, dry_run=False, now=NOW, ignore_hours=True)
    assert s.sent == 0 and s.blocked and "postal address" in s.blocked


def test_retry_cap_parks_lead_as_failed(db_session, monkeypatch):
    monkeypatch.setattr(engine, "send_outreach_email",
                        lambda l, c, e, s, dry_run: SendResult(ok=False, error="bad address", subject="x"))
    c = _campaign(db_session)
    lead = _lead(db_session, c)
    for _ in range(engine.MAX_SEND_ERRORS):
        engine.process_campaign(db_session, c, dry_run=False, now=NOW, ignore_hours=True, gap_ms=0)
    db_session.refresh(lead)
    assert lead.status == "failed"
    assert db_session.query(Event).filter_by(type="error").count() == engine.MAX_SEND_ERRORS


def test_rolling_24h_cap_enforced(db_session, monkeypatch):
    _mock_ok(monkeypatch)
    c = _campaign(db_session)
    # 3 due leads, but only allow 2 this run.
    for i in range(3):
        _lead(db_session, c, email=f"a{i}@x.com")
    s = engine.process_campaign(db_session, c, dry_run=False, now=NOW, ignore_hours=True,
                                gap_ms=0, max_this_run=2)
    assert s.sent == 2  # budget capped this run


def test_process_campaign_blocked_when_paused_or_archived(db_session, monkeypatch):
    """A paused/archived campaign must not send for real; dry-run previews
    stay allowed regardless of status."""
    _mock_ok(monkeypatch)
    c = _campaign(db_session)
    c.status = "paused"
    lead = _lead(db_session, c)
    db_session.commit()

    s = engine.process_campaign(db_session, c, dry_run=False, now=NOW, ignore_hours=True, gap_ms=0)
    assert s.blocked == "campaign is paused"
    assert s.sent == 0
    db_session.refresh(lead)
    assert lead.step == 0 and lead.status == "active"

    c.status = "archived"
    db_session.commit()
    s2 = engine.process_campaign(db_session, c, dry_run=False, now=NOW, ignore_hours=True, gap_ms=0)
    assert s2.blocked == "campaign is archived"
    assert s2.sent == 0

    s3 = engine.process_campaign(db_session, c, dry_run=True, now=NOW, ignore_hours=True)
    assert s3.blocked is None
    assert s3.sent == 1


def test_empty_sequence_blocked_and_does_not_complete_leads(db_session):
    """Regression guard: a brand-new Group has campaign.sequence == [] and its
    leads start at step=0, so `lead.step >= len(sequence)` (0 >= 0) would be
    true immediately — process_campaign must short-circuit BEFORE that check,
    for both dry-run and real-send, so a fresh Group's leads never get
    silently marked completed before any sequence has actually run."""
    c = _campaign(db_session)
    c.sequence = []
    lead = _lead(db_session, c)

    s = engine.process_campaign(db_session, c, dry_run=True, now=NOW, ignore_hours=True)
    assert s.blocked == "no sequence"
    db_session.refresh(lead)
    assert lead.status == "active"

    s2 = engine.process_campaign(db_session, c, dry_run=False, now=NOW, ignore_hours=True, gap_ms=0)
    assert s2.blocked == "no sequence"
    db_session.refresh(lead)
    assert lead.status == "active"


def test_tier_ordering_a_before_null_before_c(db_session, monkeypatch):
    """A lead's tier orders the send queue: "A" first, NULL tier (counts as
    "B") next, "C" last. With a budget of 1, only the "A" lead sends."""
    _mock_ok(monkeypatch)
    c = _campaign(db_session)
    lead_c = _lead(db_session, c, email="c@x.com", tier="C")
    lead_null = _lead(db_session, c, email="null@x.com", tier=None)
    lead_a = _lead(db_session, c, email="a@x.com", tier="A")

    s = engine.process_campaign(db_session, c, dry_run=False, now=NOW, ignore_hours=True,
                                gap_ms=0, max_this_run=1)
    assert s.sent == 1
    db_session.refresh(lead_a)
    db_session.refresh(lead_null)
    db_session.refresh(lead_c)
    assert lead_a.step == 1          # "A" sent first
    assert lead_null.step == 0       # NULL (as "B") not reached this run
    assert lead_c.step == 0          # "C" not reached this run

    s2 = engine.process_campaign(db_session, c, dry_run=False, now=NOW, ignore_hours=True,
                                 gap_ms=0, max_this_run=1)
    assert s2.sent == 1
    db_session.refresh(lead_null)
    db_session.refresh(lead_c)
    assert lead_null.step == 1       # NULL/"B" sent before "C"
    assert lead_c.step == 0


def test_cas_claim_is_single_winner(db_session):
    """The compare-and-swap that prevents double-sends: only the first UPDATE
    active→sending at (id, step) wins; a second sees rowcount 0."""
    c = _campaign(db_session)
    lead = _lead(db_session, c)

    def claim():
        return db_session.execute(
            update(Lead)
            .where(Lead.id == lead.id, Lead.step == 0, Lead.status == "active")
            .values(status="sending")
        ).rowcount

    assert claim() == 1  # first worker wins
    assert claim() == 0  # second worker loses — no double-send
