# Template Editor Two-Column Redesign + List Clone/Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the standalone email template editor into a two-column layout (edit left, live preview right) with a lightweight 5-button toolbar and pre-save test-send, and extend the template list page with a Content column, Clone, and Archive/Unarchive.

**Architecture:** FastAPI + Jinja2 + HTMX server-rendered app (`src/awkns_outreach/web/`), no JS bundler, no SPA framework. New behavior is added as: (1) two new HTMX-fragment routes that render partial templates independent of any saved template id, so the editor works pre-save, and (2) a `status` column + whitelisted transition table on `EmailTemplate`, mirroring the existing `Campaign.status` archive pattern in `admin.py`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0 (Mapped/mapped_column), Alembic, Jinja2Templates, HTMX 2.0.3 (already loaded in `base.html`), vanilla JS (no new dependencies), pytest + respx for tests, Postgres 16 (local via `docker-compose`, already running) for the actual migration.

## Global Constraints

- Body field stays a plain-text `<textarea>`. No HTML body storage, no change to `mailer.py`'s plain-text → HTML send pipeline. (Spec §Goal, out of scope list.)
- No real image/attachment upload or storage — toolbar buttons insert placeholder text tokens only. (Spec §1.3.)
- No Folder/Tags/Owner fields. (Spec §Goal.)
- Confirmation copy for test-send must read exactly: `Test email sent! Check your inbox at {recipient}.` on success, `Test send failed: {error}` on failure. (Spec §1.4, user's original request.)
- Archive/Clone/list changes mirror the existing `Campaign.status` pattern in `src/awkns_outreach/web/routes/admin.py` (`_STATUS_TRANSITIONS`, `_archived_edit_guard`) — same shape, not reinvented. (Spec §2.)
- All new/modified routes stay inside the existing `router = APIRouter(dependencies=[Depends(require_admin)])` in `templates_lib.py` — no auth changes.

---

## File Structure

- Modify: `src/awkns_outreach/db/models.py` — add `EmailTemplate.status`.
- Create: `src/awkns_outreach/db/migrations/versions/0005_email_template_status.py` — new column, Postgres migration.
- Modify: `src/awkns_outreach/web/routes/templates_lib.py` — new fragment/clone/status routes, simplified `update_template`, list filtering, shared render/send helpers.
- Modify: `src/awkns_outreach/web/templates/template_edit.html` — two-column layout, toolbar, HTMX wiring.
- Create: `src/awkns_outreach/web/templates/_template_preview_fragment.html` — Subject/Body preview partial (included on initial page load, returned by the preview-fragment endpoint).
- Create: `src/awkns_outreach/web/templates/_template_test_send_fragment.html` — mailbox picker + Send Test Email button + status line partial (included on initial page load, returned by the test-send-fragment endpoint).
- Modify: `src/awkns_outreach/web/templates/template_list.html` — Content column, Clone/Archive/Unarchive actions, status filter links.
- Modify: `tests/test_templates_web.py` — move preview/test-send tests to the new id-less endpoints, add coverage for the toolbar-adjacent routes (clone, archive/unarchive, archived-edit guard, mailboxes-on-new).

---

## Task 1: `EmailTemplate.status` column + migration

**Files:**
- Modify: `src/awkns_outreach/db/models.py:241-258` (the `EmailTemplate` class)
- Create: `src/awkns_outreach/db/migrations/versions/0005_email_template_status.py`
- Test: `tests/test_templates_web.py` (new test, appended)

**Interfaces:**
- Produces: `EmailTemplate.status: str`, default `"active"`, values `"active" | "archived"`. All later tasks (list filter, archive/unarchive, clone) read/write this attribute.

- [ ] **Step 1: Add the column to the model**

In `src/awkns_outreach/db/models.py`, the `EmailTemplate` class currently reads (lines 241-258):

```python
class EmailTemplate(Base):
    """A standalone, reusable email (name/subject/body) — Apollo's "New
    Template" concept. Not tied to any campaign; sequence steps can copy one
    in via the editor's "insert template" dropdown."""

    __tablename__ = "email_template"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

Add a `status` column right after `body`:

```python
class EmailTemplate(Base):
    """A standalone, reusable email (name/subject/body) — Apollo's "New
    Template" concept. Not tied to any campaign; sequence steps can copy one
    in via the editor's "insert template" dropdown."""

    __tablename__ = "email_template"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="active")  # active | archived

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: Write the migration**

Create `src/awkns_outreach/db/migrations/versions/0005_email_template_status.py`:

```python
"""add email_template.status

Revision ID: 0005_email_template_status
Revises: 0004_mailboxes_templates
Create Date: 2026-07-10

Template list gains archive/unarchive, mirroring campaign.status: a new
status column defaulting existing rows to "active" so nothing already
saved disappears from the default list view.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_email_template_status"
down_revision: Union[str, None] = "0004_mailboxes_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_template",
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
    )


def downgrade() -> None:
    op.drop_column("email_template", "status")
```

- [ ] **Step 3: Run the migration against the local Postgres**

Postgres is already running locally (`docker-compose up -d`, container `awkns-outreach-db`).

Run: `~/.local/bin/uv run alembic upgrade head`
Expected: last line `INFO  [alembic.runtime.migration] Running upgrade 0004_mailboxes_templates -> 0005_email_template_status, add email_template.status`, exit code 0.

Verify: `~/.local/bin/uv run alembic current` → prints `0005_email_template_status (head)`.

- [ ] **Step 4: Write a failing test for the default status**

Append to `tests/test_templates_web.py`:

```python
def test_new_template_defaults_to_active_status(client, session):
    r = client.post("/templates", auth=AUTH, follow_redirects=False, data={
        "name": "Intro", "subject": "s", "body": "b",
    })
    assert r.status_code == 303
    t = session.query(EmailTemplate).one()
    assert t.status == "active"
```

