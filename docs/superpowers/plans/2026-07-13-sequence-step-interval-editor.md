# Sequence Step Interval Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the sequence editor's inline "Send email in [ ] day(s) after previous" badge with a click-to-open popover positioned *between* step cards, supporting minutes/hours/days, backed by a `delay_minutes` field that stays backward-compatible with existing `delay_days`-only data.

**Architecture:** Backend stores/reads step delay as `delay_minutes` (int); a single compatibility helper (`step_delay_minutes`) falls back to `delay_days * 1440` for any step dict that predates this change, used identically by the scheduler engine and the editor's prefill path — so no DB migration is needed for `MailSequence.steps` or in-flight `Task.steps_by_tier` snapshots. The editor UI moves the delay control out of each step card into a small connector pill rendered between step N-1 and step N; clicking it opens a popover (number input + unit `<select>`) that writes live to a hidden `delay_minutes` form field. A disabled/enabled hidden field inside each step card (enabled only on the current first step) preserves the exact submitted-field-count invariant `_build_steps`'s `zip()` depends on.

**Tech Stack:** FastAPI + Jinja2 (server-rendered), vanilla JS (no bundler), pytest + `TestClient` for HTTP-level tests. No browser/JS test runner exists in this repo — JS behavior (popover open/close, live label updates) is verified manually, not via automated tests.

## Global Constraints

- No destructive DB migration — legacy `delay_days`-only step dicts (in `MailSequence.steps` and any `Task.steps_by_tier` snapshot) must keep working unmigrated, via the `step_delay_minutes()` compat helper.
- No "immediately after previous" vs "execute after N" radio toggle — delay `0` already means immediate, matching today's forced-zero-on-first-step behavior.
- No Save/Cancel buttons on the popover — edits apply live; the popover closes on outside click or Escape.
- All **new** writes (`_build_steps`) use only the `delay_minutes` key — never write `delay_days`.
- Every step (including the first) must submit exactly one `delay_minutes` form value, in step order, so `_build_steps`'s `zip(step_key, delay_minutes, subject, ...)` never truncates.

---

### Task 1: Engine — `step_delay_minutes` compat helper + minute-based scheduling

**Files:**
- Modify: `src/awkns_outreach/sequencer/engine.py:210`, `:218`
- Test: `tests/test_sequencer.py`

**Interfaces:**
- Produces: `step_delay_minutes(step: dict) -> int` in `src/awkns_outreach/sequencer/engine.py`, importable as `from awkns_outreach.sequencer.engine import step_delay_minutes`. Returns `step["delay_minutes"]` if present, else `step.get("delay_days", 0) * 1440`.

- [ ] **Step 1: Write failing tests for `step_delay_minutes` and minute-based `next_action_at`**

Add to `tests/test_sequencer.py`, right after the existing `_SEQ` constant (currently ends at line 22):

```python
def test_step_delay_minutes_reads_new_field_directly():
    assert engine.step_delay_minutes({"delay_minutes": 45}) == 45


def test_step_delay_minutes_falls_back_to_legacy_delay_days():
    assert engine.step_delay_minutes({"delay_days": 3}) == 3 * 1440


def test_step_delay_minutes_prefers_new_field_over_legacy():
    # A step should never carry both in practice, but if it does, the new
    # field wins — it's the one a fresh save would have written.
    assert engine.step_delay_minutes({"delay_minutes": 10, "delay_days": 3}) == 10


def test_step_delay_minutes_defaults_to_zero():
    assert engine.step_delay_minutes({}) == 0
```

Then update the existing `test_real_send_advances_step_and_logs` (currently lines 95-108) to use a `delay_minutes`-based sequence instead of the legacy `_SEQ`, so the engine's *primary* (non-fallback) path is exercised directly. Add a new fixture list right after `_SEQ` (around line 22) and a new test:

