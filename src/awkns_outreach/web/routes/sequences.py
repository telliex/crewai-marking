"""Standalone mail sequence content library (Apollo "New Sequence" page):
list, create, edit, archive/unarchive, delete. Content-only, like
EmailTemplate but with an ordered list of steps instead of one email — see
`MailSequence` in db/models.py. A `Task` assigns a sequence per lead tier to
a Campaign and owns the actual send lifecycle (see web/routes/tasks.py).

The editor page gives each step its own rich Quill editor, live HTMX
preview, and test-send widget, reusing the same
`_template_preview_fragment.html` / `_template_test_send_fragment.html`
fragments and `_render_preview`/`_connected_mailboxes` helpers as the
single-template editor.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.db.models import EmailTemplate, MailSequence, Task
from awkns_outreach.sequencer.engine import step_delay_minutes
from awkns_outreach.web.deps import get_db, require_admin, templates
from awkns_outreach.web.routes.admin import SEQUENCE_PLACEHOLDERS
from awkns_outreach.web.routes.templates_lib import (
    _clean_body,
    _connected_mailboxes,
    _parse_attachments,
    _render_preview,
)

router = APIRouter(dependencies=[Depends(require_admin)])

_STATUS_FILTERS = ("active", "archived", "all")
_STATUS_TRANSITIONS = {
    "archive": {"active": "archived"},
    "unarchive": {"archived": "active"},
}
# Statuses in which a Task's sequence assignment blocks that sequence from
# being deleted — anything actively in play or queued to be.
_ASSIGNMENT_BLOCKING_STATUSES = ("draft", "scheduled", "running", "paused")


def _get_sequence(db: Session, seq_id: str) -> MailSequence:
    seq = db.get(MailSequence, seq_id)
    if not seq:
        raise HTTPException(404, "Sequence not found")
    return seq


def _archived_edit_guard(seq: MailSequence) -> Optional[RedirectResponse]:
    """Server-side backup for the disabled Edit link: an archived sequence
    can't be edited (both GET and POST) until unarchived."""
    if seq.status == "archived":
        return RedirectResponse(
            "/sequences?msg=Archived sequences can't be edited — unarchive first.",
            status_code=303,
        )
    return None


def _assigning_task(db: Session, seq_id: str) -> Optional[Task]:
    """The first in-play Task (draft/scheduled/running/paused) whose per-tier
    assignment references this sequence, if any — used to block delete."""
    tasks = db.scalars(
        select(Task).where(Task.status.in_(_ASSIGNMENT_BLOCKING_STATUSES))
    ).all()
    for task in tasks:
        if seq_id in (task.sequences or {}).values():
            return task
    return None


def _template_options(db: Session) -> list[dict]:
    # Templates as a JSON blob in the page — the "insert template" dropdown
    # copies subject/body/attachments into a step client-side, no round-trip.
    return [
        {"id": t.id, "name": t.name, "subject": t.subject, "body": t.body, "attachments": t.attachments}
        for t in db.scalars(
            select(EmailTemplate).where(EmailTemplate.status == "active").order_by(EmailTemplate.name)
        ).all()
    ]


_DELAY_UNITS = (1440, 60, 1)  # days, hours, minutes — largest unit first
_DELAY_UNIT_NAMES = {1440: "day", 60: "hour", 1: "minute"}


def _format_delay(minutes: int) -> dict:
    """Precompute the connector pill's display fields for a step's delay, in
    the largest unit that divides `minutes` evenly (falls back to minutes).
    `0` reads as "immediately" and defaults its (unused-until-edited) unit
    dropdown to days, matching the old days-only control's default."""
    if not minutes:
        return {"delay_value": 0, "delay_unit": 1440, "delay_label": "Immediately after previous"}
    # _DELAY_UNITS ends in 1, and every int is divisible by 1, so this loop
    # always finds a match — no need for a for/else fallback.
    for unit in _DELAY_UNITS:
        if minutes % unit == 0:
            value = minutes // unit
            break
    name = _DELAY_UNIT_NAMES[unit]
    label = f"{value} {name}{'s' if value != 1 else ''} after previous"
    return {"delay_value": value, "delay_unit": unit, "delay_label": label}


def _steps_for_editor(steps: list[dict]) -> list[dict]:
    """Augment each saved step dict with the derived keys the editor
    template needs but doesn't persist: `attachments_json` (for the rich
    editor's hidden attachments-initial field), `preview` (the card's
    initial preview-pane render, before any HTMX interaction), and the
    normalized `delay_minutes`/`delay_value`/`delay_unit`/`delay_label`
    (works for both new steps and legacy `delay_days`-only ones, via
    `step_delay_minutes`) — mirrors templates_lib.py's edit_template_form
    convention exactly."""
    out = []
    for step in steps:
        step = dict(step)
        step["attachments_json"] = json.dumps(step.get("attachments") or [])
        step["preview"] = _render_preview(
            step.get("subject", ""), step.get("body", ""), step.get("attachments") or []
        )
        step["delay_minutes"] = step_delay_minutes(step)
        step.update(_format_delay(step["delay_minutes"]))
        out.append(step)
    return out


def _build_steps(
    step_key: list[str], delay_minutes: list[str], subject: list[str], body: list[str],
    attachments: list[str], source_template_id: list[str],
) -> list[dict]:
    steps: list[dict] = []
    for i, (k, d, subj, b, a, sid) in enumerate(
        zip(step_key, delay_minutes, subject, body, attachments, source_template_id)
    ):
        # Skip fully blank rows (a step needs at least a subject or a body).
        if not subj.strip() and not b.strip():
            continue
        try:
            delay = max(0, int(d))
        except (TypeError, ValueError):
            delay = 0
        steps.append({
            "key": k.strip() or f"step{i + 1}",
            "delay_minutes": delay,
            "subject": subj.strip(),
            "body": _clean_body(b),
            "attachments": _parse_attachments(a),
            "source_template_id": sid.strip() or None,
        })
    if steps:
        steps[0]["delay_minutes"] = 0  # first step always fires immediately
    return steps


