# Seed Import Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate CSV/JSON seed imports at create-campaign and companies-edit time, blocking on duplicate email and format/missing-name via an interrupting popup, so bad data never silently reaches the later Convert step.

**Architecture:** One pure validator (`duplicate_email_problems`) in `apollo/seed.py`. Two routes (`create_campaign`, `save_companies` import branch) parse → validate → on any problem, re-render the same form (server-side, approach A1) with a `problems` list and do NOT persist. A shared `<dialog>` partial auto-opens listing the problems.

**Tech Stack:** FastAPI, Jinja2 templates (server-rendered), SQLAlchemy, pytest + FastAPI TestClient, in-memory SQLite for tests.

## Global Constraints

- Validation is server-side only (single source of truth; no front-end parsing). — spec "Approach A1"
- Email normalization MUST match Convert/insert: `email.strip().lower()`. — spec "Validation logic"
- Blocking problems this version: **duplicate email** and **format error / missing `name`**. Missing email and soft data-quality issues do NOT block. — spec "Scope"
- Duplicate check runs on the **final stored list**: imported rows for create/replace; `existing + imported` for append. — spec "Validation logic"
- Preserve pasted text on re-render; note that uploaded files can't be auto-restored. — spec "Popup UX"

---

### Task 1: `duplicate_email_problems` validator

**Files:**
- Modify: `src/awkns_outreach/apollo/seed.py` (add function at end of file)
- Test: `tests/test_seed.py` (append tests)

**Interfaces:**
- Consumes: nothing (pure function over `list[dict]`).
- Produces: `duplicate_email_problems(rows: list[dict]) -> list[str]` — one human-readable string per email used by 2+ rows; `[]` when none. Rows without an email are ignored. Emails normalized via `strip().lower()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_seed.py`:

```python
from awkns_outreach.apollo.seed import duplicate_email_problems


def test_duplicate_email_problems_none_when_all_unique():
    rows = [{"name": "A", "email": "a@x.com"}, {"name": "B", "email": "b@x.com"}]
    assert duplicate_email_problems(rows) == []


def test_duplicate_email_problems_flags_repeated_email():
    rows = [
        {"name": "A", "email": "shan@shanhair.com"},
        {"name": "B", "email": "shan@shanhair.com"},
    ]
    assert duplicate_email_problems(rows) == [
        "Duplicate email used by 2 rows: shan@shanhair.com"
    ]


def test_duplicate_email_problems_normalizes_case_and_whitespace():
    rows = [{"name": "A", "email": "A@X.com "}, {"name": "B", "email": "a@x.com"}]
    assert duplicate_email_problems(rows) == [
        "Duplicate email used by 2 rows: a@x.com"
    ]


def test_duplicate_email_problems_ignores_rows_without_email():
    rows = [{"name": "A"}, {"name": "B", "email": "b@x.com"}, {"name": "C"}]
    assert duplicate_email_problems(rows) == []


def test_duplicate_email_problems_reports_each_group_sorted():
    rows = [
        {"email": "b@x.com"}, {"email": "b@x.com"},
        {"email": "a@x.com"}, {"email": "a@x.com"}, {"email": "a@x.com"},
    ]
    assert duplicate_email_problems(rows) == [
        "Duplicate email used by 3 rows: a@x.com",
        "Duplicate email used by 2 rows: b@x.com",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_seed.py -k duplicate_email_problems -v`
Expected: FAIL with `ImportError: cannot import name 'duplicate_email_problems'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/awkns_outreach/apollo/seed.py`:

```python
def duplicate_email_problems(rows: list[dict[str, str]]) -> list[str]:
    """One problem string per email used by 2+ rows; [] when none.

    Emails are normalized (strip().lower()) exactly like
    convert_seed_companies_to_leads and the Lead insert, so what we flag here is
    precisely what would later collide on UniqueConstraint(campaign_id, email).
    Rows without an email are ignored — a missing email is allowed at seed stage
    (Apollo may fill it in later).
    """
    from collections import Counter

    counts = Counter(
        r["email"].strip().lower() for r in rows if r.get("email")
    )
    return [
        f"Duplicate email used by {n} rows: {email}"
        for email, n in sorted(counts.items())
        if n > 1
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_seed.py -k duplicate_email_problems -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/apollo/seed.py tests/test_seed.py
git commit -m "feat: duplicate_email_problems seed validator"
```

