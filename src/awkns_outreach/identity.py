"""Resolve the sender identity for a send.

A campaign may override any field via its `sender_identity` JSON; anything it
omits falls back to the global env settings. One resolver so compliance.py and
send/mailer.py always agree on who the email is from.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from awkns_outreach.config import settings


@dataclass
class Identity:
    from_email: str
    from_name: str
    reply_to: str
    sender_name: str
    company: str
    postal_address: str
    unsubscribe_mailto: str


def resolve_identity(overrides: Optional[dict[str, Any]] = None) -> Identity:
    o = overrides or {}
    from_email = o.get("from") or settings.outreach_from
    return Identity(
        from_email=from_email,
        from_name=o.get("from_name") or settings.outreach_from_name,
        reply_to=o.get("reply_to") or settings.reply_to,
        sender_name=o.get("sender_name") or settings.outreach_sender_name,
        company=o.get("company") or settings.outreach_company,
        postal_address=o.get("postal_address") or settings.outreach_postal_address,
        unsubscribe_mailto=o.get("unsubscribe_mailto")
        or settings.outreach_unsubscribe_mailto,
    )
