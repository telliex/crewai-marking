# Manual Lead Conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator convert a campaign's seed companies directly into leads (skipping Apollo) by adding `email`/`contact_name`/`contact_title` to the seed-company schema and a "Convert" action on the campaign page.

**Architecture:** Extend `apollo/seed.py`'s canonical `SEED_FIELDS` tuple (used everywhere seed data is defined/parsed/rendered) with three new optional keys. A new POST route reads `Campaign.seed_companies` directly (no new input UI needed there), validates every row has `name`+`email` and no two rows share an email, and either creates one `Lead` per row or blocks the whole operation with an error message — no partial conversion.

**Tech Stack:** FastAPI + Jinja2, SQLAlchemy, pytest + `TestClient`.

## Global Constraints

- No DB migration — `Campaign.seed_companies` is a JSON column; new keys need no schema change.
- Conversion is all-or-nothing: if any seed-company row is missing `name` or `email`, or two rows share an email, **zero** leads are created and the operator sees a message explaining what's wrong.
- The Convert route must refuse to run if the campaign already has any `Lead` rows (enforced server-side, not just by hiding the button in the template).
- `seed_companies` is never mutated by conversion (non-destructive, matching how Apollo enrich never touches it).
- The Convert card only renders on `campaign.html` when `leads` is empty.

---

### Task 1: `apollo/seed.py` — add email/contact fields to the canonical seed schema

**Files:**
- Modify: `src/awkns_outreach/apollo/seed.py:21` (`SEED_FIELDS`), `:24-37` (`_ALIASES`)
- Test: `tests/test_seed.py`