---

### Task 2: Shared popup partial + create-campaign validation

**Files:**
- Create: `src/awkns_outreach/web/templates/_import_problems_dialog.html`
- Modify: `src/awkns_outreach/web/templates/new_campaign.html` (include partial)
- Modify: `src/awkns_outreach/web/routes/admin.py` (import validator; rework `create_campaign`)
- Test: `tests/test_web.py` (append)

**Interfaces:**
- Consumes: `duplicate_email_problems` (Task 1); template var `problems: list[str]`.
- Produces: `create_campaign` re-renders `new_campaign.html` with `problems` (HTTP 200) and creates nothing when problems exist. The partial renders a `<dialog id="import-problems">` iff `problems` is truthy.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web.py`:

```python
def test_create_campaign_blocks_on_duplicate_email(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    csv = (
        "name,email\n"
        "Shan Hair,shan@shanhair.com\n"
        "Shan Hair,shan@shanhair.com\n"
    )
    r = client.post("/campaigns", auth=auth, data={
        "name": "Beauty", "titles": "", "seed_text": csv, "angle_prompt": "",
    }, follow_redirects=False)

    assert r.status_code == 200  # re-rendered, not a redirect
    assert session.query(Campaign).count() == 0  # nothing created
    assert "shan@shanhair.com" in r.text  # the offending email is shown
    assert 'id="import-problems"' in r.text  # the popup is present
    assert "Beauty" in r.text  # name preserved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web.py::test_create_campaign_blocks_on_duplicate_email -v`
Expected: FAIL — a duplicate-email CSV currently creates the campaign (redirect 303), so `status_code == 200` and `count() == 0` assertions fail.

- [ ] **Step 3: Create the shared popup partial**

Create `src/awkns_outreach/web/templates/_import_problems_dialog.html`:

```html
{# Interrupting popup for blocked seed imports. Renders only when `problems`
   (a list[str]) is non-empty; auto-opens on load. Included by new_campaign.html
   and seed_companies_edit.html. #}
{% if problems %}
<dialog id="import-problems" class="rounded border p-0 max-w-md w-full">
  <div class="p-4 space-y-3">
    <h2 class="text-sm font-semibold text-red-700">Import stopped — fix these first</h2>
    <ul class="list-disc pl-5 text-sm text-slate-700 space-y-1">
      {% for p in problems %}<li>{{ p }}</li>{% endfor %}
    </ul>
    <p class="text-xs text-slate-500">
      Uploaded files can’t be restored automatically — re-select the file after
      fixing. Pasted text is kept.
    </p>
    <div class="flex justify-end">
      <button type="button"
              onclick="document.getElementById('import-problems').close()"
              class="rounded bg-slate-900 text-white text-xs px-3 py-1.5">Close and fix</button>
    </div>
  </div>
</dialog>
<script>
  (function () {
    var d = document.getElementById("import-problems");
    if (d && typeof d.showModal === "function") d.showModal();
  })();
</script>
{% endif %}
```

- [ ] **Step 4: Include the partial in new_campaign.html**

In `src/awkns_outreach/web/templates/new_campaign.html`, add the include as the first line inside the content block. Change:

```html
{% block content %}
<a href="/" class="text-xs text-slate-500 hover:underline">&larr; Campaigns</a>
```

to:

```html
{% block content %}
{% include "_import_problems_dialog.html" %}
<a href="/" class="text-xs text-slate-500 hover:underline">&larr; Campaigns</a>
```

- [ ] **Step 5: Rework `create_campaign` to validate and re-render**

In `src/awkns_outreach/web/routes/admin.py`, update the import on line 18. Change:

```python
from awkns_outreach.apollo.seed import SEED_FIELDS, parse_seed_companies
```

to:

```python
from awkns_outreach.apollo.seed import (
    SEED_FIELDS,
    duplicate_email_problems,
    parse_seed_companies,
)
```

Then replace the body of `create_campaign` (currently lines 144-167, from `try:` through the final `return RedirectResponse(...)`) with:

```python
    problems: list[str] = []
    seed_companies: list[dict] = []
    try:
        seed_companies = _read_seed_input(seed_file, seed_text)
    except ValueError as exc:
        problems.append(f"Seed import failed: {exc}")
    else:
        problems.extend(duplicate_email_problems(seed_companies))

    if problems:
        return templates.TemplateResponse(
            request, "new_campaign.html",
            {
                "problems": problems,
                "name": name,
                "titles": titles,
                "angle_prompt": angle_prompt,
                "seed_text": seed_text,
            },
        )

    c = Campaign(
        name=name.strip(),
        target_titles=_split_lines(titles),
        seed_companies=seed_companies,
        angle_prompt=angle_prompt.strip() or None,
        sender_identity={},
    )
    db.add(c)
    db.commit()
    msg = f"Campaign created with {len(seed_companies)} seed companies."
    return RedirectResponse(f"/campaigns/{c.id}?msg={msg}", status_code=303)
```

- [ ] **Step 6: Run the new test + the two existing block tests**

Run: `uv run pytest tests/test_web.py -k "duplicate_email or malformed_seed or missing_name_row or create_and_view" -v`
Expected: PASS (4 passed) — the new duplicate-email test, plus the pre-existing malformed-seed and missing-name tests still green (their `"Seed import failed"` / `"missing required field"` strings now render inside the dialog), plus the happy-path create still redirects.

- [ ] **Step 7: Commit**

```bash
git add src/awkns_outreach/web/templates/_import_problems_dialog.html \
        src/awkns_outreach/web/templates/new_campaign.html \
        src/awkns_outreach/web/routes/admin.py \
        tests/test_web.py
git commit -m "feat: block create-campaign import on duplicate email via popup"
```

---

### Task 3: companies-edit import validation

**Files:**
- Modify: `src/awkns_outreach/web/routes/admin.py` (`save_companies`: add `request` param; rework import branch)
- Modify: `src/awkns_outreach/web/templates/seed_companies_edit.html` (include partial)
- Test: `tests/test_web.py` (append)

**Interfaces:**
- Consumes: `duplicate_email_problems` (Task 1); the `_import_problems_dialog.html` partial (Task 2); template var `problems`.
- Produces: `save_companies` import branch re-renders `seed_companies_edit.html` with `problems` (HTTP 200) and leaves `c.seed_companies` unchanged when problems exist.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web.py`:

```python
def test_import_companies_blocks_on_duplicate_email(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    csv = "name,email\nA,dup@x.com\nB,dup@x.com\n"
    r = client.post(f"/campaigns/{c.id}/companies", auth=auth, data={
        "action": "import", "import_mode": "replace", "seed_text": csv,
    }, follow_redirects=False)

    assert r.status_code == 200  # re-rendered, not a redirect
    assert 'id="import-problems"' in r.text
    assert "dup@x.com" in r.text
    session.refresh(c)
    assert c.seed_companies == []  # unchanged


def test_import_companies_append_detects_collision_with_existing(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[],
                 seed_companies=[{"name": "A", "email": "dup@x.com"}])
    session.add(c)
    session.commit()

    csv = "name,email\nB,dup@x.com\n"
    r = client.post(f"/campaigns/{c.id}/companies", auth=auth, data={
        "action": "import", "import_mode": "append", "seed_text": csv,
    }, follow_redirects=False)

    assert r.status_code == 200
    assert "dup@x.com" in r.text
    session.refresh(c)
    assert c.seed_companies == [{"name": "A", "email": "dup@x.com"}]  # unchanged


def test_import_companies_clean_replace_still_saves(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    csv = "name,email\nA,a@x.com\nB,b@x.com\n"
    r = client.post(f"/campaigns/{c.id}/companies", auth=auth, data={
        "action": "import", "import_mode": "replace", "seed_text": csv,
    }, follow_redirects=False)

    assert r.status_code == 303  # saved + redirect, unchanged happy path
    session.refresh(c)
    assert len(c.seed_companies) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web.py -k "import_companies" -v`
Expected: FAIL — duplicate-email imports currently save and redirect (303), so the `status_code == 200` and `c.seed_companies == []` assertions fail.

- [ ] **Step 3: Add `request` param to `save_companies`**

In `src/awkns_outreach/web/routes/admin.py`, in the `save_companies` signature (starts at the `def save_companies(` line), add `request: Request` as the first parameter after `campaign_id`. Change:

```python
def save_companies(
    campaign_id: str,
    action: str = Form("save"),
```

to:

```python
def save_companies(
    campaign_id: str,
    request: Request,
    action: str = Form("save"),
```

- [ ] **Step 4: Rework the import branch to validate**

In `save_companies`, replace the `elif action == "import":` branch (currently):

```python
    elif action == "import":
        try:
            imported = _read_seed_input(seed_file, seed_text)
        except ValueError as exc:
            return RedirectResponse(
                f"/campaigns/{c.id}/companies?msg=Import failed: {exc}", status_code=303
            )
        if import_mode == "append":
            c.seed_companies = (c.seed_companies or []) + imported
            msg = f"Appended {len(imported)} companies (total {len(c.seed_companies)})."
        else:
            c.seed_companies = imported
            msg = f"Imported {len(imported)} companies (replaced)."
```

with:

```python
    elif action == "import":
        problems: list[str] = []
        imported: list[dict] = []
        try:
            imported = _read_seed_input(seed_file, seed_text)
        except ValueError as exc:
            problems.append(f"Import failed: {exc}")
        else:
            final_rows = (
                (c.seed_companies or []) + imported
                if import_mode == "append"
                else imported
            )
            problems.extend(duplicate_email_problems(final_rows))

        if problems:
            return templates.TemplateResponse(
                request, "seed_companies_edit.html",
                {
                    "c": c,
                    "companies": c.seed_companies or [],
                    "fields": SEED_FIELDS,
                    "problems": problems,
                },
            )

        c.seed_companies = final_rows
        if import_mode == "append":
            msg = f"Appended {len(imported)} companies (total {len(final_rows)})."
        else:
            msg = f"Imported {len(imported)} companies (replaced)."
```

- [ ] **Step 5: Include the partial in seed_companies_edit.html**

In `src/awkns_outreach/web/templates/seed_companies_edit.html`, add the include as the first line inside the content block. Change:

```html
{% block content %}
<div class="mb-4">
```

to:

```html
{% block content %}
{% include "_import_problems_dialog.html" %}
<div class="mb-4">
```

- [ ] **Step 6: Run the import tests + the existing save test**

Run: `uv run pytest tests/test_web.py -k "import_companies or save_companies_persists" -v`
Expected: PASS (4 passed) — the three new import tests plus the existing field-by-field save test (unaffected: the `save` branch is untouched).

- [ ] **Step 7: Commit**

```bash
git add src/awkns_outreach/web/routes/admin.py \
        src/awkns_outreach/web/templates/seed_companies_edit.html \
        tests/test_web.py
git commit -m "feat: block companies-edit import on duplicate email via popup"
```

---

### Task 4: Full-suite regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `uv run pytest -q`
Expected: PASS — all tests green, including the pre-existing `test_web.py` create/import tests whose presentation changed (they assert on substrings still present in the dialog).

- [ ] **Step 2: If anything fails, fix and re-run**

Investigate failures with `superpowers:systematic-debugging`. Do not proceed until `uv run pytest -q` is clean.

---

## Self-Review

**Spec coverage:**
- Validator (duplicate email, normalized, ignores missing email) → Task 1. ✓
- Format error / missing name still blocks, now via popup → Tasks 2 & 3 (reuses existing `parse_seed_companies` ValueError). ✓
- create_campaign entry point → Task 2. ✓
- save_companies import entry point (append uses merged list) → Task 3. ✓
- A1 server re-render + auto-open modal, preserve pasted text, file-upload caveat → Task 2 partial + includes. ✓
- Out of scope (missing-email block, soft warnings, front-end validation) → not implemented. ✓
- Tests: unit validator + web routes (block + no-persist + happy path) → Tasks 1-3; full regression → Task 4. ✓

**Placeholder scan:** none — all steps contain concrete code/commands.

**Type consistency:** `duplicate_email_problems(rows: list[dict]) -> list[str]` defined in Task 1, consumed with that exact signature in Tasks 2 & 3. Template var `problems: list[str]` produced by both routes and consumed by `_import_problems_dialog.html` and both including templates. `save_companies` gains `request: Request` (Task 3 Step 3) before it is used by `templates.TemplateResponse` (Task 3 Step 4). ✓
