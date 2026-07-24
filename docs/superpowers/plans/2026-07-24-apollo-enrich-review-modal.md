# Apollo Enrich Review Modal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After running Apollo enrich (preview or reveal), show the actual candidate list in a modal that opens automatically, with a same-page-load "Review" button to reopen it.

**Architecture:** `enrich_campaign()` already computes a full candidate list but the web route discards everything except three aggregate counts before redirecting. Two changes: (1) `apollo/enrich.py` starts recording a real per-person outcome row (`created`/`updated`) for reveal mode instead of leaving `EnrichSummary.candidates` as the stale pre-reveal masked list; (2) `admin.py`'s `run_enrich` stops redirecting on success and instead renders `campaign.html` directly with the full summary attached, and the template shows it in an auto-opening `<dialog>` with a Review button to reopen it.

**Tech Stack:** FastAPI + Jinja2 (server-rendered HTML), SQLAlchemy, pytest + respx (HTTP mocking) for `apollo/enrich.py` tests, pytest + FastAPI `TestClient` for route tests.

## Global Constraints

- Rows without a real email (`bulk_match` couldn't unlock one) never appear in the candidates table — they only count toward `EnrichSummary.total_found`. (Spec: "Out of scope".)
- The `RuntimeError` failure path in `run_enrich` (e.g. Apollo API errors) is unchanged — still redirects with `msg=Enrich failed: ...`.
- No persistence of enrich results across page reloads/navigation — same-page-load only. Do not add any DB column or server-side cache for this.
- Preview mode's existing masked-candidate shape (`_preview()`: `apollo_id`, `name`, `title`, `company`, `email_status`, `email_masked`) is unchanged.

---

### Task 1: `apollo/enrich.py` — reveal mode records per-person outcome rows

**Files:**
- Modify: `src/awkns_outreach/apollo/enrich.py:129-205` (`enrich_campaign`, `_upsert_lead`)
- Test: `tests/test_apollo.py`

**Interfaces:**
- Consumes: existing `_merge_fields(seed, person) -> dict[str, Any]` (unchanged, `enrich.py:101-126`), existing `ApolloPerson` (`apollo/client.py`) with `.id`, `.name`, `.title`, `.email` attributes.
- Produces: `_upsert_lead(session, campaign, person, seed) -> tuple[str, dict[str, Any]]` — outcome (`"created"` or `"updated"`) plus the merged fields dict used to build/refresh the `Lead`. `enrich_campaign(...)` still returns `EnrichSummary`, but in reveal mode (`reveal=True`) `summary.candidates` is now `list[dict]` shaped `{"name": str | None, "title": str | None, "company": str, "email": str, "outcome": str}` — one entry per person who had a real email unlocked, in the order `bulk_match` returned them. In preview mode (`reveal=False`) `summary.candidates` is unchanged (the existing masked-preview shape).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_apollo.py` (after `test_enrich_reveal_creates_leads_idempotently`, i.e. after line 126):

```python
@respx.mock
def test_enrich_reveal_records_candidate_outcome_rows(db_session):
    respx.post(f"{BASE}/mixed_people/api_search").mock(
        return_value=httpx.Response(200, json={
            "people": [{"id": "abc123", "title": "Creative Director",
                        "email": "kenji@email_not_unlocked@toyota.co.jp",
                        "organization": {"name": "Toyota"}}],
            "pagination": {"total_entries": 1}})
    )
    respx.post(f"{BASE}/people/bulk_match").mock(
        return_value=httpx.Response(200, json={"matches": [
            {"id": "abc123", "name": "Kenji Tanaka", "email": "K.Tanaka@Toyota.co.jp",
             "title": "Creative Director", "organization": {"name": "Toyota"}}]})
    )
    c = _campaign(db_session)

    s1 = enrich_campaign(db_session, c, reveal=True, limit=10)
    assert s1.candidates == [
        {"name": "Kenji Tanaka", "title": "Creative Director", "company": "Toyota",
         "email": "k.tanaka@toyota.co.jp", "outcome": "created"}
    ]

    s2 = enrich_campaign(db_session, c, reveal=True, limit=10)  # re-run: lead already exists
    assert s2.candidates == [
        {"name": "Kenji Tanaka", "title": "Creative Director", "company": "Toyota",
         "email": "k.tanaka@toyota.co.jp", "outcome": "updated"}
    ]


@respx.mock
def test_enrich_reveal_omits_candidates_without_real_email(db_session):
    respx.post(f"{BASE}/mixed_people/api_search").mock(
        return_value=httpx.Response(200, json={
            "people": [{"id": "abc123", "title": "Creative Director",
                        "email": "kenji@email_not_unlocked@toyota.co.jp",
                        "organization": {"name": "Toyota"}}],
            "pagination": {"total_entries": 1}})
    )
    respx.post(f"{BASE}/people/bulk_match").mock(
        return_value=httpx.Response(200, json={"matches": [
            {"id": "abc123", "name": "Kenji Tanaka",
             "email": "kenji@email_not_unlocked@toyota.co.jp",  # bulk_match still didn't unlock it
             "title": "Creative Director", "organization": {"name": "Toyota"}}]})
    )
    c = _campaign(db_session)

    summary = enrich_campaign(db_session, c, reveal=True, limit=10)
    assert summary.total_found == 1
    assert summary.unlocked == 0
    assert summary.candidates == []
    assert db_session.query(Lead).count() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_apollo.py -k "outcome_rows or omits_candidates" -v`

Expected: `test_enrich_reveal_records_candidate_outcome_rows` FAILS with an `AssertionError` (current `s1.candidates` still holds the stale masked-preview list with `email_masked`/`apollo_id` keys, not the new shape). `test_enrich_reveal_omits_candidates_without_real_email` currently PASSES by coincidence (candidates list still has the one masked-preview entry from the search phase — wait, check: it will actually FAIL too, since `summary.candidates` before this change is the search-phase masked list which has 1 entry, not `[]`). Confirm both show a clear `AssertionError`, not a collection error.

- [ ] **Step 3: Write minimal implementation**

In `src/awkns_outreach/apollo/enrich.py`, replace `_upsert_lead` (lines 170-205) with:

```python
def _upsert_lead(
    session: Session, campaign: Campaign, person: ApolloPerson, seed: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Insert or refresh a lead for this email. Returns (outcome, merged fields).

    outcome is "created" for a brand-new lead or "updated" when one already
    existed for this email — new lead: all merged fields; existing lead:
    refresh Apollo facts, only backfill tier/angle if still empty (don't
    clobber edits).
    """
    email = (person.email or "").strip().lower()
    fields = _merge_fields(seed, person)
    existing = session.scalar(
        select(Lead).where(Lead.campaign_id == campaign.id, Lead.email == email)
    )
    if existing is None:
        session.add(
            Lead(
                campaign_id=campaign.id,
                email=email,
                step=0,
                status="active",
                **fields,
            )
        )
        return "created", fields

    # Refresh Apollo-derived facts (overwrite only when Apollo has a value).
    for key in ("apollo_person_id", "company", "contact_name", "contact_title",
                "country", "category", "website", "seniority", "employee_count"):
        value = fields.get(key)
        if value:
            setattr(existing, key, value)
    # Seed-only fields: fill just when the lead has none yet.
    for key in ("tier", "angle"):
        if fields.get(key) and not getattr(existing, key):
            setattr(existing, key, fields[key])
    return "updated", fields
```

Replace the reveal block inside `enrich_campaign` (lines 145-166 — everything from the `seed_by_id` comment through `return summary`) with:

```python
    # Unlock emails in batches of 10 (Apollo's bulk_match cap), then map each
    # matched person back to its seed via the Apollo id.
    seed_by_id = {p.id: seed for p, seed in candidates if p.id}
    ids = list(seed_by_id.keys())
    matched: list[ApolloPerson] = []
    for i in range(0, len(ids), 10):
        matched.extend(bulk_match(ids[i : i + 10]))
    summary.unlocked = sum(1 for p in matched if has_real_email(p.email))

    # Reveal mode: replace the masked search-phase preview with one outcome
    # row per unlocked person (name/title/company/real email/created|updated).
    # People bulk_match couldn't unlock a real email for get no row here —
    # they still count toward total_found/unlocked above.
    reveal_rows: list[dict[str, Any]] = []
    for person in matched:
        if not has_real_email(person.email):
            continue
        seed = seed_by_id.get(person.id, {})
        outcome, fields = _upsert_lead(session, campaign, person, seed)
        if outcome == "created":
            summary.created += 1
        else:
            summary.updated += 1
        reveal_rows.append({
            "name": person.name,
            "title": person.title,
            "company": fields["company"],
            "email": (person.email or "").strip().lower(),
            "outcome": outcome,
        })
    summary.candidates = reveal_rows
    session.flush()
    return summary
```

Note this drops the dead `skipped_existing` `else` branch from the old `if/elif/else` — `_upsert_lead` never actually returned a third outcome (it only ever returned `"created"` or `"updated"`), so that branch was unreachable. `EnrichSummary.skipped_existing` field stays (still a public field other code might read) but nothing increments it anymore; leave it defaulted to `0`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_apollo.py -v`

Expected: all tests in the file PASS, including the two new ones and all pre-existing ones (`test_enrich_reveal_creates_leads_idempotently`, `test_enrich_carries_seed_metadata_and_apollo_overwrites`, `test_enrich_captures_seniority_and_employee_count`, `test_enrich_legacy_seed_priority_key_still_populates_tier`, `test_reenrich_refreshes_facts_but_keeps_existing_angle`, `test_enrich_preview_spends_no_credits`).

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/apollo/enrich.py tests/test_apollo.py
git commit -m "feat: record per-person outcome rows for Apollo reveal"
```

---

### Task 2: `admin.py` + `campaign.html` — show enrich results in an auto-opening modal with a Review button

**Files:**
- Modify: `src/awkns_outreach/web/routes/admin.py:273-322` (`campaign_detail`, `run_enrich`; add `_campaign_detail_ctx` helper)
- Modify: `src/awkns_outreach/web/templates/campaign.html:54-59` (Apollo enrich form) — add Review button + dialog markup
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `EnrichSummary` from Task 1 (`from awkns_outreach.apollo.enrich import enrich_campaign`, already imported in `admin.py`) — reads `.reveal`, `.total_found`, `.created`, `.updated`, `.skipped_existing`, `.candidates` (list of dicts, shape depends on `.reveal` per Task 1).
- Produces: `_campaign_detail_ctx(db: Session, c: Campaign, tier_filter: Optional[str] = None) -> dict` — new module-level helper in `admin.py`, used by both `campaign_detail` and `run_enrich`. Returns a dict with keys `c`, `stats`, `leads`, `tier_filter`, `tier_counts`, `tier_total` (no `request`/`msg`/`enrich_result` — callers add those).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_web.py`. First add this import near the top, alongside the existing `from awkns_outreach.writer.tiers import TierSummary` (line 18):

```python
from awkns_outreach.apollo.enrich import EnrichSummary
```

Then add these tests after `test_enrich_route_surfaces_runtime_error` (i.e. after line 524):

```python
def test_enrich_route_renders_preview_candidates_in_page(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="Widgets", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    canned = EnrichSummary(
        reveal=False, total_found=1,
        candidates=[{
            "apollo_id": "abc123", "name": "Kenji Tanaka", "title": "Creative Director",
            "company": "Toyota", "email_status": "verified",
            "email_masked": "kenji@email_not_unlocked@toyota.co.jp",
        }],
    )
    monkeypatch.setattr(admin, "enrich_campaign", lambda *a, **kw: canned)

    r = client.post(f"/campaigns/{c.id}/enrich", auth=auth, data={"limit": "10"}, follow_redirects=False)
    assert r.status_code == 200
    assert "Kenji Tanaka" in r.text
    assert "kenji@email_not_unlocked@toyota.co.jp" in r.text
    assert 'id="enrich-dialog"' in r.text
    assert "Review" in r.text


def test_enrich_route_renders_reveal_outcomes_in_page(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="Widgets", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    canned = EnrichSummary(
        reveal=True, total_found=1, unlocked=1, created=1,
        candidates=[{
            "name": "Kenji Tanaka", "title": "Creative Director", "company": "Toyota",
            "email": "k.tanaka@toyota.co.jp", "outcome": "created",
        }],
    )
    monkeypatch.setattr(admin, "enrich_campaign", lambda *a, **kw: canned)

    r = client.post(f"/campaigns/{c.id}/enrich", auth=auth,
                     data={"limit": "10", "reveal": "1"}, follow_redirects=False)
    assert r.status_code == 200
    assert "k.tanaka@toyota.co.jp" in r.text
    assert "created" in r.text


def test_campaign_detail_has_no_enrich_modal_by_default(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="Widgets", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    r = client.get(f"/campaigns/{c.id}", auth=auth)
    assert r.status_code == 200
    assert 'id="enrich-dialog"' not in r.text
    assert "enrich-review-btn" not in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web.py -k "renders_preview_candidates or renders_reveal_outcomes or has_no_enrich_modal" -v`

Expected: `test_enrich_route_renders_preview_candidates_in_page` and `test_enrich_route_renders_reveal_outcomes_in_page` FAIL with `assert 200 == 303` (route still redirects). `test_campaign_detail_has_no_enrich_modal_by_default` PASSES already (nothing to show yet) — that's fine, it becomes a real regression guard once Step 3 lands; leave it in.

- [ ] **Step 3: Write minimal implementation**

In `src/awkns_outreach/web/routes/admin.py`, replace `campaign_detail` and `run_enrich` (lines 273-322) with:

```python
def _campaign_detail_ctx(db: Session, c: Campaign, tier_filter: Optional[str] = None) -> dict:
    counts = dict(
        db.execute(
            select(Lead.tier, func.count())
            .where(Lead.campaign_id == c.id)
            .group_by(Lead.tier)
        ).all()
    )
    tier_counts = {t: counts.get(t, 0) for t in (*TIERS, None)}
    tier_total = sum(tier_counts.values())

    stmt = select(Lead).where(Lead.campaign_id == c.id)
    if tier_filter == "unclassified":
        stmt = stmt.where(Lead.tier.is_(None))
    elif tier_filter in TIERS:
        stmt = stmt.where(Lead.tier == tier_filter)
    leads = db.scalars(stmt.order_by(Lead.created_at.desc()).limit(200)).all()

    return {
        "c": c, "stats": campaign_stats(db, c), "leads": leads,
        "tier_filter": tier_filter, "tier_counts": tier_counts, "tier_total": tier_total,
    }


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def campaign_detail(
    campaign_id: str, request: Request, db: Session = Depends(get_db),
    msg: Optional[str] = None, tier: Optional[str] = None,
):
    c = _get_campaign(db, campaign_id)
    tier_filter = tier if tier in _TIER_FILTERS else None
    ctx = _campaign_detail_ctx(db, c, tier_filter)
    ctx["msg"] = msg
    return templates.TemplateResponse(request, "campaign.html", ctx)


@router.post("/campaigns/{campaign_id}/enrich", response_class=HTMLResponse)
def run_enrich(
    campaign_id: str,
    request: Request,
    reveal: str = Form(""),
    limit: int = Form(10),
    db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    try:
        summary = enrich_campaign(db, c, reveal=bool(reveal), limit=limit)
    except RuntimeError as exc:
        return RedirectResponse(f"/campaigns/{c.id}?msg=Enrich failed: {exc}", status_code=303)
    db.commit()
    verb = "revealed" if reveal else "found (preview)"
    msg = f"Enrich: {summary.total_found} {verb}; created {summary.created}, skipped {summary.skipped_existing}."
    ctx = _campaign_detail_ctx(db, c)
    ctx["msg"] = msg
    ctx["enrich_result"] = summary
    return templates.TemplateResponse(request, "campaign.html", ctx)
```

In `src/awkns_outreach/web/templates/campaign.html`, replace the Apollo enrich `<form>` block (lines 54-59):

```html
  <form method="post" action="/campaigns/{{ c.id }}/enrich" class="rounded border bg-white p-3">
    <div class="text-xs font-medium text-slate-500 mb-2">Apollo enrich</div>
    <label class="text-xs">Limit <input name="limit" type="number" value="10" class="w-16 border rounded px-1 py-0.5"></label>
    <label class="text-xs ml-2"><input name="reveal" type="checkbox" value="1"> reveal (spends credits)</label>
    <button class="ml-2 rounded bg-slate-900 text-white text-xs px-2 py-1">Run</button>
  </form>
```

with:

```html
  <form method="post" action="/campaigns/{{ c.id }}/enrich" class="rounded border bg-white p-3">
    <div class="text-xs font-medium text-slate-500 mb-2">Apollo enrich</div>
    <label class="text-xs">Limit <input name="limit" type="number" value="10" class="w-16 border rounded px-1 py-0.5"></label>
    <label class="text-xs ml-2"><input name="reveal" type="checkbox" value="1"> reveal (spends credits)</label>
    <button class="ml-2 rounded bg-slate-900 text-white text-xs px-2 py-1">Run</button>
    {% if enrich_result %}
    <button type="button" id="enrich-review-btn" class="ml-2 rounded border text-xs px-2 py-1">Review</button>
    {% endif %}
  </form>
```

Then add this block right after the closing `</div>` of the enrich-forms row (i.e. immediately after line 77's `</div>`, before the `<h2 class="text-sm font-semibold mb-2">Leads` heading):

```html
{% if enrich_result %}
<dialog id="enrich-dialog" class="rounded border p-0 max-w-2xl w-full">
  <div class="p-4">
    <div class="flex items-center justify-between mb-3">
      <h2 class="text-sm font-semibold">
        Apollo enrich — {{ enrich_result.total_found }} found
        {% if enrich_result.reveal %}({{ enrich_result.created }} created, {{ enrich_result.updated }} updated){% endif %}
      </h2>
      <button type="button" id="enrich-dialog-close" class="text-xs text-slate-500">Close</button>
    </div>
    <table class="w-full text-sm">
      <thead class="text-slate-500 text-left">
        <tr>
          <th class="py-1">Name</th><th class="py-1">Title</th><th class="py-1">Company</th>
          <th class="py-1">Email</th>
          <th class="py-1">{% if enrich_result.reveal %}Outcome{% else %}Status{% endif %}</th>
        </tr>
      </thead>
      <tbody>
        {% for row in enrich_result.candidates %}
        <tr class="border-t">
          <td class="py-1">{{ row.name or "—" }}</td>
          <td class="py-1">{{ row.title or "—" }}</td>
          <td class="py-1">{{ row.company or "—" }}</td>
          {% if enrich_result.reveal %}
          <td class="py-1">{{ row.email }}</td>
          <td class="py-1">{{ row.outcome }}</td>
          {% else %}
          <td class="py-1">{{ row.email_masked }}</td>
          <td class="py-1">{{ row.email_status or "—" }}</td>
          {% endif %}
        </tr>
        {% else %}
        <tr><td colspan="5" class="py-2 text-slate-400">No candidates.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</dialog>
<script>
  (function () {
    var dialog = document.getElementById("enrich-dialog");
    dialog.showModal();
    document.getElementById("enrich-dialog-close").addEventListener("click", function () { dialog.close(); });
    var reviewBtn = document.getElementById("enrich-review-btn");
    if (reviewBtn) reviewBtn.addEventListener("click", function () { dialog.showModal(); });
  })();
</script>
{% endif %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_web.py -v`

Expected: all tests in the file PASS (48+ existing tests plus the 3 new ones), including `test_enrich_route_surfaces_runtime_error` (unchanged failure-path behavior).

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest`

Expected: all tests pass, no regressions elsewhere (e.g. in `test_apollo.py` from Task 1, or any other file importing `admin.py`).

- [ ] **Step 6: Commit**

```bash
git add src/awkns_outreach/web/routes/admin.py src/awkns_outreach/web/templates/campaign.html tests/test_web.py
git commit -m "feat: show Apollo enrich results in an auto-opening review modal"
```

---

## Manual verification (post-implementation)

Automated tests cover the HTML content and route status codes, but not actual browser JS/dialog behavior. After both tasks are committed, do one manual check per the repo's `verify` skill: start the app, open a campaign with at least one seed company, click "Run" under Apollo enrich (preview, unchecked), confirm the modal pops up automatically showing the masked candidate(s), close it, click the new "Review" button, confirm it reopens the same data without re-running enrich.
