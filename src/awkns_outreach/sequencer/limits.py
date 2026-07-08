"""Sending caps, warmup ramp, timezone map, and the business-hours gate.

Port of the non-copy bits of yoh's config.ts. These are global defaults; warmup
is per-campaign (each campaign's sending domain warms up on its own schedule via
Campaign.warmup_start).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo


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
    if warmup_start is None:
        return SEND.warmup_ramp[0]
    if warmup_start.tzinfo is None:
        warmup_start = warmup_start.replace(tzinfo=timezone.utc)
    days = (now - warmup_start).days
    if days < 0:
        return 0
    if days < len(SEND.warmup_ramp):
        return SEND.warmup_ramp[days]
    return SEND.hard_daily_cap


def in_business_hours(now: datetime, country: Optional[str]) -> bool:
    """True if `now` falls inside the recipient's local Mon–Fri 09:00–17:00 window."""
    local = now.astimezone(ZoneInfo(tz_for(country)))
    return (
        local.weekday() in SEND.send_days
        and SEND.send_hours[0] <= local.hour < SEND.send_hours[1]
    )