- [ ] **Step 5: Run it to confirm it already passes (model default is enough for SQLite-backed tests)**

Run: `~/.local/bin/uv run pytest tests/test_templates_web.py::test_new_template_defaults_to_active_status -v`
Expected: PASS (the test fixtures use `Base.metadata.create_all`, which already picks up the new column from the model — no dependency on the Postgres migration for tests).

- [ ] **Step 6: Commit**

```bash
git add src/awkns_outreach/db/models.py src/awkns_outreach/db/migrations/versions/0005_email_template_status.py tests/test_templates_web.py
git commit -m "feat: add EmailTemplate.status column and migration"
```

---

## Task 2: Extract shared preview/test-send helpers in `templates_lib.py`

Refactors the existing `update_template` preview/test_send branches into standalone helpers, with no route changes yet — sets up Task 3 and Task 4 to reuse them without duplicating the throwaway-Campaign/Lead construction.

**Files:**
- Modify: `src/awkns_outreach/web/routes/templates_lib.py`
- Test: `tests/test_templates_web.py`

**Interfaces:**
- Consumes: `render_template_preview(subject_tpl, body_tpl, email, identity=None) -> RenderedEmail` (from `awkns_outreach.send.mailer`, unchanged), `send_outreach_email(lead, campaign, email, step_index, dry_run) -> SendResult` (unchanged), `_connected_mailboxes(db) -> list[Mailbox]` (existing helper, unchanged), `_PREVIEW_EMAIL` (existing constant, unchanged).
- Produces: `_render_preview(subject: str, body: str) -> RenderedEmail`, `_send_test_email(db: Session, subject: str, body: str, mailbox_id: str) -> str` (the confirmation/error message string). Task 3 (preview-fragment route) uses `_render_preview`; Task 4 (test-send-fragment route) uses `_send_test_email`.

- [ ] **Step 1: Read the current file to confirm exact line ranges**

Run: `grep -n "^def \|^_PREVIEW_EMAIL\|^router" src/awkns_outreach/web/routes/templates_lib.py`
Expected: shows `_get_template`, `_connected_mailboxes`, `new_template_form`, `create_template`, `edit_template_form`, `update_template` in that order, matching the version read earlier in this conversation.

- [ ] **Step 2: Add `_render_preview` and `_send_test_email` helpers**

In `src/awkns_outreach/web/routes/templates_lib.py`, directly below `_connected_mailboxes` (currently lines 38-41), add:

```python
def _render_preview(subject: str, body: str):
    return render_template_preview(subject, body, _PREVIEW_EMAIL)


def _send_test_email(db: Session, subject: str, body: str, mailbox_id: str) -> str:
    """Build a throwaway Campaign/Lead (never persisted) and send through
    send_outreach_email's normal Gmail/Resend dispatch — same path a real
    sequence step would take. Returns the confirmation or failure message
    shown in the test-send status line."""
    mailbox = db.get(Mailbox, mailbox_id) if mailbox_id else None
    recipient = mailbox.email if mailbox else settings.outreach_from
    test_campaign = Campaign(
        id="preview", name="Template test send", target_titles=[], seed_companies=[],
        sequence=[{"key": "test", "delay_days": 0, "subject": subject, "body": body}],
        sender_identity={},
    )
    test_campaign.mailbox = mailbox
    test_lead = Lead(
        campaign_id="preview", email=recipient, company="Acme Studios",
        contact_name="Jamie Rivera", contact_title="Creative Director", country="US",
        angle="Your recent campaign work would translate beautifully into short-form video.",
        status="active", step=0,
    )
    res = send_outreach_email(test_lead, test_campaign, recipient, 0, dry_run=False)
    if res.ok:
        return f"Test email sent! Check your inbox at {recipient}."
    return f"Test send failed: {res.error}"
```

- [ ] **Step 3: Simplify `update_template` to only handle `save`/`delete`**

Replace the whole `update_template` function (currently lines 83-149) with:

```python
@router.post("/templates/{template_id}/edit", response_class=HTMLResponse)
def update_template(
    template_id: str,
    action: str = Form("save"),
    name: str = Form(""),
    subject: str = Form(""),
    body: str = Form(""),
    db: Session = Depends(get_db),
):
    t = _get_template(db, template_id)
    blocked = _archived_edit_guard(t)
    if blocked:
        return blocked

    if action == "delete":
        db.delete(t)
        db.commit()
        return RedirectResponse("/templates?msg=Template deleted.", status_code=303)

    if action != "save":
        raise HTTPException(400, f"Unknown action: {action}")

    t.name = name.strip()
    t.subject = subject.strip()
    t.body = body.rstrip()
    db.commit()
    return RedirectResponse(f"/templates/{t.id}/edit?msg=Template saved.", status_code=303)
```

Note: `_archived_edit_guard` doesn't exist yet — it's added in Task 5. For this step, temporarily stub it inline so the file imports cleanly:

```python
def _archived_edit_guard(t: EmailTemplate):
    return None
```

Place this stub directly above `update_template`. Task 5 replaces the stub body with the real check — do not duplicate the function.

- [ ] **Step 4: Update the now-obsolete preview/test-send tests to expect 400 on old actions**

