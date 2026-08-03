"""Compliance: unsubscribe tokens, headers, footer, legal gate, suppression."""
import httpx
import respx

from awkns_outreach import compliance
from awkns_outreach.compliance import (
    DEFAULT_FOOTER_HTML,
    DEFAULT_FOOTER_TEXT,
    can_send_legally,
    footer_html,
    footer_text,
    is_suppressed,
    list_unsubscribe_headers,
    make_unsub_token,
    resolve_footer,
    suppress,
    verify_unsub_token,
)
from awkns_outreach.db.models import Campaign, FooterTemplate, Lead, Suppression


def _default_footer() -> FooterTemplate:
    return FooterTemplate(
        name="Default", body_html=DEFAULT_FOOTER_HTML, body_text=DEFAULT_FOOTER_TEXT,
        is_default=True,
    )
from awkns_outreach.identity import Identity

_IDENT = Identity(
    from_email="s@mail.x.com", from_name="Steven", reply_to="s@x.com",
    sender_name="Steven Wu", company="Awkns",
    postal_address="1 Test St, Taipei", unsubscribe_mailto="",
)
_NO_ADDR = Identity(
    from_email="s@mail.x.com", from_name="Steven", reply_to="s@x.com",
    sender_name="Steven Wu", company="Awkns", postal_address="", unsubscribe_mailto="",
)


def test_unsub_token_roundtrip():
    tok = make_unsub_token("Kenji@Toyota.co.jp")
    assert verify_unsub_token(tok) == "kenji@toyota.co.jp"  # lowercased


def test_unsub_token_tamper_rejected():
    tok = make_unsub_token("a@b.com")
    payload, _sig = tok.split(".", 1)
    forged = payload + ".AAAA"
    assert verify_unsub_token(forged) is None
    assert verify_unsub_token("garbage") is None


def test_list_unsubscribe_headers_one_click():
    h = list_unsubscribe_headers("a@b.com", _IDENT)
    assert h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert h["List-Unsubscribe"].startswith("<http")


def test_default_footer_text_renders_brand_block_and_unsubscribe():
    text = footer_text(_default_footer(), "a@b.com")
    assert "Pounds Network" in text
    assert "AI-Powered Marketing Campaigns" in text
    assert "Pounds.Network | gm@wing.studio | (425)628-4276 | Seattle, WA" in text
    assert "Unsubscribe" in text
    # {unsubscribe_url} is substituted with the real per-recipient link.
    assert "{unsubscribe_url}" not in text
    assert "/outreach/unsubscribe?token=" in text


def test_default_footer_html_renders_brand_heading_and_unsubscribe_link():
    html = footer_html(_default_footer(), "a@b.com")
    assert '<div style="font-weight:600;color:#202124">Pounds Network</div>' in html
    assert "AI-Powered Marketing Campaigns" in html
    assert "Pounds.Network | gm@wing.studio | (425)628-4276 | Seattle, WA" in html
    assert "{unsubscribe_url}" not in html
    assert 'href="http' in html and "/outreach/unsubscribe?token=" in html


def test_resolve_footer_none_returns_none(db_session):
    assert resolve_footer(db_session, "none") is None


def test_resolve_footer_default_uses_default_row(db_session):
    default = FooterTemplate(name="D", body_html="<b>D</b>", body_text="D", is_default=True)
    db_session.add(default)
    db_session.commit()
    assert resolve_footer(db_session, "default").id == default.id
    assert resolve_footer(db_session, None).id == default.id


def test_resolve_footer_specific_id(db_session):
    default = FooterTemplate(name="D", body_html="d", body_text="d", is_default=True)
    other = FooterTemplate(name="Other", body_html="o", body_text="o")
    db_session.add_all([default, other])
    db_session.commit()
    assert resolve_footer(db_session, other.id).id == other.id


def test_resolve_footer_unknown_id_falls_back_to_default(db_session):
    default = FooterTemplate(name="D", body_html="d", body_text="d", is_default=True)
    db_session.add(default)
    db_session.commit()
    assert resolve_footer(db_session, "no-such-id").id == default.id


def test_resolve_footer_synthesizes_default_when_no_row(db_session):
    # No rows seeded (create_all doesn't run the seed migration): still usable.
    footer = resolve_footer(db_session, "default")
    assert footer is not None
    assert "Pounds Network" in footer.body_html


def test_legal_gate():
    ok, reason = can_send_legally(_IDENT)
    assert ok and reason is None
    ok, reason = can_send_legally(_NO_ADDR)
    assert not ok and "postal address" in reason


def test_suppress_flips_active_leads(db_session):
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    db_session.add(c)
    db_session.flush()
    lead = Lead(campaign_id=c.id, email="x@y.com", company="Y", status="active")
    db_session.add(lead)
    db_session.flush()

    assert not is_suppressed(db_session, "x@y.com")
    suppress(db_session, "X@Y.com", "unsubscribe")
    db_session.flush()

    assert is_suppressed(db_session, "x@y.com")
    assert db_session.get(Suppression, "x@y.com").reason == "unsubscribe"
    db_session.refresh(lead)
    assert lead.status == "suppressed"


def test_suppress_keeps_original_reason(db_session):
    suppress(db_session, "a@b.com", "bounce")
    db_session.flush()
    suppress(db_session, "a@b.com", "manual")  # second hit must not relabel
    db_session.flush()
    assert db_session.get(Suppression, "a@b.com").reason == "bounce"
