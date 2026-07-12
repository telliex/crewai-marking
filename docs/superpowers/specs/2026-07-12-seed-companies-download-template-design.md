# Downloadable CSV template for seed-companies import

Date: 2026-07-12
Status: draft — pending user review

## Goal

The "Seed companies" import UI (file upload + paste box) appears in two
places — the New Campaign form and an existing campaign's Edit Companies
page — and both only describe the expected columns in a line of prose plus
a JSON example in the textarea placeholder. Users asked for a concrete
downloadable example file so they know exactly what to upload.

Add a "Download template (CSV)" link next to the import UI in both places,
pointing at a new endpoint that generates a CSV file with the accepted
column headers and a couple of example rows.

Out of scope (confirmed with the user):
- No JSON template file — CSV only.
- No change to the existing inline JSON placeholder text in the textareas.
- No change to the parser (`apollo/seed.py`) — the template is generated
  from its existing `SEED_FIELDS`, not a new source of truth.

## Backend: new endpoint

Add to `src/awkns_outreach/web/routes/admin.py` (same router as the
existing campaign/seed routes, so it's covered by the same
`require_admin` dependency):

```python
@router.get("/campaigns/seed-template.csv")
def seed_template_csv():
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=SEED_FIELDS)
    writer.writeheader()
    writer.writerow({
        "name": "Toyota", "website": "toyota.co.jp", "country": "JP",
        "category": "automotive", "tier": "A", "angle": "why this fits them",
    })
    writer.writerow({
        "name": "Acme Barbershop", "website": "acmebarbershop.com",
        "country": "US", "category": "barbershop", "tier": "B", "angle": "",
    })
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=seed_companies_template.csv"},
    )
```

Requires `import csv`, `import io`, and `from fastapi import Response` (or
`fastapi.responses.Response`) added to admin.py's existing imports.
`SEED_FIELDS` is already imported there. Column order and names come
directly from `SEED_FIELDS = ("name", "website", "country", "category",
"tier", "angle")` in `apollo/seed.py`, so the template can never drift out
of sync with what `parse_seed_companies` actually accepts.

The two example rows mirror the Toyota example already used in the
textarea placeholders (for consistency) plus a second row (blank `angle`)
to show that most fields are optional and a barbershop-style row, since
that's the kind of campaign the reporting user was testing with.

## Frontend: link placement

Add a plain link, styled consistent with the surrounding small/muted text
in each page:

```html
<a href="/campaigns/seed-template.csv" class="text-xs text-blue-600 hover:underline">Download template (CSV)</a>
```

**`new_campaign.html`** (around line 18-22): place it right after the
existing description paragraph, before the file input.

**`seed_companies_edit.html`** (around line 53): same placement, right
after the existing `<p class="text-xs text-slate-500">JSON array or
CSV...` line, before the file input.

## Testing

Add one test to `tests/test_web.py` near the other campaign/seed tests
(e.g. after `test_admin_create_and_view_campaign`):

- `GET /campaigns/seed-template.csv` (with admin auth, matching how other
  tests in that file authenticate) returns `200`.
- `Content-Type` starts with `text/csv`.
- `Content-Disposition` header contains `attachment` and
  `seed_companies_template.csv`.
- The first line of the body, split on `,`, equals
  `list(SEED_FIELDS)` — so the test breaks (loudly) if `SEED_FIELDS` ever
  changes shape without the template being reconsidered.

No test is added for the template-link presence in the HTML pages — the
existing page-render tests don't assert on incidental UI text elsewhere in
these templates either, so a targeted assertion here would be
inconsistent with the file's existing test granularity.
