"""Tasks page: create/edit/delete a Task (campaign + per-tier sequence
assignment + schedule window) and drive its lifecycle
(schedule/unschedule/start/pause/resume/stop).

Split out of sequences.py (Task 3): sequences.py now owns only the
standalone MailSequence content library; this file owns the send-campaign
entity that assigns that content to a Campaign per lead tier.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from fastapi.responses import HTMLResponse, RedirectResponse

from awkns_outreach.db.models import Campaign, Lead, MailSequence, Task
from awkns_outreach.sequencer import lifecycle
from awkns_outreach.web.deps import get_db, require_admin, templates

router = APIRouter(dependencies=[Depends(require_admin)])

_TIERS = ("A", "B", "C")

# Actions dispatched by POST /tasks/{id}/lifecycle — mirrors admin.py's
# _STATUS_TRANSITIONS-style whitelist, but as functions (each transition has
# non-trivial side effects: snapshotting steps per tier, resetting/parking
# lead cursors, etc).
_LIFECYCLE_ACTIONS = {
    "pause": lambda db, task, now: lifecycle.pause_task(db, task),
    "resume": lambda db, task, now: lifecycle.resume_task(db, task),
    "stop": lambda db, task, now: lifecycle.stop_task(db, task),
    "start": lambda db, task, now: lifecycle.start_task(db, task, now),
}

# Only pre-start tasks can still have their name/campaign/assignments changed.
_EDITABLE_STATUSES = ("draft", "scheduled")
# Anything that isn't actively running/paused can be removed outright.
_DELETABLE_STATUSES = ("draft", "scheduled", "stopped", "completed")


def _get_task(db: Session, task_id: str) -> Task:
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


def _edit_guard(task: Task) -> Optional[RedirectResponse]:
    """Server-side backup for the disabled Edit link: a task that has
    already started (running/paused/stopped/completed) can't be edited."""
    if task.status not in _EDITABLE_STATUSES:
        return RedirectResponse(
            f"/tasks?msg=Task can't be edited while {task.status}.", status_code=303,
        )
    return None


def _task_campaigns(db: Session) -> list[Campaign]:
    return db.scalars(
        select(Campaign).where(Campaign.status.in_(["active", "paused"])).order_by(Campaign.name)
    ).all()


def _active_sequences(db: Session) -> list[MailSequence]:
    return db.scalars(
        select(MailSequence).where(MailSequence.status == "active").order_by(MailSequence.name)
    ).all()


def _tier_counts(db: Session, campaign_id: str) -> dict[str, int]:
    """Per-tier lead counts for the campaign-summary fragment: A, C counted
    directly; B includes NULL-tier (unclassified, sent as B) leads, with the
    unclassified portion broken out separately for the label."""
    counts = dict(
        db.execute(
            select(Lead.tier, func.count())
            .where(Lead.campaign_id == campaign_id)
            .group_by(Lead.tier)
        ).all()
    )
    unclassified = counts.get(None, 0)
    return {
        "A": counts.get("A", 0),
        "B": counts.get("B", 0) + unclassified,
        "C": counts.get("C", 0),
        "unclassified": unclassified,
    }


@router.get("/tasks", response_class=HTMLResponse)
def list_tasks(request: Request, db: Session = Depends(get_db), msg: Optional[str] = None):
    items = db.scalars(select(Task).order_by(Task.created_at.desc())).all()
    campaign_names = {c.id: c.name for c in db.scalars(select(Campaign)).all()}
    sequence_names = {s.id: s.name for s in db.scalars(select(MailSequence)).all()}
    rows = [
        {
            "t": t,
            "campaign_name": campaign_names.get(t.campaign_id, "—"),
            "assignments": [(tier, sequence_names.get(t.sequences.get(tier), "—"))
                             for tier in _TIERS if tier in (t.sequences or {})],
        }
        for t in items
    ]
    return templates.TemplateResponse(
        request, "tasks.html",
        {"rows": rows, "editable_statuses": _EDITABLE_STATUSES, "msg": msg},
    )


@router.get("/tasks/new", response_class=HTMLResponse)
def new_task_form(request: Request, db: Session = Depends(get_db), msg: Optional[str] = None):
    return templates.TemplateResponse(
        request, "task_edit.html",
        {
            "t": None, "campaigns": _task_campaigns(db), "sequences": _active_sequences(db),
            "tiers": _TIERS, "tier_counts": None, "form_action": "/tasks", "msg": msg,
        },
    )


def _parse_sequence_assignment(
    db: Session, seq_a: str, seq_b: str, seq_c: str,
) -> tuple[Optional[dict[str, str]], Optional[str]]:
    """Build the {tier: sequence_id} dict from the three per-tier form
    selects, validating each chosen id is a real MailSequence. Returns
    (sequences, None) on success or (None, error_message)."""
    raw = {"A": seq_a, "B": seq_b, "C": seq_c}
    sequences: dict[str, str] = {}
    for tier, seq_id in raw.items():
        if not seq_id:
            continue
        if not db.get(MailSequence, seq_id):
            return None, f"Tier {tier}: select a valid sequence."
        sequences[tier] = seq_id
    if not sequences:
        return None, "Assign at least one tier's sequence."
    return sequences, None


