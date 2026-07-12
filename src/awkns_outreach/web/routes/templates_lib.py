"""Standalone email template library (Apollo "New Template" page): CRUD,
a live preview against a hard-coded example contact, and "send test email to
me" (delivered to the selected mailbox's own address, or settings.outreach_from
for the implicit Resend default).

Named `templates_lib` (not `templates`) to avoid clashing with Jinja2's
`templates` object imported from web/deps.py throughout the codebase.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Optional

import nh3
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, EmailTemplate, Lead, Mailbox
from awkns_outreach.send.mailer import (
    render_template_preview,
    sanitize_rich_body,
    send_outreach_email,
)
from awkns_outreach.uploads import UPLOAD_DIR
from awkns_outreach.web.deps import get_db, require_admin, templates
from awkns_outreach.web.routes.admin import SEQUENCE_PLACEHOLDERS

router = APIRouter(dependencies=[Depends(require_admin)])

# Same address render_template_preview renders against — used again here so
# the test-send recipient matches what the preview pane showed.
_PREVIEW_EMAIL = "jamie@acmestudios.example"

# Sentinel mailbox_id value selecting the "Custom recipients" dropdown option
# (never a real Mailbox.id, which is a UUID string).
_CUSTOM_RECIPIENTS = "__custom__"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_STATUS_FILTERS = ("active", "archived", "all")
_STATUS_TRANSITIONS = {
    "archive": {"active": "archived"},
    "unarchive": {"archived": "active"},
}

# Template body images (inserted via the Quill editor's image button) are
# stored on local disk and served back at settings.app_base_url + /uploads/...
# — email clients need a real, publicly-fetchable HTTPS URL; they largely
# strip/refuse base64 data: URIs, so embedding the upload inline isn't viable.
_ALLOWED_IMAGE_TYPES = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
}
_MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Real email attachments (as opposed to inline body images): any file type,
# stored the same way, but read back off disk by mailer.py at send time and
# attached as a genuine MIME part / Resend attachment — never embedded as a
# link. Capped well under Gmail's ~25MB raw-message ceiling.
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


_ATTACHMENT_KEYS = {"filename", "stored_name", "content_type", "size"}


def _parse_attachments(raw: str) -> list[dict]:
    """Parse the hidden `attachments` form field (JSON array the editor's JS
    maintains as files are added/removed). Defensive against a malformed or
    tampered payload: unknown keys are dropped, and any entry missing the
    fields mailer.py needs to find the file on disk is skipped entirely
    rather than failing the whole request."""
    try:
        data = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [
        {k: item.get(k) for k in _ATTACHMENT_KEYS}
        for item in data
        if isinstance(item, dict) and item.get("stored_name") and item.get("filename")
    ]


def _clean_body(body: str) -> str:
    """Sanitize a Quill-authored (HTML) body before it's stored; a plain-text
    body (from the older sequence-step textarea, or a template predating the
    rich-text editor) has no "<" and passes through unchanged."""
    body = body.rstrip()
    return sanitize_rich_body(body) if "<" in body else body


def _truncate_body(body: str, length: int = 70) -> str:
    plain = nh3.clean(body, tags=set()) if "<" in body else body
    collapsed = " ".join(plain.split())
    if len(collapsed) <= length:
        return collapsed
    return collapsed[:length].rstrip() + "…"


def _get_template(db: Session, template_id: str) -> EmailTemplate:
    t = db.get(EmailTemplate, template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    return t


def _connected_mailboxes(db: Session) -> list[Mailbox]:
    return db.scalars(
        select(Mailbox).where(Mailbox.status == "connected").order_by(Mailbox.email)
    ).all()


def _render_preview(subject: str, body: str, attachments: Optional[list[dict]] = None):
    return render_template_preview(subject, body, _PREVIEW_EMAIL, attachments=attachments)


def _send_test_email_once(
    subject: str, body: str, mailbox: Optional[Mailbox], recipient: str,
    attachments: Optional[list[dict]] = None,
):
    """Build a throwaway Campaign/Lead (never persisted) and send through
    send_outreach_email's normal Gmail/Resend dispatch — same path a real
    sequence step would take. Returns the raw SendResult."""
    test_campaign = Campaign(
        id="preview", name="Template test send", target_titles=[], seed_companies=[],
        sender_identity={},
    )
    test_campaign.mailbox = mailbox
    test_lead = Lead(
        campaign_id="preview", email=recipient, company="Acme Studios",
        contact_name="Jamie Rivera", contact_title="Creative Director", country="US",
        angle="Your recent campaign work would translate beautifully into short-form video.",
        status="active", step=0,
    )
    steps = [{
        "key": "test", "delay_days": 0, "subject": subject, "body": body,
        "attachments": attachments or [],
    }]
    return send_outreach_email(test_lead, test_campaign, recipient, 0, steps, dry_run=False)


def _send_test_email(
    db: Session, subject: str, body: str, mailbox_id: str, attachments: list[dict],
) -> str:
    mailbox = db.get(Mailbox, mailbox_id) if mailbox_id else None
    recipient = mailbox.email if mailbox else settings.outreach_from
    res = _send_test_email_once(subject, body, mailbox, recipient, attachments)
    if res.ok:
        return f"Test email sent! Check your inbox at {recipient}."
    return f"Test send failed: {res.error}"


def _send_test_emails_to_custom_recipients(
    subject: str, body: str, raw: str, attachments: list[dict],
) -> str:
    """One independent send per comma-separated address, always via Resend
    (custom recipients aren't "me", so there's no mailbox to send-as). `ok`
    here only means Resend/Gmail accepted the send request, not that the
    recipient's inbox actually received it — real delivery status only
    exists later via the async Resend webhook."""
    addresses = [a.strip() for a in raw.split(",") if a.strip()]
    if not addresses:
        return "Enter at least one email address."
    lines = []
    for addr in addresses:
        if not _EMAIL_RE.match(addr):
            lines.append(f"{addr}: skipped — invalid email format")
            continue
        res = _send_test_email_once(subject, body, None, addr, attachments)
        lines.append(f"{addr}: sent" if res.ok else f"{addr}: failed — {res.error}")
    return "\n".join(lines)


@router.post("/templates/upload-image")
async def upload_template_image(file: UploadFile = File(...)):
    ext = _ALLOWED_IMAGE_TYPES.get(file.content_type)
    if not ext:
        raise HTTPException(400, "Unsupported image type — use PNG, JPEG, GIF, or WebP.")
    data = await file.read()
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(400, "Image too large (max 5MB).")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    (UPLOAD_DIR / name).write_bytes(data)
    return {"url": f"{settings.app_base_url}/uploads/{name}"}


@router.post("/templates/upload-attachment")
async def upload_template_attachment(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "Missing filename.")
    data = await file.read()
    if len(data) > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(400, "Attachment too large (max 10MB).")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ext = "".join(Path(file.filename).suffixes)[-16:]  # keep it short & bounded
    stored_name = f"{uuid.uuid4().hex}{ext}"
    (UPLOAD_DIR / stored_name).write_bytes(data)
    return {
        "filename": file.filename,
        "stored_name": stored_name,
        "content_type": file.content_type or "application/octet-stream",
        "size": len(data),
        "url": f"{settings.app_base_url}/uploads/{stored_name}",
    }


@router.post("/templates/preview-fragment", response_class=HTMLResponse)
def preview_fragment(
    request: Request, subject: str = Form(""), body: str = Form(""),
    attachments: str = Form("[]"),
):
    preview = _render_preview(subject, body, _parse_attachments(attachments))
    return templates.TemplateResponse(
        request, "_template_preview_fragment.html", {"preview": preview},
    )


@router.post("/templates/test-send-fragment", response_class=HTMLResponse)
def test_send_fragment(
    request: Request, subject: str = Form(""), body: str = Form(""),
    mailbox_id: str = Form(""), custom_recipients: str = Form(""),
    attachments: str = Form("[]"),
    db: Session = Depends(get_db),
):
    parsed_attachments = _parse_attachments(attachments)
    if mailbox_id == _CUSTOM_RECIPIENTS:
        msg = _send_test_emails_to_custom_recipients(subject, body, custom_recipients, parsed_attachments)
    else:
        msg = _send_test_email(db, subject, body, mailbox_id, parsed_attachments)
    return templates.TemplateResponse(
        request, "_template_test_send_fragment.html",
        {
            "mailboxes": _connected_mailboxes(db),
            "msg": msg,
            "selected_mailbox_id": mailbox_id or None,
            "custom_recipients": custom_recipients,
        },
    )


@router.get("/templates", response_class=HTMLResponse)
def list_templates(
    request: Request, db: Session = Depends(get_db),
    status: Optional[str] = None, msg: Optional[str] = None,
):
    status_filter = status if status in _STATUS_FILTERS else "default"
    q = select(EmailTemplate).order_by(EmailTemplate.created_at.desc())
    if status_filter in ("active", "archived"):
        q = q.where(EmailTemplate.status == status_filter)
    elif status_filter == "default":
        q = q.where(EmailTemplate.status == "active")
    items = db.scalars(q).all()
    rows = [{"t": t, "content": _truncate_body(t.body)} for t in items]
    return templates.TemplateResponse(
        request, "template_list.html",
        {"rows": rows, "status_filter": status_filter, "msg": msg},
    )


@router.get("/templates/new", response_class=HTMLResponse)
def new_template_form(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "template_edit.html",
        {
            "t": None, "placeholders": SEQUENCE_PLACEHOLDERS, "msg": None,
            "preview": _render_preview("", ""),
            "mailboxes": _connected_mailboxes(db),
            "selected_mailbox_id": None,
            "custom_recipients": None,
            "test_send_msg": None,
            "attachments_json": "[]",
        },
    )


@router.post("/templates")
def create_template(
    name: str = Form(...), subject: str = Form(""), body: str = Form(""),
    attachments: str = Form("[]"),
    db: Session = Depends(get_db),
):
    t = EmailTemplate(
        name=name.strip(), subject=subject.strip(), body=_clean_body(body),
        attachments=_parse_attachments(attachments),
    )
    db.add(t)
    db.commit()
    return RedirectResponse(f"/templates/{t.id}/edit?msg=Template created.", status_code=303)


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def edit_template_form(
    template_id: str, request: Request, db: Session = Depends(get_db), msg: Optional[str] = None,
):
    t = _get_template(db, template_id)
    blocked = _archived_edit_guard(t)
    if blocked:
        return blocked
    return templates.TemplateResponse(
        request, "template_edit.html",
        {
            "t": t, "placeholders": SEQUENCE_PLACEHOLDERS, "msg": msg,
            "preview": _render_preview(t.subject, t.body, t.attachments),
            "mailboxes": _connected_mailboxes(db),
            "selected_mailbox_id": None,
            "custom_recipients": None,
            "test_send_msg": None,
            "attachments_json": json.dumps(t.attachments),
        },
    )


def _archived_edit_guard(t: EmailTemplate) -> Optional[RedirectResponse]:
    """Server-side backup for the disabled Edit link: archived templates
    can't be edited (both GET and POST) until unarchived."""
    if t.status == "archived":
        return RedirectResponse(
            "/templates?msg=Archived templates can't be edited — unarchive first.",
            status_code=303,
        )
    return None


