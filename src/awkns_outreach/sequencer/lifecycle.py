"""Task lifecycle state machine: draft -> scheduled -> running ->
paused/stopped/completed.

Shared by the Tasks web page (schedule/start/pause/resume/stop) and the
scheduler tick (start_due_tasks / stop_expired_tasks / complete_finished_tasks).
Each transition function validates its own starting-status precondition and
returns `(ok, message)` rather than raising — a whitelist-transition-table
pattern like admin.py's `_STATUS_TRANSITIONS`, just expressed as functions
because each transition here has non-trivial side effects (snapshotting
steps per tier, resetting/parking lead cursors, mirroring campaign.status).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from awkns_outreach.db.models import Lead, MailSequence, Task

# Statuses that block a new schedule/start on the same campaign — only one
# task may be "active" (scheduled/running/paused) per Campaign at a time.
_ACTIVE_STATUSES = ("scheduled", "running", "paused")
# Lead statuses reset to a clean slate (or parked) when a task (re)starts on
# a Campaign.
_RESETTABLE_LEAD_STATUSES = ("active", "completed", "paused")


def active_conflict(
    db: Session, campaign_id: str, exclude_id: Optional[str] = None
) -> Optional[Task]:
    """The other Task blocking a new schedule/start on this campaign, if any
    (status in scheduled/running/paused), else None."""
    stmt = select(Task).where(
        Task.campaign_id == campaign_id,
        Task.status.in_(_ACTIVE_STATUSES),
    )
    if exclude_id is not None:
        stmt = stmt.where(Task.id != exclude_id)
    return db.scalars(stmt).first()


def _validate_assignments(db: Session, task: Task) -> Optional[str]:
    """Shared by schedule_task/start_task: every assigned tier's sequence
    must still exist, be active, and have at least one step (it may have
    been archived/emptied since assignment). Returns an error message naming
    the failing tier, or None if all assignments are valid."""
    if not task.sequences:
        return "Assign at least one tier's sequence first."
    for tier, seq_id in task.sequences.items():
        seq = db.get(MailSequence, seq_id)
        if seq is None:
            return f"Tier {tier}'s assigned sequence no longer exists."
        if seq.status != "active":
            return f"Tier {tier}'s assigned sequence ({seq.name}) is archived."
        if not seq.steps:
            return f"Tier {tier}'s assigned sequence ({seq.name}) has no steps."
    return None


def schedule_task(
    db: Session, task: Task, when: datetime, end_at: Optional[datetime] = None
) -> tuple[bool, str]:
    if task.status != "draft":
        return False, "Task isn't a draft."
    conflict = active_conflict(db, task.campaign_id, exclude_id=task.id)
    if conflict is not None:
        return False, f"{conflict.name} is already {conflict.status} for this campaign."
    error = _validate_assignments(db, task)
    if error is not None:
        return False, error
    if end_at is not None and end_at <= when:
        return False, "End time must be after the start time."
    task.scheduled_start_at = when
    task.end_at = end_at
    task.status = "scheduled"
    db.commit()
    return True, "Task scheduled."


def unschedule_task(db: Session, task: Task) -> tuple[bool, str]:
    if task.status != "scheduled":
        return False, "Task isn't scheduled."
    task.scheduled_start_at = None
    task.end_at = None
    task.status = "draft"
    db.commit()
    return True, "Task unscheduled."


def start_task(db: Session, task: Task, now: datetime) -> tuple[bool, str]:
    if task.status not in ("draft", "scheduled"):
        return False, "Task can't be started from its current status."
    conflict = active_conflict(db, task.campaign_id, exclude_id=task.id)
    if conflict is not None:
        return False, f"{conflict.name} is already {conflict.status} for this campaign."
    error = _validate_assignments(db, task)
    if error is not None:
        return False, error

    campaign = task.campaign
    assigned_tiers = list(task.sequences.keys())
    task.steps_by_tier = {
        tier: [dict(step) for step in db.get(MailSequence, seq_id).steps]
        for tier, seq_id in task.sequences.items()
    }

    effective_tier = func.coalesce(Lead.tier, "B")

    # 1. Resettable leads in an assigned tier -> clean slate, ready to send.
    db.execute(
        update(Lead)
        .where(
            Lead.campaign_id == campaign.id,
            Lead.status.in_(_RESETTABLE_LEAD_STATUSES),
            effective_tier.in_(assigned_tiers),
        )
        .values(
            step=0, status="active", next_action_at=None,
            thread_ref=None, last_message_id=None,
        )
    )
    # 2. Resettable leads NOT in an assigned tier -> parked (recoverable by a
    # future start, since "paused" is itself a resettable status).
    db.execute(
        update(Lead)
        .where(
            Lead.campaign_id == campaign.id,
            Lead.status.in_(_RESETTABLE_LEAD_STATUSES),
            effective_tier.not_in(assigned_tiers),
        )
        .values(status="paused")
    )

    campaign.status = "active"
    task.status = "running"
    task.started_at = now
    db.commit()
    return True, "Task started."


def pause_task(db: Session, task: Task) -> tuple[bool, str]:
    if task.status != "running":
        return False, "Task isn't running."
    task.campaign.status = "paused"
    task.status = "paused"
    db.commit()
    return True, "Task paused."


def resume_task(db: Session, task: Task) -> tuple[bool, str]:
    if task.status != "paused":
        return False, "Task isn't paused."
    conflict = active_conflict(db, task.campaign_id, exclude_id=task.id)
    if conflict is not None:
        return False, f"{conflict.name} is already {conflict.status} for this campaign."
    task.campaign.status = "active"
    task.status = "running"
    db.commit()
    return True, "Task resumed."


def stop_task(db: Session, task: Task) -> tuple[bool, str]:
    if task.status not in ("running", "paused"):
        return False, "Task can't be stopped from its current status."
    task.campaign.status = "paused"
    # Clear the stopped snapshot so a later dashboard resume of this Campaign
    # (which only re-activates a `paused` Task, not a `stopped` one) can't
    # resurrect the send: engine.py's empty-steps guard makes
    # process_campaign no-op instead of resending stale content.
    task.steps_by_tier = {}
    task.status = "stopped"
    db.commit()
    return True, "Task stopped."


def start_due_tasks(db: Session, now: datetime) -> list[Task]:
    due = db.scalars(
        select(Task)
        .where(Task.status == "scheduled", Task.scheduled_start_at <= now)
        .order_by(Task.scheduled_start_at.asc())
    ).all()
    started: list[Task] = []
    for task in due:
        ok, _msg = start_task(db, task, now)
        if ok:
            started.append(task)
    return started


def stop_expired_tasks(db: Session, now: datetime) -> list[Task]:
    """Tasks past their optional `end_at` get stopped automatically, same as
    a manual stop (resurrect-guard included)."""
    expired = db.scalars(
        select(Task).where(
            Task.status.in_(("running", "paused")),
            Task.end_at.is_not(None),
            Task.end_at <= now,
        )
    ).all()
    stopped: list[Task] = []
    for task in expired:
        ok, _msg = stop_task(db, task)
        if ok:
            stopped.append(task)
    return stopped


def complete_finished_tasks(db: Session, now: datetime) -> list[Task]:
    running = db.scalars(select(Task).where(Task.status == "running")).all()
    completed: list[Task] = []
    for task in running:
        remaining = db.scalar(
            select(Lead.id)
            .where(Lead.campaign_id == task.campaign_id, Lead.status.in_(("active", "sending")))
            .limit(1)
        )
        if remaining is None:
            task.status = "completed"
            task.completed_at = now
            db.commit()
            completed.append(task)
    return completed
