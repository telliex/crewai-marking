# Apollo enrich: show results in a modal + Review button

Date: 2026-07-24
Status: approved

## Problem

`POST /campaigns/{id}/enrich` (both preview and reveal mode) runs
`enrich_campaign()`, which returns an `EnrichSummary` including a full
`candidates` list (name, title, company, masked email, email status in
preview; nothing per-person in reveal beyond aggregate counts). The route
(`admin.py:307-322`) only pulls three aggregate numbers out of that summary
into a one-line message, then `RedirectResponse`s back to the campaign page.
The candidate list itself is discarded — there is no way to see who Apollo
actually found, only a count like "10 found (preview); created 0, skipped 0."

## Goal

After running Apollo enrich (preview or reveal), show the actual candidate
list in a modal that opens automatically, plus a "Review" button next to the
enrich form to reopen the same modal without re-running enrich — scoped to
the current page load only (no cross-reload/cross-navigation persistence;
running enrich again is how you get a fresh view later).

## Out of scope

- Persisting enrich results across page reloads or navigation (confirmed:
  same-page-load only is sufficient).
- Showing rows for people Apollo/bulk_match couldn't unlock a real email for.
  These still count toward the `total_found` number in the summary banner,
  but do not get a row in the reveal table — there's no meaningful
  created/updated/skipped outcome for them, and no email to show.
- Changing the failure path: `RuntimeError` from `enrich_campaign` (e.g. an
  Apollo API error) keeps redirecting with `msg=Enrich failed: ...` exactly
  as today — there's no candidate data to show in that case.

## 1. `enrich.py`: reveal mode records a per-person outcome

Today, `EnrichSummary.candidates` is populated once, during the search phase
(`enrich_campaign`, `admin.py` calls it before the `reveal` branch), and
holds the *masked* preview shape (`_preview()`): `apollo_id`, `name`, `title`,
`company`, `email_status`, `email_masked`. This is correct and unchanged for
preview mode.

In reveal mode, this masked list is no longer useful — the real emails have
been unlocked and leads created/updated. Change the reveal branch of
`enrich_campaign` (currently `admin.py:129-167`... actually
`src/awkns_outreach/apollo/enrich.py:129-167`) to replace
`summary.candidates` with one entry per successfully-unlocked person:

```python
{"name": person.name, "title": person.title, "company": <merged company>,
 "email": <real email>, "outcome": "created" | "updated" | "skipped_existing"}
```

`_upsert_lead` already returns `"created"` / `"updated"`; the third case
(`"skipped_existing"`) needs to be surfaced too — currently
`enrich_campaign`'s loop only increments `summary.skipped_existing` without
recording which lead that was. The loop already computes `outcome` from
`_upsert_lead`; store it per-row instead of only counting it.

People without a real email after `bulk_match` (`has_real_email(person.email)`
false) are skipped exactly as today — no row is added for them, only the
aggregate counts (`total_found`, `unlocked`) reflect their existence.

`EnrichSummary.reveal` (already a field) tells the template/route which shape
`candidates` is in — no new field needed.

## 2. `admin.py`: `run_enrich` renders instead of redirecting on success

Factor the context-building block currently inline in `campaign_detail`
(`admin.py:273-304`: tier counts, lead query, stats) into a helper —

```python
def _campaign_detail_ctx(db: Session, c: Campaign, tier: Optional[str] = None) -> dict:
    ...  # everything currently built in campaign_detail, minus request/msg
```

`campaign_detail` becomes: build `tier_filter`, call the helper, merge in
`request`/`msg`, return `TemplateResponse`.

`run_enrich` becomes:

```python
@router.post("/campaigns/{campaign_id}/enrich", response_class=HTMLResponse)
def run_enrich(
    campaign_id: str, request: Request,
    reveal: str = Form(""), limit: int = Form(10),
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
    ctx.update(request=request, msg=msg, enrich_result=summary)
    return templates.TemplateResponse(request, "campaign.html", ctx)
```

This breaks the POST/Redirect/Get pattern only for this one success path —
reloading the resulting page will prompt the browser's "resubmit form" dialog
like `create_campaign`'s existing seed-parse-failure path already does
(`admin.py:144-156`, same precedent). Acceptable since results aren't meant
to survive a reload anyway.

## 3. `campaign.html`: modal + conditional Review button

Add, near the existing Apollo enrich `<form>` (`campaign.html:54-59`):

```html
{% if enrich_result %}
<button type="button" id="enrich-review-btn" class="ml-2 rounded border text-xs px-2 py-1">Review</button>
{% endif %}
```

Add a `<dialog id="enrich-dialog">` (same pattern as the existing
`archive-dialog` in `dashboard.html`) rendered once, server-side, from
`enrich_result.candidates` — no JS data-attribute wiring needed since there's
only one result set per page load, not one per row:

```html
{% if enrich_result %}
<dialog id="enrich-dialog" class="rounded border p-0 max-w-2xl w-full">
  <div class="p-4">
    <div class="flex items-center justify-between mb-3">
      <h2 class="text-sm font-semibold">
        Apollo enrich — {{ enrich_result.total_found }} found
        {% if enrich_result.reveal %}({{ enrich_result.created }} created, {{ enrich_result.updated }} updated, {{ enrich_result.skipped_existing }} skipped){% endif %}
      </h2>
      <button type="button" id="enrich-dialog-close" class="text-xs text-slate-500">Close</button>
    </div>
    <table class="w-full text-sm">
      <thead class="text-slate-500 text-left">
        <tr>
          <th class="py-1">Name</th><th class="py-1">Title</th><th class="py-1">Company</th>
          {% if enrich_result.reveal %}
          <th class="py-1">Email</th><th class="py-1">Outcome</th>
          {% else %}
          <th class="py-1">Email</th><th class="py-1">Status</th>
          {% endif %}
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

## Testing

`tests/test_web.py` (reuse the existing `monkeypatch.setattr(admin, "enrich_campaign", ...)` pattern from `test_enrich_route_surfaces_runtime_error`):

- Preview success: POST without `reveal` → `200` (not `303`), response body contains the masked email and name from a faked `EnrichSummary`.
- Reveal success: POST with `reveal=1` → `200`, response body contains the real email and the literal string `created` (or `updated`/`skipped_existing` depending on the faked outcome).
- Existing `test_enrich_route_surfaces_runtime_error` unchanged, still expects `303` — confirms the failure path wasn't touched.

`tests/test_enrich.py` (wherever `apollo/enrich.py`'s existing unit tests live — locate via `grep -rn "enrich_campaign" tests/`):

- Reveal mode: given a fake `bulk_match`/`search_people` returning one new person and one person matching an existing `Lead`, assert `summary.candidates` has two rows with `outcome` `"created"` and `"updated"` respectively, each with the real (unmasked) email.
- A person with no real email after `bulk_match` produces no row in `candidates`, but still counts in `total_found`.
