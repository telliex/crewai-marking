# Campaign dashboard: archive / edit / filter / pagination / pause

Date: 2026-07-09
Status: approved (user), preview mockup approved

## Goal

The campaigns dashboard currently only creates and lists campaigns. Add the
rest of the lifecycle — archive (soft-delete) with a confirmation modal, edit,
pause/resume, a status filter, a description field, and pagination — without
ever hard-deleting data. Suppressions are global and untouched by any of this.

## Approved UI (see preview mockup)

Interactive mockup approved by the user:
status filter dropdown, status badges, description as a second line under the
campaign name, per-row Edit / Pause / Archive actions, archive confirmation
dialog showing Leads (total) / Active / Sent (lifetime), pagination footer.

## 1. Data model — `src/awkns_outreach/db/models.py`

- Add `Campaign.description: Mapped[Optional[str]]` (String, nullable).
- `Campaign.status` gains a third value: `active | paused | archived`
  (free-form string today; update the inline comment, no schema change).

### Migration `0003_campaign_description`

New Alembic revision in `src/awkns_outreach/db/migrations/versions/`,
following the style of `0002_seed_companies.py`:

- upgrade: `op.add_column("campaign", sa.Column("description", sa.String(), nullable=True))`
- downgrade: drop it.

## 2. Stats — `src/awkns_outreach/web/stats.py`

Add `sent_total` to `campaign_stats`: lifetime count of `Event.type == "sent"`
for the campaign (same join as `sent_last_24h`, without the time filter).
Used by the archive confirmation dialog.

## 3. Sequencer guard — `src/awkns_outreach/sequencer/engine.py`

`process_campaign` currently ignores `campaign.status`: a paused or archived
campaign still sends if the run endpoint (or cron) fires. Fix:

- In `process_campaign`, when `not dry_run` and `campaign.status != "active"`,
  set `summary.blocked = f"campaign is {campaign.status}"` and return early
  (before the legal check). Dry-run previews remain allowed for any status.

## 4. Routes — `src/awkns_outreach/web/routes/admin.py`

### Dashboard `GET /`

- New query params: `status` and `page` (plus the existing implicit `msg` —
  the dashboard route must now accept `msg: Optional[str]` and pass it to the
  template, since status-change redirects land here).
- `status` values: absent/`default` → show `active` + `paused` (archived
  hidden); `active` | `paused` | `archived` → that status only; `all` → all.
  Unknown values behave as `default`.
- Pagination: `PAGE_SIZE = 20`, `page` is 1-based, clamped to valid range.
  Query total count for the pager; order stays `created_at.desc()`.
- Template context: `rows`, `status_filter`, `page`, `pages`, `total`.

### Status changes `POST /campaigns/{id}/status`

One endpoint, `action` form field, whitelisted transitions:

| action      | from              | to         |
|-------------|-------------------|------------|
| `archive`   | active, paused    | archived   |
| `unarchive` | archived          | active     |
| `pause`     | active            | paused     |
| `resume`    | paused            | active     |

- Unknown `action` → 400. A no-op transition (e.g. pausing an already-paused
  campaign) just redirects with a message, no error.
- Forms carry hidden `status` (current filter) + `page` fields; redirect to
  `/?status=...&page=...&msg=...` so the operator stays on their filtered view.
  Message names the campaign, e.g. `Campaign “X” archived.`

### Edit `GET|POST /campaigns/{id}/edit`

- GET renders `campaign_edit.html`: name (required), description (textarea),
  target titles (textarea, one per line — reuse `_split_lines` which also
  accepts commas), angle prompt (textarea).
- POST saves those four fields, redirects to `/campaigns/{id}?msg=Campaign updated.`
- Guard (both GET and POST): archived campaign → redirect to
  `/?msg=Archived campaigns can’t be edited — unarchive first.` (server-side
  backup for the disabled button).

## 5. Templates — `src/awkns_outreach/web/templates/`

### `dashboard.html` (rework; keep Tailwind utility style of the codebase)

- Toolbar: status `<select>` inside a GET form (submit on change via
  `onchange="this.form.submit()"`), existing `+ New campaign` button.
- Table columns: Campaign (name link + description as truncated second line,
  full text in `title` attr) · Status (badge) · Leads · Active · Completed ·
  Suppressed · Sent 24h · Cap / left · Steps · Actions.
- Status badges: green (active), amber (paused), grey (archived); archived
  rows also grey out their text.
- Actions per row:
  - active: `Edit` link · `Pause` (plain POST form) · `Archive` (opens dialog)
  - paused: `Edit` link · `Resume` (plain POST) · `Archive` (opens dialog)
  - archived: `Edit` disabled with tooltip · `Unarchive` (plain POST, no confirm)
- Archive confirmation: ONE shared `<dialog>` + small vanilla JS. The Archive
  button carries `data-name`, `data-leads`, `data-active`, `data-sent`,
  `data-url`; JS fills the dialog, shows the three stats (Leads total / Active
  in sequence / Sent lifetime), a note that leads & history are kept, and a
  stronger warning when `active > 0` (“N leads are mid-sequence — archiving
  stops their sends”). Confirm submits a POST form to `data-url` with the
  hidden filter/page fields. No HTMX needed.
- Pagination footer: “Showing X–Y of N campaigns” + Prev / page links / Next,
  preserving `status` in the links. Render page links only when `pages > 1`.
- Empty states: no campaigns at all → existing “No campaigns yet” message;
  active filter with no matches → “No campaigns match this filter.”

### `campaign_edit.html` (new)

Simple form page in the style of `new_campaign.html`: the four fields, Save
button, Cancel link back to `/campaigns/{id}`.

## 6. Tests

Extend `tests/test_web.py` (same fixtures/patterns):

- default dashboard hides archived, `?status=archived` shows them,
  `?status=all` shows everything; badge/desc render.
- archive → status becomes `archived`; unarchive → `active` (via the
  `/status` endpoint); redirect preserves `status`/`page` params.
- pause → `paused`, resume → `active`; invalid action → 400; no-op
  transition redirects without error.
- edit GET+POST save name/description/titles/angle; archived campaign is
  blocked on both GET and POST.
- pagination: 25 campaigns → page 1 has 20 rows, page 2 has 5; out-of-range
  page clamps.
- `campaign_stats` returns `sent_total` (seed a couple of `sent` events, one
  older than 24 h, and assert `sent_total` counts both while `sent_last_24h`
  counts one).

Extend `tests/test_sequencer.py`:

- `process_campaign` with `dry_run=False` on a paused/archived campaign →
  `summary.blocked` set, nothing sent; `dry_run=True` still works.

## Out of scope (deliberate)

Hard delete, bulk actions, name search, column sorting, HTMX-driven partial
updates. Filter + pagination cover navigation at current scale.
