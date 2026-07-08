"""Drive the sequencer across all active campaigns — shared by the CLI and the
cron scheduler so both behave identically."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.db.models import Campaign
from awkns_outreach.sequencer import RunSummary, process_campaign


def run_all_campaigns(
    session: Session,
    *,
    dry_run: bool = True,
    max_this_run: int = 5,
    gap_ms: Optional[int] = None,
    now: Optional[datetime] = None,
) -> list[tuple[Campaign, RunSummary]]:
    campaigns = session.scalars(
        select(Campaign).where(Campaign.status == "active")
    ).all()
    results: list[tuple[Campaign, RunSummary]] = []
    for c in campaigns:
        summary = process_campaign(
            session, c, dry_run=dry_run, max_this_run=max_this_run,
            gap_ms=gap_ms, now=now,
        )
        results.append((c, summary))
    return results
