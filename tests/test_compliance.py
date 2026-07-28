"""Compliance: unsubscribe tokens, headers, footer, legal gate, suppression."""
import httpx
import respx

from awkns_outreach import compliance
from awkns_outreach.compliance import (
    can_send_legally,
    footer_html,
    footer_text,
    is_suppressed,
    list_unsubscribe_headers,
    make_unsub_token,
    suppress,
    verify_unsub_token,
)
from awkns_outreach.db.models import Campaign, Lead, Suppression
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


def test_footer_text_has_hardcoded_brand_block_and_no_postal_address():
    text = footer_text("a@b.com", _IDENT)
    # Hardcoded Pounds Network brand block, independent of the identity.
    assert "Pounds Network" in text
    assert "AI-Powered Marketing Campaigns" in text
    assert "Pounds.Network | gm@wing.studio | (425)628-4276 | Seattle, WA" in text
    # Postal address is intentionally no longer shown in the footer.
    assert "1 Test St, Taipei" not in text
    # Unsubscribe stays (List-Unsubscribe header + link).
    assert "Unsubscribe" in text
    # The env-driven sender/company line is no longer the footer heading.
    assert "Steven Wu · Awkns" not in text


def test_footer_html_hardcoded_brand_heading_and_no_postal_address():
    html = footer_html("a@b.com", _IDENT)
    # Bold brand heading, hardcoded (not the identity's "Steven Wu · Awkns").
    assert '<div style="font-weight:600;color:#202124">Pounds Network</div>' in html
    assert "Steven Wu · Awkns" not in html
    # Muted tagline + contact line, verbatim from image 2.
    assert "AI-Powered Marketing Campaigns" in html
    assert "Pounds.Network | gm@wing.studio | (425)628-4276 | Seattle, WA" in html
    # Postal address intentionally removed from the footer.
    assert "1 Test St, Taipei" not in html
    # Unsubscribe link stays, with no dangling " · " separator before it.
    assert "Unsubscribe" in html
    assert " · <a href" not in html


def test_footer_html_no_address_omits_dangling_separator():
    html = footer_html("a@b.com", _NO_ADDR)
    assert "Unsubscribe" in html
    assert " · <a href" not in html


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
