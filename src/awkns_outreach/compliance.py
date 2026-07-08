"""Anti-spam compliance for cold outreach.

Port of yoh's compliance.ts + unsubscribe-token.ts:
  • HMAC-signed unsubscribe tokens (tamper-proof, no DB lookup to verify).
  • RFC 8058 one-click List-Unsubscribe headers (Gmail/Outlook native button).
  • Identity footer with the legally-required physical postal address.
  • The suppression (do-not-contact) list, checked before EVERY send.
  • can_send_legally() — the hard gate: no postal address ⇒ no real sends.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone
from html import escape
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.config import settings
from awkns_outreach.db.models import Lead, Suppression
from awkns_outreach.identity import Identity, resolve_identity


# --- Unsubscribe token (HMAC-SHA256; email is the payload) -----------------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_unsub_token(email: str) -> str:
    """`<b64url(email)>.<b64url(hmac(secret, email))>` — verifiable offline."""
    e = email.strip().lower()
    sig = hmac.new(
        settings.outreach_unsubscribe_secret.encode(), e.encode(), hashlib.sha256
    ).digest()
    return f"{_b64url(e.encode())}.{_b64url(sig)}"


def verify_unsub_token(token: str) -> Optional[str]:
    """Return the email if the signature is valid, else None."""
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        email = _b64url_decode(payload_b64).decode()
        expected = hmac.new(
            settings.outreach_unsubscribe_secret.encode(), email.encode(), hashlib.sha256
        ).digest()
        if hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            return email
    except Exception:
        pass
    return None


def unsubscribe_url(email: str) -> str:
    base = settings.app_base_url.rstrip("/")
    return f"{base}/outreach/unsubscribe?token={make_unsub_token(email)}"


# --- Headers + footer ------------------------------------------------------

def list_unsubscribe_headers(email: str, identity: Optional[Identity] = None) -> dict[str, str]:
    ident = identity or resolve_identity()
    url = unsubscribe_url(email)
    parts = [f"<{url}>"]
    if ident.unsubscribe_mailto:
        parts.insert(0, f"<mailto:{ident.unsubscribe_mailto}?subject=unsubscribe>")
    return {
        "List-Unsubscribe": ", ".join(parts),
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def footer_text(email: str, identity: Optional[Identity] = None) -> str:
    ident = identity or resolve_identity()
    lines = ["", "—", f"{ident.sender_name} · {ident.company}"]
    if ident.postal_address:
        lines.append(ident.postal_address)
    lines.append(f"Not relevant? Unsubscribe and I won't email again: {unsubscribe_url(email)}")
    return "\n".join(lines)


def footer_html(email: str, identity: Optional[Identity] = None) -> str:
    ident = identity or resolve_identity()
    addr = f"{escape(ident.postal_address)}<br>" if ident.postal_address else ""
    return (
        '<br><br><div style="color:#9aa0a6;font-size:12px;line-height:1.5">—<br>'
        f"{escape(ident.sender_name)} · {escape(ident.company)}<br>{addr}"
        f'<a href="{unsubscribe_url(email)}" style="color:#9aa0a6">Unsubscribe</a> '
        "and I won't email again.</div>"
    )


def can_send_legally(identity: Optional[Identity] = None) -> tuple[bool, Optional[str]]:
    """True only if anti-spam identity is complete enough to send for real."""
    ident = identity or resolve_identity()
    if not ident.postal_address:
        return False, (
            "postal address is empty — a physical address is legally required "
            "in cold email (set OUTREACH_POSTAL_ADDRESS or the campaign's identity)."
        )
    return True, None


# --- Suppression list ------------------------------------------------------

def is_suppressed(session: Session, email: str) -> bool:
    e = email.strip().lower()
    return session.scalar(select(Suppression.email).where(Suppression.email == e)) is not None


def suppress(session: Session, email: str, reason: str) -> None:
    """Add to the do-not-contact list and pull any active/paused lead out of the
    send pool. Does NOT relabel an existing suppression (keeps the real reason)."""
    e = email.strip().lower()
    existing = session.get(Suppression, e)
    if existing is None:
        session.add(Suppression(email=e, reason=reason, created_at=datetime.now(timezone.utc)))
    session.query(Lead).filter(
        Lead.email == e, Lead.status.in_(["active", "paused"])
    ).update({Lead.status: "suppressed"}, synchronize_session=False)
