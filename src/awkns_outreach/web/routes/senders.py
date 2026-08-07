"""Settings → Senders: manage the sender-identity profile library.

A SenderProfile is a named, reusable sender identity a Campaign can select
(see admin.py + _sender_select.html). Blank fields fall back per-field to the
global Variables identity, so a profile only needs to set what differs.

Locking: once a profile is in use by a campaign whose task is running/paused,
its identity is already frozen onto that task — so it becomes edit/delete-locked
(but still archivable, which only hides it from future campaign dropdowns).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from awkns_outreach.db.models import Campaign, SenderProfile, Task
from awkns_outreach.web.deps import get_db, require_admin, templates

router = APIRouter(dependencies=[Depends(require_admin)])

# Task statuses that mean "this profile's identity is already frozen onto a live
# task" — while any exist, the profile can't be edited or deleted.
_LIVE_TASK_STATUSES = ("running", "paused")


def _get_profile(db: Session, profile_id: str) -> SenderProfile:
    p = db.get(SenderProfile, profile_id)
    if not p:
        raise HTTPException(404, "Sender profile not found")
    return p


def profile_locked(db: Session, profile_id: str) -> bool:
    """True if a campaign using this profile has a running/paused task."""
    return bool(
        db.scalar(
            select(func.count())
            .select_from(Task)
            .join(Campaign, Task.campaign_id == Campaign.id)
            .where(
                Campaign.sender_profile_id == profile_id,
                Task.status.in_(_LIVE_TASK_STATUSES),
            )
        )
    )


def _locked_redirect(verb: str) -> RedirectResponse:
    return RedirectResponse(
        f"/settings/senders?msg=This sender is used by a running task and can't be {verb}.",
        status_code=303,
    )


_FIELDS = (
    "from_email", "from_name", "reply_to", "sender_name",
    "company", "postal_address", "unsubscribe_mailto",
)


@router.get("/settings/senders", response_class=HTMLResponse)
def list_senders(request: Request, db: Session = Depends(get_db), msg: Optional[str] = None):
    profiles = db.scalars(
        select(SenderProfile).order_by(
            SenderProfile.status.asc(), SenderProfile.name.asc()
        )
    ).all()
    locked = {p.id for p in profiles if profile_locked(db, p.id)}
    return templates.TemplateResponse(
        request, "sender_list.html", {"profiles": profiles, "locked": locked, "msg": msg},
    )


@router.get("/settings/senders/new", response_class=HTMLResponse)
def new_sender_form(request: Request):
    return templates.TemplateResponse(
        request, "sender_edit.html", {"p": None, "locked": False, "msg": None},
    )


@router.post("/settings/senders")
def create_sender(
    request: Request,
    name: str = Form(...),
    from_email: str = Form(""),
    from_name: str = Form(""),
    reply_to: str = Form(""),
    sender_name: str = Form(""),
    company: str = Form(""),
    postal_address: str = Form(""),
    unsubscribe_mailto: str = Form(""),
    db: Session = Depends(get_db),
):
    p = SenderProfile(
        name=name.strip(),
        from_email=from_email.strip(), from_name=from_name.strip(),
        reply_to=reply_to.strip(), sender_name=sender_name.strip(),
        company=company.strip(), postal_address=postal_address.strip(),
        unsubscribe_mailto=unsubscribe_mailto.strip(),
    )
    db.add(p)
    db.commit()
    return RedirectResponse(f"/settings/senders/{p.id}/edit?msg=Sender created.", status_code=303)


@router.get("/settings/senders/{profile_id}/edit", response_class=HTMLResponse)
def edit_sender_form(
    profile_id: str, request: Request, db: Session = Depends(get_db), msg: Optional[str] = None,
):
    p = _get_profile(db, profile_id)
    return templates.TemplateResponse(
        request, "sender_edit.html",
        {"p": p, "locked": profile_locked(db, profile_id), "msg": msg},
    )


@router.post("/settings/senders/{profile_id}/edit")
def update_sender(
    profile_id: str,
    action: str = Form("save"),
    name: str = Form(""),
    from_email: str = Form(""),
    from_name: str = Form(""),
    reply_to: str = Form(""),
    sender_name: str = Form(""),
    company: str = Form(""),
    postal_address: str = Form(""),
    unsubscribe_mailto: str = Form(""),
    db: Session = Depends(get_db),
):
    p = _get_profile(db, profile_id)
    locked = profile_locked(db, profile_id)

    # Archive / unarchive stay allowed even when locked — they don't change the
    # identity, only whether it's offered for NEW campaign selections.
    if action in ("archive", "unarchive"):
        p.status = "archived" if action == "archive" else "active"
        db.commit()
        return RedirectResponse(f"/settings/senders?msg=Sender {action}d.", status_code=303)

    if action == "delete":
        if locked:
            return _locked_redirect("deleted")
        db.delete(p)
        db.commit()
        return RedirectResponse("/settings/senders?msg=Sender deleted.", status_code=303)

    if action != "save":
        raise HTTPException(400, f"Unknown action: {action}")

    if locked:
        return _locked_redirect("edited")

    p.name = name.strip()
    values = {
        "from_email": from_email, "from_name": from_name, "reply_to": reply_to,
        "sender_name": sender_name, "company": company,
        "postal_address": postal_address, "unsubscribe_mailto": unsubscribe_mailto,
    }
    for field in _FIELDS:
        setattr(p, field, values[field].strip())
    db.commit()
    return RedirectResponse(f"/settings/senders/{p.id}/edit?msg=Sender saved.", status_code=303)
