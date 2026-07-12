"""AI tier classifier — batch-grades a campaign's leads into A/B/C fit tiers.

Uses the bare Anthropic SDK (not the LiteLLM-style `crew_model` the writer/
angle.py research path uses) via `settings.tier_model`. Runs synchronously
inside `POST /campaigns/{id}/classify` — no background job, no CrewAI. A
manually- or previously-set `Lead.tier` is left alone by default; only
`reclassify_all=True` overwrites it (see `classify_campaign_tiers`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, Lead

TIERS = ("A", "B", "C")
BATCH_SIZE = 40

SYSTEM_PROMPT = (
    "You grade leads for a B2B cold-outreach campaign into three fit tiers.\n\n"
    "A = senior decision-maker (e.g. founder, C-level, VP, director/head of a "
    "relevant function) at a company that fits the campaign's target well.\n"
    "B = plausible fit or influence — a mid-level or adjacent role, or a "
    "company that's a decent but not ideal fit.\n"
    "C = weak fit or a junior/individual-contributor role with little buying "
    "influence.\n\n"
    "Use the lead's title, seniority, industry, employee_count, country, and "
    "company, weighed against the campaign context below, to make the call.\n\n"
    "Respond with ONLY a JSON object mapping each lead id to exactly one of "
    '"A", "B", or "C" — no prose, no code fences, no extra keys.'
)


@dataclass
class TierSummary:
    examined: int = 0
    classified: int = 0
    per_tier: dict[str, int] = field(default_factory=dict)
    skipped: int = 0  # malformed/unknown entries returned by the model
    errors: int = 0  # failed batches (API/JSON errors)


def _campaign_context(campaign: Campaign) -> str:
    lines = [f"Campaign: {campaign.name}"]
    if campaign.description:
        lines.append(f"Description: {campaign.description}")
    if campaign.target_titles:
        lines.append(f"Target titles: {', '.join(campaign.target_titles)}")
    return "\n".join(lines)


def _parse_response(text: str, valid_ids: set[str]) -> dict[str, str]:
    """Parse the model's id->tier JSON, tolerating an optional ``` / ```json
    fence around it. Pairs whose id isn't in `valid_ids` or whose value isn't
    in TIERS are silently dropped (the caller counts those as skipped).
    Unparseable JSON is allowed to raise — the caller counts that as a failed
    batch."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped[3:]
        if stripped[:4].lower() == "json":
            stripped = stripped[4:]
        stripped = stripped.strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()

    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object mapping lead id -> tier")

    return {
        str(lead_id): tier
        for lead_id, tier in data.items()
        if str(lead_id) in valid_ids and tier in TIERS
    }


def classify_batch(
    rows: list[dict], *, campaign_context: str, model: Optional[str] = None,
) -> dict[str, str]:
    """Send one batch of lead rows to the Anthropic API and return the parsed
    id->tier mapping.

    Imports the `anthropic` SDK lazily so paths that never classify (most web
    requests) don't pay its import cost — same rationale as writer/angle.py's
    lazy `crewai` import.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    valid_ids = {str(row["id"]) for row in rows}
    system = f"{SYSTEM_PROMPT}\n\nCampaign context:\n{campaign_context}"
    response = client.messages.create(
        model=model or settings.tier_model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": json.dumps(rows, ensure_ascii=False)}],
    )
    text = response.content[0].text
    return _parse_response(text, valid_ids)


def classify_campaign_tiers(
    session: Session,
    campaign: Campaign,
    *,
    reclassify_all: bool = False,
    batch_size: int = BATCH_SIZE,
    limit: int = 500,
    classify: Callable[..., dict[str, str]] = None,
) -> TierSummary:
    """Batch-classify a campaign's leads into A/B/C tiers.

    Default filter only selects leads with `tier IS NULL`, so an operator's
    manual edits (or a previous classify pass) are never overwritten unless
    `reclassify_all=True`. `classify` is injectable (same pattern as
    angle.py's `generate` param on `backfill_campaign_angles`) so tests never
    hit the real API. A failing batch (exception from `classify`, e.g. a bad
    API response or unparseable JSON) increments `errors` and is skipped;
    later batches still run. One `session.commit()` at the end.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    classify = classify or classify_batch

    stmt = select(Lead).where(Lead.campaign_id == campaign.id)
    if not reclassify_all:
        stmt = stmt.where(Lead.tier.is_(None))
    leads = session.scalars(
        stmt.order_by(Lead.created_at, Lead.id).limit(limit)
    ).all()

    context = _campaign_context(campaign)
    summary = TierSummary(examined=len(leads))

    for start in range(0, len(leads), batch_size):
        batch = leads[start : start + batch_size]
        rows = [
            {
                "id": lead.id,
                "title": lead.contact_title or "",
                "seniority": lead.seniority or "",
                "industry": lead.category or "",
                "employee_count": lead.employee_count,
                "country": lead.country or "",
                "company": lead.company or "",
            }
            for lead in batch
        ]
        try:
            result = classify(rows, campaign_context=context)
        except Exception:
            summary.errors += 1
            continue

        by_id = {lead.id: lead for lead in batch}
        matched = 0
        for lead_id, tier in result.items():
            lead = by_id.get(lead_id)
            if lead is None or tier not in TIERS:
                continue
            lead.tier = tier
            matched += 1
            summary.classified += 1
            summary.per_tier[tier] = summary.per_tier.get(tier, 0) + 1
        summary.skipped += len(batch) - matched

    session.commit()
    return summary