**Interfaces:**
- Produces: `SEED_FIELDS` gains `"email"`, `"contact_name"`, `"contact_title"` (appended at the end — preserves existing column order for anyone with a saved template). `_ALIASES` recognizes `email`, `contact_name`/`contact`, `contact_title`/`title`. Every consumer of `SEED_FIELDS` (Task 2's form/template, the CSV template route) picks these up automatically since none of them hardcode the old 6-tuple.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_seed.py`, after `test_parse_row_without_website_is_kept_but_has_no_domain` (currently ends at line 45):

```python
def test_parse_json_array_captures_email_and_contact_fields():
    raw = (
        '[{"name": "Toyota", "email": "jamie@toyota.co.jp",'
        ' "contact_name": "Jamie Rivera", "contact_title": "VP Finance"}]'
    )
    out = parse_seed_companies(raw, None)
    assert out == [{
        "name": "Toyota", "email": "jamie@toyota.co.jp",
        "contact_name": "Jamie Rivera", "contact_title": "VP Finance",
    }]


def test_parse_csv_accepts_contact_and_title_aliases():
    raw = "name,email,contact,title\nToyota,jamie@toyota.co.jp,Jamie Rivera,VP Finance\n"
    out = parse_seed_companies(raw, "seed.csv")
    assert out == [{
        "name": "Toyota", "email": "jamie@toyota.co.jp",
        "contact_name": "Jamie Rivera", "contact_title": "VP Finance",
    }]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_seed.py -k "email_and_contact or contact_and_title" -v`
Expected: FAIL — both assertions come back missing the `email`/`contact_name`/`contact_title` keys, since `_ALIASES` doesn't recognize those column/key names yet (they're silently dropped by `_clean_row`'s `field = _ALIASES.get(...)` returning `None`).

- [ ] **Step 3: Implement**

In `src/awkns_outreach/apollo/seed.py`, replace lines 21-37:

```python
# Canonical seed fields, in a stable order (used by the edit form too).
SEED_FIELDS = (
    "name", "website", "country", "category", "tier", "angle",
    "email", "contact_name", "contact_title",
)

# Accepted column/key aliases -> canonical field.
_ALIASES = {
    "name": "name",
    "company": "name",
    "company_name": "name",
    "website": "website",
    "domain": "website",
    "url": "website",
    "country": "country",
    "category": "category",
    "industry": "category",
    "priority": "tier",
    "tier": "tier",
    "angle": "angle",
    "email": "email",
    "contact_name": "contact_name",
    "contact": "contact_name",
    "contact_title": "contact_title",
    "title": "contact_title",
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_seed.py -v`
Expected: PASS — all tests, including the pre-existing ones (they don't reference these new fields, so unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/apollo/seed.py tests/test_seed.py
git commit -m "feat: add email/contact_name/contact_title to seed-company schema"
```

---

### Task 2: Seed-company edit UI + CSV template — expose the new fields

**Files:**
- Modify: `src/awkns_outreach/web/templates/seed_companies_edit.html:4-14` (`company_row` macro), `:28-33` (table header)
- Modify: `src/awkns_outreach/web/templates/new_campaign.html:20` (import help text)
- Modify: `src/awkns_outreach/web/routes/admin.py:113-130` (`seed_template_csv`), `:406-419` (`save_companies` signature), `:439-442` (`_rows_from_form` call)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `SEED_FIELDS` from Task 1 (already imported in `admin.py:18`).
- Produces: nothing new consumed by Task 3 — this task only makes the 3 new fields editable/importable through the existing UI; Task 3 reads `Campaign.seed_companies` directly regardless of how it was populated.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_web.py`, after `test_seed_template_csv_download` (currently ends at line 135):

```python
def test_edit_companies_form_renders_new_contact_columns(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp",
         "contact_name": "Jamie Rivera", "contact_title": "VP Finance"},
    ])
    session.add(c)
    session.commit()

    r = client.get(f"/campaigns/{c.id}/companies", auth=auth)
    assert r.status_code == 200
    assert 'value="jamie@toyota.co.jp"' in r.text
    assert 'value="Jamie Rivera"' in r.text
    assert 'value="VP Finance"' in r.text


def test_save_companies_persists_email_and_contact_fields(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/companies", auth=auth, data={
        "action": "save",
        "name": ["Toyota"], "website": [""], "country": [""], "category": [""],
        "tier": [""], "angle": [""],
        "email": ["jamie@toyota.co.jp"], "contact_name": ["Jamie Rivera"],
        "contact_title": ["VP Finance"],
    }, follow_redirects=False)
    assert r.status_code == 303
    session.refresh(c)
    assert c.seed_companies == [{
        "name": "Toyota", "email": "jamie@toyota.co.jp",
        "contact_name": "Jamie Rivera", "contact_title": "VP Finance",
    }]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web.py -k "renders_new_contact_columns or persists_email_and_contact" -v`
Expected: FAIL — `test_edit_companies_form_renders_new_contact_columns` fails because the template has no `email`/`contact_name`/`contact_title` `<input>` cells yet. `test_save_companies_persists_email_and_contact_fields` fails because `save_companies` has no `email`/`contact_name`/`contact_title` `Form(...)` parameters, so those keys never reach `_rows_from_form` and `c.seed_companies` ends up `[{"name": "Toyota"}]`.

- [ ] **Step 3: Implement**

In `src/awkns_outreach/web/templates/seed_companies_edit.html`, replace the `company_row` macro (lines 4-14):

```html
{% macro company_row(co) %}
<tr class="company-row border-t">
  <td class="px-2 py-1"><input name="name" value="{{ co.name or '' }}" class="w-full border rounded px-1.5 py-1 text-sm" placeholder="Toyota"></td>
  <td class="px-2 py-1"><input name="website" value="{{ co.website or '' }}" class="w-full border rounded px-1.5 py-1 text-sm" placeholder="toyota.co.jp"></td>
  <td class="px-2 py-1"><input name="country" value="{{ co.country or '' }}" class="w-full border rounded px-1.5 py-1 text-sm" placeholder="JP"></td>
  <td class="px-2 py-1"><input name="category" value="{{ co.category or '' }}" class="w-full border rounded px-1.5 py-1 text-sm" placeholder="automotive"></td>
  <td class="px-2 py-1"><input name="tier" value="{{ co.tier or '' }}" class="w-16 border rounded px-1.5 py-1 text-sm" placeholder="A"></td>
  <td class="px-2 py-1"><input name="angle" value="{{ co.angle or '' }}" class="w-full border rounded px-1.5 py-1 text-sm" placeholder="why this fits them"></td>
  <td class="px-2 py-1"><input name="email" value="{{ co.email or '' }}" class="w-full border rounded px-1.5 py-1 text-sm" placeholder="jamie@toyota.co.jp"></td>
  <td class="px-2 py-1"><input name="contact_name" value="{{ co.contact_name or '' }}" class="w-full border rounded px-1.5 py-1 text-sm" placeholder="Jamie Rivera"></td>
  <td class="px-2 py-1"><input name="contact_title" value="{{ co.contact_title or '' }}" class="w-full border rounded px-1.5 py-1 text-sm" placeholder="VP Finance"></td>
  <td class="px-2 py-1 text-right"><button type="button" onclick="removeRow(this)" class="text-xs text-red-600 hover:underline">Remove</button></td>
</tr>
{% endmacro %}
```

Replace the table header (lines 28-33):

```html
      <thead class="bg-slate-100 text-slate-500 text-left text-xs">
        <tr>
          <th class="px-2 py-2">Name</th><th class="px-2 py-2">Website</th>
          <th class="px-2 py-2">Country</th><th class="px-2 py-2">Category</th>
          <th class="px-2 py-2">Tier</th><th class="px-2 py-2">Angle</th>
          <th class="px-2 py-2">Email</th><th class="px-2 py-2">Contact</th>
          <th class="px-2 py-2">Title</th><th></th>
        </tr>
      </thead>
```

In `src/awkns_outreach/web/templates/new_campaign.html`, replace line 20:

```html
      Upload a <b>JSON</b> array or <b>CSV</b>. Columns: name, website, country, category, tier, angle
```
with:
```html
      Upload a <b>JSON</b> array or <b>CSV</b>. Columns: name, website, country, category, tier, angle, email, contact_name, contact_title
```

In `src/awkns_outreach/web/routes/admin.py`, replace `seed_template_csv` (lines 113-130):

```python
@router.get("/campaigns/seed-template.csv")
def seed_template_csv():
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=SEED_FIELDS)
    writer.writeheader()
    writer.writerow({
        "name": "Toyota", "website": "toyota.co.jp", "country": "JP",
        "category": "automotive", "tier": "A", "angle": "why this fits them",
        "email": "jamie@toyota.co.jp", "contact_name": "Jamie Rivera", "contact_title": "VP Finance",
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

(The second example row deliberately keeps no `email`/`contact_name`/`contact_title` — showing an Apollo-only row is still valid.)

Replace the `save_companies` signature (lines 407-420):

```python
@router.post("/campaigns/{campaign_id}/companies")
def save_companies(
    campaign_id: str,
    action: str = Form("save"),
    import_mode: str = Form("replace"),
    seed_text: str = Form(""),
    seed_file: Optional[UploadFile] = File(None),
    name: list[str] = Form(default=[]),
    website: list[str] = Form(default=[]),
    country: list[str] = Form(default=[]),
    category: list[str] = Form(default=[]),
    tier: list[str] = Form(default=[]),
    angle: list[str] = Form(default=[]),
    email: list[str] = Form(default=[]),
    contact_name: list[str] = Form(default=[]),
    contact_title: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
```

Replace the `_rows_from_form` call inside it (lines 439-442):

```python
        rows = _rows_from_form({
            "name": name, "website": website, "country": country,
            "category": category, "tier": tier, "angle": angle,
            "email": email, "contact_name": contact_name, "contact_title": contact_title,
        })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_web.py -v`
Expected: PASS, all tests (including `test_seed_template_csv_download`, which asserts the CSV header matches `list(admin.SEED_FIELDS)` dynamically — no change needed there since it already derives its expectation from `SEED_FIELDS`).

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/web/templates/seed_companies_edit.html src/awkns_outreach/web/templates/new_campaign.html src/awkns_outreach/web/routes/admin.py tests/test_web.py
git commit -m "feat: expose email/contact fields in seed-company edit/import UI and CSV template"
```

---

### Task 3: "Convert to leads" action

**Files:**
- Modify: `src/awkns_outreach/web/routes/admin.py` (new route, placed after `run_classify`, currently ending at line 325)
- Modify: `src/awkns_outreach/web/templates/campaign.html:53-68` (new card in the actions row)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `Campaign.seed_companies` (list of dicts with the `SEED_FIELDS` keys from Task 1/2), `_get_campaign(db, campaign_id)` (existing helper, `admin.py`).
- Produces: `POST /campaigns/{campaign_id}/leads/from-seed-companies` — no other task depends on this route's internals.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_web.py`, after `test_classify_route_404_for_unknown_campaign` (currently ends at line 445):

```python
def test_convert_seed_companies_no_seed_companies(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    assert "No seed companies to convert." in unquote(r.headers["location"])
    assert session.query(Lead).count() == 0


def test_convert_seed_companies_blocks_when_missing_email(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp"},
        {"name": "Sony"},
    ])
    session.add(c)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    location = unquote(r.headers["location"])
    assert "Convert failed" in location and "Sony" in location
    assert session.query(Lead).count() == 0


def test_convert_seed_companies_blocks_on_duplicate_email(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp"},
        {"name": "Toyota JP", "email": "JAMIE@toyota.co.jp"},
    ])
    session.add(c)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    location = unquote(r.headers["location"])
    assert "duplicate email" in location and "jamie@toyota.co.jp" in location
    assert session.query(Lead).count() == 0


def test_convert_seed_companies_creates_leads(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp", "contact_name": "Jamie Rivera",
         "contact_title": "VP Finance", "country": "JP", "tier": "A", "angle": "cars"},
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
    assert lead.tier == "A"
    assert lead.angle == "cars"
    assert lead.step == 0 and lead.status == "active"


def test_convert_seed_companies_blocked_when_leads_already_exist(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp"},
    ])
    session.add(c)
    session.flush()
    session.add(Lead(campaign_id=c.id, email="existing@x.com", company="X", status="active"))
    session.commit()

    r = client.post(f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    assert "already has leads" in unquote(r.headers["location"])
    assert session.query(Lead).count() == 1


def test_campaign_page_shows_convert_card_only_when_no_leads(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    r = client.get(f"/campaigns/{c.id}", auth=auth)
    assert "Convert seed companies to leads" in r.text

    session.add(Lead(campaign_id=c.id, email="x@y.com", company="X", status="active"))
    session.commit()

    r2 = client.get(f"/campaigns/{c.id}", auth=auth)
    assert "Convert seed companies to leads" not in r2.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web.py -k "convert_seed_companies or shows_convert_card" -v`
Expected: FAIL — the route doesn't exist yet (404 instead of 303 on every POST test), and the card never renders (last test's first assertion fails).

- [ ] **Step 3: Implement**

In `src/awkns_outreach/web/routes/admin.py`, add this route directly after `run_classify` (after line 325, before `set_lead_tier`):

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

In `src/awkns_outreach/web/templates/campaign.html`, insert this card right before the closing `</div>` of the actions row (currently line 68, immediately after the "AI classify" `</form>` at line 67):

```html
  {% if not leads %}
  <form method="post" action="/campaigns/{{ c.id }}/leads/from-seed-companies" class="rounded border bg-white p-3">
    <div class="text-xs font-medium text-slate-500 mb-2">Convert seed companies to leads</div>
    <p class="text-xs text-slate-400 mb-2 max-w-xs">Skip Apollo — every seed company needs an email first (<a href="/campaigns/{{ c.id }}/companies" class="underline">edit them here</a>).</p>
    <button class="rounded bg-slate-900 text-white text-xs px-2 py-1">Convert</button>
  </form>
  {% endif %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_web.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -v`
Expected: PASS, all tests.

- [ ] **Step 6: Commit**

```bash
git add src/awkns_outreach/web/routes/admin.py src/awkns_outreach/web/templates/campaign.html tests/test_web.py
git commit -m "feat: add Convert-seed-companies-to-leads action for skipping Apollo"
```