In `tests/test_templates_web.py`, the existing `test_preview_renders_example_contact`,
`test_test_send_posts_to_resend_when_no_mailbox_selected`, and
`test_test_send_uses_gmail_mailbox_recipient` currently POST `action=preview`/`action=test_send`
to `/templates/{id}/edit`. Delete these three tests now — Task 3 and Task 4 replace them with
equivalent coverage against the new id-less fragment endpoints (deleting here avoids a
red-then-rewritten intermediate state; the behavior isn't lost, just relocated).

- [ ] **Step 5: Run the remaining tests to confirm nothing else broke**

Run: `~/.local/bin/uv run pytest tests/test_templates_web.py -v`
Expected: `test_create_edit_delete_template` and `test_new_template_defaults_to_active_status` PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add src/awkns_outreach/web/routes/templates_lib.py tests/test_templates_web.py
git commit -m "refactor: extract preview/test-send helpers, drop preview/test_send actions from edit route"
```

---

## Task 3: Preview fragment endpoint + partial template

**Files:**
- Modify: `src/awkns_outreach/web/routes/templates_lib.py`
- Create: `src/awkns_outreach/web/templates/_template_preview_fragment.html`
- Test: `tests/test_templates_web.py`

**Interfaces:**
- Consumes: `_render_preview(subject, body) -> RenderedEmail` (Task 2).
- Produces: `POST /templates/preview-fragment` (form: `subject`, `body`) → renders `_template_preview_fragment.html` with context `{"preview": RenderedEmail}`. Task 6 wires the editor page's Subject/Body inputs to this route via HTMX.

- [ ] **Step 1: Write the partial template**

Create `src/awkns_outreach/web/templates/_template_preview_fragment.html`:

```html
<div class="text-xs text-slate-500 mb-2">Subject</div>
<div class="text-sm font-medium mb-4">{{ preview.subject }}</div>
<div class="text-xs text-slate-500 mb-2">Body (rendered HTML)</div>
<div class="border rounded p-3">{{ preview.html|safe }}</div>
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_templates_web.py`:

```python
def test_preview_fragment_renders_example_contact_without_saved_template(client, session):
    r = client.post("/templates/preview-fragment", auth=AUTH, data={
        "subject": "hi {company}", "body": "Hi {first_name}, {angle}",
    })
    assert r.status_code == 200
    assert "hi Acme Studios" in r.text
    assert "Hi Jamie," in r.text
    assert session.query(EmailTemplate).count() == 0  # no template was created/required
```

- [ ] **Step 3: Run it to verify it fails**

Run: `~/.local/bin/uv run pytest tests/test_templates_web.py::test_preview_fragment_renders_example_contact_without_saved_template -v`
Expected: FAIL with a 404 (route doesn't exist yet).

- [ ] **Step 4: Add the route**

In `src/awkns_outreach/web/routes/templates_lib.py`, add directly below `_send_test_email` (from Task 2):

```python
@router.post("/templates/preview-fragment", response_class=HTMLResponse)
def preview_fragment(
    request: Request, subject: str = Form(""), body: str = Form(""),
):
    return templates.TemplateResponse(
        request, "_template_preview_fragment.html", {"preview": _render_preview(subject, body)},
    )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `~/.local/bin/uv run pytest tests/test_templates_web.py::test_preview_fragment_renders_example_contact_without_saved_template -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/awkns_outreach/web/routes/templates_lib.py src/awkns_outreach/web/templates/_template_preview_fragment.html tests/test_templates_web.py
git commit -m "feat: add id-less template preview fragment endpoint"
```

---

## Task 4: Test-send fragment endpoint + partial template

**Files:**
- Modify: `src/awkns_outreach/web/routes/templates_lib.py`
- Create: `src/awkns_outreach/web/templates/_template_test_send_fragment.html`
- Test: `tests/test_templates_web.py`

**Interfaces:**
- Consumes: `_send_test_email(db, subject, body, mailbox_id) -> str` (Task 2), `_connected_mailboxes(db) -> list[Mailbox]` (existing).
- Produces: `POST /templates/test-send-fragment` (form: `subject`, `body`, `mailbox_id`) → renders `_template_test_send_fragment.html` with context `{"mailboxes": list[Mailbox], "msg": Optional[str], "selected_mailbox_id": Optional[str]}`. Task 6 wires the editor page's "Send Test Email to Me" button to this route via HTMX.

- [ ] **Step 1: Write the partial template**

Create `src/awkns_outreach/web/templates/_template_test_send_fragment.html`:

```html
<div id="test-send-widget" class="flex flex-col gap-2">
  <div class="flex items-center gap-2">
    <select name="mailbox_id" id="mailbox-field" class="border rounded px-2 py-1.5 text-sm">
      <option value="">Resend (default)</option>
      {% for mb in mailboxes %}
      <option value="{{ mb.id }}" {% if selected_mailbox_id and mb.id == selected_mailbox_id %}selected{% endif %}>{{ mb.email }}</option>
      {% endfor %}
    </select>
    <button type="button"
            hx-post="/templates/test-send-fragment"
            hx-include="#subject-field,#body-field,#mailbox-field"
            hx-target="#test-send-widget" hx-swap="outerHTML"
            class="rounded bg-yellow-400 hover:bg-yellow-300 text-slate-900 text-sm font-medium px-4 py-2">
      Send Test Email to Me
    </button>
  </div>
  {% if msg %}<div class="text-xs text-slate-600">{{ msg }}</div>{% endif %}
</div>
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_templates_web.py`:

```python
@respx.mock
def test_test_send_fragment_posts_to_resend_when_no_mailbox_selected(client, session):
    route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, json={"id": "resend-1"})
    )
    r = client.post("/templates/test-send-fragment", auth=AUTH, data={
        "subject": "s", "body": "b", "mailbox_id": "",
    })
    assert r.status_code == 200
    assert route.called
    sent_body = route.calls.last.request.content.decode()
    assert settings.outreach_from in sent_body
    assert f"Test email sent! Check your inbox at {settings.outreach_from}." in r.text


@respx.mock
def test_test_send_fragment_uses_gmail_mailbox_recipient(client, session):
    mb = Mailbox(
        email="steven@gmail.com", access_token="at", refresh_token="rt",
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1), status="connected",
    )
    session.add(mb)
    session.commit()

    gmail_route = respx.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send").mock(
        return_value=httpx.Response(200, json={"id": "g1", "threadId": "t1"})
    )
    r = client.post("/templates/test-send-fragment", auth=AUTH, data={
        "subject": "s", "body": "b", "mailbox_id": mb.id,
    })
    assert r.status_code == 200
    assert gmail_route.called
    assert f"Test email sent! Check your inbox at {mb.email}." in r.text
    assert f'value="{mb.id}" selected' in r.text  # selection preserved after swap
```

- [ ] **Step 3: Run them to verify they fail**

Run: `~/.local/bin/uv run pytest tests/test_templates_web.py::test_test_send_fragment_posts_to_resend_when_no_mailbox_selected tests/test_templates_web.py::test_test_send_fragment_uses_gmail_mailbox_recipient -v`
Expected: FAIL with 404s (route doesn't exist yet).

- [ ] **Step 4: Add the route**

In `src/awkns_outreach/web/routes/templates_lib.py`, add directly below `preview_fragment` (Task 3):

```python
@router.post("/templates/test-send-fragment", response_class=HTMLResponse)
def test_send_fragment(
    request: Request, subject: str = Form(""), body: str = Form(""),
    mailbox_id: str = Form(""), db: Session = Depends(get_db),
):
    msg = _send_test_email(db, subject, body, mailbox_id)
    return templates.TemplateResponse(
        request, "_template_test_send_fragment.html",
        {
            "mailboxes": _connected_mailboxes(db),
            "msg": msg,
            "selected_mailbox_id": mailbox_id or None,
        },
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/.local/bin/uv run pytest tests/test_templates_web.py::test_test_send_fragment_posts_to_resend_when_no_mailbox_selected tests/test_templates_web.py::test_test_send_fragment_uses_gmail_mailbox_recipient -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add src/awkns_outreach/web/routes/templates_lib.py src/awkns_outreach/web/templates/_template_test_send_fragment.html tests/test_templates_web.py
git commit -m "feat: add id-less template test-send fragment endpoint"
```

---

## Task 5: Archived-edit guard, GET routes wire in preview/test-send context, mailboxes on New

**Files:**
- Modify: `src/awkns_outreach/web/routes/templates_lib.py`
- Test: `tests/test_templates_web.py`

**Interfaces:**
- Consumes: `_render_preview`, `_send_test_email`, `_connected_mailboxes` (Task 2/4).
- Produces: real `_archived_edit_guard(t: EmailTemplate) -> Optional[RedirectResponse]` (replaces the Task 2 stub). `new_template_form` and `edit_template_form` now pass `preview` and `mailboxes`/`selected_mailbox_id`/`msg` context so the two-column page (Task 6) can `{% include %}` the same partials used by the fragment endpoints on first load, with no separate "empty state" template.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_templates_web.py`:

```python
def test_new_template_page_lists_connected_mailboxes(client, session):
    mb = Mailbox(
        email="steven@gmail.com", access_token="at", refresh_token="rt",
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1), status="connected",
    )
    session.add(mb)
    session.commit()

    r = client.get("/templates/new", auth=AUTH)
    assert r.status_code == 200
    assert "steven@gmail.com" in r.text


def test_edit_archived_template_blocked_get_and_post(client, session):
    t = EmailTemplate(name="Frozen", subject="s", body="b", status="archived")
    session.add(t)
    session.commit()

    get_r = client.get(f"/templates/{t.id}/edit", auth=AUTH, follow_redirects=False)
    assert get_r.status_code == 303
    assert get_r.headers["location"].startswith("/templates?msg=")

    post_r = client.post(f"/templates/{t.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "Should not save", "subject": "x", "body": "x",
    })
    assert post_r.status_code == 303
    assert post_r.headers["location"].startswith("/templates?msg=")
    session.refresh(t)
    assert t.name == "Frozen"  # unchanged