```python
_SEQ_MINUTES = [
    {"key": "intro", "delay_minutes": 0, "subject": "hi {company}", "body": "b0 {first_name}"},
    {"key": "bump", "delay_minutes": 90, "subject": "re: hi", "body": "b1"},
]
_STEPS_BY_TIER_MINUTES = {"B": _SEQ_MINUTES}


def test_real_send_advances_step_using_delay_minutes(db_session, monkeypatch):
    _mock_ok(monkeypatch)
    c = _campaign(db_session)
    lead = _lead(db_session, c)
    s = engine.process_campaign(db_session, c, _STEPS_BY_TIER_MINUTES, dry_run=False, now=NOW, ignore_hours=True, gap_ms=0)
    assert s.sent == 1
    db_session.refresh(lead)
    assert lead.step == 1 and lead.status == "active"
    got = lead.next_action_at.replace(tzinfo=None)
    assert got == (NOW + timedelta(minutes=90)).replace(tzinfo=None)
```

Leave the existing `test_real_send_advances_step_and_logs` test (using legacy `_SEQ` with `delay_days: 3`) exactly as-is — it now doubles as the legacy-fallback regression test end-to-end through the real engine.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_sequencer.py -k "delay_minutes" -v`
Expected: FAIL — `AttributeError: module 'awkns_outreach.sequencer.engine' has no attribute 'step_delay_minutes'` (and the new `test_real_send_advances_step_using_delay_minutes` fails because `next_action_at` is still computed via `.get("delay_days", 0)`, which is `0` for a `delay_minutes`-only step — off by 90 minutes).

- [ ] **Step 3: Implement `step_delay_minutes` and switch scheduling to minutes**

In `src/awkns_outreach/sequencer/engine.py`, add the helper near the top of the file, right after the module-level constants (after line 34, before the `RunSummary` dataclass at line 37):

```python
def step_delay_minutes(step: dict) -> int:
    """Canonical read path for a step's delay. New saves write `delay_minutes`
    directly; any step dict that predates it (persisted `MailSequence.steps`
    or a `Task.steps_by_tier` snapshot taken before this field existed) only
    has `delay_days` — convert it rather than requiring a data migration."""
    if "delay_minutes" in step:
        return step["delay_minutes"]
    return step.get("delay_days", 0) * 1440
```

Then update the two use sites:

`engine.py:210` — change:
```python
                next_delay = None if done else steps[next_step].get("delay_days", 0)
```
to:
```python
                next_delay = None if done else step_delay_minutes(steps[next_step])
```

`engine.py:218` — change:
```python
                lead.next_action_at = (
                    None if next_delay is None else now + timedelta(days=next_delay)
                )
```
to:
```python
                lead.next_action_at = (
                    None if next_delay is None else now + timedelta(minutes=next_delay)
                )
```

- [ ] **Step 4: Run all sequencer tests to verify they pass**

Run: `python -m pytest tests/test_sequencer.py -v`
Expected: PASS — all tests including the pre-existing `test_real_send_advances_step_and_logs` (legacy `delay_days: 3` → still resolves to `NOW + timedelta(days=3)` via the compat helper, since `3 * 1440` minutes equals exactly 3 days).

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/sequencer/engine.py tests/test_sequencer.py
git commit -m "feat: add delay_minutes compat helper, switch engine scheduling to minutes"
```

---

### Task 2: Sequences backend — migrate routes to `delay_minutes`

**Files:**
- Modify: `src/awkns_outreach/web/routes/sequences.py:86-128` (`_steps_for_editor`, `_build_steps`), `:165`, `:208` (route `Form(...)` params), `:176`, `:240` (call sites)
- Test: `tests/test_sequences_web.py`

**Interfaces:**
- Consumes: `step_delay_minutes(step: dict) -> int` from Task 1 (`awkns_outreach.sequencer.engine`).
- Produces: `_format_delay(minutes: int) -> dict` in `sequences.py`, returning `{"delay_value": int, "delay_unit": int, "delay_label": str}` — `delay_unit` is one of `1` (minutes), `60` (hours), `1440` (days); picks the largest unit that divides `minutes` evenly (falls back to minutes), and `0` → `delay_value=0, delay_unit=1440, delay_label="Immediately after previous"`. Consumed by Task 3's template.
- Produces: `_steps_for_editor` now also sets `step["delay_minutes"]` (int) and merges in `_format_delay(...)`'s three keys on every step dict it returns — Task 3's template reads `step.delay_minutes`, `step.delay_value`, `step.delay_unit`, `step.delay_label`.

