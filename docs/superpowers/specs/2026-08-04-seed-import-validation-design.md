# Seed import validation + interrupting popup

**Date:** 2026-08-04
**Status:** Approved design

## Problem

Importing seed companies via CSV/JSON (at **Create campaign** or on the
**companies edit** page) performs almost no validation. Malformed JSON and
rows missing `name` are caught, but **duplicate emails are not**. A duplicate
email only surfaces much later, when `convert_seed_companies_to_leads` aborts
the whole batch (the `lead` table has `UniqueConstraint(campaign_id, email)`).

Real incident: a 189-row "Tier 2 – Beauty Industry" CSV contained one
duplicate email (`shan@shanhair.com`, two different people). Convert aborted
to 0 leads, but the operator never connected the two events — the campaign
sat with stale leads from an earlier import and looked broken.

Goal: catch the blocking problems **at import time**, show them in an
interrupting popup, and **do not save** until they are fixed.

## Scope

**In scope**

- Two entry points, sharing one validator:
  - `create_campaign` (POST `/campaigns`)
  - `save_companies` import branch (POST `/campaigns/{id}/companies`, `action=import`)
- Two blocking problem classes:
  - **Format error / missing `name`** — already raised by `parse_seed_companies`
    as `ValueError`; only its *presentation* changes (banner → popup).
  - **Duplicate email** — new check.

**Out of scope (YAGNI — not this version)**

- Soft/data-quality warnings: `website=yelp.com`, mojibake, missing email,
  same-company-multiple-domains. These do **not** block and are not surfaced.
- Blocking on missing email (would break the legitimate Apollo-enrich path,
  where seed rows intentionally have no email yet).
- Front-end pre-validation. Validation is server-side only (single source of
  truth, same logic Convert relies on).

## Approach: A1 — server re-render + auto-open modal

The forms POST normally. On validation failure the server **does not save**,
re-renders the same form (preserving the user's pasted text), and includes a
`<dialog>` that a small JS snippet auto-opens (`showModal()`) listing every
problem. The user closes the modal, fixes the input, resubmits.

Rejected alternative (A2, front-end pre-blocking): would require
re-implementing CSV/JSON parsing + email normalization in JS, diverging from
the server logic — the exact "rules disagree across stages" class of bug that
caused this incident. The server must validate regardless, so A2 means
maintaining two copies. Not worth it for a low-frequency action.

## Validation logic (`apollo/seed.py`)

Add one pure function; leave `parse_seed_companies` unchanged.

```python
def duplicate_email_problems(rows: list[dict]) -> list[str]:
    """Return one human-readable problem string per email used by 2+ rows.

    Emails are normalized (strip().lower()) the same way convert() and the
    lead insert do, so what we flag here is exactly what would later collide
    on UniqueConstraint(campaign_id, email). Rows without an email are ignored
    (missing email is allowed at seed stage — Apollo may fill it later).
    Empty list means no duplicate-email problem.
    """
```

Problem string format (example):
`Duplicate email used by 2 rows: shan@shanhair.com`

The duplicate check runs on the **final list that would be stored**:

- create / import-replace → the imported rows.
- import-append → `existing_seed_companies + imported` (so an appended row that
  collides with an already-stored email is caught too).

## Route changes (`web/routes/admin.py`)

Shared shape for both routes:

1. Parse: `try: rows = _read_seed_input(...)` → on `ValueError`, `problems = [str(exc)]`, skip step 2.
2. Duplicate check: `problems += duplicate_email_problems(final_rows)`.
3. If `problems`: **do not persist**; re-render the form template with
   `problems=problems` (+ echo back the user's `name`/`titles`/`angle_prompt`/
   `seed_text` for create; the current company list for the edit page). HTTP 200.
4. Else: persist as today.

`create_campaign` already has the re-render-on-error branch; extend it to carry
a `problems` list instead of a single `msg`, and add the duplicate check.

`save_companies` import branch currently `RedirectResponse(?msg=...)`; change the
failure path to re-render `seed_companies_edit.html` with `problems` (no
redirect), and only mutate `c.seed_companies` when there are no problems.

## Popup UX

A shared modal partial (e.g. `_import_problems_dialog.html`) included by both
`new_campaign.html` and `seed_companies_edit.html`:

- Renders only when `problems` is non-empty.
- Lists each problem as a bullet.
- A small inline `<script>` calls `dialog.showModal()` on load when problems exist.
- One button: "Close and fix".
- A line noting: **file uploads can't be auto-restored — re-select the file
  after fixing.** (Pasted text is preserved and repopulated.)

No HTMX needed; plain `<dialog>` + a few lines of vanilla JS, matching the
existing archive-dialog pattern in `dashboard.html`.

## Error handling / edge cases

- Malformed JSON → single problem line (the JSON error). Can't dedup-check
  rows we couldn't parse; that's fine.
- Missing `name` on rows → `parse_seed_companies` already aggregates these into
  one `ValueError` message; shown as one problem line.
- Duplicate emails differing only by case/whitespace → normalized, so still
  flagged (matches convert/insert behavior).
- Empty import (no file, no text) → no problems, no rows; unchanged behavior.
- Append introducing a duplicate against existing seed → caught (final-list check).

## Testing (TDD)

**Unit — `duplicate_email_problems`**

- No emails / all unique → `[]`.
- One duplicated email → one problem naming that email.
- Multiple duplicate groups → one problem per group.
- Case/whitespace-only differences count as duplicates.
- Rows without email are ignored (don't count as duplicates).
- Append case: duplicate only appears once `existing + imported` are combined.

**Web — routes**

- `create_campaign` with a duplicate-email CSV → HTTP 200, response contains the
  dialog markup + the offending email, and **no new Campaign row** in the DB.
- `create_campaign` with malformed JSON / missing name → same popup path.
- `save_companies` import with a duplicate → HTTP 200, dialog shown, and
  `c.seed_companies` **unchanged**.
- Happy path (clean import) → campaign created / companies saved as before.

## Files touched

- `src/awkns_outreach/apollo/seed.py` — add `duplicate_email_problems`.
- `src/awkns_outreach/web/routes/admin.py` — wire validation into
  `create_campaign` and `save_companies`.
- `src/awkns_outreach/web/templates/_import_problems_dialog.html` — new shared modal.
- `src/awkns_outreach/web/templates/new_campaign.html` — include modal, render `problems`.
- `src/awkns_outreach/web/templates/seed_companies_edit.html` — include modal, render `problems`.
- Tests under `tests/` for the validator and both routes.
