"""Render and send one sequence step through Resend.

Port of yoh's send.ts. Deliverability-first: plain, hand-typed-looking markup
(paragraphs only, no card chrome) plus a text part, RFC 8058 unsubscribe headers,
and a Reply-To to a monitored inbox. Designed "newsletter" HTML lands in
Promotions/Spam; this doesn't.

Copy is TEMPLATED per campaign (campaign.sequence). The only AI-generated part is
each lead's `angle`, injected via the {angle} placeholder.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import Any, Optional

import httpx

from awkns_outreach.compliance import footer_html, footer_text, list_unsubscribe_headers
from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, Lead
from awkns_outreach.identity import Identity, resolve_identity

_RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT = httpx.Timeout(30.0)


# --- Templating ------------------------------------------------------------

class _SafeDict(dict):
    def __missing__(self, key: str) -> str:  # unknown {placeholder} → empty
        return ""


def _angle_line(lead: Lead) -> str:
    """Prefer an AI-generated company-specific example, then the static angle,
    then a safe generic line."""
    v: Any = lead.vars or {}
    example = (v.get("example") if isinstance(v, dict) else "") or ""
    return (
        example.strip()
        or (lead.angle or "").strip()
        or f"I think it could be genuinely useful for {lead.company or 'your team'}."
    )


def _context(lead: Lead, identity: Identity) -> _SafeDict:
    name = (lead.contact_name or "").strip()
    first_name = name.split()[0] if name else "there"
    return _SafeDict(
        first_name=first_name,
        contact_name=lead.contact_name or "",
        contact_title=lead.contact_title or "",
        company=lead.company or "there",
        country=lead.country or "",
        angle=_angle_line(lead),
        sender_name=identity.sender_name,
    )


def _render(tpl: str, ctx: _SafeDict) -> str:
    try:
        return tpl.format_map(ctx)
    except (IndexError, KeyError, ValueError):
        return tpl  # never let a bad template abort a send-render


# --- Plain-text → inbox-friendly HTML (no card chrome) ---------------------

def _linkify(escaped: str) -> str:
    def repl(m: re.Match) -> str:
        url = m.group(0)
        trail = (re.search(r"[.,!?)\]]+$", url) or [""])[0]
        if trail:
            url = url[: -len(trail)]
        return f'<a href="{url}" style="color:#1a73e8;text-decoration:underline">{url}</a>{trail}'

    return re.sub(r"https?://[^\s<]+", repl, escaped)


def _text_to_html(text: str) -> str:
    paras = re.split(r"\n{2,}", text)
    return "".join(
        f'<p style="margin:0 0 14px 0">{_linkify(escape(p)).replace(chr(10), "<br>")}</p>'
        for p in paras
    )


def _wrap_html(inner: str) -> str:
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,'
        "Helvetica,Arial,sans-serif;font-size:15px;color:#202124;line-height:1.6;"
        'max-width:560px;margin:0 auto;padding:8px 6px">' + inner + "</div>"
    )


# --- Render + send ---------------------------------------------------------

@dataclass
class RenderedEmail:
    subject: str
    text: str
    html: str


@dataclass
class SendResult:
    ok: bool
    subject: str
    id: Optional[str] = None
    error: Optional[str] = None


def render_step(
    lead: Lead, campaign: Campaign, step_index: int, email: str,
    identity: Optional[Identity] = None,
) -> RenderedEmail:
    ident = identity or resolve_identity(campaign.sender_identity)
    steps = campaign.sequence or []
    if step_index >= len(steps):
        raise IndexError(f"No sequence step at index {step_index}")
    step = steps[step_index]
    ctx = _context(lead, ident)
    subject = _render(step.get("subject", ""), ctx)
    body = _render(step.get("body", ""), ctx)
    return RenderedEmail(
        subject=subject,
        text=body + "\n" + footer_text(email, ident),
        html=_wrap_html(_text_to_html(body) + footer_html(email, ident)),
    )


def send_outreach_email(
    lead: Lead, campaign: Campaign, email: str, step_index: int, dry_run: bool = True,
) -> SendResult:
    ident = resolve_identity(campaign.sender_identity)
    rendered = render_step(lead, campaign, step_index, email, ident)
    if dry_run:
        return SendResult(ok=True, id="dry-run", subject=rendered.subject)

    try:
        resp = httpx.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": f"{ident.from_name} <{ident.from_email}>",
                "to": email,
                "reply_to": ident.reply_to,
                "subject": rendered.subject,
                "text": rendered.text,
                "html": rendered.html,
                "headers": list_unsubscribe_headers(email, ident),
            },
            timeout=_TIMEOUT,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            msg = data.get("message") or data.get("error") or resp.text[:200]
            return SendResult(ok=False, error=str(msg), subject=rendered.subject)
        return SendResult(ok=True, id=data.get("id"), subject=rendered.subject)
    except Exception as e:  # network/timeout
        return SendResult(ok=False, error=str(e), subject=rendered.subject)
