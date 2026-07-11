"""Gmail send path: dispatch via Gmail when campaign.mailbox is connected
(instead of Resend), threading across sequence steps, needs_reconnect
fast-fail (zero network), and token refresh before a send."""
import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
import respx

from awkns_outreach.db.models import Campaign, Lead, Mailbox
from awkns_outreach.gmail.mime import build_raw_message
from awkns_outreach.send.mailer import send_outreach_email

_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
_TOKEN_URL = "https://oauth2.googleapis.com/token"

_SEQUENCE = [
    {"key": "intro", "delay_days": 0, "subject": "quick idea for {company}",
     "body": "Hi {first_name},\n\n{angle}\n\n{sender_name}"},
    {"key": "bump", "delay_days": 3, "subject": "re: quick idea for {company}",
     "body": "Floating this back up."},
]


def _mailbox(**kw):
    base = dict(
        id="mb1", provider="gmail", email="steven@gmail.com", display_name="Steven Wu",
        access_token="valid-token", refresh_token="rt1",
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        status="connected",
    )
    base.update(kw)
    return Mailbox(**base)


def _campaign(mailbox, **kw):
    base = dict(name="c", target_titles=[], seed_companies=[], sequence=_SEQUENCE,
                sender_identity={"sender_name": "Steven", "postal_address": "1 Test St"})
    base.update(kw)
    c = Campaign(**base)
    c.mailbox = mailbox
    return c


def _lead(**kw):
    base = dict(campaign_id="c1", email="k@toyota.co.jp", company="Toyota",
                contact_name="Kenji Tanaka", status="active", step=0)
    base.update(kw)
    return Lead(**base)


def _decode_raw(request) -> bytes:
    body = json.loads(request.content)
    raw = body["raw"]
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


@respx.mock
def test_send_posts_to_gmail_not_resend():
    resend_route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, json={"id": "resend-x"})
    )
    gmail_route = respx.post(_SEND_URL).mock(
        return_value=httpx.Response(200, json={"id": "gmail-1", "threadId": "thread-1"})
    )
    mb = _mailbox()
    c = _campaign(mb)
    res = send_outreach_email(_lead(), c, "k@toyota.co.jp", 0, dry_run=False)
    assert res.ok and res.id == "gmail-1"
    assert gmail_route.called
    assert not resend_route.called


@respx.mock
def test_raw_mime_has_unsubscribe_headers_and_both_parts():
    gmail_route = respx.post(_SEND_URL).mock(
        return_value=httpx.Response(200, json={"id": "gmail-1", "threadId": "thread-1"})
    )
    mb = _mailbox()
    c = _campaign(mb)
    res = send_outreach_email(_lead(), c, "k@toyota.co.jp", 0, dry_run=False)
    assert res.ok
    raw = _decode_raw(gmail_route.calls.last.request)
    assert b"List-Unsubscribe:" in raw
    assert b"List-Unsubscribe-Post:" in raw
    assert b"text/plain" in raw and b"text/html" in raw


@respx.mock
def test_step0_no_thread_id_step1_has_thread_and_in_reply_to():
    gmail_route = respx.post(_SEND_URL).mock(
        return_value=httpx.Response(200, json={"id": "gmail-1", "threadId": "thread-1"})
    )
    mb = _mailbox()
    c = _campaign(mb)
    lead = _lead()

    res0 = send_outreach_email(lead, c, "k@toyota.co.jp", 0, dry_run=False)
    assert res0.ok
    body0 = json.loads(gmail_route.calls.last.request.content)
    assert "threadId" not in body0
    assert lead.thread_ref == "thread-1"
    assert lead.last_message_id  # set by the mailer for follow-up threading

    prior_message_id = lead.last_message_id
    res1 = send_outreach_email(lead, c, "k@toyota.co.jp", 1, dry_run=False)
    assert res1.ok
    body1 = json.loads(gmail_route.calls.last.request.content)
    assert body1.get("threadId") == "thread-1"
    raw1 = _decode_raw(gmail_route.calls.last.request)
    assert prior_message_id.encode() in raw1  # In-Reply-To / References header