- [ ] **Step 1: Write failing tests for the renamed form field**

In `tests/test_sequences_web.py`, update every test that posts or asserts `delay_days` to use `delay_minutes` instead — these are pure renames of the field the test already exercises, no new behavior:

`test_create_sequence_with_two_steps` (lines 65-93): change `"delay_days": ["not-a-number", "3"]` → `"delay_minutes": ["not-a-number", "3"]`; change `seq.steps[0]["delay_days"] == 0` → `seq.steps[0]["delay_minutes"] == 0`; change `seq.steps[1]["delay_days"] == 3` → `seq.steps[1]["delay_minutes"] == 3`.

`test_create_sequence_sanitizes_quill_html_body` (lines 96-108): change `"delay_days": ["0"]` → `"delay_minutes": ["0"]`.

`test_create_sequence_missing_name_errors_without_persisting` (lines 111-120): change `"delay_days": []` → `"delay_minutes": []`.

`test_edit_and_delete_blocked_while_archived` (lines 203-220): change `"delay_days": []` → `"delay_minutes": []`.

`test_save_edit_updates_name_and_steps` (lines 272-286): change `"delay_days": ["5"]` → `"delay_minutes": ["5"]`; change `seq.steps[0]["delay_days"] == 0` → `seq.steps[0]["delay_minutes"] == 0`.

Leave `test_edit_form_prefills_existing_sequence`, `test_edit_page_renders_rich_quill_editor_per_step`, and `test_edit_page_renders_independent_editor_per_step` untouched for now — they construct `MailSequence.steps` directly with legacy `delay_days` keys and exercise the *read* path, which Task 2 makes compat-safe via `step_delay_minutes`. Task 4 adds new assertions to these paths.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sequences_web.py -v`
Expected: FAIL — `_build_steps()` still expects a `delay_days` form field; with only `delay_minutes` posted, `create_sequence`/`update_sequence`'s `delay_days: list[str] = Form(default=[])` silently receives `[]`, so `zip()` in `_build_steps` produces zero steps for every test that posts a non-empty `step_key` list (`len(seq.steps) == 2` assertions fail; `seq.steps[0]["delay_minutes"]` raises `KeyError`/`IndexError`).

- [ ] **Step 3: Implement the backend field rename**

In `src/awkns_outreach/web/routes/sequences.py`:

Add the import (after line 23):
```python
from awkns_outreach.db.models import EmailTemplate, MailSequence, Task
from awkns_outreach.sequencer.engine import step_delay_minutes
```

Add `_format_delay` right before `_steps_for_editor` (before line 86):
```python
_DELAY_UNITS = (1440, 60, 1)  # days, hours, minutes — largest unit first
_DELAY_UNIT_NAMES = {1440: "day", 60: "hour", 1: "minute"}


def _format_delay(minutes: int) -> dict:
    """Precompute the connector pill's display fields for a step's delay, in
    the largest unit that divides `minutes` evenly (falls back to minutes).
    `0` reads as "immediately" and defaults its (unused-until-edited) unit
    dropdown to days, matching the old days-only control's default."""
    if not minutes:
        return {"delay_value": 0, "delay_unit": 1440, "delay_label": "Immediately after previous"}
    for unit in _DELAY_UNITS:
        if minutes % unit == 0:
            value = minutes // unit
            break
    else:
        value, unit = minutes, 1
    name = _DELAY_UNIT_NAMES[unit]
    label = f"{value} {name}{'s' if value != 1 else ''} after previous"
    return {"delay_value": value, "delay_unit": unit, "delay_label": label}
