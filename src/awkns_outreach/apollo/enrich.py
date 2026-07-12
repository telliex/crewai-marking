"""Turn a campaign's seed companies (+ titles) into leads via Apollo.

Two modes, mirroring yoh's enrich.ts:
  • preview (reveal=False) — search only, spends NO credits, writes NO leads.
    Returns masked candidates so an operator can eyeball them first.
  • reveal (reveal=True)   — bulk_match unlocks verified emails (spends credits),
    then upserts each real email as a Lead(step=0, status="active").

Per-company search: we query Apollo one seed domain at a time (like yoh's
discover.ts) so each returned person stays tied to its seed company — that lets
us carry the seed metadata (country/category/priority/angle) onto the lead.

Merge rule: seed metadata is the base; Apollo-returned facts (company/website/
country/category/contact/seniority/employee_count) overwrite where Apollo has
a value. Apollo has no tier/angle, so those always come from the seed. On
re-enrich an existing lead's Apollo facts are refreshed; tier/angle are only
backfilled if empty (never clobbering an AI-generated or hand-edited angle).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.apollo.client import (
    ApolloPerson,
    bulk_match,
    domain_from_website,
    has_real_email,
    search_people,
)
from awkns_outreach.db.models import Campaign, Lead

# Fallback decision-maker titles when a campaign sets none (yoh's TARGET_TITLES).
DEFAULT_TARGET_TITLES = [
    "creative director", "narrative director", "story lead", "content director",
    "ip development", "animation producer", "executive producer",
    "head of content", "business development", "marketing director",
    "brand", "innovation", "ai strategy",
]


@dataclass
class EnrichSummary:
    reveal: bool
    total_found: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    unlocked: int = 0
    created: int = 0
    updated: int = 0
    skipped_existing: int = 0


def _preview(p: ApolloPerson, seed: dict[str, Any]) -> dict[str, Any]:
    org = p.organization or {}
    return {
        "apollo_id": p.id,
        "name": p.name,
        "title": p.title,
        "company": org.get("name") or seed.get("name"),
        "email_status": p.email_status,
        "email_masked": p.email,
    }


def _seed_domain(seed: dict[str, Any]) -> Optional[str]:
    """A seed company's Apollo query domain, derived from its website field."""
    return domain_from_website(seed.get("website"))


def _search_candidates(
    campaign: Campaign, limit: int, per_page: int
) -> tuple[list[tuple[ApolloPerson, dict[str, Any]]], int]:
    """Search Apollo per seed company, keeping each person tied to its seed.

    Returns (person, seed) pairs up to `limit` total, plus the running total of
    people Apollo reported across the seed companies queried.
    """
    titles = campaign.target_titles or DEFAULT_TARGET_TITLES
    collected: list[tuple[ApolloPerson, dict[str, Any]]] = []
    total = 0
    for seed in campaign.seed_companies or []:
        if len(collected) >= limit:
            break
        domain = _seed_domain(seed)
        if not domain:
            continue  # no resolvable domain — can't query Apollo for this one
        people, found = search_people(
            domains=[domain], titles=titles, page=1, per_page=per_page
        )
        total += found
        for person in people:
            if len(collected) >= limit:
                break
            collected.append((person, seed))
    return collected, total


def _merge_fields(seed: dict[str, Any], person: ApolloPerson) -> dict[str, Any]:
    """Compute a lead's fields: seed metadata base, Apollo facts overwriting.

    Apollo has no tier/angle, so those come straight from the seed.
    """
    org = person.organization or {}
    company = org.get("name") or seed.get("name") or ""
    website = (
        org.get("website_url")
        or domain_from_website(org.get("primary_domain"))
        or seed.get("website")
    )
    return {
        "apollo_person_id": person.id,
        "company": company,
        "contact_name": person.name,
        "contact_title": person.title,
        "country": org.get("country") or seed.get("country"),
        "category": org.get("industry") or seed.get("category"),
        "website": website,
        "seniority": person.seniority,
        "employee_count": org.get("estimated_num_employees"),
        # Legacy seed JSON stored the field under "priority" — keep reading it.
        "tier": seed.get("tier") or seed.get("priority"),
        "angle": seed.get("angle"),
    }


def enrich_campaign(
    session: Session,
    campaign: Campaign,
    *,
    reveal: bool = False,
    limit: int = 10,
    per_page: int = 10,
) -> EnrichSummary:
    candidates, total = _search_candidates(campaign, limit, per_page)
    summary = EnrichSummary(
        reveal=reveal,
        total_found=total,
        candidates=[_preview(p, seed) for p, seed in candidates],
    )
    if not reveal:
        return summary

    # Unlock emails in batches of 10 (Apollo's bulk_match cap), then map each
    # matched person back to its seed via the Apollo id.
    seed_by_id = {p.id: seed for p, seed in candidates if p.id}
    ids = list(seed_by_id.keys())
    matched: list[ApolloPerson] = []
    for i in range(0, len(ids), 10):
        matched.extend(bulk_match(ids[i : i + 10]))
    summary.unlocked = sum(1 for p in matched if has_real_email(p.email))

    for person in matched:
        if not has_real_email(person.email):
            continue
        seed = seed_by_id.get(person.id, {})
        outcome = _upsert_lead(session, campaign, person, seed)
        if outcome == "created":
            summary.created += 1
        elif outcome == "updated":
            summary.updated += 1
        else:
            summary.skipped_existing += 1
    session.flush()
    return summary


def _upsert_lead(
    session: Session, campaign: Campaign, person: ApolloPerson, seed: dict[str, Any]
) -> str:
    """Insert or refresh a lead for this email. Returns "created" | "updated".

    New lead: all merged fields. Existing lead: refresh Apollo facts; only
    backfill tier/angle if they are still empty (don't clobber edits).
    """
    email = (person.email or "").strip().lower()
    fields = _merge_fields(seed, person)
    existing = session.scalar(
        select(Lead).where(Lead.campaign_id == campaign.id, Lead.email == email)
    )
    if existing is None:
        session.add(
            Lead(
                campaign_id=campaign.id,
                email=email,
                step=0,
                status="active",
                **fields,
            )
        )
        return "created"

    # Refresh Apollo-derived facts (overwrite only when Apollo has a value).
    for key in ("apollo_person_id", "company", "contact_name", "contact_title",
                "country", "category", "website", "seniority", "employee_count"):
        value = fields.get(key)
        if value:
            setattr(existing, key, value)
    # Seed-only fields: fill just when the lead has none yet.
    for key in ("tier", "angle"):
        if fields.get(key) and not getattr(existing, key):
            setattr(existing, key, fields[key])
    return "updated"
