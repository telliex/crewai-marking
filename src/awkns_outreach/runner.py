"""Drive the sequencer across all running Tasks — shared by the CLI and the
cron scheduler so both behave identically."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.db.models import Campaign, Task
from awkns_outreach.sequencer import RunSummary, process_campaign


def run_all_campaigns(
    session: Session,
    *,
    dry_run: bool = True,
    max_this_run: int = 5,
    gap_ms: Optional[int] = None,
    now: Optional[datetime] = None,
) -> list[tuple[Campaign, RunSummary]]:
    tasks = session.scalars(
        select(Task).where(Task.status == "running")
    ).all()
    results: list[tuple[Campaign, RunSummary]] = []
    for task in tasks:
        summary = process_campaign(
            session, task.campaign, task.steps_by_tier, dry_run=dry_run,
            max_this_run=max_this_run, gap_ms=gap_ms, now=now,
        )
        results.append((task.campaign, summary))
    return results
