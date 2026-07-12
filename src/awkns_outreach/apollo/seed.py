"""Parse a seed-company list (companies.json shape) from uploaded/pasted text.

A campaign's `seed_companies` is a list of dicts, all fields optional except
that `website` is what we derive the Apollo query domain from. Operators feed it
in as a JSON array (matching yoh's companies.json) or a CSV — this module
normalizes either into the canonical dict shape.

Field aliases are tolerated (domain→website, industry→category) so a CSV
exported from Apollo/enrich.ts or a hand-written JSON both import cleanly.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Optional

from awkns_outreach.apollo.client import domain_from_website

# Canonical seed fields, in a stable order (used by the edit form too).
SEED_FIELDS = ("name", "website", "country", "category", "tier", "angle")

# Accepted column/key aliases -> canonical field.
_ALIASES = {
    "name": "name",
    "company": "name",
    "company_name": "name",
    "website": "website",
    "domain": "website",
    "url": "website",
    "country": "country",
    "category": "category",
    "industry": "category",
    "priority": "tier",
    "tier": "tier",
    "angle": "angle",
}


def _clean_row(raw: dict[str, Any]) -> Optional[dict[str, str]]:
    """Normalize one raw record into a canonical seed dict, or None if empty.

    Keys are matched case-insensitively via _ALIASES; values are trimmed. The
    website is normalized to a bare domain so the Apollo query is consistent.
    """
    row: dict[str, str] = {}
    for key, value in raw.items():
        if key is None:
            continue
        field = _ALIASES.get(str(key).strip().lower())
        if not field or field in row:
            continue
        text = str(value).strip() if value is not None else ""
        if text:
            row[field] = text
    website = row.get("website")
    if website:
        normalized = domain_from_website(website)
        if normalized:
            row["website"] = normalized
    return row or None


def parse_seed_companies(raw: str, filename: Optional[str] = None) -> list[dict[str, str]]:
    """Parse pasted/uploaded seed text (JSON array or CSV) into seed dicts.

    Format is chosen by extension when a filename is given, else sniffed from
    the content (leading `[`/`{` → JSON). Blank rows are dropped. Raises
    ValueError on malformed JSON so the caller can surface it to the operator.
    """
    text = (raw or "").strip()
    if not text:
        return []

    is_json = False
    if filename:
        is_json = filename.lower().endswith(".json")
    else:
        is_json = text[0] in "[{"

    records: list[dict[str, Any]]
    if is_json:
        data = json.loads(text)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            raise ValueError("JSON seed must be an object or an array of objects")
        records = [r for r in data if isinstance(r, dict)]
    else:
        reader = csv.DictReader(io.StringIO(text))
        records = [dict(r) for r in reader]

    out: list[dict[str, str]] = []
    for record in records:
        cleaned = _clean_row(record)
        if cleaned:
            out.append(cleaned)
    return out
