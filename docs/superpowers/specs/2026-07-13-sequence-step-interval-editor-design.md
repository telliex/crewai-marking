# Sequence step interval editor (days/hours/minutes popover)

Date: 2026-07-13
Status: draft — pending user review

## Goal

In the sequence editor (`mail_sequence_edit.html`, `/sequences/new` and
`/sequences/{id}/edit`), the gap between two steps is currently an inline
badge embedded in each non-first step's card: "Send email in [ ] day(s)
after previous" — days only, no other units.

Redesign this into a click-to-open popover positioned **between** step
cards (Apollo-style "when to start this step"), with a number input +
`days / hours / minutes` unit dropdown, and extend the backend to store
delay at minute granularity so those units are meaningful.

Out of scope:
- No "immediately after previous step completes" vs "execute after N"
  radio toggle (Apollo has this; user explicitly asked only for the
  execute-after-N-unit control). Setting the delay to 0 already sends
  immediately, matching today's behavior for the first step.
- No Save/Cancel buttons on the popover — edits apply live; the popover
  just closes on outside click.
- No destructive DB migration of existing `MailSequence.steps` or
  in-flight `Task.steps_by_tier` snapshots — old `delay_days`-only step
  dicts keep working via a compatibility read path (below).

## 1. Data model — `delay_minutes`, legacy-compatible

