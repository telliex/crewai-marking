"""Google OAuth: consent URL, CSRF state token, code exchange, refresh, revoke.

Pure httpx against Google's token endpoint — 6 trivial calls total across
this module and gmail/api.py, so pulling in `google-api-python-client` (which
respx can't mock) buys nothing.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx

from awkns_outreach.config import settings

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_TIMEOUT = httpx.Timeout(30.0)

# gmail.send to dispatch as the mailbox; gmail.readonly to poll for replies.
SCOPES = "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly"

# No server sessions exist for this single-operator admin UI, so CSRF
# protection is a self-validating HMAC state token (same pattern as
# compliance.make_unsub_token) rather than a session-stored nonce.
_STATE_TTL_SECONDS = 10 * 60


class OAuthError(Exception):
    """Google's token endpoint returned an error with no special handling."""


class NeedsReconnect(Exception):
    """Refresh failed with invalid_grant: revoked consent, or — the *primary*
    path for personal @gmail.com apps stuck in Testing mode — the refresh
    token's 7-day expiry. Caller sets mailbox.status = "needs_reconnect"."""


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: Optional[str]
    expiry: datetime
    scope: str


def _b64url(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    return urlsafe_b64decode(s + "=" * (-len(s) % 4))


def redirect_uri() -> str:
    return settings.app_base_url.rstrip("/") + "/oauth/google/callback"


def make_oauth_state() -> str:
    """`<b64url(nonce:ts)>.<b64url(hmac)>` — verifiable offline, no DB lookup."""
    payload = f"{secrets.token_urlsafe(16)}:{int(time.time())}"
    sig = hmac.new(
        settings.outreach_unsubscribe_secret.encode(), payload.encode(), hashlib.sha256
    ).digest()
    return f"{_b64url(payload.encode())}.{_b64url(sig)}"


def verify_oauth_state(token: str) -> bool:
    """True iff the signature matches and the token is within its 10-minute
    validity window. Tampered, malformed, or expired -> False."""
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64url_decode(payload_b64).decode()
        expected = hmac.new(
            settings.outreach_unsubscribe_secret.encode(), payload.encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            return False
        _, ts = payload.split(":", 1)
        return (time.time() - int(ts)) <= _STATE_TTL_SECONDS
    except Exception:
        return False


def consent_url(state: str, login_hint: Optional[str] = None) -> str:
    """access_type=offline + prompt=consent ALWAYS (not just first-time) so a
    previously-consented user re-mints a refresh_token on reconnect."""
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{_AUTH_URL}?{urlencode(params)}"


def _bundle_from_response(data: dict, *, fallback_refresh_token: Optional[str] = None) -> TokenBundle:
    expires_in = int(data.get("expires_in") or 3600)
    return TokenBundle(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token") or fallback_refresh_token,
        expiry=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        scope=data.get("scope", ""),
    )


def exchange_code(code: str) -> TokenBundle:
    """Authorization code -> first access+refresh token pair."""
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=_TIMEOUT,
    )
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        raise OAuthError(data.get("error_description") or data.get("error") or resp.text[:200])
    return _bundle_from_response(data)


def refresh_access_token(refresh_token: str) -> TokenBundle:
    """Mint a fresh access token from a stored refresh token. Google's refresh
    response normally omits `refresh_token` (it doesn't rotate), so we carry
    the caller's value forward."""
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "refresh_token": refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "grant_type": "refresh_token",
        },
        timeout=_TIMEOUT,
    )
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        error = data.get("error", "")
        msg = data.get("error_description") or error or resp.text[:200]
        if error == "invalid_grant":
            raise NeedsReconnect(msg)
        raise OAuthError(msg)
    return _bundle_from_response(data, fallback_refresh_token=refresh_token)


def revoke(token: str) -> None:
    """Best-effort revoke — disconnect must not fail just because Google's
    revoke endpoint is unreachable or the token is already dead."""
    try:
        httpx.post(_REVOKE_URL, data={"token": token}, timeout=_TIMEOUT)
    except Exception:
        pass