```

Update `_steps_for_editor` (lines 86-100) — add the two new lines inside the loop:
```python
def _steps_for_editor(steps: list[dict]) -> list[dict]:
    """Augment each saved step dict with the derived keys the editor
    template needs but doesn't persist: `attachments_json` (for the rich
    editor's hidden attachments-initial field), `preview` (the card's
    initial preview-pane render, before any HTMX interaction), and the
    normalized `delay_minutes`/`delay_value`/`delay_unit`/`delay_label`
    (works for both new steps and legacy `delay_days`-only ones, via
    `step_delay_minutes`) — mirrors templates_lib.py's edit_template_form
    convention exactly."""
    out = []
    for step in steps:
        step = dict(step)
        step["attachments_json"] = json.dumps(step.get("attachments") or [])
        step["preview"] = _render_preview(
            step.get("subject", ""), step.get("body", ""), step.get("attachments") or []
        )
        step["delay_minutes"] = step_delay_minutes(step)
        step.update(_format_delay(step["delay_minutes"]))
        out.append(step)
    return out
```

Update `_build_steps` (lines 103-128):
```python
def _build_steps(
    step_key: list[str], delay_minutes: list[str], subject: list[str], body: list[str],
    attachments: list[str], source_template_id: list[str],
) -> list[dict]:
    steps: list[dict] = []
    for i, (k, d, subj, b, a, sid) in enumerate(
        zip(step_key, delay_minutes, subject, body, attachments, source_template_id)
    ):
        # Skip fully blank rows (a step needs at least a subject or a body).
        if not subj.strip() and not b.strip():
            continue
        try:
            delay = max(0, int(d))
        except (TypeError, ValueError):
            delay = 0
        steps.append({
            "key": k.strip() or f"step{i + 1}",
            "delay_minutes": delay,
            "subject": subj.strip(),
            "body": _clean_body(b),
            "attachments": _parse_attachments(a),
            "source_template_id": sid.strip() or None,
        })
    if steps:
        steps[0]["delay_minutes"] = 0  # first step always fires immediately
    return steps
