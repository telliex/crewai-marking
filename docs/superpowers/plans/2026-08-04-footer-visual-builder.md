# Footer Visual Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the footer HTML textarea with a drag-and-drop block builder whose structured layout compiles (via one Python renderer) to email-safe nested-table HTML — so images sit side by side and the editor matches the sent email.

**Architecture:** A `layout` JSON column is the source of truth. `render_layout(layout)` compiles it to `body_html` + `body_text` on save (send path unchanged). The builder UI (vanilla JS + SortableJS from CDN) maintains the layout model and posts it to a preview endpoint that uses the same renderer.

**Tech Stack:** FastAPI, Jinja2 (no build step; CDN scripts), SQLAlchemy + Alembic, SortableJS (CDN), htmx, pytest.

## Global Constraints

- Email-safe output only: nested `<table>` + inline styles. No `position`, flex, grid, or CSS float. — spec "Context"
- `render_layout` is the single renderer; preview and send both use it. — spec "Core idea"
- `{unsubscribe_url}` must pass through the compiler unescaped (substituted at send time by `compliance.footer_html`/`footer_text`). — spec "Compiler"
- Send path unchanged: `body_html`/`body_text` remain the stored, sent fields. — spec "Data model"
- JSON columns use `JSONType = JSON().with_variant(JSONB(), "postgresql")` (models.py:36). — existing pattern
- Block types: text, image, button, divider/spacer. Columns 1–3 per row. — spec "Scope"

---

### Task 1: `layout` column (migration + model)

**Files:**
- Create: `src/awkns_outreach/db/migrations/versions/0012_footer_layout.py`
- Modify: `src/awkns_outreach/db/models.py` (FooterTemplate)
- Test: `tests/test_settings_footers.py`

**Interfaces:**
- Produces: `FooterTemplate.layout` — `Optional[dict]`, nullable JSON. `None` ⇒ legacy HTML-editor footer.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_settings_footers.py`:

```python
def test_footer_layout_column_persists(session):
    from awkns_outreach.db.models import FooterTemplate
    f = FooterTemplate(name="L", body_html="x", body_text="x", is_default=False,
                       layout={"rows": [{"columns": [{"blocks": []}]}]})
    session.add(f)
    session.commit()
    session.refresh(f)
    assert f.layout == {"rows": [{"columns": [{"blocks": []}]}]}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_settings_footers.py::test_footer_layout_column_persists -v`
Expected: FAIL — `TypeError: 'layout' is an invalid keyword argument for FooterTemplate`.

- [ ] **Step 3: Add the model column**

In `src/awkns_outreach/db/models.py`, inside `FooterTemplate`, after the `status` column add:

```python
    # Structured block layout (visual builder source of truth). NULL for legacy
    # footers authored with the raw-HTML editor. body_html/body_text are
    # compiled from this on save; the send path only reads those two.
    layout: Mapped[Optional[dict]] = mapped_column(JSONType, nullable=True)
