# Seed Import Validation + Convert Confirm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require every seed-company row to have a `name`, stop "New campaign" from silently creating an empty campaign when seed import fails, and turn "Convert seed companies to leads" into a preview-then-confirm action like Apollo enrich already is.

**Architecture:** All three changes are small, targeted edits to existing functions — no new files, no schema changes. `parse_seed_companies` (the single JSON/CSV parser both import entry points already share) gains a validation pass. `create_campaign` gains an early-return re-render path. `convert_seed_companies_to_leads` gains a `confirm` form flag that gates the existing DB-write branch.

**Tech Stack:** FastAPI, Jinja2 templates, SQLAlchemy, pytest + `fastapi.testclient.TestClient` (existing project stack, unchanged).

## Global Constraints

- Required-field rule is `name` only — `website`/`email` stay optional (spec section 1; do not add email-format, tier-enum, or CSV-header checks — explicitly out of scope).
- Validation failures reject the whole batch (no partial import) with one combined, row-numbered error message.
- `save_companies` (Edit companies import, `admin.py:454-498`) needs **no code change** — it already blocks-and-redirects on `ValueError` without saving; it inherits the better error text for free once Task 1 lands.
- Reuse the existing `msg` template-context key and `base.html` banner for all new error/preview messages — no new banner styles or template blocks.

---

### Task 1: `parse_seed_companies` requires `name` on every row

**Files:**
- Modify: `src/awkns_outreach/apollo/seed.py:101-106`
- Test: `tests/test_seed.py`

**Interfaces:**
- Consumes: nothing new — same `parse_seed_companies(raw: str, filename: Optional[str] = None) -> list[dict[str, str]]` signature.
- Produces: `parse_seed_companies` now raises `ValueError` (same type it already raises for malformed JSON) when any non-blank row is missing `name`, with a message of the form `"row {i}: missing required field 'name'"`, multiple offending rows joined with `"; "`. `_read_seed_input` (`admin.py:59-70`) and every caller already catches `ValueError` — no signature change needed downstream.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_seed.py` (after `test_parse_bad_json_raises`, which is the last test in the file):

```python
def test_parse_json_row_missing_name_raises_with_row_number():
    raw = '[{"name": "Toyota", "website": "toyota.co.jp"}, {"website": "sony.co.jp"}]'
    with pytest.raises(ValueError, match="row 2: missing required field 'name'"):
        parse_seed_companies(raw, None)


def test_parse_csv_row_missing_name_raises_with_row_number():
    raw = "name,website\nToyota,toyota.co.jp\n,sony.co.jp\n"
    with pytest.raises(ValueError, match="row 2: missing required field 'name'"):
        parse_seed_companies(raw, "seed.csv")


def test_parse_multiple_bad_rows_lists_every_row_in_one_error():
    raw = '[{"website": "a.com"}, {"name": "Ok"}, {"website": "b.com"}]'
    with pytest.raises(ValueError) as exc_info:
        parse_seed_companies(raw, None)
    message = str(exc_info.value)
    assert "row 1: missing required field 'name'" in message
    assert "row 3: missing required field 'name'" in message
    assert "row 2" not in message  # the valid row must not be reported
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_seed.py -k "missing_name or multiple_bad_rows" -v`
Expected: 3 FAIL — rows with no `name` are currently accepted silently (no `ValueError` raised), so `pytest.raises` fails to catch anything.

- [ ] **Step 3: Implement the validation**

Replace `src/awkns_outreach/apollo/seed.py:101-106` (the closing loop of `parse_seed_companies`):

```python
    out: list[dict[str, str]] = []
    for record in records:
        cleaned = _clean_row(record)
        if cleaned:
            out.append(cleaned)
    return out
```

with:

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

- [ ] **Step 4: Run the full seed test file to verify everything passes**

Run: `.venv/bin/pytest tests/test_seed.py -v`
Expected: all tests PASS, including the 3 new ones and the pre-existing
`test_parse_row_without_website_is_kept_but_has_no_domain` and
`test_parse_json_array_captures_email_and_contact_fields` (both have `name`
on every row, so the new rule doesn't touch them).

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/apollo/seed.py tests/test_seed.py
git commit -m "$(cat <<'EOF'
feat: require name on every seed-company row

parse_seed_companies is the single JSON/CSV entry point both "New
campaign" and "Edit companies" import through — validating there
means a row with no name is now rejected (with a row-numbered
message) instead of silently producing a useless lead later.
EOF
)"
```