```

Update the two route signatures and their `_build_steps` call sites:

`create_sequence` (line 165): `delay_days: list[str] = Form(default=[]),` → `delay_minutes: list[str] = Form(default=[]),`; line 176: `_build_steps(step_key, delay_days, ...)` → `_build_steps(step_key, delay_minutes, ...)`.

`update_sequence` (line 208): same rename; line 240: same call-site rename.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sequences_web.py tests/test_mail_sequence_model.py tests/test_task_lifecycle.py tests/test_sequencer.py -v`
Expected: PASS. (`test_mail_sequence_model.py` and `test_task_lifecycle.py` construct `MailSequence`/`Task` rows directly with legacy `delay_days` and don't touch the form path — unaffected by this task, included here as a regression check since they share the model.)

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/web/routes/sequences.py tests/test_sequences_web.py
git commit -m "feat: migrate sequence step delay storage from delay_days to delay_minutes"
```

---

### Task 3: Editor UI — connector popover markup + JS

**Files:**
- Modify: `src/awkns_outreach/web/templates/mail_sequence_edit.html` (full file — macro at lines 9-78, content loop at lines 99-101, `<template id="step-tpl">` at line 113, inline `<script>` at lines 116-229)

**Interfaces:**
- Consumes: `step.delay_minutes`, `step.delay_value`, `step.delay_unit`, `step.delay_label` (from Task 2's `_steps_for_editor`).

- [ ] **Step 1: Update the `step_block` macro — remove the inline delay badge, add the disabled-by-default own-delay field**

In `mail_sequence_edit.html`, replace lines 19-30:
```html
  <div class="flex flex-wrap items-center gap-2 mb-3">
    <span class="delay-badge-first hidden text-xs font-medium text-slate-500 rounded-full bg-slate-100 px-3 py-1">
      Sent immediately
    </span>
    <label class="delay-badge-later text-xs font-medium text-slate-600 rounded-full bg-amber-50 border border-amber-200 px-3 py-1 inline-flex items-center gap-1">
      Send email in
      <input name="delay_days" type="number" min="0"
             value="{{ step.delay_days if step.delay_days is not none else 0 }}"
             class="w-14 border rounded px-1 py-0.5 text-xs text-center">
      day(s) after previous
    </label>
  </div>
```
with:
```html
  <div class="flex flex-wrap items-center gap-2 mb-3">
    <span class="delay-badge-first hidden text-xs font-medium text-slate-500 rounded-full bg-slate-100 px-3 py-1">
      Sent immediately
    </span>
    <!-- Only the current first step's delay is its own — every later step's
         delay lives on the connector pill rendered before it (step_connector
         below). This field stays disabled (excluded from form submission)
         except when renumber() marks this card as first, so exactly one
         delay_minutes value is submitted per step regardless of position. -->
    <input type="hidden" name="delay_minutes" value="0" class="step-own-delay-minutes" disabled>
  </div>
```

- [ ] **Step 2: Add the `step_connector` macro**

Immediately after the `step_block` macro's closing `{% endmacro %}` (currently line 78), add:
```html

{% macro step_connector(step) %}
<div class="step-connector relative z-10 flex justify-center -my-1">
  <button type="button"
          class="connector-pill inline-flex items-center gap-1 rounded-full border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-600 shadow-sm"
          onclick="toggleConnectorPopover(this)">
    <span aria-hidden="true">&#9201;</span>
    <span class="connector-label">{{ step.delay_label }}</span>
  </button>
  <div class="connector-popover hidden absolute top-full z-20 mt-1 flex items-center gap-2 whitespace-nowrap rounded border bg-white p-3 shadow-lg">
    <span class="text-xs text-slate-500">Execute step after</span>
    <input type="number" min="0" value="{{ step.delay_value }}"
           class="connector-value w-14 border rounded px-1 py-0.5 text-xs text-center">
    <select class="connector-unit border rounded px-1 py-0.5 text-xs">
      <option value="1" {% if step.delay_unit == 1 %}selected{% endif %}>minutes</option>
      <option value="60" {% if step.delay_unit == 60 %}selected{% endif %}>hours</option>
      <option value="1440" {% if step.delay_unit == 1440 %}selected{% endif %}>days</option>
    </select>
  </div>
  <input type="hidden" name="delay_minutes" value="{{ step.delay_minutes }}" class="connector-minutes">
</div>
{% endmacro %}
```

- [ ] **Step 3: Wire the connector into the steps loop and add its clone template**

Replace lines 99-101:
```html
  <div id="steps">
    {% for step in steps_for_editor %}{{ step_block(step) }}{% endfor %}
  </div>
```
with:
```html
  <div id="steps">
    {% for step in steps_for_editor %}
      {% if not loop.first %}{{ step_connector(step) }}{% endif %}
      {{ step_block(step) }}
    {% endfor %}
  </div>
```

Replace line 113:
```html
<template id="step-tpl">{{ step_block({}) }}</template>
```
with:
```html
<template id="step-tpl">{{ step_block({}) }}</template>
<template id="connector-tpl">{{ step_connector({'delay_minutes': 0, 'delay_value': 0, 'delay_unit': 1440, 'delay_label': 'Immediately after previous'}) }}</template>
```

- [ ] **Step 4: Add the connector JS — popover open/close, live label sync, add/remove/renumber updates**

In the inline `<script>` block, add these functions right after `refreshPreviewNow` (after line 146, before `function wireStepBlock`):
```javascript
  function formatDelayLabel(minutes) {
    if (!minutes) return 'Immediately after previous';
    let value, unitName;
    if (minutes % 1440 === 0) { value = minutes / 1440; unitName = 'day'; }
    else if (minutes % 60 === 0) { value = minutes / 60; unitName = 'hour'; }
    else { value = minutes; unitName = 'minute'; }
    return value + ' ' + unitName + (value !== 1 ? 's' : '') + ' after previous';
  }

  function updateConnectorFromPopover(connector) {
    const valueInput = connector.querySelector('.connector-value');
    const unitSelect = connector.querySelector('.connector-unit');
    const minutes = Math.max(0, parseInt(valueInput.value, 10) || 0) * parseInt(unitSelect.value, 10);
    connector.querySelector('.connector-minutes').value = minutes;
    connector.querySelector('.connector-label').textContent = formatDelayLabel(minutes);
  }

  function closeAllConnectorPopovers() {
    document.querySelectorAll('.connector-popover').forEach((p) => p.classList.add('hidden'));
  }

  function toggleConnectorPopover(btn) {
    const popover = btn.closest('.step-connector').querySelector('.connector-popover');
    const willOpen = popover.classList.contains('hidden');
    closeAllConnectorPopovers();
    popover.classList.toggle('hidden', !willOpen);
  }

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.step-connector')) closeAllConnectorPopovers();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAllConnectorPopovers();
  });

  function wireConnector(connector) {
    const apply = () => updateConnectorFromPopover(connector);
    connector.querySelector('.connector-value').addEventListener('input', apply);
    connector.querySelector('.connector-unit').addEventListener('change', apply);
  }
