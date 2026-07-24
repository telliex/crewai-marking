# Seed-company import validation + Convert confirm step

Date: 2026-07-23
Status: draft — pending user review

## Goal

Two related gaps found while debugging a campaign that silently ended up
with 0 seed companies (a pasted JSON array had a syntax error that was
swallowed):

1. Seed-company import (JSON paste/upload or CSV upload, used by both the
   "New campaign" form and an existing campaign's "Edit companies" page)
   has almost no validation. A malformed or incomplete row does not block
   anything — worst case, "New campaign" creates a campaign with an empty
   seed list and no way to tell without noticing `Companies: 0`.
2. "Convert seed companies to leads" (`admin.py:329-373`) is a single
   click that validates and commits `Lead` rows in the same request. There
   is no way to see what would be created, or which rows are missing
   `email`, before it happens.

Out of scope:
- Field-level validation beyond "row has a `name`" (no email-format check,
  no `tier` enum check, no CSV header check) — deliberately deferred per
  user's scope decision.
- Any change to `Campaign` persistence semantics beyond seed import. The
  "New campaign" form change only affects what happens when seed parsing
  fails; a campaign with no seed-company issues is created exactly as
  today.
- Reworking the `Edit companies` page's own row-by-row editor
  (`_rows_from_form`) — it already round-trips through the same optional
  fields and isn't part of the reported problem.

## 1. Required-field validation in `parse_seed_companies`

`src/awkns_outreach/apollo/seed.py` is the single parser both JSON and CSV
seed input go through (`parse_seed_companies`, called by
`_read_seed_input` in `admin.py`). Adding the rule here means both formats
and both call sites (New campaign form, Edit companies import) get it for
free.

**Rule:** after alias-cleaning, every non-blank row must have a non-empty
`name`. All other fields stay optional — a metadata-only row with no
`website`/`email` remains valid (existing, tested behavior:
`tests/test_seed.py:41-45`, `tests/test_seed.py:48-57`).

**Failure mode: reject the whole batch.** If any row is missing `name`,
`parse_seed_companies` raises `ValueError` (same exception type it already
raises for malformed JSON) listing every offending row, so the operator
fixes everything in one pass instead of resubmitting repeatedly:

```
row 2: missing required field 'name'; row 5: missing required field 'name'
```

Implementation shape (replacing the current cleaning loop, lines ~101-106):

```python
out: list[dict[str, str]] = []
errors: list[str] = []
for i, record in enumerate(records, start=1):
    cleaned = _clean_row(record)
    if not cleaned:
        continue
    if not cleaned.get("name"):
        errors.append(f"row {i}: missing required field 'name'")
        continue
    out.append(cleaned)
if errors:
    raise ValueError("; ".join(errors))
return out
```

Row numbers are 1-based positions within `records` (the JSON array items,
or the CSV data rows) — i.e. the same order the operator sees in their
source file/paste, ignoring only fully-blank rows that were always
silently dropped.

## 2. "New campaign" form blocks creation on seed-import failure

Today, `create_campaign` (`admin.py:134-159`) catches the `ValueError`
from `_read_seed_input`, but still creates the campaign with
`seed_companies=[]` and appends a note to the redirect `msg` — which is
easy to miss and leaves a dead, empty campaign behind.

**New behavior:** on `ValueError`, do not create the `Campaign` row at
all. Re-render `new_campaign.html` (same `GET` template, no redirect —
a redirect would lose the pasted `seed_text`) with the error and the
form's other posted values pre-filled, so the operator fixes it in place:

```python
@router.post("/campaigns")
def create_campaign(
    request: Request,
    name: str = Form(...),
    titles: str = Form(""),
    angle_prompt: str = Form(""),
    seed_text: str = Form(""),
    seed_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    try:
        seed_companies = _read_seed_input(seed_file, seed_text)
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "new_campaign.html",
            {
                "error": f"Seed import failed: {exc}",
                "name": name, "titles": titles,
                "angle_prompt": angle_prompt, "seed_text": seed_text,
            },
        )
    c = Campaign(
        name=name.strip(), target_titles=_split_lines(titles),
        seed_companies=seed_companies,
        angle_prompt=angle_prompt.strip() or None, sender_identity={},
    )
    db.add(c)
    db.commit()
    msg = f"Campaign created with {len(seed_companies)} seed companies."
    return RedirectResponse(f"/campaigns/{c.id}?msg={msg}", status_code=303)
```

`new_campaign.html` changes:
- Add `request: Request` param to the route (needed for
  `TemplateResponse`; `new_campaign_form` GET already doesn't need it
  since it has no dynamic values today, but the POST failure path does).
- Reuse the existing blue `msg`-style banner for `error` (same visual
  treatment `base.html:26-28` already gives "Import failed" messages
  elsewhere — no new banner style).
- Pre-fill `value="{{ name or '' }}"` on the name input, and
  `{{ titles or '' }}` / `{{ angle_prompt or '' }}` / `{{ seed_text or ''
  }}` inside their respective textareas.
- If the failure came from an uploaded `seed_file` rather than pasted
  text, there's nothing to pre-fill (browsers won't let a script set a
  file input's value) — the error message is enough to tell the operator
  to re-upload the corrected file or paste it as text instead.

**Unchanged:** `save_companies` action `"import"` (Edit companies page,
`admin.py:454-498`) already redirects with `msg=Import failed: {exc}`
without saving anything on `ValueError` — no code change needed there,
it automatically inherits the clearer per-row error text.

## 3. Convert seed companies to leads: preview / confirm

Mirrors the existing Apollo-enrich pattern (`reveal` checkbox = preview by
default, explicit opt-in to actually write). Same endpoint, one added
`confirm` form field.

`admin.py` (`convert_seed_companies_to_leads`, currently lines 329-373):
the existing pre-flight checks (already-has-leads, empty seed list,
missing name/email, duplicate email) run unconditionally, unchanged. Only
what happens *after* they pass changes:

```python
@router.post("/campaigns/{campaign_id}/leads/from-seed-companies")
def convert_seed_companies_to_leads(
    campaign_id: str,
    confirm: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    # ...unchanged: existing-leads check, empty-rows check,
    # missing name/email + duplicate-email check, all redirect-and-return
    # on failure exactly as today...

    if not confirm:
        plural = "y" if len(rows) == 1 else "ies"
        names = ", ".join((r.get("name") or "").strip() for r in rows)
        msg = (
            f"Preview: would convert {len(rows)} seed compan{plural} to "
            f"leads ({names}). Nothing saved — check “confirm” "
            f"and click Convert again to save."
        )
        return RedirectResponse(f"/campaigns/{c.id}?msg={msg}", status_code=303)

    for r in rows:
        db.add(Lead(...))  # unchanged
    db.commit()
    msg = f"Converted {len(rows)} seed compan{plural} to leads."
    return RedirectResponse(f"/campaigns/{c.id}?msg={msg}", status_code=303)
```

`campaign.html` (`campaign.html:70-75`): add a checkbox next to the button,
same style as Apollo enrich's `reveal` checkbox:

```html
<form method="post" action="/campaigns/{{ c.id }}/leads/from-seed-companies" class="rounded border bg-white p-3">
  <div class="text-xs font-medium text-slate-500 mb-2">Convert seed companies to leads</div>
  <p class="text-xs text-slate-400 mb-2 max-w-xs">Skip Apollo — every seed company needs an email first (<a href="/campaigns/{{ c.id }}/companies" class="underline">edit them here</a>).</p>
  <label class="text-xs"><input name="confirm" type="checkbox" value="1"> confirm (writes leads)</label>
  <button class="ml-2 rounded bg-slate-900 text-white text-xs px-2 py-1">Convert</button>
</form>
```

**Why this satisfies "go back and reprocess the original data":** since
preview never writes anything, there's nothing to undo — validation
failures (missing email, duplicate email) surface in the preview message
exactly as they do today, and the operator follows the existing "edit
them here" link to `Edit companies`, fixes the rows, and re-submits
Convert (still unchecked) to preview again. Only checking "confirm" once
the preview looks right actually commits.

## Testing

`tests/test_seed.py` (new cases, existing cases unchanged):
- JSON array with one row missing `name` → `parse_seed_companies` raises
  `ValueError` mentioning `row 2`.
- CSV with one data row missing `name` → same, row-numbered relative to
  data rows (header excluded).
- Multiple bad rows → single `ValueError` listing all of them.
- Existing `test_parse_row_without_website_is_kept_but_has_no_domain` and
  `test_parse_json_array_captures_email_and_contact_fields` (rows with
  `name` but no `website`) keep passing unchanged.

`tests/test_web.py` (new cases alongside existing route tests):
- `POST /campaigns` with malformed seed JSON → no `Campaign` row created,
  200 response re-renders the form with the error and posted values.
- `POST /campaigns` with valid seed data → unchanged, creates and
  redirects as today.
- `POST /campaigns/{id}/companies` (action=import) with a row missing
  `name` → redirect with the row-numbered error, existing
  `seed_companies` untouched (already covered by existing "Import
  failed" tests if present; extend if not).
- `POST /campaigns/{id}/leads/from-seed-companies` without `confirm` →
  redirect with a "Preview: would convert…" message, zero `Lead` rows
  created (even when validation would otherwise pass).
- Same route with `confirm=1` and valid rows → `Lead` rows created,
  "Converted N…" message (existing behavior, now gated behind the flag).
- Same route with `confirm=1` but a row missing `email` → still rejected
  with the existing failure message, zero `Lead` rows created (pre-flight
  checks run regardless of `confirm`).
