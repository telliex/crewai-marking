"""Command-line driver for the outreach funnel.

    uv run outreach list
    uv run outreach enrich <campaign_id> --limit 10          # search only (free)
    uv run outreach enrich <campaign_id> --limit 10 --reveal # unlock emails (credits)
    uv run outreach angles <campaign_id>                     # AI-write per-lead angle
    uv run outreach run <campaign_id>                        # DRY-RUN (default)
    uv run outreach run <campaign_id> --send                 # send for real
    uv run outreach run-all --send                           # every active campaign
    uv run outreach cron --interval 15 --send                # scheduled small batches
"""
from __future__ import annotations

import logging

import typer

from awkns_outreach.db.models import Campaign
from awkns_outreach.db.session import session_scope

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = typer.Typer(add_completion=False, help="Awkns cold-outreach funnel.")


def _get(session, campaign_id: str) -> Campaign:
    c = session.get(Campaign, campaign_id)
    if not c:
        typer.secho(f"Campaign {campaign_id} not found", fg="red")
        raise typer.Exit(1)
    return c


@app.command("list")
def list_campaigns() -> None:
    """List campaigns and their lead counts."""
    from sqlalchemy import func, select
    from awkns_outreach.db.models import Lead

    with session_scope() as s:
        for c in s.scalars(select(Campaign).order_by(Campaign.created_at)).all():
            n = s.scalar(select(func.count()).select_from(Lead).where(Lead.campaign_id == c.id))
            typer.echo(f"{c.id}  {c.name}  ({n} leads, {len(c.sequence or [])} steps)")


@app.command()
def enrich(
    campaign_id: str,
    limit: int = typer.Option(10, help="Max people to pull."),
    reveal: bool = typer.Option(False, help="Unlock verified emails (spends Apollo credits)."),
) -> None:
    """Find decision-makers via Apollo; --reveal unlocks emails and creates leads."""
    from awkns_outreach.apollo.enrich import enrich_campaign

    with session_scope() as s:
        c = _get(s, campaign_id)
        summary = enrich_campaign(s, c, reveal=reveal, limit=limit)
    if reveal:
        typer.echo(f"found={summary.total_found} unlocked={summary.unlocked} "
                   f"created={summary.created} skipped={summary.skipped_existing}")
    else:
        typer.echo(f"preview: {summary.total_found} found (no credits spent):")
        for cand in summary.candidates:
            typer.echo(f"  - {cand['name']} · {cand['title']} @ {cand['company']} [{cand['email_status']}]")


@app.command()
def angles(campaign_id: str, limit: int = typer.Option(20)) -> None:
    """AI-generate the personalized `angle` for leads that don't have one."""
    from awkns_outreach.writer.angle import backfill_campaign_angles

    with session_scope() as s:
        c = _get(s, campaign_id)
        n = backfill_campaign_angles(s, c, limit=limit)
    typer.echo(f"angles written: {n}")


@app.command()
def run(
    campaign_id: str,
    send: bool = typer.Option(False, help="Send for real (default is a dry run)."),
    max_this_run: int = typer.Option(5, "--max", help="Cap sends this invocation."),
    ignore_hours: bool = typer.Option(False, help="Bypass the business-hours gate (testing/manual)."),
) -> None:
    """Advance one campaign's sequence. DRY-RUN unless --send."""
    from awkns_outreach.sequencer import process_campaign

    with session_scope() as s:
        c = _get(s, campaign_id)
        summary = process_campaign(s, c, dry_run=not send, max_this_run=max_this_run,
                                   ignore_hours=ignore_hours)
    if summary.blocked:
        typer.secho(f"BLOCKED: {summary.blocked}", fg="red")
        raise typer.Exit(1)
    typer.echo(f"{'SENT' if send else 'DRY-RUN'}: sent={summary.sent} skipped={summary.skipped} "
               f"suppressed={summary.suppressed} errors={summary.errors} "
               f"(cap={summary.cap} remaining={summary.daily_remaining})")


@app.command("run-all")
def run_all(
    send: bool = typer.Option(False),
    max_this_run: int = typer.Option(5, "--max"),
) -> None:
    """Advance every active campaign once."""
    from awkns_outreach.runner import run_all_campaigns

    with session_scope() as s:
        results = run_all_campaigns(s, dry_run=not send, max_this_run=max_this_run)
    for c, summary in results:
        typer.echo(f"[{c.name}] sent={summary.sent} skipped={summary.skipped} "
                   f"errors={summary.errors} blocked={summary.blocked or '-'}")


@app.command()
def cron(
    interval: int = typer.Option(15, help="Minutes between ticks."),
    send: bool = typer.Option(False),
    max_per_tick: int = typer.Option(5, "--max"),
) -> None:
    """Run the scheduler (blocks). Small batches per tick; cadence gives spacing."""
    from awkns_outreach import scheduler

    scheduler.start(interval_minutes=interval, send=send, max_per_tick=max_per_tick)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
