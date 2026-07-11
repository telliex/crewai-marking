"""Mailer: template rendering, footer/unsubscribe, dry-run, Resend send."""
import httpx
import respx

from awkns_outreach.db.models import Campaign, Lead
from awkns_outreach.send.mailer import render_step, render_template_preview, send_outreach_email

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
