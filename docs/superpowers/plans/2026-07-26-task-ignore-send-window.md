# Task Ignore Send-Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator override the sequencer's send-window gate per task along two independent dimensions — ignore business hours (09:00–17:00) and ignore workdays (Mon–Fri) — via a checkbox modal on the Schedule / Start now / Run actions.

**Architecture:** Two boolean columns on `Task` store the persistent choice (set by Schedule and Start now, read every scheduler tick by `runner.run_all_campaigns`). The engine's single `ignore_hours` flag is split into two independent flags threaded through `limits.in_send_window` → `engine.process_campaign` → `runner`. The manual Run action passes the two flags transiently to `process_campaign` without persisting them. The Tasks page wraps each action's form in a native `<dialog>` with two checkboxes.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0 (Mapped/mapped_column), Alembic, FastAPI, Jinja2 templates, htmx 2.0.3 + Tailwind (CDN), pytest (sqlite in-memory fixtures).

## Global Constraints

- Daily send cap / warmup ramp is untouched — this feature relaxes timing only, never volume.
- New columns: `Boolean`, `NOT NULL`, `server_default='false'` — existing rows keep today's behavior.
- `start_task`'s flag parameters default to `None` meaning "leave the task's stored value unchanged"; only explicit bools overwrite. This preserves schedule-time flags when the scheduler auto-starts a scheduled task.
- Run's flags are transient — never written to the Task row.
- Tests use the sqlite in-memory `db_session` fixture (`Base.metadata.create_all`); they do not run Alembic. The Alembic migration mirrors the model for real Postgres.
- HTML checkboxes use `value="1"`; FastAPI reads them as `bool = Form(False)`.

---

### Task 1: Task model columns + Alembic migration

**Files:**
- Modify: `src/awkns_outreach/db/models.py` (Task class, after `end_at` block ~line 336)
- Create: `src/awkns_outreach/db/migrations/versions/0010_task_send_window_overrides.py`
- Test: `tests/test_task_lifecycle.py`

**Interfaces:**
- Produces: `Task.ignore_business_hours: bool` and `Task.ignore_workdays: bool`, both defaulting to `False`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_task_lifecycle.py`:

```python
def test_task_send_window_flags_default_false(db_session):
    c = _campaign(db_session)
    task = _task(db_session, c)
    assert task.ignore_business_hours is False
    assert task.ignore_workdays is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_task_lifecycle.py::test_task_send_window_flags_default_false -v`
Expected: FAIL — `AttributeError: 'Task' object has no attribute 'ignore_business_hours'`

- [ ] **Step 3: Add the columns to the Task model**

In `src/awkns_outreach/db/models.py`, in the `Task` class, immediately after the `end_at` column block (the one ending `nullable=True\n    )` around line 336), insert:

```python
    # Send-window overrides (set via Schedule / Start now, read every tick by
    # runner.run_all_campaigns). Relax *timing* only — the daily cap still
    # applies. Independent: a task may ignore workdays but still honor 09:00–17:00.
    ignore_business_hours: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_false()
    )
    ignore_workdays: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_false()
    )
```

At the top of `models.py`, ensure `Boolean` and a false server-default are importable. Check the existing SQLAlchemy import line (it already imports names like `String`, `DateTime`, `ForeignKey`, `Index`, `func`). Add `Boolean` to that import if missing, and add `false as sa_false` from `sqlalchemy`:

```python
from sqlalchemy import Boolean, false as sa_false
```

(Merge into the existing `from sqlalchemy import ...` line rather than adding a duplicate import.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_task_lifecycle.py::test_task_send_window_flags_default_false -v`
Expected: PASS

- [ ] **Step 5: Create the Alembic migration**

Create `src/awkns_outreach/db/migrations/versions/0010_task_send_window_overrides.py`:

```python
"""task send-window overrides

Revision ID: 0010_task_send_window_overrides
Revises: 0009_tasks_restructure
Create Date: 2026-07-26

Adds two per-task booleans that relax the sequencer's send-window gate:
`ignore_business_hours` (bypass the recipient-local 09:00–17:00 check) and
`ignore_workdays` (bypass the Mon–Fri check). Both default false, so existing
tasks keep today's business-hours-only sending.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_task_send_window_overrides"
down_revision: Union[str, None] = "0009_tasks_restructure"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task",
        sa.Column("ignore_business_hours", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )
    op.add_column(
        "task",
        sa.Column("ignore_workdays", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("task", "ignore_workdays")
    op.drop_column("task", "ignore_business_hours")
```

