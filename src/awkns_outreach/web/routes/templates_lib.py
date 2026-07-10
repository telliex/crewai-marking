"""Standalone email template library (Apollo "New Template" page): CRUD,
a live preview against a hard-coded example contact, and "send test email to
me" (delivered to the selected mailbox's own address, or settings.outreach_from
for the implicit Resend default).

Named `templates_lib` (not `templates`) to avoid clashing with Jinja2's
`templates` object imported from web/deps.py throughout the codebase.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, EmailTemplate, Lead, Mailbox
from awkns_outreach.send.mailer import render_template_preview, send_outreach_email
from awkns_outreach.web.deps import get_db, require_admin, templates
from awkns_outreach.web.routes.admin import SEQUENCE_PLACEHOLDERS

router = APIRouter(dependencies=[Depends(require_admin)])

# Same address render_template_preview renders against — used again here so
# the test-send recipient matches what the preview pane showed.
_PREVIEW_EMAIL = "jamie@acmestudios.example"


def _get_template(db: Session, template_id: str) -> EmailTemplate:
    t = db.get(EmailTemplate, template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    return t


def _connected_mailboxes(db: Session) -> list[Mailbox]:
    return db.scalars(
        select(Mailbox).where(Mailbox.status == "connected").order_by(Mailbox.email)
    ).all()


def _render_preview(subject: str, body: str):
    return render_template_preview(subject, body, _PREVIEW_EMAIL)


def _send_test_email(db: Session, subject: str, body: str, mailbox_id: str) -> str:
    """Build a throwaway Campaign/Lead (never persisted) and send through
    send_outreach_email's normal Gmail/Resend dispatch — same path a real
    sequence step would take. Returns the confirmation or failure message
    shown in the test-send status line."""
    mailbox = db.get(Mailbox, mailbox_id) if mailbox_id else None
    recipient = mailbox.email if mailbox else settings.outreach_from
    test_campaign = Campaign(
        id="preview", name="Template test send", target_titles=[], seed_companies=[],
        sequence=[{"key": "test", "delay_days": 0, "subject": subject, "body": body}],
        sender_identity={},
    )
    test_campaign.mailbox = mailbox
    test_lead = Lead(
        campaign_id="preview", email=recipient, company="Acme Studios",
        contact_name="Jamie Rivera", contact_title="Creative Director", country="US",
        angle="Your recent campaign work would translate beautifully into short-form video.",
        status="active", step=0,
    )
    res = send_outreach_email(test_lead, test_campaign, recipient, 0, dry_run=False)
    if res.ok:
        return f"Test email sent! Check your inbox at {recipient}."
    return f"Test send failed: {res.error}"


@router.post("/templates/preview-fragment", response_class=HTMLResponse)
def preview_fragment(
    request: Request, subject: str = Form(""), body: str = Form(""),
):
    return templates.TemplateResponse(
        request, "_template_preview_fragment.html", {"preview": _render_preview(subject, body)},
    )


@router.post("/templates/test-send-fragment", response_class=HTMLResponse)
def test_send_fragment(
    request: Request, subject: str = Form(""), body: str = Form(""),
    mailbox_id: str = Form(""), db: Session = Depends(get_db),
):
    msg = _send_test_email(db, subject, body, mailbox_id)
    return templates.TemplateResponse(
        request, "_template_test_send_fragment.html",
        {
            "mailboxes": _connected_mailboxes(db),
            "msg": msg,
            "selected_mailbox_id": mailbox_id or None,
        },
    )


@router.get("/templates", response_class=HTMLResponse)
def list_templates(request: Request, db: Session = Depends(get_db), msg: Optional[str] = None):
    items = db.scalars(select(EmailTemplate).order_by(EmailTemplate.created_at.desc())).all()
    return templates.TemplateResponse(request, "template_list.html", {"items": items, "msg": msg})


@router.get("/templates/new", response_class=HTMLResponse)
def new_template_form(request: Request):
    return templates.TemplateResponse(
        request, "template_edit.html",
        {"t": None, "preview": None, "placeholders": SEQUENCE_PLACEHOLDERS, "mailboxes": [], "msg": None},
    )


@router.post("/templates")
def create_template(
    name: str = Form(...), subject: str = Form(""), body: str = Form(""),
    db: Session = Depends(get_db),
):
    t = EmailTemplate(name=name.strip(), subject=subject.strip(), body=body.rstrip())
    db.add(t)
    db.commit()
    return RedirectResponse(f"/templates/{t.id}/edit?msg=Template created.", status_code=303)


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def edit_template_form(
    template_id: str, request: Request, db: Session = Depends(get_db), msg: Optional[str] = None,
):
    t = _get_template(db, template_id)
    return templates.TemplateResponse(
        request, "template_edit.html",
        {
            "t": t, "preview": None, "placeholders": SEQUENCE_PLACEHOLDERS,
            "mailboxes": _connected_mailboxes(db), "msg": msg,
        },
    )


def _archived_edit_guard(t: EmailTemplate):
    return None


@router.post("/templates/{template_id}/edit", response_class=HTMLResponse)
def update_template(
    template_id: str,
    action: str = Form("save"),
    name: str = Form(""),
    subject: str = Form(""),
    body: str = Form(""),
    db: Session = Depends(get_db),
):
    t = _get_template(db, template_id)
    blocked = _archived_edit_guard(t)
    if blocked:
        return blocked

    if action == "delete":
        db.delete(t)
        db.commit()
        return RedirectResponse("/templates?msg=Template deleted.", status_code=303)

    if action != "save":
        raise HTTPException(400, f"Unknown action: {action}")

    t.name = name.strip()
    t.subject = subject.strip()
    t.body = body.rstrip()
    db.commit()
    return RedirectResponse(f"/templates/{t.id}/edit?msg=Template saved.", status_code=303)
