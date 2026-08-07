"""Runtime-editable global settings, backed by the `app_setting` DB table.

The pydantic `Settings` singleton (config.py) is built once from `.env` at
import — great for a static process, but it can't be changed from the admin UI.
This module layers a DB override on top: the Variables settings page writes
key/value rows to `app_setting`, and `apply_overrides()` pushes those values
into BOTH the live `settings` singleton AND `os.environ`.

Why both: most code reads `settings.<attr>` (Apollo/Resend/tier-Anthropic/
sender identity) and picks up a mutated singleton immediately — but crewai /
litellm (writer/angle.py: SerperDevTool, LLM) read SERPER_API_KEY /
ANTHROPIC_API_KEY straight from `os.environ` and ignore the singleton. So an
override must land in both places.

Effective value = DB override if present, else the `.env`/default baseline
snapshotted at import (before any override is applied). `apply_overrides` is
idempotent — it always recomputes from baseline + current DB rows — so clearing
an override row and re-applying restores the original `.env` value.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.config import settings


@dataclass(frozen=True)
class VarSpec:
    env_name: str          # the .env / os.environ key, also the app_setting PK
    settings_attr: str     # attribute on the Settings singleton
    label: str
    category: str
    is_secret: bool = False
    # True for sender-identity vars: a running task keeps the value it was
    # started with (frozen onto the Task); changing them only affects tasks
    # started afterwards. False vars apply globally & immediately.
    freeze: bool = False


CAT_IDENTITY = "Sender Identity"
CAT_KEYS = "API Keys"
CAT_MODELS = "AI Models"
CAT_SENDING = "Sending / Warmup"


REGISTRY: list[VarSpec] = [
    # --- Sender identity (frozen per-task at start) ---
    VarSpec("OUTREACH_FROM", "outreach_from", "From email", CAT_IDENTITY, freeze=True),
    VarSpec("OUTREACH_FROM_NAME", "outreach_from_name", "From name", CAT_IDENTITY, freeze=True),
    VarSpec("OUTREACH_REPLY_TO", "outreach_reply_to", "Reply-To", CAT_IDENTITY, freeze=True),
    VarSpec("OUTREACH_SENDER_NAME", "outreach_sender_name", "Sender name", CAT_IDENTITY, freeze=True),
    VarSpec("OUTREACH_COMPANY", "outreach_company", "Company", CAT_IDENTITY, freeze=True),
    VarSpec("OUTREACH_POSTAL_ADDRESS", "outreach_postal_address", "Postal address (required for real sends)", CAT_IDENTITY, freeze=True),
    VarSpec("OUTREACH_UNSUBSCRIBE_MAILTO", "outreach_unsubscribe_mailto", "Unsubscribe mailto", CAT_IDENTITY, freeze=True),
    # --- API keys (secret; applied globally & immediately) ---
    VarSpec("APOLLO_API_KEY", "apollo_api_key", "Apollo API key", CAT_KEYS, is_secret=True),
    VarSpec("RESEND_API_KEY", "resend_api_key", "Resend API key", CAT_KEYS, is_secret=True),
    VarSpec("ANTHROPIC_API_KEY", "anthropic_api_key", "Anthropic API key", CAT_KEYS, is_secret=True),
    VarSpec("SERPER_API_KEY", "serper_api_key", "Serper API key", CAT_KEYS, is_secret=True),
    # --- AI models (applied globally & immediately) ---
    VarSpec("CREW_MODEL", "crew_model", "Crew model (LiteLLM id, e.g. anthropic/claude-...)", CAT_MODELS),
    VarSpec("TIER_MODEL", "tier_model", "Tier model (bare Anthropic id)", CAT_MODELS),
    # --- Sending limits / warmup (applied globally & immediately) ---
    VarSpec("WARMUP_DAILY_CAP", "warmup_daily_cap", "Daily send cap per campaign", CAT_SENDING),
    VarSpec("WARMUP_RAMP", "warmup_ramp", "Warmup ramp (comma-separated per-day caps)", CAT_SENDING),
    VarSpec("WARMUP_DEFAULT_MODE", "warmup_default_mode", "New-campaign warmup: warm | full | none", CAT_SENDING),
    VarSpec("SEND_HOURS", "send_hours", "Send window hours, local (start-end, e.g. 9-17)", CAT_SENDING),
    VarSpec("SEND_DAYS", "send_days", "Send weekdays (0=Mon … 6=Sun, comma-separated)", CAT_SENDING),
    VarSpec("SEND_MIN_GAP_MS", "send_min_gap_ms", "Min gap between sends (ms)", CAT_SENDING),
    VarSpec("SEND_JITTER_MS", "send_jitter_ms", "Random extra gap jitter (ms)", CAT_SENDING),
    VarSpec("MAX_SEND_ERRORS", "max_send_errors", "Errors before a lead is failed", CAT_SENDING),
    VarSpec("STALE_CLAIM_SECONDS", "stale_claim_seconds", "Stale 'sending' claim recovery (seconds)", CAT_SENDING),
]

BY_KEY: dict[str, VarSpec] = {spec.env_name: spec for spec in REGISTRY}

# The .env/default value of every variable, captured at import BEFORE any
# override is applied — the fallback `apply_overrides` reverts to on reset.
_BASELINE: dict[str, str] = {
    spec.env_name: str(getattr(settings, spec.settings_attr) or "") for spec in REGISTRY
}


def _rows(session: Session) -> dict[str, str]:
    from awkns_outreach.db.models import AppSetting  # avoid import cycle at module load

    return {r.key: r.value for r in session.scalars(select(AppSetting)).all()}


def _effective(rows: dict[str, str], spec: VarSpec) -> str:
    return rows.get(spec.env_name, _BASELINE[spec.env_name])


def apply_overrides(session: Session) -> None:
    """Push effective values (baseline + DB overrides) into the live settings
    singleton and os.environ. Idempotent — safe to call at every process
    startup and after every save."""
    rows = _rows(session)
    for spec in REGISTRY:
        value = _effective(rows, spec)
        setattr(settings, spec.settings_attr, value)
        os.environ[spec.env_name] = value


def effective_values(session: Session) -> dict[str, str]:
    """{env_name: effective value} for rendering the Variables form."""
    rows = _rows(session)
    return {spec.env_name: _effective(rows, spec) for spec in REGISTRY}


def overridden_keys(session: Session) -> set[str]:
    """env_names that currently have a DB override (i.e. diverge from .env)."""
    return set(_rows(session).keys()) & set(BY_KEY.keys())


def set_override(session: Session, key: str, value: str) -> None:
    """Upsert an override row. Caller commits."""
    from awkns_outreach.db.models import AppSetting

    if key not in BY_KEY:
        raise KeyError(f"Unknown variable: {key}")
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def clear_override(session: Session, key: str) -> None:
    """Delete an override row (revert to the .env baseline). Caller commits."""
    from awkns_outreach.db.models import AppSetting

    row = session.get(AppSetting, key)
    if row is not None:
        session.delete(row)


def mask_secret(value: str) -> str:
    """Never expose a full secret to the browser — show only the last 4 chars."""
    if not value:
        return "(not set)"
    return "••••" + value[-4:] if len(value) > 4 else "••••"