@router.get("/sequences", response_class=HTMLResponse)
def list_sequences(
    request: Request, db: Session = Depends(get_db),
    status: Optional[str] = None, msg: Optional[str] = None,
):
    status_filter = status if status in _STATUS_FILTERS else "active"
    q = select(MailSequence).order_by(MailSequence.created_at.desc())
    if status_filter != "all":
        q = q.where(MailSequence.status == status_filter)
    items = db.scalars(q).all()
    rows = [{"s": s} for s in items]
    return templates.TemplateResponse(
        request, "sequence_list.html",
        {"rows": rows, "status_filter": status_filter, "msg": msg},
    )


@router.get("/sequences/new", response_class=HTMLResponse)
def new_sequence_form(request: Request, db: Session = Depends(get_db), msg: Optional[str] = None):
    return templates.TemplateResponse(
        request, "mail_sequence_edit.html",
        {
            "seq": None, "template_options": _template_options(db),
            "steps_for_editor": _steps_for_editor([]),
            "mailboxes": _connected_mailboxes(db),
            "placeholders": SEQUENCE_PLACEHOLDERS, "form_action": "/sequences", "msg": msg,
        },
    )


@router.post("/sequences")
def create_sequence(
    name: str = Form(""),
    step_key: list[str] = Form(default=[]),
    delay_minutes: list[str] = Form(default=[]),
    subject: list[str] = Form(default=[]),
    body: list[str] = Form(default=[]),
    attachments: list[str] = Form(default=[]),
    source_template_id: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        return RedirectResponse("/sequences/new?msg=Name is required.", status_code=303)

    steps = _build_steps(step_key, delay_minutes, subject, body, attachments, source_template_id)
    seq = MailSequence(name=name, steps=steps)
    db.add(seq)
    db.commit()
    return RedirectResponse("/sequences?msg=Sequence created.", status_code=303)


@router.get("/sequences/{seq_id}/edit", response_class=HTMLResponse)
def edit_sequence_form(
    seq_id: str, request: Request, db: Session = Depends(get_db), msg: Optional[str] = None,
):
    seq = _get_sequence(db, seq_id)
    blocked = _archived_edit_guard(seq)
    if blocked:
        return blocked
    return templates.TemplateResponse(
        request, "mail_sequence_edit.html",
        {
            "seq": seq, "template_options": _template_options(db),
            "steps_for_editor": _steps_for_editor(seq.steps or []),
            "mailboxes": _connected_mailboxes(db),
            "placeholders": SEQUENCE_PLACEHOLDERS, "form_action": f"/sequences/{seq.id}/edit", "msg": msg,
        },
    )


@router.post("/sequences/{seq_id}/edit", response_class=HTMLResponse)
def update_sequence(
    seq_id: str,
    action: str = Form("save"),
    name: str = Form(""),
    step_key: list[str] = Form(default=[]),
    delay_minutes: list[str] = Form(default=[]),
    subject: list[str] = Form(default=[]),
    body: list[str] = Form(default=[]),
    attachments: list[str] = Form(default=[]),
    source_template_id: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    seq = _get_sequence(db, seq_id)

    if action == "delete":
        blocking_task = _assigning_task(db, seq.id)
        if blocking_task is not None:
            return RedirectResponse(
                f"/sequences?msg=Sequence is assigned to task “{blocking_task.name}” — unassign it first.",
                status_code=303,
            )
        db.delete(seq)
        db.commit()
        return RedirectResponse("/sequences?msg=Sequence deleted.", status_code=303)

    if action != "save":
        raise HTTPException(400, f"Unknown action: {action}")

    blocked = _archived_edit_guard(seq)
    if blocked:
        return blocked

    name = name.strip()
    if not name:
        return RedirectResponse(f"/sequences/{seq.id}/edit?msg=Name is required.", status_code=303)

    seq.name = name
    seq.steps = _build_steps(step_key, delay_minutes, subject, body, attachments, source_template_id)
    db.commit()
    return RedirectResponse(f"/sequences/{seq.id}/edit?msg=Sequence saved.", status_code=303)


@router.post("/sequences/{seq_id}/status")
def change_sequence_status(
    seq_id: str, action: str = Form(...), status: str = Form("active"),
    db: Session = Depends(get_db),
):
    seq = _get_sequence(db, seq_id)
    transitions = _STATUS_TRANSITIONS.get(action)
    if transitions is None:
        raise HTTPException(400, f"Unknown action: {action}")
    new_status = transitions.get(seq.status)
    if new_status is None:
        msg = f'Sequence "{seq.name}" is already {seq.status}.'
    elif new_status == "archived":
        # Same guard as delete: archiving a sequence still assigned to an
        # in-play Task would leave that task's every future scheduler tick
        # silently failing to start (start_task's _validate_assignments
        # rejects non-active sequences) with no visible error anywhere.
        blocking_task = _assigning_task(db, seq.id)
        if blocking_task is not None:
            return RedirectResponse(
                f"/sequences?status={status}&msg=Sequence is assigned to task "
                f"“{blocking_task.name}” — unassign it first.",
                status_code=303,
            )
        seq.status = new_status
        db.commit()
        msg = f'Sequence "{seq.name}" archived.'
    else:
        seq.status = new_status
        db.commit()
        msg = f'Sequence "{seq.name}" unarchived.'
    return RedirectResponse(f"/sequences?status={status}&msg={msg}", status_code=303)
