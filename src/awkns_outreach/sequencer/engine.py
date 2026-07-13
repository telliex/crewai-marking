"""The outreach engine — one campaign at a time.

Port of yoh's sequencer.ts. Picks due leads and, for each, enforces (in order):
the rolling-24h send cap (warmup-aware), the recipient's local business hours,
the global suppression list, a compare-and-swap CLAIM that prevents double-sends
under concurrency, and human-scale pacing — then advances the lead one step.

Defaults to DRY RUN. Nothing is sent unless dry_run is explicitly False.

Driven two ways (see cli.py / web cron):
  • CLI batch — larger max_this_run, self-paced with sleeps.
  • Cron tick — tiny max_this_run, no sleeps; the cron cadence gives the spacing.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from awkns_outreach.compliance import can_send_legally, is_suppressed, suppress
from awkns_outreach.db.models import Campaign, Event, Lead
from awkns_outreach.identity import resolve_identity
from awkns_outreach.send.mailer import send_outreach_email
from awkns_outreach.sequencer.limits import SEND, in_business_hours, warmup_cap

# Stop retrying a lead after this many send errors at the same step.
MAX_SEND_ERRORS = 3
# A "sending" claim older than this is considered crashed and reclaimable.
STALE_CLAIM_SECONDS = 10 * 60


def step_delay_minutes(step: dict) -> int:
    """Canonical read path for a step's delay. New saves write `delay_minutes`
    directly; any step dict that predates it (persisted `MailSequence.steps`
    or a `Task.steps_by_tier` snapshot taken before this field existed) only
    has `delay_days` — convert it rather than requiring a data migration."""
    if "delay_minutes" in step:
        return step["delay_minutes"]
    return step.get("delay_days", 0) * 1440


@dataclass
class RunSummary:
    dry_run: bool
    considered: int = 0
    sent: int = 0
    skipped: int = 0
    errors: int = 0
    suppressed: int = 0
    completed: int = 0
    cap: int = 0
    sent_last_24h: int = 0
    daily_remaining: int = 0
    blocked: Optional[str] = None
    details: list[dict[str, Any]] = field(default_factory=list)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def process_campaign(
    session: Session,
    campaign: Campaign,
    steps_by_tier: dict[str, list[dict[str, Any]]],
    *,
    dry_run: bool = True,
    max_this_run: int = 5,
    gap_ms: Optional[int] = None,
    ignore_hours: bool = False,
    now: Optional[datetime] = None,
) -> RunSummary:
    now = now or _utcnow()
    max_this_run = max(0, max_this_run)
    steps_by_tier = steps_by_tier or {}
    summary = RunSummary(dry_run=dry_run)

    if not any(steps_by_tier.values()):
        summary.blocked = "no steps"
        return summary

    if not dry_run:
        # A paused/archived campaign must not send for real even if the run
        # endpoint (or cron) fires. Dry-run previews stay allowed for any status.
        if campaign.status != "active":
            summary.blocked = f"campaign is {campaign.status}"
            return summary
        ok, reason = can_send_legally(resolve_identity(campaign.sender_identity))
        if not ok:
            summary.blocked = reason
            return summary
        # Recover leads stranded in a "sending" claim by a crashed prior run.
        stale_before = now - timedelta(seconds=STALE_CLAIM_SECONDS)
        session.execute(
            update(Lead)
            .where(Lead.campaign_id == campaign.id, Lead.status == "sending",
                   Lead.updated_at < stale_before)
            .values(status="active")
        )
        session.commit()

    # Rolling 24h send budget for THIS campaign (warmup-aware).
    since = now - timedelta(hours=24)
    sent_last_24h = session.scalar(
        select(func.count())
        .select_from(Event)
        .join(Lead, Event.lead_id == Lead.id)
        .where(Lead.campaign_id == campaign.id, Event.type == "sent",
               Event.created_at >= since)
    ) or 0
    cap = min(SEND.hard_daily_cap, warmup_cap(campaign.warmup_start, now))
    daily_remaining = max(0, cap - sent_last_24h)
    summary.cap = cap
    summary.sent_last_24h = sent_last_24h
    summary.daily_remaining = daily_remaining

    budget = min(max_this_run, daily_remaining)
    if budget <= 0:
        return summary

    candidates = session.scalars(
        select(Lead)
        .where(
            Lead.campaign_id == campaign.id,
            Lead.status == "active",
            (Lead.next_action_at.is_(None)) | (Lead.next_action_at <= now),
        )
        .order_by(
            func.coalesce(Lead.tier, "B").asc(),  # "A" < "B" < "C"; NULL tier counts as "B"
            Lead.next_action_at.asc(),
            Lead.created_at.asc(),
        )
        .limit(budget * 5)  # overfetch — many get filtered by hours/suppression
    ).all()

    real_send_done = False
    for lead in candidates:
        if budget <= 0:
            break
        summary.considered += 1
        email = lead.email

        # No sequence assigned to this lead's tier (partial assignment, or
        # the lead's tier changed after the task started)? Park it rather
        # than sending nothing forever from the active pool.
        steps = steps_by_tier.get(lead.tier or "B")
        if not steps:
            lead.status = "paused"
            session.commit()
            summary.skipped += 1
            summary.details.append({"email": email, "result": "skipped:no-tier-sequence"})
            continue

        # Sequence finished?
        if lead.step >= len(steps):
            lead.status = "completed"
            session.commit()
            summary.completed += 1
            continue

        # Do-not-contact? Flip out of the pool WITHOUT relabeling the reason.
        if is_suppressed(session, email):
            session.execute(
                update(Lead)
                .where(Lead.email == email, Lead.status.in_(["active", "paused"]))
                .values(status="suppressed")
            )
            session.commit()
            summary.suppressed += 1
            summary.details.append({"email": email, "result": "suppressed"})
            continue

        # Recipient's local business hours?
        if not ignore_hours and not in_business_hours(now, lead.country):
            summary.skipped += 1
            summary.details.append({"email": email, "result": "skipped:hours"})
            continue

        # Claim this lead at this step (real sends only): compare-and-swap
        # active → sending. Losing the race ⇒ skip. This is what prevents
        # double-sends and the crash-retry double-send.
        if not dry_run:
            claimed = session.execute(
                update(Lead)
                .where(Lead.id == lead.id, Lead.step == lead.step, Lead.status == "active")
                .values(status="sending", updated_at=now)
            )
            session.commit()
            if claimed.rowcount == 0:
                summary.skipped += 1
                summary.details.append({"email": email, "result": "skipped:claimed"})
                continue

        # Human-scale spacing between real sends (not before the first).
        if not dry_run and real_send_done:
            gap = gap_ms if gap_ms is not None else (
                SEND.min_gap_ms + int(random.random() * SEND.jitter_ms)
            )
            if gap > 0:
                time.sleep(gap / 1000.0)

        res = send_outreach_email(lead, campaign, email, lead.step, steps, dry_run=dry_run)

        if res.ok:
            real_send_done = True
            budget -= 1
            summary.sent += 1
            summary.details.append({
                "email": email, "step": lead.step,
                "result": "dry-run" if dry_run else "sent", "subject": res.subject,
            })
            if not dry_run:
                next_step = lead.step + 1
                done = next_step >= len(steps)
                next_delay = None if done else step_delay_minutes(steps[next_step])
                session.add(Event(lead_id=lead.id, type="sent", step=lead.step,
                                  detail=res.id, subject=res.subject))
                lead.step = next_step
                lead.last_sent_at = now
                lead.status = "completed" if done else "active"  # releases the claim
                lead.thread_ref = lead.thread_ref or res.id
                lead.next_action_at = (
                    None if next_delay is None else now + timedelta(minutes=next_delay)
                )
                session.commit()
        else:
            summary.errors += 1
            summary.details.append({
                "email": email, "step": lead.step, "result": f"error:{res.error}",
            })
            if not dry_run:
                session.add(Event(lead_id=lead.id, type="error", step=lead.step,
                                  detail=(res.error or "")[:200]))
                session.flush()
                err_count = session.scalar(
                    select(func.count()).select_from(Event).where(
                        Event.lead_id == lead.id, Event.type == "error",
                        Event.step == lead.step,
                    )
                ) or 0
                # Park a permanently-bad address as "failed" so it leaves the pool;
                # otherwise release the claim for a retry next tick.
                lead.status = "failed" if err_count >= MAX_SEND_ERRORS else "active"
                session.commit()
                if err_count >= MAX_SEND_ERRORS:
                    summary.details.append(
                        {"email": email, "step": lead.step, "result": "failed:max_errors"}
                    )

    return summary
