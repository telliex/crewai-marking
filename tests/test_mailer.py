"""Mailer: template rendering, footer/unsubscribe, dry-run, Resend send."""
import httpx
import respx

from awkns_outreach.db.models import Campaign, Lead
from awkns_outreach.send.mailer import (
    render_step,
    render_template_preview,
    sanitize_rich_body,
    send_outreach_email,
)

_SEQUENCE = [
    {"key": "intro", "delay_days": 0,
     "subject": "quick idea for {company}",
     "body": "Hi {first_name},\n\n{angle}\n\n{sender_name}"},
    {"key": "bump", "delay_days": 3,
     "subject": "re: quick idea for {company}", "body": "Floating this back up."},
]


def _campaign():
    return Campaign(name="c", target_titles=[], seed_companies=[], sequence=_SEQUENCE,
                    sender_identity={"sender_name": "Steven", "from": "s@mail.x.com",
                                     "from_name": "Steven", "postal_address": "1 Test St"})


def _lead(**kw):
    base = dict(campaign_id="c1", email="k@toyota.co.jp", company="Toyota",
                contact_name="Kenji Tanaka", status="active", step=0)
    base.update(kw)
    return Lead(**base)


def test_render_fills_placeholders():
    r = render_step(_lead(angle="Your stories would animate beautifully."), _campaign(), 0, "k@toyota.co.jp")
    assert r.subject == "quick idea for Toyota"
    assert "Hi Kenji," in r.text
    assert "Your stories would animate beautifully." in r.text
    assert "Unsubscribe" in r.text and "1 Test St" in r.text
    assert "<p" in r.html  # inbox-friendly paragraphs


def test_angle_fallback_when_missing():
    r = render_step(_lead(angle=None, vars=None), _campaign(), 0, "k@toyota.co.jp")
    assert "genuinely useful for Toyota" in r.text


def test_angle_prefers_ai_example():
    r = render_step(_lead(angle="static", vars={"example": "AI example line"}), _campaign(), 0, "k@toyota.co.jp")
    assert "AI example line" in r.text
    assert "static" not in r.text


def test_first_name_fallback():
    r = render_step(_lead(contact_name=None), _campaign(), 0, "k@toyota.co.jp")
    assert "Hi there," in r.text


def test_preview_shows_unrecognized_placeholder_literally():
    r = render_template_preview(
        "hi {company_name}", "Hi {first_name}, re {service}. Best, {your_name}", "jamie@x.com",
    )
    assert "{company_name}" in r.subject
    assert "{service}" in r.text and "{your_name}" in r.text
    assert "Hi Jamie," in r.text  # recognized placeholders still substitute


def test_real_send_blanks_unrecognized_placeholder_not_literal():
    campaign = _campaign()
    campaign.sequence = [{"key": "intro", "delay_days": 0, "subject": "s", "body": "re {service}."}]
    r = render_step(_lead(), campaign, 0, "k@toyota.co.jp")
    assert "{service}" not in r.text
    assert "re ." in r.text


def test_dry_run_sends_nothing():
    res = send_outreach_email(_lead(), _campaign(), "k@toyota.co.jp", 0, dry_run=True)
    assert res.ok and res.id == "dry-run"


@respx.mock
def test_send_posts_to_resend():
    route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, json={"id": "resend-123"})
    )
    res = send_outreach_email(_lead(), _campaign(), "k@toyota.co.jp", 0, dry_run=False)
    assert res.ok and res.id == "resend-123"
    sent = route.calls.last.request
    body = sent.content.decode()
    assert "List-Unsubscribe" in body  # header present in payload
    assert "k@toyota.co.jp" in body


@respx.mock
def test_send_error_surfaces():
    respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(422, json={"message": "invalid from"})
    )
    res = send_outreach_email(_lead(), _campaign(), "k@toyota.co.jp", 0, dry_run=False)
    assert not res.ok and "invalid from" in res.error