@respx.mock
def test_unhealthy_mailbox_fast_fails_zero_network():
    # Any non-connected status — needs_reconnect AND disconnected (tokens
    # cleared while still assigned to a campaign) — must fail without a
    # single network call.
    for status in ("needs_reconnect", "disconnected"):
        mb = _mailbox(status=status)
        c = _campaign(mb)
        res = send_outreach_email(_lead(), c, "k@toyota.co.jp", 0, dry_run=False)
        assert not res.ok and res.error == f"mailbox {status}"
        assert len(respx.calls) == 0


@respx.mock
def test_expired_token_triggers_refresh_before_send():
    token_route = respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "fresh-token", "expires_in": 3600, "scope": "gmail.send"},
        )
    )
    gmail_route = respx.post(_SEND_URL).mock(
        return_value=httpx.Response(200, json={"id": "gmail-1", "threadId": "thread-1"})
    )
    mb = _mailbox(access_token="stale", token_expiry=datetime.now(timezone.utc) - timedelta(seconds=5))
    c = _campaign(mb)
    res = send_outreach_email(_lead(), c, "k@toyota.co.jp", 0, dry_run=False)
    assert res.ok
    assert token_route.called
    assert mb.access_token == "fresh-token"
    assert gmail_route.calls.last.request.headers["Authorization"] == "Bearer fresh-token"


@respx.mock
def test_dry_run_gmail_mailbox_sends_nothing():
    mb = _mailbox()
    c = _campaign(mb)
    res = send_outreach_email(_lead(), c, "k@toyota.co.jp", 0, dry_run=True)
    assert res.ok and res.id == "dry-run"
    assert len(respx.calls) == 0


def test_build_raw_message_attaches_real_file():
    raw = build_raw_message(
        from_addr="a@x.com", from_name="A", to_addr="b@x.com", subject="s",
        text="hi", html="<p>hi</p>", message_id="<m@x.com>",
        attachments=[{"filename": "notes.txt", "content_type": "text/plain", "data": b"hello world"}],
    )
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    assert b'filename="notes.txt"' in decoded
    assert b"aGVsbG8gd29ybGQ=" in decoded  # base64 of "hello world"
    assert b"multipart/mixed" in decoded


@respx.mock
def test_gmail_send_includes_attachment_from_disk(tmp_path, monkeypatch):
    import awkns_outreach.send.mailer as mailer_module
    monkeypatch.setattr(mailer_module, "UPLOAD_DIR", tmp_path)
    (tmp_path / "abc123.pdf").write_bytes(b"%PDF-fake-content")

    gmail_route = respx.post(_SEND_URL).mock(
        return_value=httpx.Response(200, json={"id": "gmail-1", "threadId": "thread-1"})
    )
    mb = _mailbox()
    c = _campaign(mb, sequence=[{
        "key": "intro", "delay_days": 0, "subject": "s", "body": "b",
        "attachments": [{"filename": "proposal.pdf", "stored_name": "abc123.pdf", "content_type": "application/pdf"}],
    }])
    res = send_outreach_email(_lead(), c, "k@toyota.co.jp", 0, dry_run=False)
    assert res.ok
    raw = _decode_raw(gmail_route.calls.last.request)
    assert b'filename="proposal.pdf"' in raw
    assert base64.b64encode(b"%PDF-fake-content") in raw


@respx.mock
def test_gmail_send_skips_missing_attachment_file(tmp_path, monkeypatch):
    import awkns_outreach.send.mailer as mailer_module
    monkeypatch.setattr(mailer_module, "UPLOAD_DIR", tmp_path)  # empty dir — no file written

    gmail_route = respx.post(_SEND_URL).mock(
        return_value=httpx.Response(200, json={"id": "gmail-1", "threadId": "thread-1"})
    )
    mb = _mailbox()
    c = _campaign(mb, sequence=[{
        "key": "intro", "delay_days": 0, "subject": "s", "body": "b",
        "attachments": [{"filename": "gone.pdf", "stored_name": "missing.pdf", "content_type": "application/pdf"}],
    }])
    res = send_outreach_email(_lead(), c, "k@toyota.co.jp", 0, dry_run=False)
    assert res.ok  # send still succeeds, just without the missing attachment
    raw = _decode_raw(gmail_route.calls.last.request)
    assert b"gone.pdf" not in raw