```

- [ ] **Step 2: Run them to verify they fail**

Run: `~/.local/bin/uv run pytest tests/test_templates_web.py::test_new_template_page_lists_connected_mailboxes tests/test_templates_web.py::test_edit_archived_template_blocked_get_and_post -v`
Expected: `test_new_template_page_lists_connected_mailboxes` FAILs (mailboxes not passed yet, so `steven@gmail.com` isn't in the response); `test_edit_archived_template_blocked_get_and_post` FAILs (the Task 2 stub always returns `None`, so both requests get 200s, not 303s).

- [ ] **Step 3: Replace the Task 2 stub with the real guard, and update the GET routes**

In `src/awkns_outreach/web/routes/templates_lib.py`, replace:

```python
def _archived_edit_guard(t: EmailTemplate):
    return None
```

with:

```python
def _archived_edit_guard(t: EmailTemplate) -> Optional[RedirectResponse]:
    """Server-side backup for the disabled Edit link: archived templates
    can't be edited (both GET and POST) until unarchived."""
    if t.status == "archived":
        return RedirectResponse(
            "/templates?msg=Archived templates can't be edited — unarchive first.",
            status_code=303,
        )
    return None
```

Replace `new_template_form` (currently):

```python
@router.get("/templates/new", response_class=HTMLResponse)
def new_template_form(request: Request):
    return templates.TemplateResponse(
        request, "template_edit.html",
        {"t": None, "preview": None, "placeholders": SEQUENCE_PLACEHOLDERS, "mailboxes": [], "msg": None},
    )
```

with:

```python
@router.get("/templates/new", response_class=HTMLResponse)
def new_template_form(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "template_edit.html",
        {
            "t": None, "placeholders": SEQUENCE_PLACEHOLDERS, "msg": None,
            "preview": _render_preview("", ""),
            "mailboxes": _connected_mailboxes(db),
            "selected_mailbox_id": None,
            "test_send_msg": None,
        },
    )
