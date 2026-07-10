"""Build the raw RFC 822 message the Gmail API's `messages.send` expects.

Stdlib `email.message.EmailMessage` only — no MIME library dependency needed
for a text+html multipart/alternative message with a few extra headers.
"""
from __future__ import annotations

import base64
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Optional


def new_message_id(domain: str) -> str:
    """Mint our own RFC-822 Message-ID (Gmail's API doesn't return one on
    send), so a later sequence step can set In-Reply-To/References without an
    extra `messages.get` round-trip."""
    return make_msgid(domain=domain)


def build_raw_message(
    *,
    from_addr: str,
    from_name: str,
    to_addr: str,
    subject: str,
    text: str,
    html: str,
    message_id: str,
    reply_to: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> str:
    """Return the message base64url-encoded (the Gmail API `raw` field)."""
    msg = EmailMessage()
    msg["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if reply_to:
        msg["Reply-To"] = reply_to
    if in_reply_to:
        # Both headers point at the previous step's Message-ID so Gmail (and
        # any other client) threads this as a reply.
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    for key, value in (extra_headers or {}).items():
        msg[key] = value

    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