`MailSequence.steps` (`db/models.py:272-291`) is a JSON list of step dicts.
`Task.steps_by_tier` snapshots these at task-start time
(`db/models.py:287`'s docstring), so old `delay_days`-only dicts can be
baked into in-flight tasks indefinitely — a hard migration would need to
rewrite both tables and risks getting live scheduling wrong. Instead:

- New canonical field: `delay_minutes` (int, minutes). All new writes use
  only this key.
- Shared compatibility helper, `step_delay_minutes(step: dict) -> int`,
  added to `sequencer/engine.py` (imported by `sequences.py` where the
  editor needs to prefill the popover for existing steps):
  ```python
  def step_delay_minutes(step: dict) -> int:
      if "delay_minutes" in step:
          return step["delay_minutes"]
      return step.get("delay_days", 0) * 1440
  ```
- `engine.py:210`: `steps[next_step].get("delay_days", 0)` →
  `step_delay_minutes(steps[next_step])`.
- `engine.py:218`: `timedelta(days=next_delay)` → `timedelta(minutes=next_delay)`.
- `sequences.py::_build_steps`: form param renamed `delay_days` →
  `delay_minutes` (`list[str]`), parsed the same way (`max(0, int(d))`,
  default 0 on parse failure), written as `steps[i]["delay_minutes"]`.
  First step still forced to 0 (`steps[0]["delay_minutes"] = 0`).
- `create_sequence` / `update_sequence` route signatures: `delay_days:
  list[str] = Form(...)` → `delay_minutes: list[str] = Form(...)`.
- `_steps_for_editor` (used to prefill the edit form): for each step, set
  a `delay_minutes` key via `step_delay_minutes(step)` so the template
  always has a normalized value regardless of whether the underlying
  step dict is legacy or new — the template never reads `delay_days`.

## 2. Editor UI — connector pill + popover

### 2.1 Structure

`step_block` macro (`mail_sequence_edit.html:9-78`) loses the
`delay-badge-later` `<label>` (lines 23-29) — steps no longer carry a
*visible* delay control. `delay-badge-first` ("Sent immediately", lines
20-22) stays, unchanged, for step 1.

**Form-field count/order constraint:** `_build_steps` zips `step_key`,
`delay_minutes`, `subject`, etc. as equal-length, index-aligned lists
(`sequences.py:103-128`). The connector only exists *between* steps
(N-1 of them for N steps), but every step — including the first — must
still contribute exactly one `delay_minutes` value, in step order, or
`zip()` silently truncates to the shortest list and **drops the last
step**. So `step_block` keeps one hidden input:
```html
<input type="hidden" name="delay_minutes" value="0" class="step-own-delay-minutes">
```
disabled by default via the `disabled` attribute (disabled fields are
excluded from form submission — no manual add/remove needed). `renumber()`
(2.3) sets `.disabled = !isFirst` on this input for every step: enabled
(→ submits its `0`) only on the current first step, disabled on all
others, whose delay instead comes from their preceding connector's own
hidden `delay_minutes` input. Net result: exactly N `delay_minutes`
values submitted in step order — one from the first step's own hidden
field, one from each connector — matching `step_key`'s length exactly.

A new macro, `step_connector(delay_minutes)`, renders the pill placed
**between** step cards:

```html
<div class="step-connector relative flex justify-center my-[-0.5rem] z-10">
  <button type="button" class="connector-pill ..." onclick="toggleConnectorPopover(this)">
    ⏱ <span class="connector-label">{{ delay_label }}</span>
  </button>
  <div class="connector-popover hidden absolute top-full mt-1 rounded border bg-white shadow-lg p-3 flex items-center gap-2">
    <span class="text-xs text-slate-500">Execute step after</span>
    <input type="number" min="0" class="connector-value w-14 border rounded px-1 py-0.5 text-xs text-center">
    <select class="connector-unit border rounded px-1 py-0.5 text-xs">
      <option value="1">minutes</option>
      <option value="60">hours</option>
      <option value="1440" selected>days</option>
    </select>
  </div>
  <input type="hidden" name="delay_minutes" class="connector-minutes" value="{{ delay_minutes }}">
</div>
```

`delay_label` is computed server-side for the initial render the same way
the client JS computes it after edits (2.3) — e.g. "2 days after
previous", "3 hours after previous", "Immediately after previous" for 0.

### 2.2 Layout wiring

`{% for step in steps_for_editor %}` (`mail_sequence_edit.html:100`)
becomes: render `step_block(step)` for index 0, then for each subsequent
step render `step_connector(step.delay_minutes)` followed by
`step_block(step)`. The `<template id="step-tpl">` gets a matching
sibling `<template id="connector-tpl">` for JS-side cloning.

### 2.3 JS changes (`mail_sequence_edit.html` inline `<script>`)

- `toggleConnectorPopover(btn)`: toggles `.connector-popover` visibility
  on the clicked pill's wrapper; a single `document.addEventListener('click', ...)`
  closes any open popover when the click target isn't inside a
  `.step-connector` (standard outside-click-close pattern), also closes
  on `Escape`.
- Popover's `.connector-value` / `.connector-unit` get an `input`/`change`
  listener that recomputes `minutes = value * unitMultiplier`, writes it
  to the sibling `.connector-minutes` hidden input, and updates
  `.connector-label` text (same formatting as the server-side label:
  singular/plural unit name, "Immediately after previous" at 0).
- `addStep()`: if `#steps` already has at least one `.step-block`, clone
  `#connector-tpl` and append it before appending the new step block; if
  `#steps` is empty (first step), append only the step block, no
  connector — mirrors the existing first-step-has-no-delay invariant.
- `removeStep(btn)`: also removes the connector immediately preceding the
  removed step block (if any); if the removed block was index 0 and a
  connector now sits at the very start of `#steps`, remove that leading
  connector too (renumber's first-step invariant, extended to connectors).
- `renumber()`: unchanged step-numbering logic, still toggles
  `delay-badge-first` visibility on step 1's own card; additionally sets
  `.step-own-delay-minutes.disabled = !isFirst` on every step block (2.1)
  so exactly one step (the current first) submits its own zero delay and
  every other step's delay comes from its preceding connector.

## 3. Testing

- `tests/test_web.py` sequence create/edit tests: post `delay_minutes`
  instead of `delay_days`; assert the stored `MailSequence.steps` have
  `delay_minutes` (not `delay_days`).
- New test for `step_delay_minutes()`: a dict with only legacy
  `delay_days` returns the correct minute value; a dict with
  `delay_minutes` returns it directly, ignoring any stray `delay_days`.
- Engine test: a lead's `next_action_at` computed correctly in minutes
  for a fresh `delay_minutes` step, and correctly (via the compat
  fallback) for a legacy `delay_days`-only step.
