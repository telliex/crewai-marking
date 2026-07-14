# Manual lead conversion from seed companies (no-Apollo path)

Date: 2026-07-13
Status: draft — pending user review

## Goal

Let an operator skip Apollo entirely (e.g. no paid API access) and turn
already-entered seed-company rows directly into `Lead` rows, provided each
row carries an email. Reuses the existing seed-company edit/import UI
instead of building a separate paste box.

Out of scope:
- No paste-box / separate import UI on the campaign page — the "Convert"
  action reads the campaign's already-saved `seed_companies` only.
- No partial conversion — if any seed-company row is missing `email` (or
  `name`) or two rows share an email, zero leads are created.
- No mutation of `seed_companies` after conversion (non-destructive, same
  as Apollo enrich never touches it).

## 1. Seed-company schema gains contact fields

`Campaign.seed_companies` (`db/models.py:58-62`) is a JSON list of dicts;
no DB migration needed (JSON column, no fixed columns). Every place that
defines/reads/renders its shape gains three new optional keys: `email`,
`contact_name`, `contact_title`.

- `apollo/seed.py`:
  - `SEED_FIELDS` (line 21): `("name", "website", "country", "category", "tier", "angle")`
    → `("name", "website", "country", "category", "tier", "angle", "email", "contact_name", "contact_title")`.
  - `_ALIASES` (lines 24-37): add `"email": "email"`, `"contact_name": "contact_name"`,
    `"contact": "contact_name"`, `"contact_title": "contact_title"`, `"title": "contact_title"`.
  - `_clean_row` / `parse_seed_companies`: unchanged logic — new keys flow through
    the same alias-matching, trim, and blank-row-drop rules already applied
    to every other field.
- `seed_companies_edit.html`: `company_row` macro gains 3 more `<input>`
  cells (`email`, `contact_name`, `contact_title`) and 3 more `<th>`
  headers, same styling/placeholder convention as the existing columns
  (e.g. placeholder `jamie@toyota.co.jp`, `Jamie Rivera`, `VP Finance`).
- `new_campaign.html`: the "Seed companies" help text's column list
  updated to include the 3 new columns.
- CSV template route (`GET /campaigns/seed-template.csv`, `admin.py`
  around line 116's `csv.DictWriter(buf, fieldnames=SEED_FIELDS)`):
  automatically picks up the new columns since it already writes
  `fieldnames=SEED_FIELDS` — no separate change needed beyond the
  `SEED_FIELDS` tuple update above.

## 2. Convert-to-leads action

### 2.1 Campaign page card

New card in the `campaign.html` flex row (`campaign.html:53-68`), same
`rounded border bg-white p-3` sizing as "Apollo enrich"/"AI classify".
Rendered only when `leads|length == 0`:

```html
{% if not leads %}
<form method="post" action="/campaigns/{{ c.id }}/leads/from-seed-companies" class="rounded border bg-white p-3">
  <div class="text-xs font-medium text-slate-500 mb-2">Convert seed companies to leads</div>
  <p class="text-xs text-slate-400 mb-2">Skip Apollo — every seed company needs an email first (edit them on the Seed companies page).</p>
  <button class="rounded bg-slate-900 text-white text-xs px-2 py-1">Convert</button>
</form>
{% endif %}
```

### 2.2 Route: `POST /campaigns/{id}/leads/from-seed-companies`

New route in `admin.py`, alongside `run_enrich`/`run_classify`:

```python
@router.post("/campaigns/{campaign_id}/leads/from-seed-companies")
def convert_seed_companies_to_leads(
    campaign_id: str,
    db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    rows = c.seed_companies or []
    if not rows:
        return RedirectResponse(f"/campaigns/{c.id}?msg=No seed companies to convert.", status_code=303)

    missing = [r.get("name") or "(unnamed)" for r in rows if not (r.get("name") and r.get("email"))]
    emails = [r["email"].strip().lower() for r in rows if r.get("email")]
    dupes = {e for e in emails if emails.count(e) > 1}
    if missing or dupes:
        parts = []
        if missing:
            parts.append(f"missing name/email: {', '.join(missing)}")
        if dupes:
            parts.append(f"duplicate email: {', '.join(sorted(dupes))}")
        return RedirectResponse(
            f"/campaigns/{c.id}?msg=Convert failed — {'; '.join(parts)}.", status_code=303,
        )

    for r in rows:
        db.add(Lead(
            campaign_id=c.id, email=r["email"].strip().lower(), company=r["name"].strip(),
            contact_name=r.get("contact_name") or None, contact_title=r.get("contact_title") or None,
            country=r.get("country") or None, category=r.get("category") or None,
            tier=r.get("tier") or None, angle=r.get("angle") or None, website=r.get("website") or None,
            step=0, status="active",
        ))
    db.commit()
    return RedirectResponse(f"/campaigns/{c.id}?msg=Converted {len(rows)} seed compan{'y' if len(rows)==1 else 'ies'} to leads.", status_code=303)
```

Validation is pre-flight and all-or-nothing: either every row is valid and
all leads are created, or none are (matches the user's explicit
requirement — a popup-style blocking message via the existing `msg`
redirect-banner convention, not a partial/skip-invalid-rows behavior).

## Testing

- `apollo/seed.py` parsing: new columns round-trip through both CSV and
  JSON input (`tests/test_seed.py`, new cases for `email`/`contact_name`/
  `contact_title` aliases, matching that file's existing per-alias test
  style).
- `seed_companies_edit.html` renders the 3 new columns and pre-fills
  existing values (`tests/test_web.py`, alongside existing seed-companies
  edit-page tests).
- New route tests (`tests/test_web.py`, alongside existing
  `run_enrich`/`run_classify` route tests):
  - Zero seed companies → redirect with "No seed companies to convert."
  - A row missing email → redirect with failure message, zero `Lead` rows
    created (transaction rolled back / never added).
  - Two rows sharing an email → redirect with duplicate-email failure
    message, zero `Lead` rows created.
  - All rows valid → one `Lead` per row created with fields mapped
    correctly, redirect with success message.
  - Card only renders on `campaign.html` when `leads` is empty.