@router.post("/templates/{template_id}/edit", response_class=HTMLResponse)
def update_template(
    template_id: str,
    action: str = Form("save"),
    name: str = Form(""),
    subject: str = Form(""),
    body: str = Form(""),
    attachments: str = Form("[]"),
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
    t.body = _clean_body(body)
    t.attachments = _parse_attachments(attachments)
    db.commit()
    return RedirectResponse(f"/templates/{t.id}/edit?msg=Template saved.", status_code=303)


@router.post("/templates/{template_id}/clone")
def clone_template(template_id: str, db: Session = Depends(get_db)):
    t = _get_template(db, template_id)
    clone = EmailTemplate(
        name=f"{t.name} (Copy)", subject=t.subject, body=t.body, status="active",
        attachments=t.attachments,
    )
    db.add(clone)
    db.commit()
    return RedirectResponse(f"/templates/{clone.id}/edit?msg=Template cloned.", status_code=303)


@router.post("/templates/{template_id}/status")
def change_template_status(
    template_id: str, action: str = Form(...), status: str = Form("default"),
    db: Session = Depends(get_db),
):
    t = _get_template(db, template_id)
    transitions = _STATUS_TRANSITIONS.get(action)
    if transitions is None:
        raise HTTPException(400, f"Unknown action: {action}")
    new_status = transitions.get(t.status)
    if new_status is None:
        msg = f'Template "{t.name}" is already {t.status}.'
    else:
        t.status = new_status
        db.commit()
        msg = f'Template "{t.name}" {"archived" if new_status == "archived" else "unarchived"}.'
    return RedirectResponse(f"/templates?status={status}&msg={msg}", status_code=303)