---

### Task 2: "New campaign" form blocks creation on seed-import failure

**Files:**
- Modify: `src/awkns_outreach/web/routes/admin.py:134-159` (`create_campaign`)
- Modify: `src/awkns_outreach/web/templates/new_campaign.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `parse_seed_companies` / `_read_seed_input` raising `ValueError` (Task 1 makes this happen more often, but the `except ValueError` path already exists).
- Produces: `POST /campaigns` now returns a `200` re-render of `new_campaign.html` (not a `303` redirect) when seed import fails, with template context `msg`, `name`, `titles`, `angle_prompt`, `seed_text`. No `Campaign` row is created in that case.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web.py` (after `test_admin_create_and_view_campaign`, which is the closest related test):

```python
def test_create_campaign_blocks_on_malformed_seed_and_prefills_form(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    r = client.post("/campaigns", auth=auth, data={
        "name": "JP studios",
        "titles": "creative director",
        "seed_text": '[{"name":"Toyota","website":"toyota.jp,"country":"JP"}]',
        "angle_prompt": "",
    }, follow_redirects=False)

    assert r.status_code == 200  # re-rendered form, not a redirect
    assert session.query(Campaign).count() == 0  # nothing created
    assert "Seed import failed" in r.text
    assert 'value="JP studios"' in r.text  # name preserved
    assert "creative director" in r.text  # titles preserved
    assert "toyota.jp" in r.text  # seed_text preserved


def test_create_campaign_with_missing_name_row_blocks_creation(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    r = client.post("/campaigns", auth=auth, data={
        "name": "JP studios",
        "titles": "",
        "seed_text": '[{"website": "toyota.co.jp"}]',
        "angle_prompt": "",
    }, follow_redirects=False)

    assert r.status_code == 200
    assert session.query(Campaign).count() == 0
    assert "missing required field" in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_web.py -k test_create_campaign_blocks_on_malformed_seed_and_prefills_form -v`
Expected: FAIL — today the route returns `303` and creates the campaign anyway (0 seed companies), so `r.status_code == 200` and `session.query(Campaign).count() == 0` both fail.

- [ ] **Step 3: Update `create_campaign`**

Replace `src/awkns_outreach/web/routes/admin.py:134-159`:

```python
@router.post("/campaigns")
def create_campaign(
    name: str = Form(...),
    titles: str = Form(""),
    angle_prompt: str = Form(""),
    seed_text: str = Form(""),
    seed_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    try:
        seed_companies = _read_seed_input(seed_file, seed_text)
        import_note = ""
    except ValueError as exc:
        seed_companies = []
        import_note = f" (seed import failed: {exc} — add companies on the edit page)"
    c = Campaign(
        name=name.strip(),
        target_titles=_split_lines(titles),
        seed_companies=seed_companies,
        angle_prompt=angle_prompt.strip() or None,
        sender_identity={},
    )
    db.add(c)
    db.commit()
    msg = f"Campaign created with {len(seed_companies)} seed companies.{import_note}"
    return RedirectResponse(f"/campaigns/{c.id}?msg={msg}", status_code=303)
```

with:

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
                "msg": f"Seed import failed: {exc}",
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

(`msg` reuses the same context key `base.html:26-28` already renders as the
blue banner for every other page — no template banner code needed here.)

- [ ] **Step 4: Update `new_campaign.html` to pre-fill posted values**

Replace the `<form>` block in `src/awkns_outreach/web/templates/new_campaign.html` (currently lines 7-37):

