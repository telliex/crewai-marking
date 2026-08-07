"""Sending caps, warmup ramp, timezone map, and the business-hours gate.

Port of the non-copy bits of yoh's config.ts. These are global defaults; warmup
is per-campaign (each campaign's sending domain warms up on its own schedule via
Campaign.warmup_start).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from awkns_outreach.config import settings


@dataclass(frozen=True)
class SendLimits:
    # Absolute ceiling per 24h on ONE sending domain. Past ~100/day, add more
    # sending domains — do not raise this.
    hard_daily_cap: int = 100
    # Max sends on day N since warmup_start (0-indexed). Ramps a new domain's
    # reputation from 5/day → 100/day over ~2.5 weeks. Until warmup_start is set,
    # the cap stays at warmup_ramp[0] (ultra conservative).
    warmup_ramp: tuple[int, ...] = (5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100)
    send_hours: tuple[int, int] = (9, 17)  # [start, end) in recipient local TZ
    send_days: tuple[int, ...] = (0, 1, 2, 3, 4)  # Mon–Fri (Python weekday: Mon=0)
    min_gap_ms: int = 90_000
    jitter_ms: int = 150_000


SEND = SendLimits()


# --- Operator-tunable accessors -------------------------------------------
# These read the live (DB-overridable) settings and parse the string values,
# falling back to the SEND defaults above if unset or malformed. Call them at
# use time (not import) so a Variables-page save takes effect immediately.

def _int(value: object, default: int) -> int:
    try:
        v = int(str(value).strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _int_tuple(value: object, default: tuple[int, ...]) -> tuple[int, ...]:
    try:
        parts = [int(x) for x in str(value).replace(" ", "").split(",") if x != ""]
        return tuple(parts) if parts else default
    except (TypeError, ValueError):
        return default


def daily_cap() -> int:
    """Absolute per-campaign 24h ceiling."""
    return _int(settings.warmup_daily_cap, SEND.hard_daily_cap)


def ramp() -> tuple[int, ...]:
    """Per-day cap curve from warmup_start (day 0, day 1, … then hold)."""
    return _int_tuple(settings.warmup_ramp, SEND.warmup_ramp)


def send_window_hours() -> tuple[int, int]:
    """Local send window as [start, end) 24h. Falls back if malformed."""
    try:
        a, b = str(settings.send_hours).split("-")
        a, b = int(a), int(b)
        if 0 <= a < b <= 24:
            return (a, b)
    except (TypeError, ValueError):
        pass
    return SEND.send_hours


def send_workdays() -> tuple[int, ...]:
    """Allowed weekdays (0=Mon … 6=Sun). Falls back if empty/malformed."""
    days = tuple(d for d in _int_tuple(settings.send_days, SEND.send_days) if 0 <= d <= 6)
    return days or SEND.send_days


def min_gap_ms() -> int:
    return _int(settings.send_min_gap_ms, SEND.min_gap_ms)


def jitter_ms() -> int:
    return _int(settings.send_jitter_ms, SEND.jitter_ms)


def initial_warmup_start(now: datetime) -> Optional[datetime]:
    """The warmup_start to stamp on a NEW campaign, per WARMUP_DEFAULT_MODE:
    "warm" → now (ramp from day 0); "full" → far enough back to be at full
    speed immediately; "none" → None (stays at the ultra-conservative floor)."""
    mode = str(settings.warmup_default_mode).strip().lower()
    if mode == "none":
        return None
    if mode == "full":
        return now - timedelta(days=len(ramp()))
    return now  # "warm" (default)


_TZ: dict[str, str] = {
    "JP": "Asia/Tokyo", "JAPAN": "Asia/Tokyo",
    "KR": "Asia/Seoul", "KOREA": "Asia/Seoul", "SOUTH KOREA": "Asia/Seoul",
    "TW": "Asia/Taipei", "TAIWAN": "Asia/Taipei",
    "US": "America/Los_Angeles", "USA": "America/Los_Angeles",
    "CN": "Asia/Shanghai", "CHINA": "Asia/Shanghai",
    "HK": "Asia/Hong_Kong", "HONG KONG": "Asia/Hong_Kong",
    "SG": "Asia/Singapore", "SINGAPORE": "Asia/Singapore",
}


def tz_for(country: Optional[str]) -> str:
    return _TZ.get((country or "").strip().upper(), "Asia/Taipei")


def warmup_cap(warmup_start: Optional[datetime], now: datetime) -> int:
    """Max sends allowed today given how long this domain has been warming up."""
    curve = ramp()
    if warmup_start is None:
        return curve[0]
    if warmup_start.tzinfo is None:
        warmup_start = warmup_start.replace(tzinfo=timezone.utc)
    days = (now - warmup_start).days
    if days < 0:
        return 0
    if days < len(curve):
        return curve[days]
    return daily_cap()


def in_send_window(
    now: datetime, country: Optional[str], *,
    ignore_hours: bool = False, ignore_days: bool = False,
) -> bool:
    """True if `now` is inside the recipient's local send window. By default
    that's Mon–Fri 09:00–17:00; `ignore_days` drops the weekday check and
    `ignore_hours` drops the time-of-day check (each independently)."""
    local = now.astimezone(ZoneInfo(tz_for(country)))
    hours = send_window_hours()
    day_ok = ignore_days or local.weekday() in send_workdays()
    hour_ok = ignore_hours or hours[0] <= local.hour < hours[1]
    return day_ok and hour_ok
