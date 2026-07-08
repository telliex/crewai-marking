"""Public, unauthenticated endpoints: one-click unsubscribe and the Resend
webhook. These keep the sender out of the spam folder, so they must always work
even when the admin UI is disabled."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.compliance import suppress, verify_unsub_token
from awkns_outreach.db.models import Event, Lead
from awkns_outreach.web.deps import get_db, templates

router = APIRouter()


def _unsubscribe(token: str, db: Session) -> bool:
    email = verify_unsub_token(token)
    if not email:
        return False
    suppress(db, email, "unsubscribe")
    db.commit()
    return True


@router.get("/outreach/unsubscribe", response_class=HTMLResponse)
def unsubscribe_get(token: str, request: Request, db: Session = Depends(get_db)):
    ok = _unsubscribe(token, db)
    return templates.TemplateResponse(
        request, "unsubscribe.html", {"ok": ok}, status_code=200 if ok else 400
    )


@router.post("/outreach/unsubscribe")
def unsubscribe_post(token: str, db: Session = Depends(get_db)):
    """RFC 8058 one-click POST target (Gmail/Outlook native button)."""
    ok = _unsubscribe(token, db)
    return Response(status_code=status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST)


# Resend event.type → how we treat it.
_SUPPRESS_ON = {"email.bounced": "bounce", "email.complained": "complaint"}
_EVENT_ON = {"email.opened": "open", "email.clicked": "click", "email.bounced": "bounce"}


def _recipient(data: dict[str, Any]) -> str:
    to = data.get("to")
    if isinstance(to, list) and to:
        return str(to[0])
    return str(to or data.get("email") or "")


@router.post("/webhooks/resend")
async def resend_webhook(request: Request, db: Session = Depends(get_db)):
    """Feed deliverability signals back into the funnel: hard bounces and
    complaints go straight onto the suppression list; opens/clicks are logged.

    NOTE: signature verification (svix) is a follow-up — wire RESEND_WEBHOOK_SECRET
    before exposing this publicly."""
    payload = await request.json()
    etype = payload.get("type", "")
    data = payload.get("data", {}) or {}
    email = _recipient(data).strip().lower()
    if not email:
        return {"ok": True, "ignored": "no recipient"}

    # A matching lead (any campaign) lets us log an engagement event.
    lead_id = db.scalar(select(Lead.id).where(Lead.email == email).limit(1))

    if etype in _SUPPRESS_ON:
        suppress(db, email, _SUPPRESS_ON[etype])
    if lead_id and etype in _EVENT_ON:
        db.add(Event(lead_id=lead_id, type=_EVENT_ON[etype],
                     detail=(payload.get("data", {}) or {}).get("email_id")))
    db.commit()
    return {"ok": True, "type": etype}