```html
<form method="post" action="/campaigns" enctype="multipart/form-data" class="max-w-xl space-y-4">
  <div>
    <label class="block text-sm font-medium mb-1">Name</label>
    <input name="name" required class="w-full border rounded px-2 py-1.5" placeholder="e.g. JP animation studios">
  </div>
  <div>
    <label class="block text-sm font-medium mb-1">Target titles</label>
    <textarea name="titles" rows="3" class="w-full border rounded px-2 py-1.5"
      placeholder="one per line, or comma-separated&#10;creative director&#10;head of content"></textarea>
  </div>
  <div>
    <label class="block text-sm font-medium mb-1">Seed companies <span class="text-slate-400 font-normal">(optional — add or edit later)</span></label>
    <p class="text-xs text-slate-500 mb-1">
      Upload a <b>JSON</b> array or <b>CSV</b>. Columns: name, website, country, category, tier, angle, email, contact_name, contact_title
      (all optional; only <code>website</code> is used to query Apollo).
      <a href="/campaigns/seed-template.csv" class="text-blue-600 hover:underline">Download template (CSV)</a>
    </p>
    <input type="file" name="seed_file" accept=".json,.csv"
      class="block w-full text-sm mb-2 file:mr-2 file:rounded file:border file:bg-white file:px-2 file:py-1">
    <textarea name="seed_text" rows="4" class="w-full border rounded px-2 py-1.5 font-mono text-xs"
      placeholder='…or paste JSON/CSV here, e.g.&#10;[{"name":"Toyota","website":"toyota.co.jp","country":"JP","tier":"A","email":"jamie@toyota.co.jp","contact_name":"Jamie Rivera"}]'></textarea>
    <button type="button" onclick="copyJsonExample(this)" data-json-example='[{"name":"Toyota","website":"toyota.co.jp","country":"JP","tier":"A","email":"jamie@toyota.co.jp","contact_name":"Jamie Rivera"}]'
      class="mt-1 text-xs text-blue-600 hover:underline">Copy JSON example</button>
  </div>
  <div>
    <label class="block text-sm font-medium mb-1">Angle prompt <span class="text-slate-400 font-normal">(optional)</span></label>
    <textarea name="angle_prompt" rows="2" class="w-full border rounded px-2 py-1.5"
      placeholder="Leave blank to use the default research prompt."></textarea>
  </div>
  <button class="rounded bg-slate-900 text-white text-sm px-4 py-2">Create campaign</button>
</form>
```

with:

```html
<form method="post" action="/campaigns" enctype="multipart/form-data" class="max-w-xl space-y-4">
  <div>
    <label class="block text-sm font-medium mb-1">Name</label>
    <input name="name" required value="{{ name or '' }}" class="w-full border rounded px-2 py-1.5" placeholder="e.g. JP animation studios">
  </div>
  <div>
    <label class="block text-sm font-medium mb-1">Target titles</label>
    <textarea name="titles" rows="3" class="w-full border rounded px-2 py-1.5"
      placeholder="one per line, or comma-separated&#10;creative director&#10;head of content">{{ titles or '' }}</textarea>
  </div>
  <div>
    <label class="block text-sm font-medium mb-1">Seed companies <span class="text-slate-400 font-normal">(optional — add or edit later)</span></label>
    <p class="text-xs text-slate-500 mb-1">
      Upload a <b>JSON</b> array or <b>CSV</b>. Columns: name, website, country, category, tier, angle, email, contact_name, contact_title
      (all optional; only <code>website</code> is used to query Apollo).
      <a href="/campaigns/seed-template.csv" class="text-blue-600 hover:underline">Download template (CSV)</a>
    </p>
    <input type="file" name="seed_file" accept=".json,.csv"
      class="block w-full text-sm mb-2 file:mr-2 file:rounded file:border file:bg-white file:px-2 file:py-1">
    <textarea name="seed_text" rows="4" class="w-full border rounded px-2 py-1.5 font-mono text-xs"
      placeholder='…or paste JSON/CSV here, e.g.&#10;[{"name":"Toyota","website":"toyota.co.jp","country":"JP","tier":"A","email":"jamie@toyota.co.jp","contact_name":"Jamie Rivera"}]'>{{ seed_text or '' }}</textarea>
    <button type="button" onclick="copyJsonExample(this)" data-json-example='[{"name":"Toyota","website":"toyota.co.jp","country":"JP","tier":"A","email":"jamie@toyota.co.jp","contact_name":"Jamie Rivera"}]'
      class="mt-1 text-xs text-blue-600 hover:underline">Copy JSON example</button>
  </div>
  <div>
    <label class="block text-sm font-medium mb-1">Angle prompt <span class="text-slate-400 font-normal">(optional)</span></label>
    <textarea name="angle_prompt" rows="2" class="w-full border rounded px-2 py-1.5"
      placeholder="Leave blank to use the default research prompt.">{{ angle_prompt or '' }}</textarea>
  </div>
  <button class="rounded bg-slate-900 text-white text-sm px-4 py-2">Create campaign</button>
</form>
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `.venv/bin/pytest tests/test_web.py -k "test_create_campaign_blocks_on_malformed_seed_and_prefills_form or test_create_campaign_with_missing_name_row_blocks_creation" -v`
Expected: both PASS.

- [ ] **Step 6: Run the full web test file to check for regressions**

Run: `.venv/bin/pytest tests/test_web.py -v`
Expected: all PASS, including unchanged `test_admin_create_and_view_campaign` (valid seed data, still creates and redirects `303` as before).

- [ ] **Step 7: Commit**

```bash
git add src/awkns_outreach/web/routes/admin.py src/awkns_outreach/web/templates/new_campaign.html tests/test_web.py
git commit -m "$(cat <<'EOF'
fix: block campaign creation when seed import fails