```

Replace `edit_template_form` (currently):

```python
@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def edit_template_form(
    template_id: str, request: Request, db: Session = Depends(get_db), msg: Optional[str] = None,
):
    t = _get_template(db, template_id)
    return templates.TemplateResponse(
        request, "template_edit.html",
        {
            "t": t, "preview": None, "placeholders": SEQUENCE_PLACEHOLDERS,
            "mailboxes": _connected_mailboxes(db), "msg": msg,
        },
    )
```

with:

```python
@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def edit_template_form(
    template_id: str, request: Request, db: Session = Depends(get_db), msg: Optional[str] = None,
):
    t = _get_template(db, template_id)
    blocked = _archived_edit_guard(t)
    if blocked:
        return blocked
    return templates.TemplateResponse(
        request, "template_edit.html",
        {
            "t": t, "placeholders": SEQUENCE_PLACEHOLDERS, "msg": msg,
            "preview": _render_preview(t.subject, t.body),
            "mailboxes": _connected_mailboxes(db),
            "selected_mailbox_id": None,
            "test_send_msg": None,
        },
    )
```

Also add the same guard to the top of `update_template` (Task 2 already left a `blocked = _archived_edit_guard(t)` call in place — no further change needed there since it now calls the real function).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/.local/bin/uv run pytest tests/test_templates_web.py::test_new_template_page_lists_connected_mailboxes tests/test_templates_web.py::test_edit_archived_template_blocked_get_and_post -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full template test file**

Run: `~/.local/bin/uv run pytest tests/test_templates_web.py -v`
Expected: all tests PASS (this will fail at this point because `template_edit.html` still references the old `{% if t %}`-gated preview block and the old inline mailbox select/Preview/test-send buttons, which now receive `preview`/`mailboxes` context of a different shape — Jinja won't error on unused/extra context keys, so this should still render 200s; if any test fails here, read the failure and confirm it's not a route-layer regression before moving to Task 6, which replaces the template.)

- [ ] **Step 6: Commit**

```bash
git add src/awkns_outreach/web/routes/templates_lib.py tests/test_templates_web.py
git commit -m "feat: enforce archived-template edit guard, pass mailboxes to new-template page"
```

---

## Task 6: Two-column `template_edit.html` — layout, toolbar, HTMX wiring

**Files:**
- Modify: `src/awkns_outreach/web/templates/template_edit.html`
- Test: `tests/test_templates_web.py`

**Interfaces:**
- Consumes: `t` (Optional[EmailTemplate]), `placeholders` (list[str]), `msg` (Optional[str]), `preview` (RenderedEmail, from Task 5's GET routes), `mailboxes` (list[Mailbox]), `selected_mailbox_id` (Optional[str]), all supplied by `new_template_form`/`edit_template_form` (Task 5). Includes `_template_preview_fragment.html` (Task 3) and `_template_test_send_fragment.html` (Task 4) for the initial render, then relies on `/templates/preview-fragment` and `/templates/test-send-fragment` for live updates.
- Produces: the rendered page HTML that Task 7's tests assert against (grid layout markers, toolbar button presence, form field ids `subject-field`/`body-field`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_templates_web.py`:

```python
def test_new_template_page_is_two_column_with_preview_and_toolbar(client, session):
    r = client.get("/templates/new", auth=AUTH)
    assert r.status_code == 200
    assert "Template Preview" in r.text
    assert 'id="preview-pane"' in r.text
    assert 'id="body-field"' in r.text
    # 5-button toolbar: T, link, image, attachment, code-view
    assert "twToggleFormatHelp" in r.text
    assert "twOpenLinkPopover" in r.text
    assert 'accept="image/*"' in r.text
    assert "twToggleCodeView" in r.text
    # test-send widget present even though nothing is saved yet
    assert "Send Test Email to Me" in r.text


def test_edit_template_page_prefills_preview_from_saved_body(client, session):
    t = EmailTemplate(name="Intro", subject="hi {company}", body="Hi {first_name}, {angle}")
    session.add(t)
    session.commit()

    r = client.get(f"/templates/{t.id}/edit", auth=AUTH)
    assert r.status_code == 200
    assert "hi Acme Studios" in r.text  # right column pre-rendered from saved body, no extra request
    assert "Hi Jamie," in r.text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `~/.local/bin/uv run pytest tests/test_templates_web.py::test_new_template_page_is_two_column_with_preview_and_toolbar tests/test_templates_web.py::test_edit_template_page_prefills_preview_from_saved_body -v`
Expected: FAIL — `test_new_template_page_is_two_column_with_preview_and_toolbar` fails on the toolbar/preview-pane assertions (current template has neither); `test_edit_template_page_prefills_preview_from_saved_body` fails because the current template only shows the preview block when `preview` was set by a `POST action=preview` (removed in Task 2), not on plain GET.

- [ ] **Step 3: Replace `template_edit.html`**

Replace the entire contents of `src/awkns_outreach/web/templates/template_edit.html` with:

```html
{% extends "base.html" %}
{% block title %}{% if t %}Edit {{ t.name }}{% else %}New template{% endif %} — Awkns Outreach{% endblock %}
{% block content %}
<a href="/templates" class="text-xs text-slate-500 hover:underline">&larr; Templates</a>
<h1 class="text-lg font-semibold mb-4">{% if t %}Edit template{% else %}New template{% endif %}</h1>

<div class="mb-4 rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
  Placeholders (filled per lead):
  {% for p in placeholders %}<code class="mx-0.5 rounded bg-white border px-1">{{ '{' ~ p ~ '}' }}</code>{% endfor %}
  · The identity footer + unsubscribe line are added automatically.
</div>