```

Update `renumber()` (currently lines 191-200) — replace:
```javascript
  function renumber() {
    const blocks = document.querySelectorAll('#steps .step-block');
    blocks.forEach((el, i) => {
      el.querySelector('.step-num').textContent = i + 1;
      const isFirst = i === 0;
      el.classList.toggle('is-first-step', isFirst);
      el.querySelector('.delay-badge-first').classList.toggle('hidden', !isFirst);
      el.querySelector('.delay-badge-later').classList.toggle('hidden', isFirst);
    });
  }
```
with:
```javascript
  function renumber() {
    const blocks = document.querySelectorAll('#steps .step-block');
    blocks.forEach((el, i) => {
      el.querySelector('.step-num').textContent = i + 1;
      const isFirst = i === 0;
      el.classList.toggle('is-first-step', isFirst);
      el.querySelector('.delay-badge-first').classList.toggle('hidden', !isFirst);
      el.querySelector('.step-own-delay-minutes').disabled = !isFirst;
    });
  }
```

Update `addStep()` (currently lines 202-209) — replace:
```javascript
  function addStep() {
    const tpl = document.getElementById('step-tpl');
    const node = tpl.content.firstElementChild.cloneNode(true);
    document.getElementById('steps').appendChild(node);
    twInitEditor(node.querySelector('.tw-editor'));
    wireStepBlock(node);
    renumber();
  }
```
with:
```javascript
  function addStep() {
    const stepsEl = document.getElementById('steps');
    const hasExisting = stepsEl.querySelectorAll('.step-block').length > 0;
    if (hasExisting) {
      const connTpl = document.getElementById('connector-tpl');
      const connNode = connTpl.content.firstElementChild.cloneNode(true);
      stepsEl.appendChild(connNode);
      wireConnector(connNode);
    }
    const tpl = document.getElementById('step-tpl');
    const node = tpl.content.firstElementChild.cloneNode(true);
    stepsEl.appendChild(node);
    twInitEditor(node.querySelector('.tw-editor'));
    wireStepBlock(node);
    renumber();
  }
```

Update `removeStep()` (currently lines 211-214) — replace:
```javascript
  function removeStep(btn) {
    btn.closest('.step-block').remove();
    renumber();
  }
```
with:
```javascript
  function removeStep(btn) {
    const block = btn.closest('.step-block');
    const prev = block.previousElementSibling;
    if (prev && prev.classList.contains('step-connector')) prev.remove();
    block.remove();
    // If the removed block was the first step, the connector that used to
    // sit between it and the (former) second step is now leading — a first
    // step never has a connector before it, so drop it too.
    const stepsEl = document.getElementById('steps');
    const first = stepsEl.firstElementChild;
    if (first && first.classList.contains('step-connector')) first.remove();
    renumber();
  }
```

Update the page-load wiring block (currently lines 220-225) — add connector wiring right before `renumber();` (line 226):
```javascript
  document.querySelectorAll('#steps .step-block').forEach((block) => {
    block.dataset.templateSync = '1';
    twInitEditor(block.querySelector('.tw-editor'));
    wireStepBlock(block);
    setTimeout(() => { delete block.dataset.templateSync; }, 0);
  });
  document.querySelectorAll('#steps .step-connector').forEach(wireConnector);
  renumber();
