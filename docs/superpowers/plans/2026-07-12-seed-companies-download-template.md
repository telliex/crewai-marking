# Seed-Companies Download Template Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `GET /campaigns/seed-template.csv` endpoint that returns a downloadable example CSV for the seed-companies import feature, and link to it from both places that offer seed-companies import (New Campaign form, Edit Companies page).

**Architecture:** One new route handler in the existing `admin.py` router (already gated by `require_admin`), built from the existing `SEED_FIELDS` tuple so the template can't drift from what `parse_seed_companies` accepts. Two one-line template edits add the download link next to the existing import UI. One new test asserts the endpoint's headers and body shape.

**Tech Stack:** FastAPI (`fastapi.responses.Response`), Python stdlib `csv`/`io`, Jinja2 templates, pytest + `fastapi.testclient.TestClient`.

## Global Constraints

- CSV only — no JSON template file (spec: "No JSON template file — CSV only").
- Do not change the existing inline JSON placeholder text in the textareas.
- Do not change `apollo/seed.py` (`SEED_FIELDS`, `parse_seed_companies`) — the template is generated from the existing source of truth, not a new one.
- Column order/names must come from `SEED_FIELDS = ("name", "website", "country", "category", "tier", "angle")` (`src/awkns_outreach/apollo/seed.py:21`).

---

### Task 1: `GET /campaigns/seed-template.csv` endpoint

**Files:**
- Modify: `src/awkns_outreach/web/routes/admin.py` (imports near top, ~lines 1-21; new route near `new_campaign_form` at line 107-109)
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: route `GET /campaigns/seed-template.csv` → `text/csv` body, `Content-Disposition: attachment; filename=seed_companies_template.csv` header, gated by the router's existing `require_admin` dependency (same as every other route in this file).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web.py`, directly after `test_admin_create_and_view_campaign` (after line 121, i.e. right after its closing `assert detail.status_code == 200 and "JP studios" in detail.text` line):

```python
def test_seed_template_csv_download(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    r = client.get("/campaigns/seed-template.csv", auth=auth)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "seed_companies_template.csv" in r.headers["content-disposition"]
    first_line = r.text.splitlines()[0]
    assert first_line.split(",") == list(admin.SEED_FIELDS)
```

This file already imports `from awkns_outreach.web.routes import admin` (line 16) and `from awkns_outreach.config import settings` (line 12), so no new imports are needed for the test itself.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web.py::test_seed_template_csv_download -v`
Expected: FAIL with a 404 (route doesn't exist yet) — e.g. `assert 404 == 200`.

- [ ] **Step 3: Add imports to `admin.py`**

At the top of `src/awkns_outreach/web/routes/admin.py`, the current imports are:

```python
from __future__ import annotations

from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
```

Change to:

```python
from __future__ import annotations

import csv
import io
from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
```

(Only the `import csv`, `import io` lines are new, and `Response` is added to the existing `fastapi.responses` import line.)

- [ ] **Step 4: Add the route**

In `src/awkns_outreach/web/routes/admin.py`, immediately after `new_campaign_form` (currently lines 107-109):

```python
@router.get("/campaigns/new", response_class=HTMLResponse)
def new_campaign_form(request: Request):
    return templates.TemplateResponse(request, "new_campaign.html", {})
```

add:

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

`SEED_FIELDS` is already imported in this file (line 16: `from awkns_outreach.apollo.seed import SEED_FIELDS, parse_seed_companies`) — no new import needed for it.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_web.py::test_seed_template_csv_download -v`
Expected: PASS

- [ ] **Step 6: Run the full test file to check for regressions**

Run: `uv run pytest tests/test_web.py -v`
Expected: all tests PASS (no route ordering or import conflicts introduced)

- [ ] **Step 7: Commit**

```bash
git add src/awkns_outreach/web/routes/admin.py tests/test_web.py
git commit -m "feat: add downloadable CSV template for seed-companies import"
```

---

### Task 2: Link the download from both import UIs

**Files:**
- Modify: `src/awkns_outreach/web/templates/new_campaign.html:18-24`
- Modify: `src/awkns_outreach/web/templates/seed_companies_edit.html:53-55`

**Interfaces:**
- Consumes: `GET /campaigns/seed-template.csv` from Task 1 (must be merged/present first).

- [ ] **Step 1: Add the link to `new_campaign.html`**

Current (lines 18-24):

```html
    <label class="block text-sm font-medium mb-1">Seed companies <span class="text-slate-400 font-normal">(optional — add or edit later)</span></label>
    <p class="text-xs text-slate-500 mb-1">
      Upload a <b>JSON</b> array or <b>CSV</b>. Columns: name, website, country, category, tier, angle
      (all optional; only <code>website</code> is used to query Apollo).
    </p>
    <input type="file" name="seed_file" accept=".json,.csv"
      class="block w-full text-sm mb-2 file:mr-2 file:rounded file:border file:bg-white file:px-2 file:py-1">
```

Replace with:

```html
    <label class="block text-sm font-medium mb-1">Seed companies <span class="text-slate-400 font-normal">(optional — add or edit later)</span></label>
    <p class="text-xs text-slate-500 mb-1">
      Upload a <b>JSON</b> array or <b>CSV</b>. Columns: name, website, country, category, tier, angle
      (all optional; only <code>website</code> is used to query Apollo).
      <a href="/campaigns/seed-template.csv" class="text-blue-600 hover:underline">Download template (CSV)</a>
    </p>
    <input type="file" name="seed_file" accept=".json,.csv"
      class="block w-full text-sm mb-2 file:mr-2 file:rounded file:border file:bg-white file:px-2 file:py-1">
```

- [ ] **Step 2: Add the link to `seed_companies_edit.html`**

Current (line 53):

```html
    <p class="text-xs text-slate-500">JSON array or CSV · columns: name, website, country, category, tier, angle</p>
```

Replace with:

```html
    <p class="text-xs text-slate-500">JSON array or CSV · columns: name, website, country, category, tier, angle ·
      <a href="/campaigns/seed-template.csv" class="text-blue-600 hover:underline">Download template (CSV)</a>
    </p>
```

- [ ] **Step 3: Manually verify both pages render the link**

Run: `uv run uvicorn awkns_outreach.web.app:app --reload` (or whatever the project's existing dev-server command is — check `README.md` if unsure), then in a browser:
- Visit `/campaigns/new`, confirm the "Download template (CSV)" link appears under Seed companies and downloads a CSV file with header `name,website,country,category,tier,angle` and two example rows.
- Visit an existing campaign's `/campaigns/{id}/companies` page, confirm the same link appears in the "Import companies" box and works.

- [ ] **Step 4: Run the full test suite once more**

Run: `uv run pytest tests/test_web.py -v`
Expected: all tests PASS (template edits don't add executable logic, but this confirms no Jinja syntax errors broke page rendering — `test_admin_create_and_view_campaign` renders `new_campaign.html`-adjacent pages and would fail on a template syntax error).

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/web/templates/new_campaign.html src/awkns_outreach/web/templates/seed_companies_edit.html
git commit -m "feat: link seed-companies CSV template from both import UIs"
```

---

## Self-Review Notes

- **Spec coverage:** Backend endpoint (Task 1), frontend links in both locations (Task 2), test (Task 1) — all three spec sections covered. JSON template and parser changes are explicitly out of scope per the spec and untouched.
- **Placeholder scan:** none found — all steps show exact code/commands.
- **Type/name consistency:** `SEED_FIELDS` used identically in the route (Task 1) and the test assertion (Task 1); no cross-task signature drift since Task 2 only consumes the URL path, not any Python symbol.