<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
  <div>
    <h2 class="text-sm font-semibold mb-2">Email Template</h2>
    <form method="post" action="{% if t %}/templates/{{ t.id }}/edit{% else %}/templates{% endif %}" class="space-y-4">
      <div>
        <label class="block text-sm font-medium mb-1">Name</label>
        <input name="name" required value="{{ t.name if t else '' }}" class="w-full border rounded px-2 py-1.5">
      </div>
      <div>
        <label class="block text-sm font-medium mb-1">Subject</label>
        <input name="subject" id="subject-field" value="{{ t.subject if t else '' }}" class="w-full border rounded px-2 py-1.5"
               placeholder="quick idea for {company}"
               hx-post="/templates/preview-fragment" hx-trigger="input changed delay:500ms"
               hx-target="#preview-pane" hx-include="#subject-field,#body-field">
      </div>
      <div>
        <label class="block text-sm font-medium mb-1">Body</label>
        <div class="border rounded overflow-hidden">
          <textarea name="body" id="body-field" rows="10" class="w-full px-2 py-1.5 text-sm block focus:outline-none"
                    placeholder="Hi {first_name},&#10;&#10;{angle}&#10;&#10;{sender_name}"
                    hx-post="/templates/preview-fragment" hx-trigger="input changed delay:500ms"
                    hx-target="#preview-pane" hx-include="#subject-field,#body-field">{{ t.body if t else '' }}</textarea>
          <div class="flex items-center gap-1 border-t px-2 py-1.5 relative bg-slate-50">
            <button type="button" onclick="twToggleFormatHelp()" title="Formatting help"
                    class="w-8 h-8 rounded hover:bg-slate-200 font-serif text-sm">T</button>
            <button type="button" onclick="twOpenLinkPopover()" title="Insert link"
                    class="w-8 h-8 rounded hover:bg-slate-200">&#128279;</button>
            <label class="w-8 h-8 rounded hover:bg-slate-200 flex items-center justify-center cursor-pointer" title="Insert image">
              &#128247;<input type="file" accept="image/*" class="hidden" onchange="twFilePicked(this, 'image')">
            </label>
            <label class="w-8 h-8 rounded hover:bg-slate-200 flex items-center justify-center cursor-pointer" title="Attach file">
              &#128206;<input type="file" class="hidden" onchange="twFilePicked(this, 'attachment')">
            </label>
            <button type="button" onclick="twToggleCodeView()" title="Toggle monospace view"
                    class="w-8 h-8 rounded hover:bg-slate-200 text-xs font-mono">&lt;&gt;</button>

            <div id="format-help" class="hidden absolute left-0 top-full mt-1 z-10 w-72 rounded border bg-white shadow p-2 text-xs text-slate-600">
              Plain text only — a blank line starts a new paragraph; raw https:// links auto-linkify.
            </div>

            <div id="link-popover" class="hidden absolute left-8 top-full mt-1 z-10 w-64 rounded border bg-white shadow p-2 space-y-1">
              <input id="link-url" placeholder="Paste or type link here" class="w-full border rounded px-2 py-1 text-xs">
              <input id="link-label" placeholder="Display text (optional)" class="w-full border rounded px-2 py-1 text-xs">
              <div class="flex justify-end gap-1">
                <button type="button" onclick="twCloseLinkPopover()" class="text-xs px-2 py-1">Cancel</button>
                <button type="button" onclick="twInsertLink()" class="text-xs px-2 py-1 rounded bg-slate-900 text-white">Insert</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      {% if t %}
      <div class="flex flex-wrap items-center gap-2">
        <button type="submit" name="action" value="save" class="rounded bg-slate-900 text-white text-sm px-4 py-2">Save</button>
        <button type="submit" name="action" value="delete"
                onclick="if(!confirm('Delete this template? This can\'t be undone.')){event.preventDefault();}"
                class="rounded border border-red-300 text-red-700 text-sm px-4 py-2 ml-auto">Delete</button>
      </div>
      {% else %}
      <div class="flex gap-2">
        <button class="rounded bg-slate-900 text-white text-sm px-4 py-2">Create</button>
        <a href="/templates" class="rounded border text-sm px-4 py-2">Cancel</a>
      </div>
      {% endif %}
    </form>
  </div>

  <div>
    <h2 class="text-sm font-semibold mb-2">Template Preview</h2>
    <div class="rounded border bg-white p-4">
      <div id="preview-pane">
        {% include "_template_preview_fragment.html" %}
      </div>
      <div class="mt-4 pt-4 border-t">
        {% include "_template_test_send_fragment.html" %}
      </div>
    </div>
  </div>
</div>

<script>
function twInsertAtCursor(el, text) {
  const start = el.selectionStart, end = el.selectionEnd;
  el.value = el.value.slice(0, start) + text + el.value.slice(end);
  const pos = start + text.length;
  el.selectionStart = el.selectionEnd = pos;
  el.focus();
  el.dispatchEvent(new Event('input', { bubbles: true }));
}
function twToggleFormatHelp() {
  document.getElementById('format-help').classList.toggle('hidden');
}
function twOpenLinkPopover() {
  document.getElementById('link-popover').classList.remove('hidden');
}
function twCloseLinkPopover() {
  document.getElementById('link-popover').classList.add('hidden');
}
function twInsertLink() {
  const url = document.getElementById('link-url').value.trim();
  const label = document.getElementById('link-label').value.trim();
  if (!url) return;
  const text = label ? (label + ' ' + url) : url;
  twInsertAtCursor(document.getElementById('body-field'), text);
  document.getElementById('link-url').value = '';
  document.getElementById('link-label').value = '';
  twCloseLinkPopover();
}
function twFilePicked(input, kind) {
  if (!input.files || !input.files.length) return;
  const name = input.files[0].name;
  twInsertAtCursor(document.getElementById('body-field'), '[' + kind + ': ' + name + ']');
  input.value = '';
}
function twToggleCodeView() {
  document.getElementById('body-field').classList.toggle('font-mono');
}
</script>
{% endblock %}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/.local/bin/uv run pytest tests/test_templates_web.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/web/templates/template_edit.html tests/test_templates_web.py
git commit -m "feat: two-column template editor with live preview and lightweight toolbar"
```

- [ ] **Step 6: Manually verify in the browser**

Run: `~/.local/bin/uv run uvicorn awkns_outreach.web.app:app --reload` (check `README.md` if the actual entrypoint module differs), then in a browser:
1. Go to `http://localhost:8000/templates/new`, log in with the admin password from `.env`.
2. Confirm two columns: left "Email Template" form, right "Template Preview".
3. Type in Subject/Body, wait ~500ms, confirm the right column updates without a page reload.
4. Click each of the 5 toolbar buttons (T, link, image, attachment, `<>`) and confirm each does something visible (tooltip, popover, file picker, mono-font toggle).
5. Click "Send Test Email to Me" and confirm the status line appears under the button without a page reload.
6. Repeat on an existing template's edit page (`/templates/{id}/edit`) and confirm the right column is pre-filled with that template's rendered preview on load.

