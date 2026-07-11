"""Standalone mail sequence CRUD (Apollo "New Sequence" page): list, create,
edit, delete. Each sequence targets one existing Campaign ("Group") and
snapshots an ordered list of email steps — see `MailSequence` in db/models.py.

This task's editor page is a deliberately plain form (inputs + textareas);
the rich multi-Quill editor lands in Task 3. Lifecycle actions
(schedule/start/pause/stop) land in Task 4 — not built here.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.db.models import Campaign, EmailTemplate, MailSequence
from awkns_outreach.web.deps import get_db, require_admin, templates
from awkns_outreach.web.routes.admin import SEQUENCE_PLACEHOLDERS
from awkns_outreach.web.routes.templates_lib import _clean_body, _parse_attachments

router = APIRouter(dependencies=[Depends(require_admin)])

_STATUS_FILTERS = ("draft", "scheduled", "running", "paused", "stopped", "completed", "all")
# Only pre-start sequences can still have their name/group/steps changed.
_EDITABLE_STATUSES = ("draft", "scheduled")
# Anything that isn't actively running/paused can be removed outright.
_DELETABLE_STATUSES = ("draft", "scheduled", "stopped", "completed")


def _get_sequence(db: Session, seq_id: str) -> MailSequence:
    seq = db.get(MailSequence, seq_id)
    if not seq:
        raise HTTPException(404, "Sequence not found")
    return seq


def _edit_guard(seq: MailSequence) -> Optional[RedirectResponse]:
    """Server-side backup for the disabled Edit link: a sequence that has
    already started (running/paused/stopped/completed) can't be edited."""
    if seq.status not in _EDITABLE_STATUSES:
        return RedirectResponse(
            f"/sequences?msg=Sequence can't be edited while {seq.status}.",
            status_code=303,
        )
    return None


def _campaign_groups(db: Session) -> list[Campaign]:
    return db.scalars(
        select(Campaign).where(Campaign.status.in_(["active", "paused"])).order_by(Campaign.name)
    ).all()


def _template_options(db: Session) -> list[dict]:
    # Templates as a JSON blob in the page — the "insert template" dropdown
    # copies subject/body/attachments into a step client-side, no round-trip.
    return [
        {"id": t.id, "name": t.name, "subject": t.subject, "body": t.body, "attachments": t.attachments}
        for t in db.scalars(
            select(EmailTemplate).where(EmailTemplate.status == "active").order_by(EmailTemplate.name)
        ).all()
    ]


def _build_steps(
    step_key: list[str], delay_days: list[str], subject: list[str], body: list[str],
    attachments: list[str], source_template_id: list[str],
) -> list[dict]:
    steps: list[dict] = []
    for i, (k, d, subj, b, a, sid) in enumerate(
        zip(step_key, delay_days, subject, body, attachments, source_template_id)
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
            "delay_days": delay,
            "subject": subj.strip(),
            "body": _clean_body(b),
            "attachments": _parse_attachments(a),
            "source_template_id": sid.strip() or None,
        })
    if steps:
        steps[0]["delay_days"] = 0  # first step always fires immediately
    return steps


@router.get("/sequences", response_class=HTMLResponse)
def list_sequences(
    request: Request, db: Session = Depends(get_db),
    status: Optional[str] = None, msg: Optional[str] = None,
):
    status_filter = status if status in _STATUS_FILTERS else "all"
    q = select(MailSequence).order_by(MailSequence.created_at.desc())
    if status_filter != "all":
        q = q.where(MailSequence.status == status_filter)
    items = db.scalars(q).all()
    campaign_names = {c.id: c.name for c in db.scalars(select(Campaign)).all()}
    rows = [{"s": s, "campaign_name": campaign_names.get(s.campaign_id, "—")} for s in items]
    return templates.TemplateResponse(
        request, "sequence_list.html",
        {
            "rows": rows, "status_filter": status_filter, "msg": msg,
            "editable_statuses": _EDITABLE_STATUSES, "deletable_statuses": _DELETABLE_STATUSES,
        },
    )


@router.get("/sequences/new", response_class=HTMLResponse)
def new_sequence_form(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "mail_sequence_edit.html",
        {
            "seq": None, "groups": _campaign_groups(db), "template_options": _template_options(db),
            "placeholders": SEQUENCE_PLACEHOLDERS, "form_action": "/sequences", "msg": None,
        },
    )


@router.post("/sequences")
def create_sequence(
    name: str = Form(""),
    campaign_id: str = Form(""),
    step_key: list[str] = Form(default=[]),
    delay_days: list[str] = Form(default=[]),
    subject: list[str] = Form(default=[]),
    body: list[str] = Form(default=[]),
    attachments: list[str] = Form(default=[]),
    source_template_id: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        return RedirectResponse("/sequences/new?msg=Name is required.", status_code=303)
    if not db.get(Campaign, campaign_id):
        return RedirectResponse("/sequences/new?msg=Select a valid group.", status_code=303)

    steps = _build_steps(step_key, delay_days, subject, body, attachments, source_template_id)
    seq = MailSequence(name=name, campaign_id=campaign_id, steps=steps)
    db.add(seq)
    db.commit()
    return RedirectResponse("/sequences?msg=Sequence created.", status_code=303)


@router.get("/sequences/{seq_id}/edit", response_class=HTMLResponse)
def edit_sequence_form(
    seq_id: str, request: Request, db: Session = Depends(get_db), msg: Optional[str] = None,
):
    seq = _get_sequence(db, seq_id)
    blocked = _edit_guard(seq)
    if blocked:
        return blocked
    return templates.TemplateResponse(
        request, "mail_sequence_edit.html",
        {
            "seq": seq, "groups": _campaign_groups(db), "template_options": _template_options(db),
            "placeholders": SEQUENCE_PLACEHOLDERS, "form_action": f"/sequences/{seq.id}/edit", "msg": msg,
        },
    )


@router.post("/sequences/{seq_id}/edit", response_class=HTMLResponse)
def update_sequence(
    seq_id: str,
    action: str = Form("save"),
    name: str = Form(""),
    campaign_id: str = Form(""),
    step_key: list[str] = Form(default=[]),
    delay_days: list[str] = Form(default=[]),
    subject: list[str] = Form(default=[]),
    body: list[str] = Form(default=[]),
    attachments: list[str] = Form(default=[]),
    source_template_id: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    seq = _get_sequence(db, seq_id)

    if action == "delete":
        if seq.status not in _DELETABLE_STATUSES:
            return RedirectResponse(
                f"/sequences?msg=Sequence can't be deleted while {seq.status}.", status_code=303,
            )
        db.delete(seq)
        db.commit()
        return RedirectResponse("/sequences?msg=Sequence deleted.", status_code=303)

    if action != "save":
        raise HTTPException(400, f"Unknown action: {action}")

    blocked = _edit_guard(seq)
    if blocked:
        return blocked

    name = name.strip()
    if not name:
        return RedirectResponse(f"/sequences/{seq.id}/edit?msg=Name is required.", status_code=303)
    if not db.get(Campaign, campaign_id):
        return RedirectResponse(f"/sequences/{seq.id}/edit?msg=Select a valid group.", status_code=303)

    seq.name = name
    seq.campaign_id = campaign_id
    seq.steps = _build_steps(step_key, delay_days, subject, body, attachments, source_template_id)
    db.commit()
    return RedirectResponse(f"/sequences/{seq.id}/edit?msg=Sequence saved.", status_code=303)