@router.post("/tasks")
def create_task(
    name: str = Form(""),
    campaign_id: str = Form(""),
    seq_A: str = Form(""),
    seq_B: str = Form(""),
    seq_C: str = Form(""),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        return RedirectResponse("/tasks/new?msg=Name is required.", status_code=303)
    if not db.get(Campaign, campaign_id):
        return RedirectResponse("/tasks/new?msg=Select a valid campaign.", status_code=303)
    sequences, error = _parse_sequence_assignment(db, seq_A, seq_B, seq_C)
    if error is not None:
        return RedirectResponse(f"/tasks/new?msg={error}", status_code=303)

    task = Task(name=name, campaign_id=campaign_id, sequences=sequences)
    db.add(task)
    db.commit()
    return RedirectResponse("/tasks?msg=Task created.", status_code=303)


@router.get("/tasks/{task_id}/edit", response_class=HTMLResponse)
def edit_task_form(
    task_id: str, request: Request, db: Session = Depends(get_db), msg: Optional[str] = None,
):
    task = _get_task(db, task_id)
    blocked = _edit_guard(task)
    if blocked:
        return blocked
    return templates.TemplateResponse(
        request, "task_edit.html",
        {
            "t": task, "campaigns": _task_campaigns(db), "sequences": _active_sequences(db),
            "tiers": _TIERS, "tier_counts": _tier_counts(db, task.campaign_id),
            "form_action": f"/tasks/{task.id}/edit", "msg": msg,
        },
    )


@router.post("/tasks/{task_id}/edit", response_class=HTMLResponse)
def update_task(
    task_id: str,
    action: str = Form("save"),
    name: str = Form(""),
    campaign_id: str = Form(""),
    seq_A: str = Form(""),
    seq_B: str = Form(""),
    seq_C: str = Form(""),
    db: Session = Depends(get_db),
):
    task = _get_task(db, task_id)

    if action == "delete":
        if task.status not in _DELETABLE_STATUSES:
            return RedirectResponse(
                f"/tasks?msg=Task can't be deleted while {task.status}.", status_code=303,
            )
        db.delete(task)
        db.commit()
        return RedirectResponse("/tasks?msg=Task deleted.", status_code=303)

    if action != "save":
        raise HTTPException(400, f"Unknown action: {action}")

    blocked = _edit_guard(task)
    if blocked:
        return blocked

    name = name.strip()
    if not name:
        return RedirectResponse(f"/tasks/{task.id}/edit?msg=Name is required.", status_code=303)
    if not db.get(Campaign, campaign_id):
        return RedirectResponse(f"/tasks/{task.id}/edit?msg=Select a valid campaign.", status_code=303)
    sequences, error = _parse_sequence_assignment(db, seq_A, seq_B, seq_C)
    if error is not None:
        return RedirectResponse(f"/tasks/{task.id}/edit?msg={error}", status_code=303)

    # A `scheduled` task already occupies its current Campaign's one-active
    # slot — reassigning it to a different Campaign that already has an
    # active task would silently violate that invariant. Re-check whenever
    # the Campaign is actually changing.
    if campaign_id != task.campaign_id:
        conflict = lifecycle.active_conflict(db, campaign_id, exclude_id=task.id)
        if conflict is not None:
            return RedirectResponse(
                f"/tasks/{task.id}/edit?msg={conflict.name} is already {conflict.status} for this campaign.",
                status_code=303,
            )

    task.name = name
    task.campaign_id = campaign_id
    task.sequences = sequences
    db.commit()
    return RedirectResponse(f"/tasks/{task.id}/edit?msg=Task saved.", status_code=303)


@router.get("/tasks/campaign-summary", response_class=HTMLResponse)
def task_campaign_summary(
    request: Request, campaign_id: str = "", db: Session = Depends(get_db),
):
    tier_counts = _tier_counts(db, campaign_id) if campaign_id else None
    return templates.TemplateResponse(
        request, "_task_tier_counts.html", {"tier_counts": tier_counts},
    )


@router.post("/tasks/{task_id}/schedule")
def schedule_task_route(
    task_id: str,
    scheduled_start_at: str = Form(...),
    end_at: str = Form(""),
    db: Session = Depends(get_db),
):
    task = _get_task(db, task_id)
    try:
        when = (
            datetime.fromisoformat(scheduled_start_at)
            .replace(tzinfo=ZoneInfo("Asia/Taipei"))
            .astimezone(timezone.utc)
        )
        end = (
            datetime.fromisoformat(end_at).replace(tzinfo=ZoneInfo("Asia/Taipei")).astimezone(timezone.utc)
            if end_at.strip()
            else None
        )
    except ValueError:
        return RedirectResponse("/tasks?msg=Invalid date/time.", status_code=303)
    _ok, msg = lifecycle.schedule_task(db, task, when, end_at=end)
    return RedirectResponse(f"/tasks?msg={msg}", status_code=303)


@router.post("/tasks/{task_id}/unschedule")
def unschedule_task_route(task_id: str, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    _ok, msg = lifecycle.unschedule_task(db, task)
    return RedirectResponse(f"/tasks?msg={msg}", status_code=303)


@router.post("/tasks/{task_id}/lifecycle")
def lifecycle_action_route(
    task_id: str,
    action: str = Form(...),
    db: Session = Depends(get_db),
):
    task = _get_task(db, task_id)
    handler = _LIFECYCLE_ACTIONS.get(action)
    if handler is None:
        raise HTTPException(400, f"Unknown action: {action}")
    now = datetime.now(timezone.utc)
    _ok, msg = handler(db, task, now)
    return RedirectResponse(f"/tasks?msg={msg}", status_code=303)