```

- [ ] **Step 5: Manually verify in a browser**

Run: use the `run` skill (or start the dev server per its instructions) and open `/sequences/new`.
Expected, click through each:
- A single step renders with no connector above it.
- "+ Add step" adds a second step with a connector pill reading "Immediately after previous" between them.
- Clicking the pill opens a popover with `0` / `days` selected; entering `2` and selecting `hours` updates the pill to "2 hours after previous" live, with no page reload.
- Clicking anywhere outside the popover closes it; pressing Escape closes it too.
- "Remove" on the second step removes both that step and its connector, leaving a single step with no connector.
- "+ Add step" twice, then "Remove" on the *first* step: the connector that was between steps 1 and 2 disappears too (no leading connector on the new first step).

This step has no automated pass/fail — record what you observed in your task handoff.

- [ ] **Step 6: Commit**

```bash
git add src/awkns_outreach/web/templates/mail_sequence_edit.html
git commit -m "feat: replace inline step delay badge with connector popover (days/hours/minutes)"
```

---

### Task 4: Integration tests — connector rendering, new + legacy data

**Files:**
- Modify: `tests/test_sequences_web.py`

**Interfaces:**
- Consumes: nothing new — exercises the full stack from Tasks 1-3 through `TestClient` HTTP calls.

- [ ] **Step 1: Write the new tests**

Add to `tests/test_sequences_web.py`, after `test_edit_page_renders_independent_editor_per_step` (currently ends at line 200):

```python
def test_edit_page_renders_connector_between_steps_with_delay_label(client, session):
    seq = MailSequence(
        name="Two steps", status="active",
        steps=[
            {"key": "intro", "delay_minutes": 0, "subject": "s1", "body": "b1",
             "attachments": [], "source_template_id": None},
            {"key": "bump", "delay_minutes": 4320, "subject": "s2", "body": "b2",
             "attachments": [], "source_template_id": None},
        ],
    )
    session.add(seq)
    session.commit()

    r = client.get(f"/sequences/{seq.id}/edit", auth=AUTH)
    assert r.status_code == 200
    live_html = r.text.split('<template id="step-tpl">')[0]
    # Exactly one connector — between step 1 and step 2, none before step 1.
    assert live_html.count('class="step-connector') == 1
    assert "3 days after previous" in live_html
    assert 'name="delay_minutes" value="4320"' in live_html


def test_edit_page_connector_falls_back_to_legacy_delay_days(client, session):
    seq = MailSequence(
        name="Legacy seq", status="active",
        steps=[
            {"key": "intro", "delay_days": 0, "subject": "s1", "body": "b1",
             "attachments": [], "source_template_id": None},
            {"key": "bump", "delay_days": 3, "subject": "s2", "body": "b2",
             "attachments": [], "source_template_id": None},
        ],
    )
    session.add(seq)
    session.commit()

    r = client.get(f"/sequences/{seq.id}/edit", auth=AUTH)
    assert r.status_code == 200
    assert "3 days after previous" in r.text
    assert 'name="delay_minutes" value="4320"' in r.text


def test_new_sequence_form_has_no_connector_before_first_step(client, session):
    r = client.get("/sequences/new", auth=AUTH)
    assert r.status_code == 200
    live_html = r.text.split('<template id="step-tpl">')[0]
    assert 'class="step-connector' not in live_html
```

- [ ] **Step 2: Run to verify they fail (if run before Tasks 1-3) or pass (if run after)**

Run: `python -m pytest tests/test_sequences_web.py -v`
Expected: PASS if Tasks 1-3 are already committed (this task only adds coverage, no new production code). If this task is somehow reached before Task 3's template changes exist, `test_edit_page_renders_connector_between_steps_with_delay_label` and `test_edit_page_connector_falls_back_to_legacy_delay_days` fail on the `step-connector`/`delay_minutes` assertions — confirming they actually test the new markup.

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest -v`
Expected: PASS, all tests.

- [ ] **Step 4: Commit**

```bash
git add tests/test_sequences_web.py
git commit -m "test: cover connector rendering for new and legacy delay_days sequences"
```
