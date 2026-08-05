# Footer visual builder (email-safe block layout)

**Date:** 2026-08-04
**Status:** Approved design

## Context
The current footer editor uses the Quill rich-text editor for the HTML body.
Users find it unworkable: **images can't sit side by side** (Quill puts each
image on its own line), and the **editing area doesn't match the Preview**
(the editor is left-aligned/full-width; the sent email is centered in a 560px
wrapper — [mailer.py:188](src/awkns_outreach/send/mailer.py#L188)).

The user asked for a free-form canvas (excalidraw-style, absolutely-positioned
elements). **That is not viable for email:** Gmail strips the CSS `position`
property entirely and Outlook's Word engine has no absolute positioning, so an
absolutely-positioned layout collapses in the two biggest clients. The only
layout method that survives across email clients is **HTML tables with inline
styles**.

Chosen direction (confirmed with user): a **drag-and-drop block builder** whose
editing feels arrange-y but which **compiles to email-safe nested tables**.

## Core idea
A structured **layout JSON** is the single source of truth. One **Python
compiler** turns it into email-safe HTML + plain text. That compiler feeds both
the live preview and the sent email, so *editor == preview == inbox* by
construction.

```
layout JSON ──► render_layout(layout) ──► body_html (nested tables) + body_text
   ▲                     │
   edited in             └──► live Preview (htmx) uses the SAME compiler
   the builder
```

## Scope

**In scope**
- New nullable `layout` JSON column on `footer_template`; `body_html`/`body_text`
  become compiled outputs written on save. **Send path unchanged** —
  `compliance.footer_html`/`footer_text` still read `body_html`/`body_text`.
- Python compiler `render_layout(layout) -> (html, text)` (email-safe tables,
  inline styles, 560px centered) — single source of truth.
- Four block types: **text** (inline HTML with links + `{unsubscribe_url}`),
  **image** (upload + optional link + align + width), **button/CTA** (label,
  href, bg/text color), **divider/spacer**.
- Layout shape `rows[] → columns[] (1–3) → blocks[]`. Side-by-side images = one
  row with two image columns.
- Builder UI: block palette, per-block settings, **true drag-and-drop** reorder
  and move-between-columns via **SortableJS (CDN)**.
- Live Preview pane via htmx rendering the compiled table.
- Plain text **auto-generated** from blocks (text + button `label (href)` +
  image alt/link + unsubscribe URL).
- Compliance guard: warn on save if no block contains `{unsubscribe_url}`.
- Legacy fallback: footers with `layout = NULL` keep today's HTML editor, with a
  "Switch to visual builder" action that starts an empty layout.

**Out of scope (YAGNI)**
- Free-pixel/absolute positioning (breaks in email — the whole reason for this).
- Nested rows-in-columns beyond one level; per-column background images; merge
  tags beyond `{unsubscribe_url}`; templated color themes.
- Migrating existing footers' HTML into layout automatically (they stay on the
  HTML editor until re-authored).

## Data model
Add to `footer_template` (Alembic migration, mirror `0011_footer_templates.py`):
- `layout` — JSON, nullable. `NULL` ⇒ legacy HTML-editor footer.

Layout JSON:
```json
{
  "width": 560,
  "rows": [
    { "columns": [ { "blocks": [ {"type":"text", ...} ] } ] },
    { "columns": [ { "blocks":[{"type":"image",...}] },
                   { "blocks":[{"type":"image",...}] } ] }
  ]
}
```
Block variants:
- `{"type":"text","html":"…","align":"left|center|right"}`
- `{"type":"image","src":"…","href":"…?","alt":"…","width":120,"align":"…"}`
- `{"type":"button","label":"…","href":"…","bg":"#0f172a","color":"#ffffff","align":"…"}`
- `{"type":"divider"}` / `{"type":"spacer","height":16}`

## Compiler (`awkns_outreach/footers/layout.py`)
`render_layout(layout: dict) -> tuple[str, str]`:
- Outer `<table>` width 100%, inner content `max-width:560px;margin:0 auto`.
- Each row → a table row; N columns → N `<td width="{100//n}%">`; blocks stacked
  in a column via a nested table. Every element inline-styled only.
- Text/image/button/divider each a small pure renderer.
- Text derivation: join text-block text, `button.label (href)`, image
  `alt`/`href`, and any `{unsubscribe_url}` line; collapse blank lines.
- **This is the only renderer**; the send path and preview both use it.

## Routes (`web/routes/settings.py`)
- `create_footer` / `update_footer`: accept a `layout` form field (JSON string).
  When present, parse → `render_layout` → store `body_html`/`body_text` +
  `layout`. When absent (legacy HTML editor), behave as today.
- `POST /settings/footers/preview-fragment`: body = layout JSON → compile →
  return the HTML fragment (htmx live preview), mirroring
  `/templates/preview-fragment`.
- Compliance guard: if the compiled HTML lacks `{unsubscribe_url}` substitution
  target, return a warning (surface via the existing popup/banner pattern).

## Editor UX (`footer_edit.html` + `_footer_builder.html` + JS)
- Fixed-width canvas (= layout width, 560px) rendering rows/columns/blocks close
  to output.
- Palette buttons: + Text / + Image / + Button / + Divider.
- Per-block settings (inline forms): text+link; image upload (reuse
  `/templates/upload-image`) + href + align + width; button label/href/colors;
  divider/spacer height.
- **SortableJS** (CDN, e.g. `cdn.jsdelivr.net/npm/sortablejs`) for drag reorder
  and moving blocks between columns; a hidden field holds the serialized layout
  JSON, kept in sync on every change.
- Preview pane: htmx posts the layout to the preview-fragment endpoint on change.
- Legacy footers: show current HTML editor + "Switch to visual builder".

## Testing
- **Compiler unit tests** (highest value, deterministic):
  - two image blocks in a 2-column row → one table, two `<td>`, both `<img>`.
  - button renders inline-styled anchor with bg/color.
  - `{unsubscribe_url}` in a text block survives into `body_html`.
  - plain-text derivation includes text, button `label (href)`, unsubscribe.
  - empty/edge layouts don't crash.
- **Web tests**: saving a layout compiles `body_html`/`body_text` and stores
  `layout`; preview-fragment returns compiled HTML; legacy (no layout) footer
  still edits; compliance guard fires when no `{unsubscribe_url}`.
- **Drag-and-drop**: verified manually / headless (SortableJS interactions).

## Build sequence (one plan, foundation-first)
1. Migration + `render_layout` compiler + compiler tests.
2. Save/preview wiring (layout → body_html/body_text; htmx preview) + web tests.
3. Builder UI: palette + block settings + reorder buttons.
4. SortableJS drag-and-drop layer.
5. Legacy fallback + compliance guard.

Drag (step 4) rides on a working structured editor, so the feature is usable
before drag lands.

## Files
- Migration: `src/awkns_outreach/db/migrations/versions/0012_footer_layout.py`
- Model: `src/awkns_outreach/db/models.py` (add `layout`)
- Compiler: `src/awkns_outreach/footers/layout.py` (new) + tests
- Routes: `src/awkns_outreach/web/routes/settings.py`
- Templates: `footer_edit.html`, new `_footer_builder.html`, builder JS partial
- Tests: `tests/test_footer_layout.py` (compiler), `tests/test_settings_footers.py` (web)
