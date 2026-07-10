# Template editor two-column redesign + list page content/clone/archive

Date: 2026-07-10
Status: draft — pending user review

## Goal

Two independent but related changes to the standalone email template library
(`src/awkns_outreach/web/routes/templates_lib.py`, Apollo-style "New Template"
concept):

1. Redesign the New/Edit template page (`template_edit.html`) into a
   two-column layout — left: editable form, right: live "Template Preview" —
   matching an Apollo-style reference mockup the user provided, with a
   lightweight 5-button toolbar under Body and a "Send Test Email to Me"
   button available even before the template is first saved.
2. Extend the template list page (`template_list.html`) with a Content
   column (truncated body preview) and Clone / Archive actions, mirroring the
   campaign dashboard's existing archive pattern
   (`docs/superpowers/specs/2026-07-09-campaign-archive-dashboard-design.md`).

Out of scope (explicitly deferred, confirmed with the user):
- No rich-text/HTML body editor. Body stays a plain-text `<textarea>`; the
  send pipeline's plain-text → HTML conversion (`mailer.py:_text_to_html`)
  is unchanged.
- No real image/attachment upload or storage.
- No Folder/Tags/Owner fields (not in the current data model, not requested).

## Part 1 — Template editor page

### 1.1 Layout

`template_edit.html` becomes a two-column CSS grid (`grid grid-cols-2 gap-6`,
matching the `max-w-5xl` container already used by `base.html`):

- **Left column — "Email Template"**: existing Name / Subject / Body fields,
  unchanged validation, plus the new toolbar row under Body (1.3), plus
  Save/Cancel (new template) or Save/Delete (existing template) — unchanged
  from today, still a normal full-page POST.
- **Right column — "Template Preview"**: rendered Subject + HTML body
  (against the existing hard-coded example contact, Jamie Rivera / Acme
  Studios), a mailbox `<select>`, a "Send Test Email to Me" button, and a
  status line under it for the confirmation/error message.

This layout applies to **both** `/templates/new` and `/templates/{id}/edit` —
today the preview/test-send UI only renders once a template exists
(`{% if t %}` in `template_edit.html:29`); that gate is removed. The two
routes continue to render the same template with `t=None` vs `t=<EmailTemplate>`.

### 1.2 Live preview (debounced, no full page reload)

New endpoint, independent of any saved template id (works pre-save):

```
POST /templates/preview-fragment
  form: subject, body
  → renders and returns only the right-column fragment (partial template
    template_preview_fragment.html), via the existing
    render_template_preview(subject, body, _PREVIEW_EMAIL)
```

- Subject and Body inputs get `hx-post="/templates/preview-fragment"`,
  `hx-trigger="input changed delay:500ms"`, `hx-target="#preview-pane"`,
  `hx-include="[name='subject'],[name='body']"`.
- `#preview-pane` is the right column's inner container; HTMX swaps its
  `innerHTML` on each debounced update. The mailbox `<select>` and the send
  button/status line live in a separate, not-swapped region so an in-flight
  mailbox choice or "sent!" message isn't wiped out by a preview refresh
  (see 1.4).
- No JS beyond HTMX attributes — consistent with the rest of the app.

### 1.3 Toolbar — 5 buttons, lightweight

A single icon row under Body (`template_edit.html`, replacing nothing —
there is no toolbar today):

| Icon | Label | Behavior |
|------|-------|----------|
| T | Formatting help | Click toggles a small inline tip: "Plain text only — a blank line starts a new paragraph; raw https:// links auto-linkify." No state, no textarea mutation. |
| 🔗 | Link | Click opens a small popover (two inputs: URL, optional display text + "Insert"). Inserts `{display text }url` (or just `url` if no display text) as plain text at the textarea cursor. Relies on `mailer.py:_linkify()`, which already auto-links raw `https?://` URLs in the sent HTML — no backend change needed. |
| 🖼 | Image | Click opens a native `<input type="file" accept="image/*">` (hidden, triggered via label). On file selection, inserts `[image: <filename>]` as plain text at the cursor. No upload; the file is never read past its name. |
| 📎 | Attachment | Same as Image but no `accept` filter and inserts `[attachment: <filename>]`. |
| `<>` | Code view | Toggles the Body textarea's font class between the current default and `font-mono` (cosmetic only — body is already raw plain text, so there's no separate "source" to switch to). |

All five are plain buttons (no server round-trip except where noted); state
(popover open/closed, font toggle) is local, minimal inline `<script>` in
`template_edit.html` (no new JS file, consistent with there being no bundler).

### 1.4 Send Test Email to Me

New endpoint, independent of any saved template id (works pre-save):

