"""Reply detection: poll a connected Gmail inbox and auto-stop replying leads.

Works regardless of which channel actually sent the email (Resend or Gmail) —
replies always land in the mailbox's own inbox. Out of scope: a reply from a
DIFFERENT address than the one we emailed (e.g. a colleague CC'd in) is not
matched; only an exact From-address match against a lead's email counts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.db.models import Event, Lead, Mailbox
from awkns_outreach.gmail.api import ensure_fresh_token, get_message_metadata, list_message_ids
from awkns_outreach.gmail.oauth import NeedsReconnect

# Re-scan the last N minutes on every poll: `after:` is second-granular and
# Gmail doesn't guarantee delivery ordering, so a message landing right at the
# watermark could otherwise be missed. The reply Event dedupe (below) makes
# re-scanning safe.
_OVERLAP_MINUTES = 10
# A lead can still be marked replied while "sending" (claimed mid-send) or
# "completed" (sequence finished, but a late reply should still stop follow-up
# consideration) — but not once suppressed/bounced/failed/already-replied.
_REPLYABLE_STATUSES = ("active", "sending", "completed", "paused")


@dataclass
class PollSummary:
    mailbox_email: str
    considered: int = 0
    matched: int = 0
    error: Optional[str] = None


def _aware(dt: datetime) -> datetime:
    """SQLite doesn't persist a tz offset even for a DateTime(timezone=True)
    column, so a re-fetched value comes back naive; `.timestamp()` on a naive
    datetime assumes the SERVER's local zone, which would silently skew the
    Gmail query. Treat naive as UTC (what we always write)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _search_query(mailbox: Mailbox) -> str:
    if mailbox.last_poll_at is not None:
        since = _aware(mailbox.last_poll_at) - timedelta(minutes=_OVERLAP_MINUTES)
        return f"in:inbox -from:me after:{int(since.timestamp())}"
    return "in:inbox -from:me newer_than:2d"  # first poll ever: a short backfill


def poll_mailbox_replies(
    session: Session, mailbox: Mailbox, now: Optional[datetime] = None,
) -> PollSummary:
    """Poll one mailbox for new inbound mail and mark matching leads replied.

    Idempotency: the watermark (`mailbox.last_poll_at`) only advances on a
    fully successful poll; a reply Event with the same Gmail message id as
    `detail` is never written twice, so the 10-minute overlap window can never
    double-mark a lead.
    """
    now = now or datetime.now(timezone.utc)
    summary = PollSummary(mailbox_email=mailbox.email)

    if mailbox.status != "connected":
        # Covers needs_reconnect AND disconnected (manual poll button on a
        # disconnected row) — no point burning a token refresh either way.
        summary.error = f"mailbox {mailbox.status}"
        return summary

    try:
        access_token = ensure_fresh_token(mailbox)
    except NeedsReconnect as exc:
        summary.error = str(exc)
        session.commit()  # persist status=needs_reconnect set by ensure_fresh_token
        return summary

    query = _search_query(mailbox)
    try:
        message_ids = list_message_ids(access_token, query)
    except Exception as exc:
        summary.error = str(exc)
        session.commit()  # keep any refreshed access token even though the poll failed
        return summary

    for message_id in message_ids:
        summary.considered += 1
        try:
            meta = get_message_metadata(access_token, message_id)
        except Exception:
            continue  # one bad message id shouldn't sink the whole poll

        _, addr = parseaddr(meta.get("from", ""))
        addr = addr.strip().lower()
        if not addr:
            continue

        leads = session.scalars(
            select(Lead).where(Lead.email == addr, Lead.status.in_(_REPLYABLE_STATUSES))
        ).all()
        for lead in leads:
            already = session.scalar(
                select(Event.id).where(
                    Event.lead_id == lead.id, Event.type == "reply", Event.detail == message_id,
                )
            )
            if already:
                continue
            lead.status = "replied"
            lead.replied_at = now
            lead.next_action_at = None
            session.add(Event(lead_id=lead.id, type="reply", detail=message_id))
            summary.matched += 1

    mailbox.last_poll_at = now
    session.commit()
    return summary


def poll_all_mailboxes(session: Session, now: Optional[datetime] = None) -> list[PollSummary]:
    """Poll every mailbox that isn't disconnected (needs_reconnect ones are
    still "polled" — they just fast-fail and report an error, same as sends)."""
    mailboxes = session.scalars(
        select(Mailbox).where(Mailbox.status != "disconnected")
    ).all()
    return [poll_mailbox_replies(session, mb, now=now) for mb in mailboxes]