@respx.mock
def test_resend_send_includes_base64_attachment_from_disk(tmp_path, monkeypatch):
    import awkns_outreach.send.mailer as mailer_module
    monkeypatch.setattr(mailer_module, "UPLOAD_DIR", tmp_path)
    (tmp_path / "abc123.pdf").write_bytes(b"%PDF-fake-content")

    route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, json={"id": "resend-123"})
    )
    campaign = _campaign()
    campaign.sequence = [{
        "key": "intro", "delay_days": 0, "subject": "s", "body": "b",
        "attachments": [{"filename": "proposal.pdf", "stored_name": "abc123.pdf", "content_type": "application/pdf"}],
    }]
    res = send_outreach_email(_lead(), campaign, "k@toyota.co.jp", 0, dry_run=False)
    assert res.ok
    payload = route.calls.last.request.content.decode()
    assert '"filename":"proposal.pdf"' in payload.replace(" ", "")
    import base64
    assert base64.b64encode(b"%PDF-fake-content").decode() in payload


@respx.mock
def test_resend_send_without_attachments_omits_attachments_key():
    route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, json={"id": "resend-123"})
    )
    send_outreach_email(_lead(), _campaign(), "k@toyota.co.jp", 0, dry_run=False)
    payload = route.calls.last.request.content.decode()
    assert "attachments" not in payload


# --- Rich-text (Quill-authored) template bodies -----------------------------

def test_sanitize_rich_body_strips_disallowed_tags_and_attributes():
    dirty = '<p style="color:red" onclick="x()">Hi <script>alert(1)</script><b>there</b></p>'
    clean = sanitize_rich_body(dirty)
    assert "style=" not in clean and "onclick=" not in clean
    assert "<script>" not in clean and "alert(1)" not in clean
    assert "<p>Hi there</p>" == clean  # <b> isn't in the allowlist, text is kept


def test_sanitize_rich_body_keeps_allowed_formatting_and_link_href():
    clean = sanitize_rich_body('<p><strong>Hi</strong> <em>there</em>, <a href="https://x.com">link</a></p>')
    assert '<strong>Hi</strong>' in clean
    assert '<em>there</em>' in clean
    assert '<a href="https://x.com" rel="noopener noreferrer">link</a>' in clean


def test_render_step_with_quill_html_body_produces_readable_text_alt_part():
    campaign = _campaign()
    campaign.sequence = [{
        "key": "intro", "delay_days": 0, "subject": "s",
        "body": "<p>Hi {first_name},</p><p>{angle}</p><ul><li>Point one</li><li>Point two</li></ul>",
    }]
    r = render_step(_lead(angle="Nice work."), campaign, 0, "k@toyota.co.jp")
    assert "<p>Hi Kenji,</p>" in r.html and "<li>Point one</li>" in r.html
    assert "Hi Kenji," in r.text and "Nice work." in r.text
    assert "- Point one" in r.text and "- Point two" in r.text
    assert "<p>" not in r.text and "<li>" not in r.text


def test_sanitize_rich_body_keeps_img_src_and_alt_strips_other_attrs():
    clean = sanitize_rich_body('<p><img src="https://x.com/a.png" alt="A" onerror="x()" style="width:9999px"></p>')
    assert '<img src="https://x.com/a.png" alt="A">' in clean


def test_sanitize_rich_body_strips_javascript_scheme_img_src():
    clean = sanitize_rich_body('<img src="javascript:alert(1)">')
    assert "javascript:" not in clean


def test_render_step_with_image_produces_bracketed_url_in_text_alt_part():
    campaign = _campaign()
    campaign.sequence = [{
        "key": "intro", "delay_days": 0, "subject": "s",
        "body": '<p>Hi {first_name}</p><img src="https://x.com/a.png" alt="logo">',
    }]
    r = render_step(_lead(), campaign, 0, "k@toyota.co.jp")
    assert '<img src="https://x.com/a.png" alt="logo">' in r.html
    assert "[image: https://x.com/a.png]" in r.text


def test_preview_sanitizes_and_renders_quill_html_body():
    r = render_template_preview(
        "hi {company}", '<p onclick="x()">Hi {first_name}, <a href="https://x.com">link</a></p>', "jamie@x.com",
    )
    assert "onclick" not in r.html
    assert '<a href="https://x.com" rel="noopener noreferrer">link</a>' in r.html
    assert "Hi Jamie, link (https://x.com)" in r.text
