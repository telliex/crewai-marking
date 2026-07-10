"""Gmail mailbox connect/disconnect (admin-gated): OAuth consent + callback,
reconnect, disconnect, and a manual "check replies now" button.

The OAuth callback lives on this same Basic-auth-gated router (not the public
router) — the browser already has the admin's Basic credentials cached for
this origin and replays them automatically when Google redirects back here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.db.models import Mailbox
from awkns_outreach.gmail.api import GmailAPIError, fetch_profile_email
from awkns_outreach.gmail.oauth import (
    OAuthError,
    consent_url,
    exchange_code,
    make_oauth_state,
    revoke,
    verify_oauth_state,
)
from awkns_outreach.gmail.replies import poll_mailbox_replies
from awkns_outreach.web.deps import get_db, require_admin, templates

router = APIRouter(dependencies=[Depends(require_admin)])


def _get_mailbox(db: Session, mailbox_id: str) -> Mailbox:
    mb = db.get(Mailbox, mailbox_id)
    if not mb:
        raise HTTPException(404, "Mailbox not found")
    return mb


@router.get("/mailboxes", response_class=HTMLResponse)
def list_mailboxes(request: Request, db: Session = Depends(get_db), msg: Optional[str] = None):
    mailboxes = db.scalars(select(Mailbox).order_by(Mailbox.created_at.desc())).all()
    return templates.TemplateResponse(
        request, "mailboxes.html", {"mailboxes": mailboxes, "msg": msg}
    )


@router.get("/mailboxes/connect")
def connect_mailbox(login_hint: Optional[str] = None):
    state = make_oauth_state()
    return RedirectResponse(consent_url(state, login_hint=login_hint), status_code=302)


@router.post("/mailboxes/{mailbox_id}/reconnect")
def reconnect_mailbox(mailbox_id: str, db: Session = Depends(get_db)):
    mb = _get_mailbox(db, mailbox_id)
    state = make_oauth_state()
    return RedirectResponse(consent_url(state, login_hint=mb.email), status_code=302)


@router.get("/oauth/google/callback")
def oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if error:
        msg = (
            "Google sign-in was cancelled." if error == "access_denied"
            else f"Google OAuth error: {error}"
        )
        return RedirectResponse(f"/mailboxes?msg={msg}", status_code=303)

    if not state or not verify_oauth_state(state):
        raise HTTPException(400, "Invalid or expired OAuth state")
    if not code:
        raise HTTPException(400, "Missing authorization code")

    try:
        bundle = exchange_code(code)
    except OAuthError as exc:
        return RedirectResponse(f"/mailboxes?msg=Google OAuth error: {exc}", status_code=303)

    if not bundle.refresh_token:
        # Google only mints a refresh_token the FIRST time a user consents (or
        # when prompt=consent forces re-consent, which we always request) —
        # if it's still missing, the account has stale leftover access.
        msg = (
            "Google didn't return a refresh token — remove Awkns Outreach's "
            "access at myaccount.google.com/permissions, then reconnect."
        )
        return RedirectResponse(f"/mailboxes?msg={msg}", status_code=303)

    try:
        email = fetch_profile_email(bundle.access_token)
    except GmailAPIError as exc:
        return RedirectResponse(f"/mailboxes?msg=Could not read Gmail profile: {exc}", status_code=303)

    now = datetime.now(timezone.utc)
    mailbox = db.scalar(select(Mailbox).where(Mailbox.email == email))
    if mailbox is None:
        mailbox = Mailbox(email=email, provider="gmail")
        db.add(mailbox)
    mailbox.access_token = bundle.access_token
    mailbox.refresh_token = bundle.refresh_token
    mailbox.token_expiry = bundle.expiry
    mailbox.scopes = bundle.scope
    mailbox.status = "connected"
    mailbox.last_error = None
    mailbox.connected_at = now
    db.commit()
    return RedirectResponse(f"/mailboxes?msg=Connected {email}.", status_code=303)


@router.post("/mailboxes/{mailbox_id}/disconnect")
def disconnect_mailbox(mailbox_id: str, db: Session = Depends(get_db)):
    mb = _get_mailbox(db, mailbox_id)
    if mb.refresh_token:
        revoke(mb.refresh_token)  # best-effort; never blocks clearing our own tokens
    mb.access_token = None
    mb.refresh_token = None
    mb.token_expiry = None
    mb.status = "disconnected"
    mb.last_error = None
    db.commit()
    return RedirectResponse(f"/mailboxes?msg=Disconnected {mb.email}.", status_code=303)


@router.post("/mailboxes/{mailbox_id}/poll")
def poll_mailbox_now(mailbox_id: str, db: Session = Depends(get_db)):
    mb = _get_mailbox(db, mailbox_id)
    summary = poll_mailbox_replies(db, mb)
    if summary.error:
        msg = f"Poll failed: {summary.error}"
    else:
        msg = f"Poll complete: {summary.matched} replied out of {summary.considered} checked."
    return RedirectResponse(f"/mailboxes?msg={msg}", status_code=303)
