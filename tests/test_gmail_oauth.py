"""Gmail OAuth: consent URL, CSRF state token, code exchange, refresh."""
import time

import httpx
import pytest
import respx

from awkns_outreach.config import settings
from awkns_outreach.gmail.oauth import (
    NeedsReconnect,
    consent_url,
    exchange_code,
    make_oauth_state,
    refresh_access_token,
    verify_oauth_state,
)

_TOKEN_URL = "https://oauth2.googleapis.com/token"


def test_consent_url_has_required_params(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "cid123")
    monkeypatch.setattr(settings, "app_base_url", "https://app.example.com")
    url = consent_url("state123")
    assert "client_id=cid123" in url
    assert "redirect_uri=https%3A%2F%2Fapp.example.com%2Foauth%2Fgoogle%2Fcallback" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "state=state123" in url
    assert "gmail.send" in url and "gmail.readonly" in url


def test_consent_url_login_hint():
    url = consent_url("s", login_hint="a@b.com")
    assert "login_hint=a%40b.com" in url


def test_state_round_trips():
    state = make_oauth_state()
    assert verify_oauth_state(state)


def test_state_tamper_detected():
    # Flip a character in the MIDDLE of the signature, not the last one: the
    # final base64url char of a 32-byte HMAC carries only 2 significant bits,
    # so changing it can decode to the identical signature (trailing-bit
    # malleability) and wouldn't count as tampering.
    state = make_oauth_state()
    payload_b64, sig_b64 = state.split(".", 1)
    mid = len(sig_b64) // 2
    flipped = "A" if sig_b64[mid] != "A" else "B"
    tampered = f"{payload_b64}.{sig_b64[:mid]}{flipped}{sig_b64[mid + 1:]}"
    assert not verify_oauth_state(tampered)


def test_state_expiry(monkeypatch):
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() - 700)  # 11+ minutes ago
    state = make_oauth_state()
    monkeypatch.setattr(time, "time", real_time)
    assert not verify_oauth_state(state)


def test_state_garbage_rejected():
    assert not verify_oauth_state("not-a-valid-token")


@respx.mock
def test_exchange_code_happy_path():
    respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "at1", "refresh_token": "rt1", "expires_in": 3600, "scope": "gmail.send",
        })
    )
    bundle = exchange_code("authcode")
    assert bundle.access_token == "at1"
    assert bundle.refresh_token == "rt1"


@respx.mock
def test_refresh_invalid_grant_raises_needs_reconnect():
    respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token expired"})
    )
    with pytest.raises(NeedsReconnect):
        refresh_access_token("rt1")


@respx.mock
def test_refresh_success_keeps_refresh_token_if_omitted():
    respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "at2", "expires_in": 3600, "scope": "gmail.send"})
    )
    bundle = refresh_access_token("rt-original")
    assert bundle.access_token == "at2"
    assert bundle.refresh_token == "rt-original"