```

(`JSONType` and `Optional` are already imported in this module.)

- [ ] **Step 4: Create the migration**

Create `src/awkns_outreach/db/migrations/versions/0012_footer_layout.py`:

```python
"""footer visual-builder layout column

Revision ID: 0012_footer_layout
Revises: 0011_footer_templates
Create Date: 2026-08-04

Adds a nullable `footer_template.layout` JSON column — the structured block
layout the visual builder edits. body_html/body_text stay the compiled,
sent fields; layout is NULL for legacy raw-HTML footers.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_footer_layout"
down_revision: Union[str, None] = "0011_footer_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("footer_template", sa.Column("layout", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("footer_template", "layout")
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_settings_footers.py::test_footer_layout_column_persists -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/awkns_outreach/db/models.py \
        src/awkns_outreach/db/migrations/versions/0012_footer_layout.py \
        tests/test_settings_footers.py
git commit -m "feat: footer_template.layout JSON column + migration"
```

---

### Task 2: `render_layout` compiler

**Files:**
- Create: `src/awkns_outreach/footers/__init__.py` (empty)
- Create: `src/awkns_outreach/footers/layout.py`
- Test: `tests/test_footer_layout.py`

**Interfaces:**
- Produces: `render_layout(layout: dict) -> tuple[str, str]` → `(body_html, body_text)`. Email-safe nested tables + inline styles; `{unsubscribe_url}` passes through unescaped.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_footer_layout.py`:

```python
from awkns_outreach.footers.layout import render_layout


def _two_image_row():
    return {"rows": [{"columns": [
        {"blocks": [{"type": "image", "src": "http://x/a.png", "alt": "A"}]},
        {"blocks": [{"type": "image", "src": "http://x/b.png", "alt": "B"}]},
    ]}]}


def test_two_images_render_side_by_side_in_one_row():
    html, _ = render_layout(_two_image_row())
    assert html.count("<td") == 2          # two columns → two cells in one row
    assert html.count("<tr") == 1
    assert "http://x/a.png" in html and "http://x/b.png" in html


def test_button_renders_inline_styled_anchor():
    html, _ = render_layout({"rows": [{"columns": [{"blocks": [
        {"type": "button", "label": "Visit", "href": "http://x", "bg": "#111", "color": "#fff"},
    ]}]}]})
    assert "background:#111" in html and ">Visit</a>" in html and 'href="http://x"' in html


def test_unsubscribe_token_passes_through_unescaped():
    html, text = render_layout({"rows": [{"columns": [{"blocks": [
        {"type": "text", "html": 'Bye · <a href="{unsubscribe_url}">Unsubscribe</a>'},
    ]}]}]})
    assert "{unsubscribe_url}" in html
    assert "{unsubscribe_url}" in text  # link href surfaced in plain text


def test_plain_text_includes_text_and_button():
    _, text = render_layout({"rows": [{"columns": [{"blocks": [
        {"type": "text", "html": "<b>Hello</b> world"},
        {"type": "button", "label": "Go", "href": "http://x"},
    ]}]}]})
    assert "Hello world" in text
    assert "Go (http://x)" in text


def test_empty_layout_does_not_crash():
    html, text = render_layout({"rows": []})
    assert "<table" in html
    assert text == ""
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_footer_layout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'awkns_outreach.footers'`.

- [ ] **Step 3: Create the package + compiler**

Create empty `src/awkns_outreach/footers/__init__.py`.

Create `src/awkns_outreach/footers/layout.py`:

```python
"""Compile a footer's structured block layout into email-safe HTML + plain
text. Nested tables + inline styles only — Gmail/Outlook strip position/flex/
grid, so tables are the one portable layout method. This renderer is the single
source of truth: the save path stores its output as body_html/body_text and the
live preview renders from it, so the editor never diverges from the sent email.

