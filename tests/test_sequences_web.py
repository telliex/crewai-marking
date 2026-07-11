"""Sequences web: CRUD for the standalone MailSequence list/editor (create,
edit, delete), status filtering, and the pre-start edit/delete guards."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, MailSequence
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
    c = _make_campaign(session)
    attachments_0 = '[{"filename": "a.pdf", "stored_name": "s1.pdf", "content_type": "application/pdf", "size": 10}]'
    r = client.post("/sequences", auth=AUTH, follow_redirects=False, data={
        "name": "Q3 outreach",
        "campaign_id": c.id,
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
    assert seq.campaign_id == c.id
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
    c = _make_campaign(session)
    r = client.post("/sequences", auth=AUTH, follow_redirects=False, data={
        "name": "Rich",
        "campaign_id": c.id,
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


def test_create_sequence_unknown_campaign_id_errors_without_persisting(client, session):
    r = client.post("/sequences", auth=AUTH, data={
        "name": "Orphan",
        "campaign_id": "does-not-exist",
        "step_key": ["intro"],
        "delay_days": ["0"],
        "subject": ["s"],
        "body": ["b"],
        "attachments": ["[]"],
        "source_template_id": [""],
    })
    assert r.status_code == 200  # redirect followed back to the form
    assert r.request.url.path == "/sequences/new"
    assert "Select a valid group." in r.text  # error actually rendered
    assert session.query(MailSequence).count() == 0


def test_create_sequence_missing_name_errors_without_persisting(client, session):
    c = _make_campaign(session)
    r = client.post("/sequences", auth=AUTH, data={
        "name": "  ",
        "campaign_id": c.id,
        "step_key": [], "delay_days": [], "subject": [], "body": [],
        "attachments": [], "source_template_id": [],
    })
    assert r.status_code == 200  # redirect followed back to the form
    assert r.request.url.path == "/sequences/new"
    assert "Name is required." in r.text  # error actually rendered
    assert session.query(MailSequence).count() == 0


def test_list_page_shows_name_campaign_and_status_and_filters(client, session):
    c = _make_campaign(session, name="Widgets Co")
    draft = MailSequence(name="Draft seq", campaign_id=c.id, status="draft", steps=[])
    running = MailSequence(name="Running seq", campaign_id=c.id, status="running", steps=[])
    session.add_all([draft, running])
    session.commit()

    r = client.get("/sequences", auth=AUTH)
    assert r.status_code == 200
    assert "Draft seq" in r.text and "Running seq" in r.text
    assert "Widgets Co" in r.text

    filtered = client.get("/sequences?status=draft", auth=AUTH)
    assert filtered.status_code == 200
    assert "Draft seq" in filtered.text
    assert "Running seq" not in filtered.text


def test_edit_form_prefills_existing_sequence(client, session):
    c = _make_campaign(session)
    seq = MailSequence(
        name="Prefill me", campaign_id=c.id, status="draft",
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
    c = _make_campaign(session)
    seq = MailSequence(
        name="Rich seq", campaign_id=c.id, status="draft",
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
    c = _make_campaign(session)
    seq = MailSequence(
        name="Two steps", campaign_id=c.id, status="draft",
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
    # Two independent editor instances (and preview/test-send widgets) render,
    # each multi-instance-safe (no shared/global ids). Match on each markup
    # block's actual opening tag rather than a bare class name, since the
    # editor's <style> block (embedded once per instance, by Task 3a design)
    # also mentions "tw-quill-toolbar" as a CSS selector.
    assert live_steps_html.count('<div class="tw-editor border rounded overflow-visible">') == 2
    assert live_steps_html.count('<div class="tw-quill-toolbar flex') == 2
    assert live_steps_html.count('class="tw-preview-pane"') == 2
    assert live_steps_html.count('id="test-send-widget" class="tw-test-send-widget') == 2
    # Both steps' own content is present, not just the first one's.
    assert "first subject line" in r.text and "second subject line" in r.text
    assert "first body text" in r.text and "second body text" in r.text


def test_edit_and_delete_blocked_while_running(client, session):
    c = _make_campaign(session)
    seq = MailSequence(name="Live seq", campaign_id=c.id, status="running", steps=[])
    session.add(seq)
    session.commit()

    get_r = client.get(f"/sequences/{seq.id}/edit", auth=AUTH, follow_redirects=False)
    assert get_r.status_code == 303
    assert get_r.headers["location"].startswith("/sequences?msg=")

    post_r = client.post(f"/sequences/{seq.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "Should not apply", "campaign_id": c.id,
        "step_key": [], "delay_days": [], "subject": [], "body": [],
        "attachments": [], "source_template_id": [],
    })
    assert post_r.status_code == 303
    assert post_r.headers["location"].startswith("/sequences?msg=")
    session.refresh(seq)
    assert seq.name == "Live seq"  # unchanged

    delete_r = client.post(f"/sequences/{seq.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "delete",
    })
    assert delete_r.status_code == 303
    assert delete_r.headers["location"].startswith("/sequences?msg=")
    assert session.query(MailSequence).count() == 1  # still present


def test_delete_draft_sequence_removes_row(client, session):
    c = _make_campaign(session)
    seq = MailSequence(name="Throwaway", campaign_id=c.id, status="draft", steps=[])
    session.add(seq)
    session.commit()

    r = client.post(f"/sequences/{seq.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "delete",
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/sequences?msg=Sequence%20deleted."
    assert session.query(MailSequence).count() == 0


def test_save_edit_updates_name_group_and_steps(client, session):
    c1 = _make_campaign(session, name="Group A")
    c2 = _make_campaign(session, name="Group B")
    seq = MailSequence(name="Original", campaign_id=c1.id, status="draft", steps=[])
    session.add(seq)
    session.commit()

    r = client.post(f"/sequences/{seq.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "Updated", "campaign_id": c2.id,
        "step_key": ["intro"], "delay_days": ["5"], "subject": ["s"], "body": ["b"],
        "attachments": ["[]"], "source_template_id": [""],
    })
    assert r.status_code == 303
    session.refresh(seq)
    assert seq.name == "Updated"
    assert seq.campaign_id == c2.id
    assert len(seq.steps) == 1
    assert seq.steps[0]["delay_days"] == 0  # first step forced to 0 even on edit


def test_save_edit_rejects_group_reassignment_when_target_group_has_active_sequence(client, session):
    """Regression for Important #1: a scheduled sequence already occupies its
    current Group's one-active-sequence slot — reassigning it to a Group that
    already has an active sequence must be rejected, not silently applied."""
    c1 = _make_campaign(session, name="Group A")
    c2 = _make_campaign(session, name="Group B")
    seq = MailSequence(
        name="Scheduled seq", campaign_id=c1.id, status="scheduled",
        steps=[{"key": "intro", "delay_days": 0, "subject": "s", "body": "b",
                "attachments": [], "source_template_id": None}],
    )
    other = MailSequence(name="Already running", campaign_id=c2.id, status="running", steps=[])
    session.add_all([seq, other])
    session.commit()

    r = client.post(f"/sequences/{seq.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "Scheduled seq", "campaign_id": c2.id,
        "step_key": ["intro"], "delay_days": ["0"], "subject": ["s"], "body": ["b"],
        "attachments": ["[]"], "source_template_id": [""],
    })
    assert r.status_code == 303
    assert r.headers["location"] == (
        f"/sequences/{seq.id}/edit?msg=Already%20running%20is%20already%20running%20for%20this%20group."
    )
    session.refresh(seq)
    assert seq.campaign_id == c1.id  # unchanged — reassignment rejected


def test_save_edit_allows_group_reassignment_when_target_group_is_free(client, session):
    c1 = _make_campaign(session, name="Group A")
    c2 = _make_campaign(session, name="Group B")
    seq = MailSequence(name="Scheduled seq", campaign_id=c1.id, status="scheduled", steps=[])
    session.add(seq)
    session.commit()

    r = client.post(f"/sequences/{seq.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "Scheduled seq", "campaign_id": c2.id,
        "step_key": [], "delay_days": [], "subject": [], "body": [],
        "attachments": [], "source_template_id": [],
    })
    assert r.status_code == 303
    assert r.headers["location"] == f"/sequences/{seq.id}/edit?msg=Sequence%20saved."
    session.refresh(seq)
    assert seq.campaign_id == c2.id  # applied — target group was free


def test_edit_form_disables_group_select_for_scheduled_sequence(client, session):
    c = _make_campaign(session)
    seq = MailSequence(name="Scheduled seq", campaign_id=c.id, status="scheduled", steps=[])
    session.add(seq)
    session.commit()

    r = client.get(f"/sequences/{seq.id}/edit", auth=AUTH)
    assert r.status_code == 200
    assert '<select name="campaign_id" required class="mt-1 w-full border rounded px-2 py-1.5 text-sm"\n              disabled>' in r.text
    assert f'<input type="hidden" name="campaign_id" value="{c.id}">' in r.text


def test_edit_form_leaves_group_select_enabled_for_draft_sequence(client, session):
    c = _make_campaign(session)
    seq = MailSequence(name="Draft seq", campaign_id=c.id, status="draft", steps=[])
    session.add(seq)
    session.commit()

    r = client.get(f"/sequences/{seq.id}/edit", auth=AUTH)
    assert r.status_code == 200
    assert 'name="campaign_id" required class="mt-1 w-full border rounded px-2 py-1.5 text-sm"\n              disabled' not in r.text


def test_post_edit_unknown_action_returns_400(client, session):
    c = _make_campaign(session)
    seq = MailSequence(name="Seq", campaign_id=c.id, status="draft", steps=[])
    session.add(seq)
    session.commit()

    r = client.post(f"/sequences/{seq.id}/edit", auth=AUTH, data={"action": "bogus"})
    assert r.status_code == 400


def test_unknown_sequence_id_returns_404(client, session):
    r = client.get("/sequences/does-not-exist/edit", auth=AUTH)
    assert r.status_code == 404


def test_sequences_require_auth(client):
    assert client.get("/sequences").status_code == 401