```
POST /templates/test-send-fragment
  form: subject, body, mailbox_id
  → builds the same throwaway Campaign/Lead send_outreach_email uses today
    (templates_lib.py:126-144, lifted out of update_template's test_send
    branch into a shared helper), returns a small fragment: the button
    + a status line, e.g.
      "Test email sent! Check your inbox at jamie@... ." (success)
      "Test send failed: <error>" (failure)
```

- Button: `hx-post="/templates/test-send-fragment"`,
  `hx-include="[name='subject'],[name='body'],[name='mailbox_id']"`,
  `hx-target="#test-send-status"`, `hx-swap="outerHTML"` on a wrapper that
  contains both the button and the status line, so re-clicking replaces the
  whole thing (button included) rather than appending status lines.
- The existing `update_template` action=`test_send` branch
  (`templates_lib.py:126-144`) is refactored into a shared helper
  (`_send_test_email(db, subject, body, mailbox_id) -> str` returning the
  message) used by both the old saved-template flow and this new endpoint,
  rather than duplicated.

### 1.5 Routes summary (`templates_lib.py`)

| Route | Change |
|-------|--------|
| `GET /templates/new` | Now also passes `mailboxes=_connected_mailboxes(db)` (was `[]`) so the new-template preview pane's mailbox picker works pre-save. |
| `GET /templates/{id}/edit` | Unchanged. |
| `POST /templates` (create) | Unchanged. |
| `POST /templates/{id}/edit` | `action` no longer needs to handle `preview`/`test_send` (moved to the new fragment endpoints below), only `save` / `delete`. |
| `POST /templates/preview-fragment` | **New.** No template id. Body/Subject only. Returns preview fragment. |
| `POST /templates/test-send-fragment` | **New.** No template id. Body/Subject/mailbox_id. Returns button+status fragment. |

## Part 2 — Template list page (`template_list.html`)

### 2.1 Data model

`EmailTemplate` (`db/models.py:241`) gains:

```python
status: Mapped[str] = mapped_column(String, default="active")  # active | archived
```

### Migration `0005_email_template_status`

New Alembic revision, following `0003_campaign_description.py`'s style:

- upgrade: `op.add_column("email_template", sa.Column("status", sa.String(), nullable=False, server_default="active"))`
- downgrade: drop the column.

### 2.2 List route `GET /templates`

- New `status` query param, same semantics as the campaign dashboard
  (`admin.py` `_STATUS_FILTERS`/dashboard route): absent/`default` → active
  only; `archived` → archived only; `all` → both. A small filter control
  (link/dropdown) added above the table, matching the dashboard's existing
  pattern.
- Query orders/filters by the new `status` column; `msg` continues to be
  accepted/displayed for redirect confirmations.

### 2.3 Table columns

`Name | Subject | Content | Actions`

- **Content**: `t.body` with newlines collapsed to spaces, truncated to ~70
  chars with a trailing `…` when longer, full text in a `title=` attribute
  for hover tooltip. Truncation done in the route (small helper), not Jinja,
  so it's unit-testable.
- **Actions**: `Edit` (unchanged) + `Clone` + `Archive`/`Unarchive`
  (whichever applies to the row's current status).

### 2.4 Clone `POST /templates/{id}/clone`

- Creates a new `EmailTemplate(name=f"{t.name} (Copy)", subject=t.subject, body=t.body, status="active")`.
- Redirects straight to the new template's edit page:
  `/templates/{new_id}/edit?msg=Template cloned.`

### 2.5 Archive/Unarchive `POST /templates/{id}/status`

One endpoint, whitelisted transition table (mirrors
`admin.py:_STATUS_TRANSITIONS`):

| action | from | to |
|--------|------|-----|
| `archive` | active | archived |
| `unarchive` | archived | active |

- Unknown `action` → 400. No-op transition (e.g. archiving an already
  archived template) redirects with a message, no error.
- Redirects back to `/templates?status=...&msg=...` so the operator stays on
  their filtered view.

### 2.6 Archived-edit guard

`GET|POST /templates/{id}/edit` gains the same guard as campaigns
(`admin.py:_archived_edit_guard`): an archived template can't be opened for
editing (redirect to `/templates?msg=Archived templates can't be edited — unarchive first.`).
The list page's "Edit" link is only rendered for active templates (archived
rows show only "Unarchive").

## Testing

- `tests/test_web.py` (or a new `tests/test_templates_lib.py`, following
  whichever convention the campaign archive tests used) covers: two-column
  page renders for both new and existing templates; preview-fragment and
  test-send-fragment endpoints work without a saved template id; clone
  creates a second row and redirects to its edit page; archive/unarchive
  transitions and the default-hides-archived list filter; archived-edit
  guard blocks GET and POST.
