"""Render and send one sequence step — through Resend by default, or through a
connected Gmail mailbox when `campaign.mailbox_id` is set.

Port of yoh's send.ts. Deliverability-first: plain, hand-typed-looking markup
(paragraphs only, no card chrome) plus a text part, RFC 8058 unsubscribe headers,
and a Reply-To to a monitored inbox. Designed "newsletter" HTML lands in
Promotions/Spam; this doesn't.

Copy is TEMPLATED per campaign (campaign.sequence). The only AI-generated part is
each lead's `angle`, injected via the {angle} placeholder.

`send_outreach_email`'s signature and `SendResult` contract are unchanged by the
Gmail mailbox feature — sequencer/engine.py dispatches through this one function
either way and is otherwise untouched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import Any, Optional

import httpx

from awkns_outreach.compliance import footer_html, footer_text, list_unsubscribe_headers
from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, Lead, Mailbox
from awkns_outreach.gmail.api import ensure_fresh_token, send_raw
from awkns_outreach.gmail.mime import build_raw_message, new_message_id
from awkns_outreach.gmail.oauth import NeedsReconnect
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


def _render_email(subject_tpl: str, body_tpl: str, lead: Lead, ident: Identity, email: str) -> RenderedEmail:
    ctx = _context(lead, ident)
    subject = _render(subject_tpl, ctx)
    body = _render(body_tpl, ctx)
    return RenderedEmail(
        subject=subject,
        text=body + "\n" + footer_text(email, ident),
        html=_wrap_html(_text_to_html(body) + footer_html(email, ident)),
    )


def render_step(
    lead: Lead, campaign: Campaign, step_index: int, email: str,
    identity: Optional[Identity] = None,
) -> RenderedEmail:
    ident = identity or resolve_identity(campaign.sender_identity)
    steps = campaign.sequence or []
    if step_index >= len(steps):
        raise IndexError(f"No sequence step at index {step_index}")
    step = steps[step_index]
    return _render_email(step.get("subject", ""), step.get("body", ""), lead, ident, email)


# Hard-coded example contact for the standalone template library's preview
# pane / test-send — same rendering pipeline as render_step (_SafeDict +
# compliance footer), just not tied to any real campaign/lead.
_EXAMPLE_LEAD = Lead(
    campaign_id="preview", email="jamie@acmestudios.example", company="Acme Studios",
    contact_name="Jamie Rivera", contact_title="Creative Director", country="US",
    angle="Your recent campaign work would translate beautifully into short-form video.",
)


def render_template_preview(
    subject_tpl: str, body_tpl: str, email: str, identity: Optional[Identity] = None,
) -> RenderedEmail:
    """Render a standalone EmailTemplate's subject/body against the hard-coded
    example contact — used by the template library's preview pane."""
    ident = identity or resolve_identity()
    return _render_email(subject_tpl, body_tpl, _EXAMPLE_LEAD, ident, email)


def _apply_mailbox_identity(ident: Identity, mailbox: Mailbox, campaign: Campaign) -> None:
    """Gmail forces From to the connected mailbox (it rewrites arbitrary From
    addresses anyway). An explicit campaign sender_identity override still
    wins over the mailbox's display_name, which wins over the env default."""
    overrides = campaign.sender_identity or {}
    ident.from_email = mailbox.email
    if not overrides.get("from_name") and mailbox.display_name:
        ident.from_name = mailbox.display_name
    if not overrides.get("reply_to"):
        ident.reply_to = mailbox.email


def _send_via_gmail(
    lead: Lead, campaign: Campaign, mailbox: Mailbox, email: str, step_index: int,
    rendered: RenderedEmail, ident: Identity,
) -> SendResult:
    if mailbox.status != "connected":
        # Fast-fail, zero network — covers needs_reconnect AND a mailbox that
        # was disconnected (tokens cleared) while still assigned to a campaign.
        return SendResult(ok=False, error=f"mailbox {mailbox.status}", subject=rendered.subject)

    try:
        access_token = ensure_fresh_token(mailbox)
    except NeedsReconnect:
        # ensure_fresh_token already set mailbox.status/last_error; the
        # engine's own session.commit() (right after this call returns)
        # persists it alongside the error Event — see sequencer/engine.py.
        return SendResult(ok=False, error="mailbox needs reconnect", subject=rendered.subject)
    except Exception as e:
        return SendResult(ok=False, error=str(e), subject=rendered.subject)

    domain = mailbox.email.split("@", 1)[-1] or "localhost"
    message_id = new_message_id(domain)
    # Threading: step 0 starts a new thread; later steps reference the
    # previous step's Message-ID/threadId (both set on the lead by THIS
    # function on a prior successful send).
    in_reply_to = lead.last_message_id if step_index > 0 else None
    thread_id = lead.thread_ref if step_index > 0 else None

    try:
        raw = build_raw_message(
            from_addr=ident.from_email, from_name=ident.from_name, to_addr=email,
            subject=rendered.subject, text=rendered.text, html=rendered.html,
            message_id=message_id, reply_to=ident.reply_to, in_reply_to=in_reply_to,
            extra_headers=list_unsubscribe_headers(email, ident),
        )
        data = send_raw(access_token, raw, thread_id=thread_id)
    except Exception as e:
        return SendResult(ok=False, error=str(e), subject=rendered.subject)

    # WHY mutate `lead` here instead of returning thread info in SendResult:
    # send_outreach_email's signature/contract is shared with the Resend path
    # and untouched by design (engine.py isn't touched either). The engine
    # commits the SAME session right after this call, atomically with the
    # `sent` Event — see the WHY comment in send_outreach_email below.
    lead.thread_ref = data.get("threadId") or thread_id
    lead.last_message_id = message_id
    return SendResult(ok=True, id=data.get("id"), subject=rendered.subject)


def send_outreach_email(
    lead: Lead, campaign: Campaign, email: str, step_index: int, dry_run: bool = True,
) -> SendResult:
    ident = resolve_identity(campaign.sender_identity)
    rendered = render_step(lead, campaign, step_index, email, ident)
    if dry_run:
        return SendResult(ok=True, id="dry-run", subject=rendered.subject)

    mailbox = campaign.mailbox
    if mailbox is not None:
        # WHY no explicit commit here: `lead` and `mailbox` are ORM objects in
        # the same SQLAlchemy session (both hang off `campaign`, loaded by the
        # caller), and the caller (sequencer.engine.process_campaign) always
        # commits right after send_outreach_email returns — whether we sent or
        # errored — so mutations made to `lead`/`mailbox` below ride along in
        # that commit atomically with the `sent`/`error` Event. See
        # _send_via_gmail.
        _apply_mailbox_identity(ident, mailbox, campaign)
        return _send_via_gmail(lead, campaign, mailbox, email, step_index, rendered, ident)

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
