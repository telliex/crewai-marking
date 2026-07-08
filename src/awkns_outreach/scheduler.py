"""Small-batch cron: every tick, advance each active campaign by a few sends.

The tick cadence provides the human-scale spacing (so gap_ms=0 here — no in-tick
sleeps), mirroring yoh's cron mode. Start conservative and manual (CLI) for the
first week of a new sending domain, then turn this on.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from awkns_outreach.db.session import session_scope
from awkns_outreach.runner import run_all_campaigns

log = logging.getLogger("awkns_outreach.scheduler")


def tick(*, send: bool, max_per_tick: int) -> None:
    with session_scope() as session:
        results = run_all_campaigns(
            session, dry_run=not send, max_this_run=max_per_tick, gap_ms=0
        )
    for campaign, s in results:
        if s.blocked:
            log.warning("[%s] blocked: %s", campaign.name, s.blocked)
        elif s.sent or s.errors or s.suppressed:
            log.info(
                "[%s] sent=%d skipped=%d suppressed=%d errors=%d (cap=%d left=%d)",
                campaign.name, s.sent, s.skipped, s.suppressed, s.errors,
                s.cap, s.daily_remaining,
            )


def start(*, interval_minutes: int = 15, send: bool = False, max_per_tick: int = 5) -> None:
    mode = "SEND" if send else "DRY-RUN"
    log.info("Scheduler starting — every %dm, %s, %d/tick", interval_minutes, mode, max_per_tick)
    scheduler = BlockingScheduler()
    scheduler.add_job(
        lambda: tick(send=send, max_per_tick=max_per_tick),
        "interval", minutes=interval_minutes,
    )
    scheduler.start()
