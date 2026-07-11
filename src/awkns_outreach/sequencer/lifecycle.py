"""MailSequence lifecycle state machine: draft -> scheduled -> running ->
paused/stopped/completed.

Shared by the Tasks web page (schedule/start/pause/resume/stop) and the
scheduler tick (start_due_sequences / complete_finished_sequences). Each
transition function validates its own starting-status precondition and
returns `(ok, message)` rather than raising — a whitelist-transition-table
pattern like admin.py's `_STATUS_TRANSITIONS`, just expressed as functions
because each transition here has non-trivial side effects (snapshotting
steps, resetting lead cursors, mirroring campaign.status).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from awkns_outreach.db.models import Lead, MailSequence

# Statuses that block a new schedule/start on the same campaign — only one
# sequence may be "active" (scheduled/running/paused) per Group at a time.
_ACTIVE_STATUSES = ("scheduled", "running", "paused")
# Lead statuses reset to a clean slate when a sequence (re)starts on a Group.
_RESETTABLE_LEAD_STATUSES = ("active", "completed", "paused")


def active_conflict(
    db: Session, campaign_id: str, exclude_id: Optional[str] = None
) -> Optional[MailSequence]:
    """The other MailSequence blocking a new schedule/start on this campaign,
    if any (status in scheduled/running/paused), else None."""
    stmt = select(MailSequence).where(
        MailSequence.campaign_id == campaign_id,
        MailSequence.status.in_(_ACTIVE_STATUSES),
    )
    if exclude_id is not None:
        stmt = stmt.where(MailSequence.id != exclude_id)
    return db.scalars(stmt).first()


def schedule_sequence(db: Session, seq: MailSequence, when: datetime) -> tuple[bool, str]:
    if seq.status != "draft":
        return False, "Sequence isn't a draft."
    conflict = active_conflict(db, seq.campaign_id, exclude_id=seq.id)
    if conflict is not None:
        return False, f"{conflict.name} is already {conflict.status} for this group."
    seq.scheduled_start_at = when
    seq.status = "scheduled"
    db.commit()
    return True, "Sequence scheduled."


def unschedule_sequence(db: Session, seq: MailSequence) -> tuple[bool, str]:
    if seq.status != "scheduled":
        return False, "Sequence isn't scheduled."
    seq.scheduled_start_at = None
    seq.status = "draft"
    db.commit()
    return True, "Sequence unscheduled."


def start_sequence(db: Session, seq: MailSequence, now: datetime) -> tuple[bool, str]:
    if seq.status not in ("draft", "scheduled"):
        return False, "Sequence can't be started from its current status."
    conflict = active_conflict(db, seq.campaign_id, exclude_id=seq.id)
    if conflict is not None:
        return False, f"{conflict.name} is already {conflict.status} for this group."
    if not seq.steps:
        return False, "Sequence has no steps."

    campaign = seq.campaign
    campaign.sequence = [dict(step) for step in seq.steps]

    db.execute(
        update(Lead)
        .where(
            Lead.campaign_id == campaign.id,
            Lead.status.in_(_RESETTABLE_LEAD_STATUSES),
        )
        .values(
            step=0, status="active", next_action_at=None,
            thread_ref=None, last_message_id=None,
        )
    )

    campaign.status = "active"
    seq.status = "running"
    seq.started_at = now
    db.commit()
    return True, "Sequence started."


def pause_sequence(db: Session, seq: MailSequence) -> tuple[bool, str]:
    if seq.status != "running":
        return False, "Sequence isn't running."
    seq.campaign.status = "paused"
    seq.status = "paused"
    db.commit()
    return True, "Sequence paused."


def resume_sequence(db: Session, seq: MailSequence) -> tuple[bool, str]:
    if seq.status != "paused":
        return False, "Sequence isn't paused."
    conflict = active_conflict(db, seq.campaign_id, exclude_id=seq.id)
    if conflict is not None:
        return False, f"{conflict.name} is already {conflict.status} for this group."
    seq.campaign.status = "active"
    seq.status = "running"
    db.commit()
    return True, "Sequence resumed."


def stop_sequence(db: Session, seq: MailSequence) -> tuple[bool, str]:
    if seq.status not in ("running", "paused"):
        return False, "Sequence can't be stopped from its current status."
    seq.campaign.status = "paused"
    seq.status = "stopped"
    db.commit()
    return True, "Sequence stopped."


def start_due_sequences(db: Session, now: datetime) -> list[MailSequence]:
    due = db.scalars(
        select(MailSequence)
        .where(MailSequence.status == "scheduled", MailSequence.scheduled_start_at <= now)
        .order_by(MailSequence.scheduled_start_at.asc())
    ).all()
    started: list[MailSequence] = []
    for seq in due:
        ok, _msg = start_sequence(db, seq, now)
        if ok:
            started.append(seq)
    return started


def complete_finished_sequences(db: Session, now: datetime) -> list[MailSequence]:
    running = db.scalars(select(MailSequence).where(MailSequence.status == "running")).all()
    completed: list[MailSequence] = []
    for seq in running:
        remaining = db.scalar(
            select(Lead.id)
            .where(Lead.campaign_id == seq.campaign_id, Lead.status.in_(("active", "sending")))
            .limit(1)
        )
        if remaining is None:
            seq.status = "completed"
            seq.completed_at = now
            db.commit()
            completed.append(seq)
    return completed