`{unsubscribe_url}` is passed through verbatim (never escaped) — it is
substituted per-recipient at send time by compliance.footer_html/footer_text.
"""
from __future__ import annotations

import re
from html import escape
from typing import Any, Callable

_DEFAULT_WIDTH = 560


def _text_block(b: dict[str, Any]) -> str:
    align = b.get("align", "left")
    # Operator-authored inline HTML (links, {unsubscribe_url}); trusted content.
    return (
        f'<div style="font-size:13px;line-height:1.6;color:#5f6368;'
        f'text-align:{align}">{b.get("html", "")}</div>'
    )


def _image_block(b: dict[str, Any]) -> str:
    src = escape(str(b.get("src", "")), quote=True)
    alt = escape(str(b.get("alt", "")), quote=True)
    align = b.get("align", "left")
    width = int(b.get("width") or 0)
    wattr = f' width="{width}"' if width else ""
    img = (
        f'<img src="{src}" alt="{alt}"{wattr} '
        f'style="display:inline-block;border:0;max-width:100%;height:auto">'
    )
    href = b.get("href")
    if href:
        img = f'<a href="{escape(str(href), quote=True)}" target="_blank">{img}</a>'
    return f'<div style="text-align:{align}">{img}</div>'


def _button_block(b: dict[str, Any]) -> str:
    label = escape(str(b.get("label", "")))
    href = escape(str(b.get("href", "")), quote=True)
    bg = b.get("bg", "#0f172a")
    color = b.get("color", "#ffffff")
    align = b.get("align", "left")
    return (
        f'<div style="text-align:{align}">'
        f'<a href="{href}" target="_blank" style="display:inline-block;'
        f'background:{bg};color:{color};text-decoration:none;padding:10px 18px;'
        f'border-radius:6px;font-size:14px;font-weight:600">{label}</a></div>'
    )


def _divider_block(b: dict[str, Any]) -> str:
    if b.get("type") == "spacer":
        h = int(b.get("height") or 16)
        return f'<div style="height:{h}px;line-height:{h}px;font-size:0">&nbsp;</div>'
    return '<div style="border-top:1px solid #e0e0e0;margin:12px 0"></div>'


_RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "text": _text_block,
    "image": _image_block,
    "button": _button_block,
    "divider": _divider_block,
    "spacer": _divider_block,
}


def _block_html(b: dict[str, Any]) -> str:
    return _RENDERERS.get(b.get("type", ""), lambda _b: "")(b)


def _column_html(col: dict[str, Any]) -> str:
    return "".join(
        f'<div style="padding:4px 0">{_block_html(b)}</div>'
        for b in col.get("blocks", []) or []
    )


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "")


def _plain_text(layout: dict[str, Any]) -> str:
    lines: list[str] = []
    for row in layout.get("rows", []) or []:
        for col in row.get("columns", []) or []:
            for b in col.get("blocks", []) or []:
                t = b.get("type")
                if t == "text":
                    # Keep {unsubscribe_url} if present in an href.
                    hrefs = re.findall(r'href="([^"]+)"', b.get("html", ""))
                    line = _strip_tags(b.get("html", "")).strip()
                    for h in hrefs:
                        if h not in line:
                            line = f"{line} ({h})".strip()
                    lines.append(line)
                elif t == "button":
                    lines.append(f'{b.get("label", "")} ({b.get("href", "")})'.strip())
                elif t == "image" and b.get("href"):
                    lines.append(f'{b.get("alt") or "image"} ({b["href"]})')
    return "\n".join(x for x in lines if x).strip()


def render_layout(layout: dict[str, Any]) -> tuple[str, str]:
    """Compile a layout dict into (body_html, body_text)."""
    layout = layout or {}
    width = int(layout.get("width") or _DEFAULT_WIDTH)
    trs: list[str] = []
    for row in layout.get("rows", []) or []:
        cols = row.get("columns", []) or []
        n = max(len(cols), 1)
        pct = 100 // n
        tds = "".join(
            f'<td valign="top" width="{pct}%" '
            f'style="padding:0 6px;vertical-align:top">{_column_html(col)}</td>'
            for col in cols
        )
        trs.append(f"<tr>{tds}</tr>")
    html = (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" style="max-width:{width}px;margin:0 auto">{"".join(trs)}</table>'
    )
    return html, _plain_text(layout)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_footer_layout.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/footers/ tests/test_footer_layout.py
git commit -m "feat: render_layout footer compiler (email-safe tables)"
```

---

### Task 3: Save + preview wiring

**Files:**
- Modify: `src/awkns_outreach/web/routes/settings.py`
- Create: `src/awkns_outreach/web/templates/_footer_preview_fragment.html`
- Test: `tests/test_settings_footers.py`

**Interfaces:**
- Consumes: `render_layout` (Task 2); `FooterTemplate.layout` (Task 1).
- Produces: `create_footer`/`update_footer` accept a `layout` form field (JSON). When present → compile → store `body_html`/`body_text`/`layout`. New `POST /settings/footers/preview-fragment` (form `layout`) → compiled HTML fragment.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings_footers.py`:

```python
import json


def test_create_footer_from_layout_compiles_body(client, session):
    from awkns_outreach.db.models import FooterTemplate
    layout = {"rows": [{"columns": [
        {"blocks": [{"type": "image", "src": "http://x/a.png", "alt": "A"}]},
        {"blocks": [{"type": "image", "src": "http://x/b.png", "alt": "B"}]},
    ]}]}
    r = client.post("/settings/footers", auth=AUTH, follow_redirects=False, data={
        "name": "Built", "layout": json.dumps(layout),
    })
    assert r.status_code == 303
    f = session.query(FooterTemplate).filter_by(name="Built").one()
    assert f.layout == layout
    assert f.body_html.count("<td") == 2  # compiled to a 2-column row


def test_footer_preview_fragment_renders_layout(client):
    layout = {"rows": [{"columns": [{"blocks": [
        {"type": "button", "label": "Go", "href": "http://x"}]}]}]}
    r = client.post("/settings/footers/preview-fragment", auth=AUTH,
                    data={"layout": json.dumps(layout)})
    assert r.status_code == 200
    assert ">Go</a>" in r.text


def test_create_footer_warns_when_no_unsubscribe(client, session):
    layout = {"rows": [{"columns": [{"blocks": [
        {"type": "text", "html": "No link here"}]}]}]}
    r = client.post("/settings/footers", auth=AUTH, follow_redirects=False, data={
        "name": "NoUnsub", "layout": json.dumps(layout),
    })
    assert r.status_code == 303
    assert "unsubscribe" in r.headers["location"].lower()  # warning in redirect msg
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_settings_footers.py -k "from_layout or preview_fragment or warns_when_no_unsub" -v`
Expected: FAIL — `layout` isn't accepted; preview-fragment route 404s.

- [ ] **Step 3: Add the compile helper + wire the routes**

In `src/awkns_outreach/web/routes/settings.py`, add imports near the top:

```python
import json

from awkns_outreach.footers.layout import render_layout
```

Add a helper above `create_footer`:

```python
def _apply_layout(f, layout_json: str) -> Optional[str]:
    """Compile a builder layout into the footer's stored fields. Returns a
    warning message (or None). Raises ValueError on malformed JSON."""
    layout = json.loads(layout_json)
    html, text = render_layout(layout)
    f.layout = layout
    f.body_html = html
    f.body_text = text
    if "{unsubscribe_url}" not in html and "{unsubscribe_url}" not in text:
        return "Saved, but no unsubscribe link — add {unsubscribe_url} (legally required)."
    return None
```

Replace `create_footer` body with:

```python
def create_footer(
    name: str = Form(...), body_html: str = Form(""), body_text: str = Form(""),
    layout: str = Form(""), db: Session = Depends(get_db),
):
    f = FooterTemplate(name=name.strip(), body_html=body_html.strip(),
                       body_text=body_text.strip(), is_default=False)
    warn = None
    if layout.strip():
        try:
            warn = _apply_layout(f, layout)
        except ValueError:
            return RedirectResponse(
                "/settings/footers/new?msg=Invalid layout data.", status_code=303)
    db.add(f)
    db.commit()
    msg = warn or "Footer created."
    return RedirectResponse(f"/settings/footers/{f.id}/edit?msg={msg}", status_code=303)
```

In `update_footer`, add `layout: str = Form("")` to the signature, and replace the
final save block (`f.name = ... db.commit() ... return RedirectResponse(...saved.)`) with:

```python
    f.name = name.strip()
    warn = None
    if layout.strip():
        try:
            warn = _apply_layout(f, layout)
        except ValueError:
            return RedirectResponse(
                f"/settings/footers/{f.id}/edit?msg=Invalid layout data.",
                status_code=303)
    else:
        f.body_html = body_html.strip()
        f.body_text = body_text.strip()
    db.commit()
    msg = warn or "Footer saved."
    return RedirectResponse(f"/settings/footers/{f.id}/edit?msg={msg}", status_code=303)
```

Add the preview endpoint (near `_preview_html`):

```python
@router.post("/settings/footers/preview-fragment", response_class=HTMLResponse)
def footer_preview_fragment(request: Request, layout: str = Form("{}")):
    try:
        html, _ = render_layout(json.loads(layout))
    except (ValueError, TypeError):
        html = '<p style="color:#b91c1c">Invalid layout.</p>'
    # Show a placeholder unsubscribe target so the preview link isn't literal.
    html = html.replace("{unsubscribe_url}", "#preview-unsubscribe")
    return templates.TemplateResponse(
        request, "_footer_preview_fragment.html", {"preview": html})
```

- [ ] **Step 4: Create the preview fragment template**

Create `src/awkns_outreach/web/templates/_footer_preview_fragment.html`:

```html
<div class="border rounded p-3 bg-white">{{ preview | safe }}</div>
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_settings_footers.py -k "from_layout or preview_fragment or warns_when_no_unsub" -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Full footer suite (no regressions)**

Run: `uv run pytest tests/test_settings_footers.py -v`
Expected: PASS (legacy create/edit/default-immutable/delete tests still green — the legacy `body_html` path is untouched when `layout` is empty).

- [ ] **Step 7: Commit**

```bash
git add src/awkns_outreach/web/routes/settings.py \
        src/awkns_outreach/web/templates/_footer_preview_fragment.html \
        tests/test_settings_footers.py
git commit -m "feat: compile footer layout on save + live preview endpoint"
```

---

### Task 4: Builder UI (blocks, palette, settings, live preview)

**Files:**
- Create: `src/awkns_outreach/web/templates/_footer_builder.html` (markup + JS)
- Modify: `src/awkns_outreach/web/templates/footer_edit.html` (use builder when layout, else legacy)

**Interfaces:**
- Consumes: preview endpoint (Task 3); `/templates/upload-image` for image blocks.
- Produces: a hidden `<input name="layout">` kept in sync with the in-page layout model; the builder renders rows/columns/blocks and a live preview.

This task delivers a working builder WITHOUT drag (add/reorder via buttons); Task 5 adds SortableJS drag on top. Each step is a real, complete unit.

- [ ] **Step 1: Create the builder partial**

Create `src/awkns_outreach/web/templates/_footer_builder.html`:

```html
{# Visual footer builder. Expects `layout_json` (a JSON string, or "") in context
   and a form that will POST a `layout` field. Include _footer_builder_script
   after this and call twFooterInit(). #}
<input type="hidden" name="layout" id="footer-layout-field" value='{{ layout_json or "" }}'>
<div class="flex gap-2 mb-2 text-xs">
  <button type="button" onclick="twFbAddRow(1)" class="rounded border px-2 py-1">+ Row (1 col)</button>
  <button type="button" onclick="twFbAddRow(2)" class="rounded border px-2 py-1">+ Row (2 cols)</button>
  <button type="button" onclick="twFbAddRow(3)" class="rounded border px-2 py-1">+ Row (3 cols)</button>
</div>
<div id="fb-canvas" class="border rounded bg-white mx-auto"
     style="max-width:560px;min-height:120px"></div>
```

- [ ] **Step 2: Create the builder script partial**

Create `src/awkns_outreach/web/templates/_footer_builder_script.html`:

```html
<script>
// Minimal footer block builder. Holds the layout model in JS, renders the
// canvas, keeps the hidden `layout` field in sync, and posts to the preview
// endpoint on change. Drag is added by SortableJS in a later step.
let twFbLayout = { width: 560, rows: [] };

function twFbInit() {
  const raw = document.getElementById('footer-layout-field').value;
  try { if (raw) twFbLayout = JSON.parse(raw); } catch (e) {}
  if (!twFbLayout.rows) twFbLayout.rows = [];
  twFbRender();
}

function twFbSync() {
  document.getElementById('footer-layout-field').value = JSON.stringify(twFbLayout);
  twFbPreview();
}

function twFbAddRow(cols) {
  const columns = [];
  for (let i = 0; i < cols; i++) columns.push({ blocks: [] });
  twFbLayout.rows.push({ columns });
  twFbRender(); twFbSync();
}

function twFbAddBlock(r, c, type) {
  const defaults = {
    text: { type: 'text', html: 'New text · <a href="{unsubscribe_url}">Unsubscribe</a>', align: 'left' },
    image: { type: 'image', src: '', alt: '', href: '', align: 'left', width: 120 },
    button: { type: 'button', label: 'Button', href: 'https://', bg: '#0f172a', color: '#ffffff', align: 'left' },
    divider: { type: 'divider' },
  };
  twFbLayout.rows[r].columns[c].blocks.push(structuredClone(defaults[type]));
  twFbRender(); twFbSync();
}

function twFbRemoveBlock(r, c, b) {
  twFbLayout.rows[r].columns[c].blocks.splice(b, 1);
  twFbRender(); twFbSync();
}
function twFbRemoveRow(r) { twFbLayout.rows.splice(r, 1); twFbRender(); twFbSync(); }

function twFbEdit(r, c, b, field, value) {
  twFbLayout.rows[r].columns[c].blocks[b][field] = value;
  twFbSync();
}

async function twFbUploadImage(input, r, c, b) {
  if (!input.files || !input.files.length) return;
  const form = new FormData(); form.append('file', input.files[0]);
  try {
    const res = await fetch('/templates/upload-image', { method: 'POST', body: form });
    if (!res.ok) throw new Error((await res.json()).detail || 'Upload failed');
    const { url } = await res.json();
    twFbLayout.rows[r].columns[c].blocks[b].src = url;
    twFbRender(); twFbSync();
  } catch (e) { alert('Image upload failed: ' + e.message); }
  finally { input.value = ''; }
}

function twFbEsc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

function twFbBlockControls(r, c, b, block) {
  const t = block.type;
  const rm = `<button type="button" onclick="twFbRemoveBlock(${r},${c},${b})" class="text-red-600 text-xs">✕</button>`;
  if (t === 'text') {
    return `<textarea class="w-full border rounded text-xs p-1" rows="2"
      oninput="twFbEdit(${r},${c},${b},'html',this.value)">${twFbEsc(block.html)}</textarea>${rm}`;
  }
  if (t === 'image') {
    const preview = block.src ? `<img src="${twFbEsc(block.src)}" style="max-height:40px">` : '<span class="text-slate-400 text-xs">no image</span>';
    return `${preview}
      <input type="file" accept="image/*" class="text-xs" onchange="twFbUploadImage(this,${r},${c},${b})">
      <input class="w-full border rounded text-xs p-1" placeholder="link (optional)" value="${twFbEsc(block.href)}"
        oninput="twFbEdit(${r},${c},${b},'href',this.value)">
      <select class="border rounded text-xs" onchange="twFbEdit(${r},${c},${b},'align',this.value)">
        ${['left','center','right'].map(a => `<option ${block.align===a?'selected':''}>${a}</option>`).join('')}
      </select>${rm}`;
  }
  if (t === 'button') {
    return `<input class="w-full border rounded text-xs p-1" placeholder="label" value="${twFbEsc(block.label)}"
        oninput="twFbEdit(${r},${c},${b},'label',this.value)">
      <input class="w-full border rounded text-xs p-1" placeholder="href" value="${twFbEsc(block.href)}"
        oninput="twFbEdit(${r},${c},${b},'href',this.value)">${rm}`;
  }
  return `<span class="text-xs text-slate-500">divider</span>${rm}`;
}

function twFbRender() {
  const el = document.getElementById('fb-canvas');
  el.innerHTML = twFbLayout.rows.map((row, r) => `
    <div class="border-b p-2">
      <div class="flex justify-between text-xs text-slate-400 mb-1">
        <span>Row ${r + 1}</span>
        <button type="button" onclick="twFbRemoveRow(${r})" class="text-red-600">Remove row</button>
      </div>
      <div class="flex gap-2">
        ${row.columns.map((col, c) => `
          <div class="flex-1 border rounded p-1 min-h-[48px]">
            ${col.blocks.map((block, b) => `<div class="mb-1">${twFbBlockControls(r, c, b, block)}</div>`).join('')}
            <div class="flex gap-1 flex-wrap">
              ${['text','image','button','divider'].map(t =>
                `<button type="button" class="text-[10px] rounded border px-1"
                   onclick="twFbAddBlock(${r},${c},'${t}')">+${t}</button>`).join('')}
            </div>
          </div>`).join('')}
      </div>
    </div>`).join('') || '<div class="text-slate-400 text-sm p-4 text-center">Add a row to start.</div>';
}

async function twFbPreview() {
  const form = new FormData();
  form.append('layout', JSON.stringify(twFbLayout));
  try {
    const res = await fetch('/settings/footers/preview-fragment', { method: 'POST', body: form });
    document.getElementById('fb-preview').innerHTML = await res.text();
  } catch (e) {}
}
</script>
```

- [ ] **Step 3: Wire footer_edit.html to use the builder**

In `src/awkns_outreach/web/templates/footer_edit.html`, replace the `Body (HTML)` block and the Preview pane so the builder is used when the footer has a layout (or is new), and the legacy HTML editor otherwise. Concretely:

- In the form, replace the `Body (HTML)` label/editor with:

```html
    {% if f and f.body_html and not f.layout %}
    {# Legacy footer authored with raw HTML — keep the old editor. #}
    <label class="text-sm">Body (HTML)
      <textarea name="body_html" rows="8"
                class="mt-1 w-full border rounded px-2 py-1 text-xs font-mono">{{ f.body_html }}</textarea>
    </label>
    {% else %}
    <div class="text-sm">Layout
      {% set layout_json = (f.layout | tojson) if (f and f.layout) else '' %}
      {% include "_footer_builder.html" %}
    </div>
    {% endif %}
```

- Replace the Preview pane's inner `{% if preview %}…{% endif %}` with a live target:

```html
    <div class="text-sm font-medium mb-2">Preview</div>
    <div id="fb-preview">
      {% if preview is not none %}<div class="border rounded p-3 bg-white">{{ preview | safe }}</div>{% endif %}
    </div>
```

- At the end of the content block, include the builder script and init:

```html
{% include "_footer_builder_script.html" %}
<script>twFbInit();</script>
```

- [ ] **Step 4: Manual verification (headless-driven, like earlier sessions)**

Run the app on a throwaway SQLite DB (`DATABASE_URL=sqlite…`, `ADMIN_PASSWORD=…`, `Base.metadata.create_all`). Then:
- `GET /settings/footers/new` → screenshot: palette + empty canvas render.
- Drive via curl or a headless script: POST a built layout to `/settings/footers` and confirm the saved `body_html` has the expected table; `GET .../edit` reloads the builder (hidden field populated).
- Confirm `/settings/footers/preview-fragment` returns the compiled fragment.

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/web/templates/_footer_builder.html \
        src/awkns_outreach/web/templates/_footer_builder_script.html \
        src/awkns_outreach/web/templates/footer_edit.html
git commit -m "feat: footer visual builder UI (blocks + live preview)"
```

---

### Task 5: SortableJS drag-and-drop

**Files:**
- Modify: `src/awkns_outreach/web/templates/footer_edit.html` (load SortableJS CDN)
- Modify: `src/awkns_outreach/web/templates/_footer_builder_script.html` (wire Sortable)

**Interfaces:**
- Consumes: the `twFbLayout` model + `twFbRender`/`twFbSync` (Task 4).
- Produces: dragging block cards reorders/moves them within and across columns, updating `twFbLayout` and the preview.

- [ ] **Step 1: Load SortableJS**

In `footer_edit.html`'s `{% block head_extra %}`, add:

```html
<script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js"></script>
```

- [ ] **Step 2: Make block cards identifiable + attach Sortable**

In `_footer_builder_script.html`, in `twFbRender`, give each column's block container a stable hook and each block a data index. Change the column inner markup so blocks live in a `<div class="fb-col" data-r data-c>` whose children are `<div class="fb-block" data-b>`. Then after `el.innerHTML = …` in `twFbRender`, add:

```javascript
  document.querySelectorAll('.fb-col').forEach((colEl) => {
    Sortable.create(colEl, {
      group: 'fb-blocks',           // same group ⇒ drag across columns/rows
      handle: '.fb-drag',
      animation: 120,
      onEnd: twFbOnDrag,
    });
  });
```

Add the drag-commit handler (rebuilds the model from the DOM order, then re-renders):

```javascript
function twFbOnDrag(evt) {
  // Move the block in the model from (fromR,fromC,oldIndex) to (toR,toC,newIndex).
  const from = evt.from, to = evt.to;
  const fr = +from.dataset.r, fc = +from.dataset.c;
  const tr = +to.dataset.r, tc = +to.dataset.c;
  const [moved] = twFbLayout.rows[fr].columns[fc].blocks.splice(evt.oldIndex, 1);
  twFbLayout.rows[tr].columns[tc].blocks.splice(evt.newIndex, 0, moved);
  twFbRender(); twFbSync();
}
```

Add a drag handle to each block card in `twFbBlockControls` (prefix its return with):

```javascript
  const handle = '<span class="fb-drag cursor-move text-slate-400 mr-1" title="Drag">⠿</span>';
```
and wrap the card so `handle` sits before the controls; ensure each block wrapper in `twFbRender` is `<div class="fb-block" data-b="${b}">…</div>` and each column wrapper is `<div class="fb-col" data-r="${r}" data-c="${c}">…</div>`.

- [ ] **Step 3: Manual verification**

Run the app; open a footer with 2+ blocks across two columns; drag a block from one column to the other and reorder within a column. Confirm the canvas updates, the hidden `layout` field reflects the new order (inspect via DOM), and the live preview re-renders. Save; reopen; order persists.

- [ ] **Step 4: Commit**

```bash
git add src/awkns_outreach/web/templates/footer_edit.html \
        src/awkns_outreach/web/templates/_footer_builder_script.html
git commit -m "feat: SortableJS drag-and-drop for footer blocks"
```

---

### Task 6: Full regression + verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS — new compiler/route tests green; existing footer + web tests unaffected (legacy path untouched).

- [ ] **Step 2: End-to-end app check**

Launch the app on a throwaway SQLite DB. Build a footer with a text row (containing `{unsubscribe_url}`) and a 2-column image row; save; confirm:
- stored `body_html` is a nested table with a 2-`<td>` row (side-by-side images);
- the live preview matches;
- reopening the footer rehydrates the builder.
Screenshot the builder + preview. Fix anything that doesn't match before claiming done.

- [ ] **Step 3: If anything fails**

Use `superpowers:systematic-debugging`. Do not proceed until `uv run pytest -q` is clean and the manual check matches.

---

## Self-Review

**Spec coverage:** layout column (Task 1); render_layout compiler incl. `{unsubscribe_url}` passthrough + plain-text derivation (Task 2); save-compiles-body + preview endpoint + compliance warning + legacy path (Task 3); builder UI with 4 block types + palette + settings + live preview (Task 4); true drag-and-drop via SortableJS (Task 5); regression + e2e (Task 6). All spec sections mapped. ✓

**Placeholder scan:** none — backend steps carry complete code + commands; front-end steps carry complete builder/script markup and concrete Sortable wiring.

**Type consistency:** `render_layout(layout: dict) -> tuple[str,str]` defined in Task 2, consumed by `_apply_layout` and the preview route in Task 3. `FooterTemplate.layout: Optional[dict]` (Task 1) read by `footer_edit.html` (`f.layout | tojson`) and written by `_apply_layout`. Hidden field `name="layout"` produced by `_footer_builder.html` and consumed by `create_footer`/`update_footer`/`footer_preview_fragment`. JS model `twFbLayout` mirrors the same rows→columns→blocks shape the compiler expects. ✓