create_campaign used to swallow a bad seed_text/seed_file and create
the campaign anyway with 0 seed companies, burying the parse error in
a note most operators never read. Now it re-renders the form in place
with the error and the operator's other input preserved, so nothing
gets created until the seed data actually parses.
EOF
)"
```

---

### Task 3: "Convert seed companies to leads" gets a preview/confirm step

**Files:**
- Modify: `src/awkns_outreach/web/routes/admin.py:329-373` (`convert_seed_companies_to_leads`)
- Modify: `src/awkns_outreach/web/templates/campaign.html:70-75`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `POST /campaigns/{id}/leads/from-seed-companies` now takes an
  optional form field `confirm` (any truthy string, e.g. `"1"`). Without
  it (or `confirm` empty/absent), the route runs the existing validation
  and, if it passes, redirects with a `"Preview: would convert…"` message
  and writes nothing. With `confirm` set and validation passing, behavior
  is identical to today (creates `Lead` rows, `"Converted N…"` message).
  Validation failure messages (no seed companies, missing name/email,
  duplicate email, campaign already has leads) are unchanged and fire
  regardless of `confirm`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web.py` (after `test_convert_seed_companies_no_seed_companies`):

```python
def test_convert_seed_companies_preview_writes_nothing(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp"},
    ])
    session.add(c)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={}, follow_redirects=False,
    )
    assert r.status_code == 303
    from urllib.parse import unquote
    location = unquote(r.headers["location"])
    assert "Preview: would convert 1 seed company to leads (Toyota)" in location
    assert "Nothing saved" in location
    assert session.query(Lead).count() == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_web.py -k test_convert_seed_companies_preview_writes_nothing -v`
Expected: FAIL — today there's no `confirm` gate, so this row is valid
and the route creates the `Lead` immediately, leaving `session.query(Lead).count() == 1`, not `0`.

- [ ] **Step 3: Update the route**

Replace `src/awkns_outreach/web/routes/admin.py:329-373`:

```python
@router.post("/campaigns/{campaign_id}/leads/from-seed-companies")
def convert_seed_companies_to_leads(
    campaign_id: str,
    db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    existing = db.scalar(
        select(func.count()).select_from(Lead).where(Lead.campaign_id == c.id)
    ) or 0
    if existing > 0:
        return RedirectResponse(
            f"/campaigns/{c.id}?msg=Convert failed — this campaign already has leads.",
            status_code=303,
        )

    rows = c.seed_companies or []
    if not rows:
        return RedirectResponse(f"/campaigns/{c.id}?msg=No seed companies to convert.", status_code=303)

    missing = [r.get("name") or "(unnamed)" for r in rows if not (r.get("name") and r.get("email"))]
    emails = [r["email"].strip().lower() for r in rows if r.get("email")]
    dupes = sorted({e for e in emails if emails.count(e) > 1})
    if missing or dupes:
        parts = []
        if missing:
            parts.append(f"missing name/email: {', '.join(missing)}")
        if dupes:
            parts.append(f"duplicate email: {', '.join(dupes)}")
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
    plural = "y" if len(rows) == 1 else "ies"
    return RedirectResponse(
        f"/campaigns/{c.id}?msg=Converted {len(rows)} seed compan{plural} to leads.", status_code=303,
    )
```

with:

