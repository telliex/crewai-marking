"""Read-only rollups for the dashboard and cron summaries."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from awkns_outreach.db.models import Campaign, Event, Lead
from awkns_outreach.sequencer.limits import SEND, warmup_cap

_STATUSES = [
    "active", "sending", "completed", "replied",
    "bounced", "suppressed", "paused", "failed",
]


def campaign_stats(session: Session, campaign: Campaign, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    by_status = dict(
        session.execute(
            select(Lead.status, func.count())
            .where(Lead.campaign_id == campaign.id)
            .group_by(Lead.status)
        ).all()
    )
    since = now - timedelta(hours=24)
    sent_last_24h = session.scalar(
        select(func.count()).select_from(Event)
        .join(Lead, Event.lead_id == Lead.id)
        .where(Lead.campaign_id == campaign.id, Event.type == "sent", Event.created_at >= since)
    ) or 0
    cap = min(SEND.hard_daily_cap, warmup_cap(campaign.warmup_start, now))
    return {
        "total": sum(by_status.values()),
        "by_status": {s: by_status.get(s, 0) for s in _STATUSES},
        "sent_last_24h": sent_last_24h,
        "cap": cap,
        "daily_remaining": max(0, cap - sent_last_24h),
        "steps": len(campaign.sequence or []),
    }