- [ ] **Step 6: Verify the full lifecycle test file still passes**

Run: `uv run pytest tests/test_task_lifecycle.py -v`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add src/awkns_outreach/db/models.py src/awkns_outreach/db/migrations/versions/0010_task_send_window_overrides.py tests/test_task_lifecycle.py
git commit -m "feat: add ignore_business_hours/ignore_workdays columns to Task"
```

---

### Task 2: Two-flag send-window in limits + engine + cli

**Files:**
- Modify: `src/awkns_outreach/sequencer/limits.py:61` (replace `in_business_hours`)
- Modify: `src/awkns_outreach/sequencer/__init__.py` (export rename)
- Modify: `src/awkns_outreach/sequencer/engine.py:29,75,179` (import, params, gate)
- Modify: `src/awkns_outreach/cli.py:89,103` (CLI options)
- Test: `tests/test_sequencer.py`

**Interfaces:**
- Produces: `limits.in_send_window(now, country, *, ignore_hours=False, ignore_days=False) -> bool`
- Produces: `engine.process_campaign(..., ignore_business_hours: bool = False, ignore_workdays: bool = False)` (the old `ignore_hours` param is removed)
- Consumes: nothing from Task 1.

- [ ] **Step 1: Write the failing unit test for `in_send_window`**

In `tests/test_sequencer.py`, replace the existing import of `in_business_hours` (currently on the `from awkns_outreach.sequencer import (...)` block around line 13) with `in_send_window`, and replace the existing business-hours test (the one using `in_business_hours` around lines 60–71) with:

```python
def test_in_send_window_dimensions():
    sat = datetime(2026, 7, 25, 3, 0, tzinfo=UTC)   # Sat, 11:00 Taipei
    mon = datetime(2026, 7, 27, 3, 0, tzinfo=UTC)   # Mon, 11:00 Taipei
    night = datetime(2026, 7, 27, 14, 0, tzinfo=UTC)  # Mon, 22:00 Taipei
    # Default gate: both must hold.
    assert not in_send_window(sat, "TW")            # weekend blocks
    assert in_send_window(mon, "TW")                # weekday + in-hours
    assert not in_send_window(night, "TW")          # off-hours blocks
    # ignore_days lets the weekend through, still honoring hours.
    assert in_send_window(sat, "TW", ignore_days=True)
    assert not in_send_window(night, "TW", ignore_days=True)  # still off-hours
    # ignore_hours lets the night through, still honoring the day.
    assert in_send_window(night, "TW", ignore_hours=True)
    assert not in_send_window(sat, "TW", ignore_hours=True)   # still weekend
    # Both ignored: always true.
    assert in_send_window(sat, "TW", ignore_hours=True, ignore_days=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sequencer.py::test_in_send_window_dimensions -v`
Expected: FAIL — `ImportError: cannot import name 'in_send_window'`

- [ ] **Step 3: Replace `in_business_hours` in `limits.py`**

In `src/awkns_outreach/sequencer/limits.py`, replace the whole `in_business_hours` function (lines 61–69) with:

```python
def in_send_window(
    now: datetime, country: Optional[str], *,
    ignore_hours: bool = False, ignore_days: bool = False,
) -> bool:
    """True if `now` is inside the recipient's local send window. By default
    that's Mon–Fri 09:00–17:00; `ignore_days` drops the weekday check and
    `ignore_hours` drops the time-of-day check (each independently)."""
    local = now.astimezone(ZoneInfo(tz_for(country)))
    day_ok = ignore_days or local.weekday() in SEND.send_days
    hour_ok = ignore_hours or SEND.send_hours[0] <= local.hour < SEND.send_hours[1]
    return day_ok and hour_ok
```

- [ ] **Step 4: Update the sequencer package exports**

In `src/awkns_outreach/sequencer/__init__.py`, replace `in_business_hours` with `in_send_window` in both the `from awkns_outreach.sequencer.limits import (...)` block and the `__all__` list:

```python
from awkns_outreach.sequencer.limits import (
    SEND,
    in_send_window,
    tz_for,
    warmup_cap,
)

__all__ = [
    "RunSummary",
    "process_campaign",
    "SEND",
    "in_send_window",
    "tz_for",
    "warmup_cap",
]
```

- [ ] **Step 5: Update the engine — import, params, gate**

In `src/awkns_outreach/sequencer/engine.py`:

Line 29 import — change `in_business_hours` to `in_send_window`:

```python
from awkns_outreach.sequencer.limits import SEND, in_send_window, warmup_cap
```

In the `process_campaign` signature (around line 75), replace `ignore_hours: bool = False,` with:

```python
    ignore_business_hours: bool = False,
    ignore_workdays: bool = False,
```

Replace the business-hours gate (line 179) with:

```python
        if not in_send_window(now, lead.country,
                              ignore_hours=ignore_business_hours,
                              ignore_days=ignore_workdays):
```

(The `summary.details.append({... "result": "skipped:hours"})` line directly below it is unchanged.)

- [ ] **Step 6: Update the CLI**

In `src/awkns_outreach/cli.py`, replace the `ignore_hours` option (line 89) with two options:

```python
    ignore_business_hours: bool = typer.Option(
        False, help="Bypass the 09:00-17:00 time-of-day gate (testing/manual)."),
    ignore_workdays: bool = typer.Option(
        False, help="Bypass the Mon-Fri gate (testing/manual)."),
```

And update the `process_campaign(...)` call (line 103) to forward both:

```python
        summary = process_campaign(s, c, task.steps_by_tier, dry_run=not send,
                                   max_this_run=max_this_run,
                                   ignore_business_hours=ignore_business_hours,
                                   ignore_workdays=ignore_workdays)
```

- [ ] **Step 7: Update existing engine tests to the new param name**

In `tests/test_sequencer.py`, every existing `process_campaign(...)` call that passes `ignore_hours=True` must become `ignore_business_hours=True, ignore_workdays=True` (the lines around 112, 123, 139, 151, 167, 189). This preserves their intent (bypass the whole window for the test). Example — the call at ~line 112 becomes:

```python
    s = engine.process_campaign(db_session, c, _STEPS_BY_TIER, dry_run=True, now=NOW,
                                ignore_business_hours=True, ignore_workdays=True)
```

Apply the same edit to each of those calls. Leave `test_business_hours_skip` (~line 173) unchanged — it relies on the default gate with no ignore kwargs.

- [ ] **Step 8: Write a test that the engine honors the two flags for an off-hours lead**

Add to `tests/test_sequencer.py` (uses the existing `_mock_ok`, `_campaign`, `_lead`, `_STEPS_BY_TIER` helpers):

```python
def test_engine_ignore_flags_bypass_window(db_session, monkeypatch):
    _mock_ok(monkeypatch)
    sat_night = datetime(2026, 7, 25, 14, 0, tzinfo=UTC)  # Sat 22:00 Taipei
    c = _campaign(db_session)
    _lead(db_session, c, country="TW")
    # Default gate: weekend + night -> skipped, nothing sent.
    blocked = engine.process_campaign(db_session, c, _STEPS_BY_TIER,
                                      dry_run=False, now=sat_night, gap_ms=0)
    assert blocked.sent == 0 and blocked.skipped == 1
    # Both flags on -> the same lead sends.
    ok = engine.process_campaign(db_session, c, _STEPS_BY_TIER, dry_run=False,
                                 now=sat_night, gap_ms=0,
                                 ignore_business_hours=True, ignore_workdays=True)
    assert ok.sent == 1
```

- [ ] **Step 9: Run the sequencer tests**

Run: `uv run pytest tests/test_sequencer.py -v`
Expected: PASS (all tests, including the two new ones)

- [ ] **Step 10: Commit**

```bash
git add src/awkns_outreach/sequencer/limits.py src/awkns_outreach/sequencer/__init__.py src/awkns_outreach/sequencer/engine.py src/awkns_outreach/cli.py tests/test_sequencer.py
git commit -m "feat: split send-window gate into ignore_business_hours + ignore_workdays"
```

---

### Task 3: Runner forwards each running task's stored flags

**Files:**
- Modify: `src/awkns_outreach/runner.py:27-30`
- Test: `tests/test_task_lifecycle.py`

**Interfaces:**
- Consumes: `Task.ignore_business_hours`, `Task.ignore_workdays` (Task 1); `process_campaign(..., ignore_business_hours=, ignore_workdays=)` (Task 2).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_task_lifecycle.py`. This drives the real engine through `run_all_campaigns` (import it and `_mock_ok`-style patch). At the top of the file, add imports if missing:

```python
from awkns_outreach.runner import run_all_campaigns
from awkns_outreach.send.mailer import SendResult
```

Then the test:

```python
def test_runner_forwards_ignore_flags(db_session, monkeypatch):
    monkeypatch.setattr(
        engine, "send_outreach_email",
        lambda l, c, e, s, steps, dry_run: SendResult(ok=True, id="m1", subject="s"),
    )
    sat_night = datetime(2026, 7, 25, 14, 0, tzinfo=UTC)  # Sat 22:00 Taipei
    c = _campaign(db_session)
    seq = _seq(db_session)
    task = _task(db_session, c, sequences={"B": seq.id},
                 ignore_business_hours=True, ignore_workdays=True)
    lifecycle.start_task(db_session, task, sat_night)
    from awkns_outreach.db.models import Lead
    db_session.add(Lead(campaign_id=c.id, email="w@x.com", company="X",
                        status="active", step=0, tier="B", country="TW"))
    db_session.commit()

    results = run_all_campaigns(db_session, dry_run=False, max_this_run=5,
                                gap_ms=0, now=sat_night)
    total_sent = sum(s.sent for _c, s in results)
    assert total_sent == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_task_lifecycle.py::test_runner_forwards_ignore_flags -v`
Expected: FAIL — `total_sent == 0` (runner doesn't forward the flags yet, so the off-hours lead is skipped)

- [ ] **Step 3: Forward the flags in `run_all_campaigns`**

In `src/awkns_outreach/runner.py`, update the `process_campaign(...)` call inside the loop (lines 27–30) to:

```python
        summary = process_campaign(
            session, task.campaign, task.steps_by_tier, dry_run=dry_run,
            max_this_run=max_this_run, gap_ms=gap_ms, now=now,
            ignore_business_hours=task.ignore_business_hours,
            ignore_workdays=task.ignore_workdays,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_task_lifecycle.py::test_runner_forwards_ignore_flags -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/runner.py tests/test_task_lifecycle.py
git commit -m "feat: runner forwards per-task ignore-window flags to the engine"
```

---

### Task 4: Lifecycle persists flags on schedule / start

**Files:**
- Modify: `src/awkns_outreach/sequencer/lifecycle.py:69` (`schedule_task`), `:95` (`start_task`)
- Test: `tests/test_task_lifecycle.py`

**Interfaces:**
- Produces: `schedule_task(db, task, when, end_at=None, *, ignore_business_hours=False, ignore_workdays=False)`
- Produces: `start_task(db, task, now, *, ignore_business_hours=None, ignore_workdays=None)` — `None` means leave the stored value unchanged.
- Consumes: `Task.ignore_business_hours`, `Task.ignore_workdays` (Task 1).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_task_lifecycle.py`:

```python
def test_schedule_task_persists_ignore_flags(db_session):
    c = _campaign(db_session)
    seq = _seq(db_session)
    task = _task(db_session, c, sequences={"B": seq.id})
    ok, _ = lifecycle.schedule_task(db_session, task, NOW,
                                    ignore_business_hours=True, ignore_workdays=True)
    assert ok
    assert task.ignore_business_hours is True
    assert task.ignore_workdays is True


def test_start_task_none_preserves_scheduled_flags(db_session):
    c = _campaign(db_session)
    seq = _seq(db_session)
    task = _task(db_session, c, sequences={"B": seq.id})
    lifecycle.schedule_task(db_session, task, NOW, ignore_business_hours=True)
    # Scheduler auto-start passes no flags -> must not reset the scheduled choice.
    ok, _ = lifecycle.start_task(db_session, task, NOW)
    assert ok
    assert task.ignore_business_hours is True
    assert task.ignore_workdays is False


def test_start_task_explicit_flags_override(db_session):
    c = _campaign(db_session)
    seq = _seq(db_session)
    task = _task(db_session, c, sequences={"B": seq.id})
    ok, _ = lifecycle.start_task(db_session, task, NOW,
                                 ignore_business_hours=True, ignore_workdays=True)
    assert ok
    assert task.ignore_business_hours is True
    assert task.ignore_workdays is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_task_lifecycle.py -k "ignore_flags or preserves_scheduled or explicit_flags" -v`
Expected: FAIL — `TypeError: schedule_task() got an unexpected keyword argument 'ignore_business_hours'`

- [ ] **Step 3: Add flag params to `schedule_task`**

In `src/awkns_outreach/sequencer/lifecycle.py`, change the `schedule_task` signature (line 69) to:

```python
def schedule_task(
    db: Session, task: Task, when: datetime, end_at: Optional[datetime] = None,
    *, ignore_business_hours: bool = False, ignore_workdays: bool = False,
) -> tuple[bool, str]:
```

Inside `schedule_task`, just before the existing `task.status = "scheduled"` line, add:

```python
    task.ignore_business_hours = ignore_business_hours
    task.ignore_workdays = ignore_workdays
```

- [ ] **Step 4: Add flag params to `start_task`**

Change the `start_task` signature (line 95) to:

```python
def start_task(
    db: Session, task: Task, now: datetime,
    *, ignore_business_hours: Optional[bool] = None,
    ignore_workdays: Optional[bool] = None,
) -> tuple[bool, str]:
```

Inside `start_task`, immediately before the `campaign.status = "active"` line (currently line 139), add:

```python
    if ignore_business_hours is not None:
        task.ignore_business_hours = ignore_business_hours
    if ignore_workdays is not None:
        task.ignore_workdays = ignore_workdays
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_task_lifecycle.py -k "ignore_flags or preserves_scheduled or explicit_flags" -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the whole lifecycle suite (regression guard for start_due_tasks)**

Run: `uv run pytest tests/test_task_lifecycle.py -v`
Expected: PASS (all tests — confirms the scheduler auto-start path still works)

- [ ] **Step 7: Commit**

```bash
git add src/awkns_outreach/sequencer/lifecycle.py tests/test_task_lifecycle.py
git commit -m "feat: schedule_task/start_task persist ignore-window flags (None preserves)"
```

---

### Task 5: Web routes accept the checkboxes

**Files:**
- Modify: `src/awkns_outreach/web/routes/tasks.py:253` (schedule), `:285` (run), `:307` (lifecycle/start)
- Test: `tests/test_tasks_web.py`

**Interfaces:**
- Consumes: `schedule_task(..., ignore_business_hours=, ignore_workdays=)` and `start_task(..., ignore_business_hours=, ignore_workdays=)` (Task 4); `process_campaign(..., ignore_business_hours=, ignore_workdays=)` (Task 2).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tasks_web.py`. Follow the file's existing helpers for creating a schedulable draft task with an assigned, active sequence (mirror whatever an existing `schedule` test does to reach a schedulable state). Tests:

```python
def test_schedule_sets_ignore_flags(client, session_factory):
    # ... arrange a draft task `t` with an assigned active sequence (as existing
    # schedule tests do), then:
    r = client.post(f"/tasks/{t.id}/schedule", auth=AUTH, data={
        "scheduled_start_at": "2026-07-27T09:00",
        "end_at": "",
        "ignore_business_hours": "1",
        "ignore_workdays": "1",
    }, follow_redirects=False)
    assert r.status_code == 303
    with session_factory() as s:
        row = s.get(Task, t.id)
        assert row.ignore_business_hours is True
        assert row.ignore_workdays is True


def test_start_now_sets_ignore_flags(client, session_factory):
    # ... arrange a draft task `t` with an assigned active sequence, then:
    r = client.post(f"/tasks/{t.id}/lifecycle", auth=AUTH, data={
        "action": "start",
        "ignore_business_hours": "1",
    }, follow_redirects=False)
    assert r.status_code == 303
    with session_factory() as s:
        row = s.get(Task, t.id)
        assert row.status == "running"
        assert row.ignore_business_hours is True
        assert row.ignore_workdays is False


def test_run_does_not_persist_ignore_flags(client, session_factory):
    # ... arrange a RUNNING task `t` (assigned sequence, started), then:
    r = client.post(f"/tasks/{t.id}/run", auth=AUTH, data={
        "max_this_run": "5",
        "send": "",
        "ignore_business_hours": "1",
        "ignore_workdays": "1",
    }, follow_redirects=False)
    assert r.status_code == 303
    with session_factory() as s:
        row = s.get(Task, t.id)
        assert row.ignore_business_hours is False  # transient — not written
        assert row.ignore_workdays is False
```

Use whatever session-access fixture the file already exposes to read the row back (named `session_factory` here — substitute the file's actual fixture; several web-test files expose a sessionmaker or reuse `engine`). If none exists, open a session from the test's `engine` fixture the same way `override_get_db` does.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tasks_web.py -k "ignore_flags" -v`
Expected: FAIL — flags are ignored by the routes, so the asserted `True` values are `False` (schedule/start) 

- [ ] **Step 3: Accept the checkboxes in `schedule_task_route`**

In `src/awkns_outreach/web/routes/tasks.py`, add two params to `schedule_task_route` (after `end_at: str = Form("")`):

```python
    ignore_business_hours: bool = Form(False),
    ignore_workdays: bool = Form(False),
```

And pass them to `schedule_task` (the `lifecycle.schedule_task(db, task, when, end_at=end)` call):

```python
    _ok, msg = lifecycle.schedule_task(
        db, task, when, end_at=end,
        ignore_business_hours=ignore_business_hours,
        ignore_workdays=ignore_workdays,
    )
```

- [ ] **Step 4: Handle `start` with flags in `lifecycle_action_route`**

Replace `lifecycle_action_route` with a version that branches `start` out to pass explicit flags:

```python
@router.post("/tasks/{task_id}/lifecycle")
def lifecycle_action_route(
    task_id: str,
    action: str = Form(...),
    ignore_business_hours: bool = Form(False),
    ignore_workdays: bool = Form(False),
    db: Session = Depends(get_db),
):
    task = _get_task(db, task_id)
    now = datetime.now(timezone.utc)
    if action == "start":
        _ok, msg = lifecycle.start_task(
            db, task, now,
            ignore_business_hours=ignore_business_hours,
            ignore_workdays=ignore_workdays,
        )
        return RedirectResponse(f"/tasks?msg={msg}", status_code=303)
    handler = _LIFECYCLE_ACTIONS.get(action)
    if handler is None:
        raise HTTPException(400, f"Unknown action: {action}")
    _ok, msg = handler(db, task, now)
    return RedirectResponse(f"/tasks?msg={msg}", status_code=303)
```

Then remove the now-unused `"start"` entry from the `_LIFECYCLE_ACTIONS` dict (around line 32–37) so the map holds only pause/resume/stop.

- [ ] **Step 5: Pass transient flags in `run_task`**

Add two params to `run_task` (after `max_this_run: int = Form(5)`):

```python
    ignore_business_hours: bool = Form(False),
    ignore_workdays: bool = Form(False),
```

And forward them to `process_campaign` (do NOT write them to `task`):

```python
    s = process_campaign(
        db, task.campaign, task.steps_by_tier, dry_run=dry,
        max_this_run=max_this_run, gap_ms=0,
        ignore_business_hours=ignore_business_hours,
        ignore_workdays=ignore_workdays,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_tasks_web.py -k "ignore_flags" -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Run the full tasks-web suite**

Run: `uv run pytest tests/test_tasks_web.py -v`
Expected: PASS (all tests — confirms pause/resume/stop still route through `_LIFECYCLE_ACTIONS`)

- [ ] **Step 8: Commit**

```bash
git add src/awkns_outreach/web/routes/tasks.py tests/test_tasks_web.py
git commit -m "feat: task routes accept ignore-window checkboxes (run stays transient)"
```

---

### Task 6: Modal checkboxes in the Tasks page

**Files:**
- Modify: `src/awkns_outreach/web/templates/tasks.html` (Schedule form ~90-104, Start now buttons ~105-108 & ~113-116, Run form ~127-133)
- Test: `tests/test_tasks_web.py`

**Interfaces:**
- Consumes: the routes from Task 5.

- [ ] **Step 1: Write the failing smoke test**

Add to `tests/test_tasks_web.py` a test that a draft task's row renders a dialog with both checkboxes:

```python
def test_tasks_page_renders_ignore_window_checkboxes(client):
    # ... arrange at least one draft task with an assigned sequence ...
    r = client.get("/tasks", auth=AUTH)
    assert r.status_code == 200
    assert 'name="ignore_business_hours"' in r.text
    assert 'name="ignore_workdays"' in r.text
    assert "<dialog" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tasks_web.py::test_tasks_page_renders_ignore_window_checkboxes -v`
Expected: FAIL — no `ignore_business_hours` / `<dialog` in the markup

- [ ] **Step 3: Add a reusable checkbox snippet and wrap the three actions in dialogs**

In `src/awkns_outreach/web/templates/tasks.html`, define a Jinja macro near the top of the file (after any `{% extends %}`/`{% block %}` opening) so the two checkboxes aren't repeated verbatim:

```jinja
{% macro window_checkboxes(t) %}
  <label class="flex items-center gap-1 text-xs text-slate-600">
    <input type="checkbox" name="ignore_business_hours" value="1"
           {% if t.ignore_business_hours %}checked{% endif %}>
    Ignore business hours (09:00–17:00)
  </label>
  <label class="flex items-center gap-1 text-xs text-slate-600">
    <input type="checkbox" name="ignore_workdays" value="1"
           {% if t.ignore_workdays %}checked{% endif %}>
    Ignore workdays (Mon–Fri)
  </label>
{% endmacro %}
```

**Schedule** (replace the draft-status `<form action=".../schedule">` block, ~90-104): turn the visible button into one that opens a dialog holding the existing datetime fields plus the checkboxes:

```jinja
<button type="button" class="text-blue-700 hover:underline"
        onclick="document.getElementById('sched-{{ t.id }}').showModal()">Schedule…</button>
<dialog id="sched-{{ t.id }}" class="rounded-lg p-4 w-96">
  <form method="post" action="/tasks/{{ t.id }}/schedule" class="flex flex-col gap-2">
    <div class="font-medium text-sm">Schedule task</div>
    <label class="text-xs text-slate-500">Start (Asia/Taipei)
      <input type="datetime-local" name="scheduled_start_at" required
             class="border rounded px-1.5 py-0.5 text-xs w-full"></label>
    <label class="text-xs text-slate-500">End (optional, Asia/Taipei)
      <input type="datetime-local" name="end_at"
             class="border rounded px-1.5 py-0.5 text-xs w-full"></label>
    {{ window_checkboxes(t) }}
    <div class="flex justify-end gap-2 mt-1">
      <button type="button" class="text-xs text-slate-500"
              onclick="document.getElementById('sched-{{ t.id }}').close()">Cancel</button>
      <button type="submit" class="rounded bg-blue-700 text-white text-xs px-2 py-1">Schedule</button>
    </div>
  </form>
</dialog>
```

**Start now** appears in BOTH the `draft` and `scheduled` branches (~105-108 and ~113-116). Replace each `<form action=".../lifecycle">` Start-now block with a dialog trigger + dialog (use a branch-unique id so the two never collide):

```jinja
<button type="button" class="text-green-700 hover:underline"
        onclick="document.getElementById('start-{{ t.id }}').showModal()">Start now…</button>
<dialog id="start-{{ t.id }}" class="rounded-lg p-4 w-96">
  <form method="post" action="/tasks/{{ t.id }}/lifecycle" class="flex flex-col gap-2">
    <input type="hidden" name="action" value="start">
    <div class="font-medium text-sm">Start now</div>
    {{ window_checkboxes(t) }}
    <div class="flex justify-end gap-2 mt-1">
      <button type="button" class="text-xs text-slate-500"
              onclick="document.getElementById('start-{{ t.id }}').close()">Cancel</button>
      <button type="submit" class="rounded bg-green-700 text-white text-xs px-2 py-1">Start</button>
    </div>
  </form>
</dialog>
```

Note: the `draft` branch already contains BOTH a Schedule form and a Start-now form. `sched-{{ t.id }}` and `start-{{ t.id }}` ids are distinct, so both dialogs coexist in one row. The `scheduled` branch only needs the Start-now dialog (id `start-{{ t.id }}`) — it is a different task row so no id clash.

**Run** (replace the running-status `<form action=".../run">` block, ~127-133):

```jinja
<button type="button" class="rounded bg-slate-900 text-white text-xs px-2 py-1"
        onclick="document.getElementById('run-{{ t.id }}').showModal()">Run…</button>
<dialog id="run-{{ t.id }}" class="rounded-lg p-4 w-96">
  <form method="post" action="/tasks/{{ t.id }}/run" class="flex flex-col gap-2">
    <div class="font-medium text-sm">Run once now</div>
    <label class="text-xs text-slate-500">Max
      <input name="max_this_run" type="number" value="5"
             class="w-16 border rounded px-1 py-0.5 text-xs"></label>
    <label class="text-xs text-slate-500 flex items-center gap-1">
      <input name="send" type="checkbox" value="1"> send for real</label>
    {{ window_checkboxes(t) }}
    <div class="text-[10px] text-slate-400">These apply to this run only — they don't change the task.</div>
    <div class="flex justify-end gap-2 mt-1">
      <button type="button" class="text-xs text-slate-500"
              onclick="document.getElementById('run-{{ t.id }}').close()">Cancel</button>
      <button type="submit" class="rounded bg-slate-900 text-white text-xs px-2 py-1">Run</button>
    </div>
  </form>
</dialog>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tasks_web.py::test_tasks_page_renders_ignore_window_checkboxes -v`
Expected: PASS

- [ ] **Step 5: Run the full tasks-web suite**

Run: `uv run pytest tests/test_tasks_web.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Manual sanity check the page renders (optional but recommended)**

Run the web app locally and open `/tasks`; confirm the Schedule…, Start now…, and Run… buttons each open a dialog with both checkboxes and submit correctly. (Use the project's normal run command.)

- [ ] **Step 7: Commit**

```bash
git add src/awkns_outreach/web/templates/tasks.html tests/test_tasks_web.py
git commit -m "feat: Schedule/Start now/Run modals with ignore-window checkboxes"
```

---

### Task 7: Full-suite regression + verification

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest -q`
Expected: PASS (no regressions across the repo)

- [ ] **Step 2: Grep for stragglers referencing the old names**

Run: `grep -rn "in_business_hours\|ignore_hours" src/ tests/`
Expected: no matches (all call sites migrated to `in_send_window` / the two-flag params). If any remain, fix and re-run Step 1.

- [ ] **Step 3: Commit any fixups (if Step 2 found stragglers)**

```bash
git add -A && git commit -m "chore: migrate remaining ignore_hours references"
```

---

## Self-Review

**Spec coverage:**
- Two independent dimensions → Task 2 (`in_send_window`), columns in Task 1. ✓
- Persist on Schedule & Start now → Task 4 + Task 5. ✓
- Run transient → Task 5 Step 5 + `test_run_does_not_persist_ignore_flags`. ✓
- Scheduler auto-start preserves schedule-time flags → `start_task` `None` semantics, Task 4 + `test_start_task_none_preserves_scheduled_flags`. ✓
- Daily cap untouched → no cap code changed; called out in Global Constraints. ✓
- Engine/limits/cli/runner threading → Tasks 2 & 3. ✓
- Alembic migration → Task 1 Step 5. ✓
- Modal with two checkboxes on all three actions → Task 6. ✓
- htmx/Tailwind, native `<dialog>` → Task 6. ✓

**Placeholder scan:** The web-test arrange steps in Task 5/6 reference "as existing schedule tests do" and a `session_factory` fixture name — these are deliberate pointers to the file's own established fixtures (the exact fixture name varies and must be read from the file), not skipped logic. All code steps show real code.

**Type consistency:** `in_send_window(now, country, *, ignore_hours, ignore_days)` is used identically in limits (def), engine (call), and the Task 2 test. `process_campaign(..., ignore_business_hours, ignore_workdays)` matches across engine def, cli, runner, run_task, and tests. `schedule_task(..., ignore_business_hours=False, ignore_workdays=False)` and `start_task(..., ignore_business_hours=None, ignore_workdays=None)` match between lifecycle defs and route call sites. Column names `ignore_business_hours` / `ignore_workdays` are consistent from model → migration → runner → lifecycle → routes → template.