```python
@router.post("/campaigns/{campaign_id}/leads/from-seed-companies")
def convert_seed_companies_to_leads(
    campaign_id: str,
    confirm: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    existing = db.scalar(
        select(func.count()).select_from(Lead).where(Lead.campaign_id == c.id)
    ) or 0
    if existing > 0:
        return RedirectResponse(
            f"/campaigns/{c.id}?msg=Convert failed — this campaign already has leads.",
            status_code=303,
        )

    rows = c.seed_companies or []
    if not rows:
        return RedirectResponse(f"/campaigns/{c.id}?msg=No seed companies to convert.", status_code=303)

    missing = [r.get("name") or "(unnamed)" for r in rows if not (r.get("name") and r.get("email"))]
    emails = [r["email"].strip().lower() for r in rows if r.get("email")]
    dupes = sorted({e for e in emails if emails.count(e) > 1})
    if missing or dupes:
        parts = []
        if missing:
            parts.append(f"missing name/email: {', '.join(missing)}")
        if dupes:
            parts.append(f"duplicate email: {', '.join(dupes)}")
        return RedirectResponse(
            f"/campaigns/{c.id}?msg=Convert failed — {'; '.join(parts)}.", status_code=303,
        )

    plural = "y" if len(rows) == 1 else "ies"
    if not confirm:
        names = ", ".join(r["name"].strip() for r in rows)
        msg = (
            f"Preview: would convert {len(rows)} seed compan{plural} to leads "
            f"({names}). Nothing saved — check “confirm” and click Convert again to save."
        )
        return RedirectResponse(f"/campaigns/{c.id}?msg={msg}", status_code=303)

    for r in rows:
        db.add(Lead(
            campaign_id=c.id, email=r["email"].strip().lower(), company=r["name"].strip(),
            contact_name=r.get("contact_name") or None, contact_title=r.get("contact_title") or None,
            country=r.get("country") or None, category=r.get("category") or None,
            tier=r.get("tier") or None, angle=r.get("angle") or None, website=r.get("website") or None,
            step=0, status="active",
        ))
    db.commit()
    return RedirectResponse(
        f"/campaigns/{c.id}?msg=Converted {len(rows)} seed compan{plural} to leads.", status_code=303,
    )
```

- [ ] **Step 4: Update `campaign.html`'s Convert card**

Replace in `src/awkns_outreach/web/templates/campaign.html` (currently lines 70-74):

```html
  <form method="post" action="/campaigns/{{ c.id }}/leads/from-seed-companies" class="rounded border bg-white p-3">
    <div class="text-xs font-medium text-slate-500 mb-2">Convert seed companies to leads</div>
    <p class="text-xs text-slate-400 mb-2 max-w-xs">Skip Apollo — every seed company needs an email first (<a href="/campaigns/{{ c.id }}/companies" class="underline">edit them here</a>).</p>
    <button class="rounded bg-slate-900 text-white text-xs px-2 py-1">Convert</button>
  </form>
```

with:

```html
  <form method="post" action="/campaigns/{{ c.id }}/leads/from-seed-companies" class="rounded border bg-white p-3">
    <div class="text-xs font-medium text-slate-500 mb-2">Convert seed companies to leads</div>
    <p class="text-xs text-slate-400 mb-2 max-w-xs">Skip Apollo — every seed company needs an email first (<a href="/campaigns/{{ c.id }}/companies" class="underline">edit them here</a>).</p>
    <label class="text-xs"><input name="confirm" type="checkbox" value="1"> confirm (writes leads)</label>
    <button class="ml-2 rounded bg-slate-900 text-white text-xs px-2 py-1">Convert</button>
  </form>
```

- [ ] **Step 5: Update the two existing tests that relied on one-click convert**

`convert_seed_companies_to_leads` now needs `confirm` to actually write. Replace
`test_convert_seed_companies_creates_leads` (currently `tests/test_web.py:536-561`):

```python
def test_convert_seed_companies_creates_leads(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp", "contact_name": "Jamie Rivera",
         "contact_title": "VP Finance", "country": "JP", "category": "automotive",
         "tier": "A", "angle": "cars", "website": "toyota.co.jp"},
    ])
    session.add(c)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    assert "Converted 1 seed company to leads." in unquote(r.headers["location"])
    lead = session.query(Lead).one()
    assert lead.email == "jamie@toyota.co.jp"
    assert lead.company == "Toyota"
    assert lead.contact_name == "Jamie Rivera"
    assert lead.contact_title == "VP Finance"
    assert lead.country == "JP"
    assert lead.category == "automotive"
    assert lead.tier == "A"
    assert lead.angle == "cars"
    assert lead.website == "toyota.co.jp"
    assert lead.step == 0 and lead.status == "active"
```