---

## Task 7: List page — Content column, Clone, Archive/Unarchive

**Files:**
- Modify: `src/awkns_outreach/web/routes/templates_lib.py`
- Modify: `src/awkns_outreach/web/templates/template_list.html`
- Test: `tests/test_templates_web.py`

**Interfaces:**
- Consumes: `EmailTemplate.status` (Task 1), `_get_template` (existing).
- Produces: `POST /templates/{id}/clone`, `POST /templates/{id}/status` (form: `action`, `status`), `_truncate_body(body: str, length: int = 70) -> str`. Nothing downstream depends on these — this is the last task.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_templates_web.py`:

```python
def test_list_shows_truncated_content_column(client, session):
    long_body = "word " * 30  # well over 70 chars once collapsed
    t = EmailTemplate(name="Long one", subject="s", body=long_body.strip())
    session.add(t)
    session.commit()

    r = client.get("/templates", auth=AUTH)
    assert r.status_code == 200
    assert "…" in r.text
    assert long_body.strip() in r.text  # full text still present, e.g. in a title attribute


def test_clone_template_creates_copy_and_redirects_to_its_edit_page(client, session):
    t = EmailTemplate(name="Intro", subject="s", body="b")
    session.add(t)
    session.commit()

    r = client.post(f"/templates/{t.id}/clone", auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    assert session.query(EmailTemplate).count() == 2
    clone = session.query(EmailTemplate).filter(EmailTemplate.id != t.id).one()
    assert clone.name == "Intro (Copy)"
    assert clone.subject == "s" and clone.body == "b"
    assert clone.status == "active"
    assert r.headers["location"] == f"/templates/{clone.id}/edit?msg=Template cloned."


def test_archive_and_unarchive_template_default_hides_archived(client, session):
    t = EmailTemplate(name="Widgets", subject="s", body="b", status="active")
    session.add(t)
    session.commit()

    r = client.post(f"/templates/{t.id}/status", auth=AUTH, follow_redirects=False,
                     data={"action": "archive", "status": "default"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/templates?status=default")
    session.refresh(t)
    assert t.status == "archived"

    default_page = client.get("/templates", auth=AUTH)
    assert "Widgets" not in default_page.text

    archived_page = client.get("/templates?status=archived", auth=AUTH)
    assert "Widgets" in archived_page.text

    r2 = client.post(f"/templates/{t.id}/status", auth=AUTH, follow_redirects=False,
                      data={"action": "unarchive", "status": "archived"})
    assert r2.status_code == 303
    session.refresh(t)
    assert t.status == "active"


def test_status_invalid_action_and_noop(client, session):
    t = EmailTemplate(name="Gadgets", subject="s", body="b", status="active")
    session.add(t)
    session.commit()

    r = client.post(f"/templates/{t.id}/status", auth=AUTH,
                     data={"action": "bogus", "status": "default"})
    assert r.status_code == 400

    r2 = client.post(f"/templates/{t.id}/status", auth=AUTH, follow_redirects=False,
                      data={"action": "unarchive", "status": "default"})
    assert r2.status_code == 303  # no-op: already active, redirects without error
    session.refresh(t)
    assert t.status == "active"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `~/.local/bin/uv run pytest tests/test_templates_web.py::test_list_shows_truncated_content_column tests/test_templates_web.py::test_clone_template_creates_copy_and_redirects_to_its_edit_page tests/test_templates_web.py::test_archive_and_unarchive_template_default_hides_archived tests/test_templates_web.py::test_status_invalid_action_and_noop -v`
Expected: all FAIL (no Content column, no `/clone` or `/status` routes yet).

- [ ] **Step 3: Add `_truncate_body`, update `list_templates`, add `clone_template` and `change_template_status`**

In `src/awkns_outreach/web/routes/templates_lib.py`, add near the top (after `_PREVIEW_EMAIL`):

```python
_STATUS_FILTERS = ("active", "archived", "all")
_STATUS_TRANSITIONS = {
    "archive": {"active": "archived"},
    "unarchive": {"archived": "active"},
}


def _truncate_body(body: str, length: int = 70) -> str:
    collapsed = " ".join(body.split())
    if len(collapsed) <= length:
        return collapsed
    return collapsed[:length].rstrip() + "…"
```

Replace `list_templates` (currently):

```python
@router.get("/templates", response_class=HTMLResponse)
def list_templates(request: Request, db: Session = Depends(get_db), msg: Optional[str] = None):
    items = db.scalars(select(EmailTemplate).order_by(EmailTemplate.created_at.desc())).all()
    return templates.TemplateResponse(request, "template_list.html", {"items": items, "msg": msg})
```

with:

```python
@router.get("/templates", response_class=HTMLResponse)
def list_templates(
    request: Request, db: Session = Depends(get_db),
    status: Optional[str] = None, msg: Optional[str] = None,
):
    status_filter = status if status in _STATUS_FILTERS else "default"
    q = select(EmailTemplate).order_by(EmailTemplate.created_at.desc())
    if status_filter in ("active", "archived"):
        q = q.where(EmailTemplate.status == status_filter)
    elif status_filter == "default":
        q = q.where(EmailTemplate.status == "active")
    items = db.scalars(q).all()
    rows = [{"t": t, "content": _truncate_body(t.body)} for t in items]
    return templates.TemplateResponse(
        request, "template_list.html",
        {"rows": rows, "status_filter": status_filter, "msg": msg},
    )
```

Add at the end of the file:

```python
@router.post("/templates/{template_id}/clone")
def clone_template(template_id: str, db: Session = Depends(get_db)):
    t = _get_template(db, template_id)
    clone = EmailTemplate(name=f"{t.name} (Copy)", subject=t.subject, body=t.body, status="active")
    db.add(clone)
    db.commit()
    return RedirectResponse(f"/templates/{clone.id}/edit?msg=Template cloned.", status_code=303)


@router.post("/templates/{template_id}/status")
def change_template_status(
    template_id: str, action: str = Form(...), status: str = Form("default"),
    db: Session = Depends(get_db),
):
    t = _get_template(db, template_id)
    transitions = _STATUS_TRANSITIONS.get(action)
    if transitions is None:
        raise HTTPException(400, f"Unknown action: {action}")
    new_status = transitions.get(t.status)
    if new_status is None:
        msg = f"Template “{t.name}” is already {t.status}."
    else:
        t.status = new_status
        db.commit()
        msg = f"Template “{t.name}” {'archived' if new_status == 'archived' else 'unarchived'}."
    return RedirectResponse(f"/templates?status={status}&msg={msg}", status_code=303)
```

- [ ] **Step 4: Replace `template_list.html`**

Replace the entire contents of `src/awkns_outreach/web/templates/template_list.html` with:

```html
{% extends "base.html" %}
{% block title %}Email templates — Awkns Outreach{% endblock %}
{% block content %}
<div class="flex items-center justify-between mb-4">
  <h1 class="text-lg font-semibold">Email templates</h1>
  <a href="/templates/new" class="rounded bg-slate-900 text-white text-sm px-3 py-1.5">+ New template</a>
</div>

<p class="text-xs text-slate-500 mb-4">
  Standalone, reusable emails — not tied to any campaign. Open one to preview it against an
  example contact, send yourself a test, or copy it into a campaign's sequence step.
</p>

<div class="mb-4 flex gap-3 text-xs">
  <a href="/templates" class="{% if status_filter == 'default' %}font-semibold text-slate-900{% else %}text-slate-500 hover:underline{% endif %}">Active</a>
  <a href="/templates?status=archived" class="{% if status_filter == 'archived' %}font-semibold text-slate-900{% else %}text-slate-500 hover:underline{% endif %}">Archived</a>
  <a href="/templates?status=all" class="{% if status_filter == 'all' %}font-semibold text-slate-900{% else %}text-slate-500 hover:underline{% endif %}">All</a>
</div>

{% if not rows %}
<p class="text-slate-500 text-sm">No templates in this view.</p>
{% else %}
<div class="overflow-x-auto rounded border bg-white">
  <table class="w-full text-sm">
    <thead class="bg-slate-100 text-slate-500 text-left">
      <tr>
        <th class="px-3 py-2">Name</th>
        <th class="px-3 py-2">Subject</th>
        <th class="px-3 py-2">Content</th>
        <th class="px-3 py-2">Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for row in rows %}
      {% set t = row.t %}
      <tr class="border-t">
        <td class="px-3 py-2"><a href="/templates/{{ t.id }}/edit" class="text-blue-700 hover:underline">{{ t.name }}</a></td>
        <td class="px-3 py-2 text-slate-500">{{ t.subject or "—" }}</td>
        <td class="px-3 py-2 text-slate-500" title="{{ t.body }}">{{ row.content or "—" }}</td>
        <td class="px-3 py-2">
          <div class="flex items-center gap-2">
            {% if t.status != 'archived' %}
            <a href="/templates/{{ t.id }}/edit" class="text-blue-700 hover:underline">Edit</a>
            {% endif %}
            <form method="post" action="/templates/{{ t.id }}/clone" class="inline">
              <button type="submit" class="text-blue-700 hover:underline">Clone</button>
            </form>
            <form method="post" action="/templates/{{ t.id }}/status" class="inline">
              <input type="hidden" name="status" value="{{ status_filter }}">
              {% if t.status == 'archived' %}
              <input type="hidden" name="action" value="unarchive">
              <button type="submit" class="text-blue-700 hover:underline">Unarchive</button>
              {% else %}
              <input type="hidden" name="action" value="archive">
              <button type="submit" class="text-red-700 hover:underline">Archive</button>
              {% endif %}
            </form>
          </div>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/.local/bin/uv run pytest tests/test_templates_web.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/awkns_outreach/web/routes/templates_lib.py src/awkns_outreach/web/templates/template_list.html tests/test_templates_web.py
git commit -m "feat: template list content column, clone, and archive/unarchive"
```

---

## Task 8: Full-suite regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `~/.local/bin/uv run pytest -q`
Expected: all tests pass, no failures anywhere else in the suite (confirms nothing in `admin.py` or elsewhere imported the removed `preview`/`test_send` actions or the old `template_list.html` `items` context key).

- [ ] **Step 2: Grep for any leftover references to removed names**

Run: `grep -rn "action.*preview\|action.*test_send\|items=items\|\"items\":" src/awkns_outreach/web/routes/templates_lib.py src/awkns_outreach/web/templates/template_list.html`
Expected: no matches (confirms the old `action=preview`/`action=test_send` form values and the old `items` context key are fully gone from both the route and the template).

- [ ] **Step 3: Confirm the migration is at head**

Run: `~/.local/bin/uv run alembic current`
Expected: `0005_email_template_status (head)`
