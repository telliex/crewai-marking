"""Sequences web: content-only CRUD for the standalone MailSequence list/
editor (create, edit, delete), status filtering, archive/unarchive, and the
edit/delete guards (archived can't be edited; a sequence assigned to an
in-play Task can't be deleted)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, MailSequence, Task
from awkns_outreach.db.session import Base, get_db
from awkns_outreach.web.app import app

AUTH = ("admin", "secret")


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def client(engine):
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def session(engine):
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = TestSession()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _admin_password(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")


def _make_campaign(session, **kwargs) -> Campaign:
    c = Campaign(name=kwargs.pop("name", "Acme"), target_titles=[], seed_companies=[], **kwargs)
    session.add(c)
    session.commit()
    return c


def test_create_sequence_with_two_steps(client, session):
    attachments_0 = '[{"filename": "a.pdf", "stored_name": "s1.pdf", "content_type": "application/pdf", "size": 10}]'
    r = client.post("/sequences", auth=AUTH, follow_redirects=False, data={
        "name": "Q3 outreach",
        "step_key": ["intro", "bump"],
        "delay_days": ["not-a-number", "3"],
        "subject": ["hi {company}", "re: hi {company}"],
        "body": ["Hi {first_name}", "Following up"],
        "attachments": [attachments_0, "[]"],
        "source_template_id": ["tpl-1", ""],
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/sequences?msg=Sequence%20created."

    seq = session.query(MailSequence).one()
    assert seq.name == "Q3 outreach"
    assert seq.status == "active"
    assert len(seq.steps) == 2
    assert seq.steps[0]["key"] == "intro"
    assert seq.steps[0]["delay_days"] == 0  # forced, even though the form sent junk
    assert seq.steps[0]["subject"] == "hi {company}"
    assert seq.steps[0]["attachments"] == [
        {"filename": "a.pdf", "stored_name": "s1.pdf", "content_type": "application/pdf", "size": 10}
    ]
    assert seq.steps[0]["source_template_id"] == "tpl-1"
    assert seq.steps[1]["key"] == "bump"
    assert seq.steps[1]["delay_days"] == 3  # tolerant parse of a real int
    assert seq.steps[1]["attachments"] == []
    assert seq.steps[1]["source_template_id"] is None


def test_create_sequence_sanitizes_quill_html_body(client, session):
    r = client.post("/sequences", auth=AUTH, follow_redirects=False, data={
        "name": "Rich",
        "step_key": ["intro"],
        "delay_days": ["0"],
        "subject": ["s"],
        "body": ['<p onclick="x()">Hi <script>alert(1)</script><strong>{first_name}</strong></p>'],
        "attachments": ["[]"],
        "source_template_id": [""],
    })
    assert r.status_code == 303
    seq = session.query(MailSequence).one()
    assert seq.steps[0]["body"] == "<p>Hi <strong>{first_name}</strong></p>"


def test_create_sequence_missing_name_errors_without_persisting(client, session):
    r = client.post("/sequences", auth=AUTH, data={
        "name": "  ",
        "step_key": [], "delay_days": [], "subject": [], "body": [],
        "attachments": [], "source_template_id": [],
    })
    assert r.status_code == 200  # redirect followed back to the form
    assert r.request.url.path == "/sequences/new"
    assert "Name is required." in r.text  # error actually rendered
    assert session.query(MailSequence).count() == 0


def test_list_page_shows_name_and_status_and_filters(client, session):
    active = MailSequence(name="Active seq", status="active", steps=[])
    archived = MailSequence(name="Archived seq", status="archived", steps=[])
    session.add_all([active, archived])
    session.commit()

    r = client.get("/sequences", auth=AUTH)
    assert r.status_code == 200
    assert "Active seq" in r.text and "Archived seq" not in r.text  # default = active only

    all_page = client.get("/sequences?status=all", auth=AUTH)
    assert "Active seq" in all_page.text and "Archived seq" in all_page.text

    archived_page = client.get("/sequences?status=archived", auth=AUTH)
    assert "Archived seq" in archived_page.text
    assert "Active seq" not in archived_page.text


def test_edit_form_prefills_existing_sequence(client, session):
    seq = MailSequence(
        name="Prefill me", status="active",
        steps=[{"key": "intro", "delay_days": 0, "subject": "hi {company}", "body": "b",
                "attachments": [], "source_template_id": None}],
    )
    session.add(seq)
    session.commit()

    r = client.get(f"/sequences/{seq.id}/edit", auth=AUTH)
    assert r.status_code == 200
    assert "Prefill me" in r.text
    assert "hi {company}" in r.text


def test_edit_page_renders_rich_quill_editor_per_step(client, session):
    seq = MailSequence(
        name="Rich seq", status="active",
        steps=[{"key": "intro", "delay_days": 0, "subject": "s", "body": "b",
                "attachments": [], "source_template_id": None}],
    )
    session.add(seq)
    session.commit()

    r = client.get(f"/sequences/{seq.id}/edit", auth=AUTH)
    assert r.status_code == 200
    # A real per-step Quill editor replaces the old plain <textarea name="body">.
    assert "<textarea name=\"body\"" not in r.text
    assert 'class="tw-editor' in r.text
    assert "tw-quill-toolbar" in r.text
    assert "tw-quill-editor" in r.text


def test_edit_page_renders_independent_editor_per_step(client, session):
    seq = MailSequence(
        name="Two steps", status="active",
        steps=[
            {"key": "intro", "delay_days": 0, "subject": "first subject line",
             "body": "first body text", "attachments": [], "source_template_id": None},
            {"key": "bump", "delay_days": 3, "subject": "second subject line",
             "body": "second body text", "attachments": [], "source_template_id": None},
        ],
    )
    session.add(seq)
    session.commit()

    r = client.get(f"/sequences/{seq.id}/edit", auth=AUTH)
    assert r.status_code == 200
    # The page also carries one hidden `<template id="step-tpl">` clone
    # source (used by addStep()'s JS, unrelated to the two real steps) which
    # itself contains one more copy of the same markup — exclude it so this
    # only counts the two live, server-rendered step cards.
    live_steps_html = r.text.split('<template id="step-tpl">')[0]
    assert live_steps_html.count('<div class="tw-editor border rounded overflow-visible">') == 2
    assert live_steps_html.count('<div class="tw-quill-toolbar flex') == 2
    assert live_steps_html.count('class="tw-preview-pane"') == 2
    assert live_steps_html.count('id="test-send-widget" class="tw-test-send-widget') == 2
    # Both steps' own content is present, not just the first one's.
    assert "first subject line" in r.text and "second subject line" in r.text
    assert "first body text" in r.text and "second body text" in r.text


def test_edit_and_delete_blocked_while_archived(client, session):
    seq = MailSequence(name="Archived seq", status="archived", steps=[])
    session.add(seq)
    session.commit()

    get_r = client.get(f"/sequences/{seq.id}/edit", auth=AUTH, follow_redirects=False)
    assert get_r.status_code == 303
    assert get_r.headers["location"].startswith("/sequences?msg=")

    post_r = client.post(f"/sequences/{seq.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "Should not apply",
        "step_key": [], "delay_days": [], "subject": [], "body": [],
        "attachments": [], "source_template_id": [],
    })
    assert post_r.status_code == 303
    assert post_r.headers["location"].startswith("/sequences?msg=")
    session.refresh(seq)
    assert seq.name == "Archived seq"  # unchanged


def test_delete_active_sequence_removes_row(client, session):
    seq = MailSequence(name="Throwaway", status="active", steps=[])
    session.add(seq)
    session.commit()

    r = client.post(f"/sequences/{seq.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "delete",
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/sequences?msg=Sequence%20deleted."
    assert session.query(MailSequence).count() == 0


def test_delete_blocked_when_assigned_to_in_play_task(client, session):
    c = _make_campaign(session)
    seq = MailSequence(name="Assigned seq", status="active", steps=[])
    session.add(seq)
    session.commit()
    task = Task(name="Draft task", campaign_id=c.id, status="draft", sequences={"B": seq.id})
    session.add(task)
    session.commit()

    r = client.post(f"/sequences/{seq.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "delete",
    })
    assert r.status_code == 303
    from urllib.parse import unquote
    assert "Draft task" in unquote(r.headers["location"])
    assert session.query(MailSequence).count() == 1


def test_delete_allowed_once_task_no_longer_blocks(client, session):
    c = _make_campaign(session)
    seq = MailSequence(name="Formerly assigned", status="active", steps=[])
    session.add(seq)
    session.commit()
    # Task in a non-blocking status (completed) doesn't block delete.
    task = Task(name="Done task", campaign_id=c.id, status="completed", sequences={"B": seq.id})
    session.add(task)
    session.commit()

    r = client.post(f"/sequences/{seq.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "delete",
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/sequences?msg=Sequence%20deleted."
    assert session.query(MailSequence).count() == 0


def test_save_edit_updates_name_and_steps(client, session):
    seq = MailSequence(name="Original", status="active", steps=[])
    session.add(seq)
    session.commit()

    r = client.post(f"/sequences/{seq.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "Updated",
        "step_key": ["intro"], "delay_days": ["5"], "subject": ["s"], "body": ["b"],
        "attachments": ["[]"], "source_template_id": [""],
    })
    assert r.status_code == 303
    session.refresh(seq)
    assert seq.name == "Updated"
    assert len(seq.steps) == 1
    assert seq.steps[0]["delay_days"] == 0  # first step forced to 0 even on edit


def test_archive_and_unarchive_sequence(client, session):
    seq = MailSequence(name="Toggle me", status="active", steps=[])
    session.add(seq)
    session.commit()

    r = client.post(f"/sequences/{seq.id}/status", auth=AUTH, follow_redirects=False,
                    data={"action": "archive", "status": "active"})
    assert r.status_code == 303
    session.refresh(seq)
    assert seq.status == "archived"

    r2 = client.post(f"/sequences/{seq.id}/status", auth=AUTH, follow_redirects=False,
                     data={"action": "unarchive", "status": "archived"})
    assert r2.status_code == 303
    session.refresh(seq)
    assert seq.status == "active"


def test_archive_blocked_when_assigned_to_in_play_task(client, session):
    c = _make_campaign(session)
    seq = MailSequence(name="Assigned seq", status="active", steps=[])
    session.add(seq)
    session.commit()
    task = Task(name="Scheduled task", campaign_id=c.id, status="scheduled", sequences={"B": seq.id})
    session.add(task)
    session.commit()

    r = client.post(f"/sequences/{seq.id}/status", auth=AUTH, follow_redirects=False,
                     data={"action": "archive", "status": "active"})
    assert r.status_code == 303
    from urllib.parse import unquote
    assert "Scheduled task" in unquote(r.headers["location"])
    session.refresh(seq)
    assert seq.status == "active"  # unchanged


def test_archive_allowed_once_task_no_longer_blocks(client, session):
    c = _make_campaign(session)
    seq = MailSequence(name="Formerly assigned", status="active", steps=[])
    session.add(seq)
    session.commit()
    # Task in a non-blocking status (completed) doesn't block archive.
    task = Task(name="Done task", campaign_id=c.id, status="completed", sequences={"B": seq.id})
    session.add(task)
    session.commit()

    r = client.post(f"/sequences/{seq.id}/status", auth=AUTH, follow_redirects=False,
                     data={"action": "archive", "status": "active"})
    assert r.status_code == 303
    session.refresh(seq)
    assert seq.status == "archived"


def test_status_change_noop_message_and_unknown_action(client, session):
    seq = MailSequence(name="Already active", status="active", steps=[])
    session.add(seq)
    session.commit()

    r = client.post(f"/sequences/{seq.id}/status", auth=AUTH, follow_redirects=False,
                    data={"action": "unarchive", "status": "active"})
    assert r.status_code == 303
    from urllib.parse import unquote
    assert "already active" in unquote(r.headers["location"])

    r2 = client.post(f"/sequences/{seq.id}/status", auth=AUTH, data={"action": "bogus"})
    assert r2.status_code == 400


def test_post_edit_unknown_action_returns_400(client, session):
    seq = MailSequence(name="Seq", status="active", steps=[])
    session.add(seq)
    session.commit()

    r = client.post(f"/sequences/{seq.id}/edit", auth=AUTH, data={"action": "bogus"})
    assert r.status_code == 400


def test_unknown_sequence_id_returns_404(client, session):
    r = client.get("/sequences/does-not-exist/edit", auth=AUTH)
    assert r.status_code == 404


def test_sequences_require_auth(client):
    assert client.get("/sequences").status_code == 401