with:

```python
def test_convert_seed_companies_creates_leads(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp", "contact_name": "Jamie Rivera",
         "contact_title": "VP Finance", "country": "JP", "category": "automotive",
         "tier": "A", "angle": "cars", "website": "toyota.co.jp"},
    ])
    session.add(c)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={"confirm": "1"}, follow_redirects=False,
    )
    assert r.status_code == 303
    from urllib.parse import unquote
    assert "Converted 1 seed company to leads." in unquote(r.headers["location"])
    lead = session.query(Lead).one()
    assert lead.email == "jamie@toyota.co.jp"
    assert lead.company == "Toyota"
    assert lead.contact_name == "Jamie Rivera"
    assert lead.contact_title == "VP Finance"
    assert lead.country == "JP"
    assert lead.category == "automotive"
    assert lead.tier == "A"
    assert lead.angle == "cars"
    assert lead.website == "toyota.co.jp"
    assert lead.step == 0 and lead.status == "active"
```

Replace `test_convert_seed_companies_maps_blank_optional_fields_to_none` (currently `tests/test_web.py:564-582`):

```python
def test_convert_seed_companies_maps_blank_optional_fields_to_none(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp"},
    ])
    session.add(c)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth, follow_redirects=False)
    assert r.status_code == 303
    lead = session.query(Lead).one()
    assert lead.contact_name is None
    assert lead.contact_title is None
    assert lead.country is None
    assert lead.category is None
    assert lead.tier is None
    assert lead.angle is None
    assert lead.website is None
```

with:

```python
def test_convert_seed_companies_maps_blank_optional_fields_to_none(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp"},
    ])
    session.add(c)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={"confirm": "1"}, follow_redirects=False,
    )
    assert r.status_code == 303
    lead = session.query(Lead).one()
    assert lead.contact_name is None
    assert lead.contact_title is None
    assert lead.country is None
    assert lead.category is None
    assert lead.tier is None
    assert lead.angle is None
    assert lead.website is None
```

- [ ] **Step 6: Pass explicit empty form bodies on the other pre-flight-check tests**

These three tests hit a failure branch that returns before the `confirm`
check, so their assertions don't change — but adding a required `Form`
parameter to the route means the request now needs a form-encoded body
(even an empty one) rather than no body at all, matching how
`test_enrich_route_surfaces_runtime_error` already calls its route with
`data={}`. Update the three `client.post(...)` calls:

In `test_convert_seed_companies_no_seed_companies` (`tests/test_web.py:493`), change:
```python
    r = client.post(f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth, follow_redirects=False)
```
to:
```python
    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={}, follow_redirects=False,
    )
```

In `test_convert_seed_companies_blocks_when_missing_email` (`tests/test_web.py:510`), change:
```python
    r = client.post(f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth, follow_redirects=False)
```
to:
```python
    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={}, follow_redirects=False,
    )
```

In `test_convert_seed_companies_blocks_on_duplicate_email` (`tests/test_web.py:528`), change:
```python
    r = client.post(f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth, follow_redirects=False)
```
to:
```python
    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={}, follow_redirects=False,
    )
```

In `test_convert_seed_companies_blocked_when_leads_already_exist` (`tests/test_web.py:596`), change:
```python
    r = client.post(f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth, follow_redirects=False)
```
to:
```python
    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={}, follow_redirects=False,
    )
```

- [ ] **Step 7: Run the convert tests to verify they pass**

Run: `.venv/bin/pytest tests/test_web.py -k convert_seed_companies -v`
Expected: all PASS (7 tests: the 6 existing ones plus the new preview test).

- [ ] **Step 8: Run the full test suite to check for regressions**

Run: `.venv/bin/pytest -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add src/awkns_outreach/web/routes/admin.py src/awkns_outreach/web/templates/campaign.html tests/test_web.py
git commit -m "$(cat <<'EOF'
feat: preview before writing leads in Convert seed companies

Convert used to validate and commit Lead rows in the same click, with
no way to see what would happen first. It now defaults to a dry-run
preview (same validation, no DB write) and only commits when the
"confirm" checkbox is checked and re-submitted — mirroring the
preview/reveal pattern Apollo enrich already uses.
EOF
)"
```
