# Task send-window overrides: ignore business hours / workdays

**Date:** 2026-07-26
**Status:** Approved design, ready for implementation plan

## Problem

The sequencer only sends during the recipient's local **Mon–Fri 09:00–17:00**
window (`limits.in_business_hours`, keyed off `lead.country`'s timezone). A
task can sit in `running` for hours yet silently send nothing — every tick hits
`skipped:hours` (which produces no log line), so from the Tasks page it looks
indistinguishable from a broken scheduler.

Operators need a way, per task, to relax this window — e.g. to send to a US lead
during a Taiwan working afternoon (US night), or to send over the weekend for a
one-off test — without waiting for the recipient's next business window.

## Goal

Let an operator override the send-window gate along **two independent
dimensions**:

- **Ignore business hours** — bypass the 09:00–17:00 time-of-day check.
- **Ignore workdays** — bypass the Mon–Fri check.

Each is toggled independently (e.g. ignore workdays but still restrict to
09:00–17:00). The per-campaign **daily send cap (warmup ramp) always still
applies** — this feature relaxes *timing* only, never *volume*.

## Non-goals

- No change to the daily cap / warmup logic.
- No per-lead or per-tier override — the setting is per task.
- No new "send at exactly time X" scheduling; this only widens the allowed window.

## Behavior by entry point

Three Tasks-page actions gain a **modal (`<dialog>`) with two checkboxes**:
`Ignore business hours (09:00–17:00)` and `Ignore workdays (Mon–Fri)`.

| Action | Modal contents | Where the choice goes |
|---|---|---|
| **Schedule** (draft) | start datetime (required), end datetime (optional), 2 checkboxes | Persisted to the Task; every scheduler tick honors it |
| **Start now** (draft/scheduled) | 2 checkboxes | Persisted to the Task; every scheduler tick honors it |
| **Run** (running, manual one-off) | max, send-for-real, 2 checkboxes | **Transient** — applied to that single `process_campaign` call only; Task fields are NOT written |

Rationale: Schedule and Start now define how the task runs on every future tick,
so they persist. Run is a manual test tool, so its overrides stay one-off and
never mutate the task's stored behavior.

## Data model

Add two columns to `Task` (both `Boolean`, `NOT NULL`, `server_default='false'`):

- `ignore_business_hours` — ignore the 09:00–17:00 time-of-day gate.
- `ignore_workdays` — ignore the Mon–Fri gate.

One Alembic migration adds both columns; existing rows default to `false`
(current behavior preserved).

## Engine changes

### `sequencer/limits.py`
Replace the single-purpose `in_business_hours(now, country)` with a
two-flag window check:

```python
def in_send_window(
    now: datetime, country: Optional[str], *,
    ignore_hours: bool = False, ignore_days: bool = False,
) -> bool:
    local = now.astimezone(ZoneInfo(tz_for(country)))
    day_ok = ignore_days or local.weekday() in SEND.send_days
    hour_ok = ignore_hours or SEND.send_hours[0] <= local.hour < SEND.send_hours[1]
    return day_ok and hour_ok
```

Update the `sequencer/__init__.py` export accordingly (drop
`in_business_hours`, export `in_send_window`).

### `sequencer/engine.py`
`process_campaign`'s single `ignore_hours: bool` parameter becomes two:
`ignore_business_hours: bool = False` and `ignore_workdays: bool = False`.
The gate at the candidate loop changes from:

```python
if not ignore_hours and not in_business_hours(now, lead.country):
```

to:

```python
if not in_send_window(now, lead.country,
                      ignore_hours=ignore_business_hours,
                      ignore_days=ignore_workdays):
```

### `runner.py`
`run_all_campaigns` reads each running task's stored flags and forwards them:

```python
process_campaign(
    session, task.campaign, task.steps_by_tier, dry_run=dry_run,
    max_this_run=max_this_run, gap_ms=gap_ms, now=now,
    ignore_business_hours=task.ignore_business_hours,
    ignore_workdays=task.ignore_workdays,
)
```

### `cli.py`
Replace `--ignore-hours` with `--ignore-business-hours` and `--ignore-workdays`,
passed through to `process_campaign`.

## Lifecycle changes (`sequencer/lifecycle.py`)

- `schedule_task(db, task, when, end_at=None, *, ignore_business_hours=False,
  ignore_workdays=False)` — sets both fields on the task before commit.
- `start_task(db, task, now, *, ignore_business_hours=None,
  ignore_workdays=None)` — when a flag is `None`, leave the task's existing
  value untouched; when a bool is given, set it. This matters because
  `start_due_tasks` (the scheduler auto-starting an already-scheduled task at
  [lifecycle.py:189]) calls `start_task` with no flags — it must **preserve** the
  values chosen at schedule time, not reset them. The "Start now" button passes
  explicit bools.

## Web route changes (`web/routes/tasks.py`)

- `schedule_task_route`: accept `ignore_business_hours: bool = Form(False)` and
  `ignore_workdays: bool = Form(False)`; pass to `schedule_task`.
- `lifecycle_action_route` (or a dedicated start handler): for `action == "start"`,
  accept the two form checkboxes and pass explicit bools to `start_task`. Other
  lifecycle actions (pause/resume/stop) are unaffected.
- `run_task`: accept the two checkboxes and pass them straight to
  `process_campaign`; do **not** touch the Task row.

## Frontend (`templates/tasks.html`)

- htmx 2.0.3 + Tailwind (CDN) are the only front-end deps; no Alpine. Use the
  native `<dialog>` element opened with `showModal()` and a one-line inline
  `onclick`.
- Each task row renders its own `<dialog>`(s) for the actions available in its
  status, so no JS is needed to rewire form `action`s. The action's existing
  form (with its specific fields) moves inside the dialog and gains the two
  checkboxes; the visible button becomes `type="button"` that opens the dialog.
- Optionally surface an at-a-glance marker next to a task whose stored flags are
  set (e.g. a small `any-time` / `any-day` badge) so operators can see a task is
  overriding the window. (Low priority; include if cheap.)

## Testing

- `limits.in_send_window`: table test over {ignore_hours × ignore_days} ×
  {weekday, weekend} × {in-hours, off-hours}, asserting the gate result.
- `lifecycle.schedule_task`: persists both flags.
- `lifecycle.start_task`: explicit bools set the fields; `None` leaves existing
  values intact (guards the scheduler auto-start path).
- `runner.run_all_campaigns`: a running task with flags set forwards them to
  `process_campaign` (verify a US lead off-hours is sent when flags on, and
  `skipped:hours` when off).
- `run_task` route: passing the checkboxes affects the run but leaves the Task
  columns unchanged.

## Migration / rollout

- One Alembic revision (add two boolean columns). Backward compatible: existing
  tasks default to `false` = today's behavior.
- No data backfill needed.
