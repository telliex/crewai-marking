"""Minimal Apollo.io client for BD lead enrichment.

Port of yoh's lib/outreach/apollo.ts. Two-step flow:
  1. search_people() — find decision-makers by title at target company domains.
     Emails come back MASKED ("email_not_unlocked@domain.com"). FREE.
  2. bulk_match()    — enrich up to 10 people per call, unlocking verified work
     emails. THIS spends Apollo enrichment credits.

Apollo is the ONLY external dependency: it gives us "who decides here" + "their
email", and nothing else. Sequencing, CRM, copy, and sending are all ours.

Auth: header `X-Api-Key: $APOLLO_API_KEY`, read from the environment.
Docs: https://docs.apollo.io/reference/people-api-search
      https://docs.apollo.io/reference/bulk-people-enrichment
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from awkns_outreach.config import settings

BASE = "https://api.apollo.io/api/v1"
_TIMEOUT = httpx.Timeout(30.0)


def _api_key() -> str:
    key = settings.apollo_api_key
    if not key:
        raise RuntimeError("APOLLO_API_KEY is not set")
    return key


def _call(path: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = httpx.post(
        f"{BASE}{path}",
        json=body,
        headers={
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "Accept": "application/json",
            "X-Api-Key": _api_key(),
        },
        timeout=_TIMEOUT,
    )
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    if resp.status_code >= 400:
        detail = data.get("error") if isinstance(data, dict) else None
        raise RuntimeError(
            f"Apollo {path} {resp.status_code}: {detail or resp.text[:200]}"
        )
    return data


@dataclass
class ApolloPerson:
    id: str
    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    email_status: Optional[str] = None
    linkedin_url: Optional[str] = None
    seniority: Optional[str] = None
    organization: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "ApolloPerson":
        return cls(
            id=d.get("id", ""),
            name=d.get("name"),
            first_name=d.get("first_name"),
            last_name=d.get("last_name"),
            title=d.get("title"),
            email=d.get("email"),
            email_status=d.get("email_status"),
            linkedin_url=d.get("linkedin_url"),
            seniority=d.get("seniority"),
            organization=d.get("organization") or {},
        )


def has_real_email(email: Optional[str]) -> bool:
    """Apollo masks locked emails as email_not_unlocked@…. True only for a real one."""
    e = (email or "").lower()
    if not e or "@" not in e:
        return False
    if "email_not_unlocked" in e:
        return False
    if e.endswith("@domain.com"):
        return False
    return True


def search_people(
    domains: list[str],
    titles: list[str],
    locations: Optional[list[str]] = None,
    page: int = 1,
    per_page: int = 10,
) -> tuple[list[ApolloPerson], int]:
    """Search people by titles at the given company domains. Emails come masked.
    FREE — spends no credits."""
    body: dict[str, Any] = {
        "q_organization_domains_list": domains,
        "person_titles": titles,
        "contact_email_status": ["verified", "likely to engage", "unverified"],
        "page": page,
        "per_page": per_page,
    }
    if locations:
        body["person_locations"] = locations
    data = _call("/mixed_people/api_search", body)
    raw = data.get("people") or data.get("contacts") or []
    people = [ApolloPerson.from_api(p) for p in raw]
    total = (data.get("pagination") or {}).get("total_entries", len(people))
    return people, total


def bulk_match(ids: list[str], reveal_personal: bool = False) -> list[ApolloPerson]:
    """Enrich up to 10 people by Apollo id, unlocking verified work emails.
    SPENDS one credit per matched person. `reveal_personal` pulls personal
    emails too (extra credits) — usually leave off for B2B."""
    if not ids:
        return []
    path = "/people/bulk_match"
    if reveal_personal:
        path += "?reveal_personal_emails=true"
    data = _call(path, {"details": [{"id": i} for i in ids[:10]]})
    raw = data.get("matches") or data.get("people") or []
    return [ApolloPerson.from_api(p) for p in raw if p]


def domain_from_website(website: Optional[str]) -> Optional[str]:
    """Extract a bare registrable domain from a website URL (no scheme/www)."""
    if not website:
        return None
    try:
        url = website if website.startswith("http") else f"https://{website}"
        host = urlparse(url).hostname or ""
        return host[4:] if host.startswith("www.") else host or None
    except Exception:
        return None
