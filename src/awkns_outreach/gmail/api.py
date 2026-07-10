"""httpx wrappers around the Gmail REST API (no google-api-python-client —
see gmail/__init__.py for why).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from awkns_outreach.db.models import Mailbox
from awkns_outreach.gmail.oauth import NeedsReconnect, refresh_access_token

_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_TIMEOUT = httpx.Timeout(30.0)
# Refresh proactively if the access token expires within this many seconds,
# rather than racing an in-flight API call against expiry.
_REFRESH_MARGIN_SECONDS = 120


class GmailAPIError(Exception):
    """A Gmail API call returned a non-2xx response."""


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _aware(dt: datetime) -> datetime:
    """SQLite (used in tests, and by any operator who skips Postgres) doesn't
    actually persist a tz offset even for a DateTime(timezone=True) column, so
    a value re-fetched from the DB comes back naive. Treat naive as UTC (what
    we always write) rather than crash comparing it to an aware `now`."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ensure_fresh_token(mailbox: Mailbox) -> str:
    """Return a valid access token for `mailbox`, refreshing it first if it's
    missing or expires within 2 minutes. Mutates `mailbox` in place (new
    token/expiry, or needs_reconnect on failure) — the caller's session commit
    is what actually persists it."""
    now = datetime.now(timezone.utc)
    if (
        mailbox.access_token
        and mailbox.token_expiry
        and _aware(mailbox.token_expiry) - now > timedelta(seconds=_REFRESH_MARGIN_SECONDS)
    ):
        return mailbox.access_token

    try:
        bundle = refresh_access_token(mailbox.refresh_token or "")
    except NeedsReconnect as exc:
        mailbox.status = "needs_reconnect"
        mailbox.last_error = str(exc)
        raise

    mailbox.access_token = bundle.access_token
    mailbox.token_expiry = bundle.expiry
    if bundle.refresh_token:
        mailbox.refresh_token = bundle.refresh_token
    if bundle.scope:
        mailbox.scopes = bundle.scope
    return mailbox.access_token


def fetch_profile_email(access_token: str) -> str:
    """The Gmail account's own address — used to confirm/derive `mailbox.email`
    on connect (never trust the client for this)."""
    resp = httpx.get(f"{_BASE}/profile", headers=_auth_headers(access_token), timeout=_TIMEOUT)
    if resp.status_code >= 400:
        raise GmailAPIError(f"profile fetch failed: {resp.text[:200]}")
    return resp.json()["emailAddress"]


def send_raw(access_token: str, raw: str, thread_id: Optional[str] = None) -> dict[str, Any]:
    """POST messages.send; returns the response JSON ({"id", "threadId", ...})."""
    body: dict[str, Any] = {"raw": raw}
    if thread_id:
        body["threadId"] = thread_id
    resp = httpx.post(
        f"{_BASE}/messages/send", headers=_auth_headers(access_token), json=body, timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise GmailAPIError(f"send failed: {resp.text[:200]}")
    return resp.json()


def list_message_ids(access_token: str, query: str) -> list[str]:
    """messages.list — return just the ids (metadata is fetched separately,
    one id at a time, so callers can stop early / dedupe cheaply)."""
    ids: list[str] = []
    params: dict[str, str] = {"q": query}
    while True:
        resp = httpx.get(
            f"{_BASE}/messages", headers=_auth_headers(access_token), params=params, timeout=_TIMEOUT,
        )
        if resp.status_code >= 400:
            raise GmailAPIError(f"list failed: {resp.text[:200]}")
        data = resp.json()
        ids.extend(m["id"] for m in data.get("messages", []))
        token = data.get("nextPageToken")
        if not token:
            break
        params["pageToken"] = token
    return ids


def get_message_metadata(access_token: str, message_id: str) -> dict[str, Any]:
    """messages.get?format=metadata — return {"id", "threadId", "from"}."""
    resp = httpx.get(
        f"{_BASE}/messages/{message_id}",
        headers=_auth_headers(access_token),
        params={"format": "metadata", "metadataHeaders": "From"},
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise GmailAPIError(f"get message failed: {resp.text[:200]}")
    data = resp.json()
    headers = {h["name"]: h["value"] for h in data.get("payload", {}).get("headers", [])}
    return {"id": data.get("id"), "threadId": data.get("threadId"), "from": headers.get("From", "")}
