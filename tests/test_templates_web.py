"""Template library web: CRUD, preview against the hard-coded example
contact, and test-send (Resend fallback, or a connected Gmail mailbox)."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach.config import settings
from awkns_outreach.db.models import EmailTemplate, Mailbox
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


def test_create_edit_delete_template(client, session):
    r = client.post("/templates", auth=AUTH, follow_redirects=False, data={
        "name": "Intro", "subject": "quick idea for {company}", "body": "Hi {first_name}, {angle}",
    })
    assert r.status_code == 303
    t = session.query(EmailTemplate).one()
    assert t.name == "Intro"

    edit_page = client.get(f"/templates/{t.id}/edit", auth=AUTH)
    assert edit_page.status_code == 200 and "Intro" in edit_page.text

    r2 = client.post(f"/templates/{t.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "Intro v2", "subject": "s2", "body": "b2",
    })
    assert r2.status_code == 303
    session.refresh(t)
    assert t.name == "Intro v2" and t.subject == "s2" and t.body == "b2"

    list_page = client.get("/templates", auth=AUTH)
    assert "Intro v2" in list_page.text

    r3 = client.post(f"/templates/{t.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "delete", "name": "x", "subject": "x", "body": "x",
    })
    assert r3.status_code == 303
    assert session.query(EmailTemplate).count() == 0


def test_new_template_defaults_to_active_status(client, session):
    r = client.post("/templates", auth=AUTH, follow_redirects=False, data={
        "name": "Intro", "subject": "s", "body": "b",
    })
    assert r.status_code == 303
    t = session.query(EmailTemplate).one()
    assert t.status == "active"


def test_preview_fragment_renders_example_contact_without_saved_template(client, session):
    r = client.post("/templates/preview-fragment", auth=AUTH, data={
        "subject": "hi {company}", "body": "Hi {first_name}, {angle}",
    })
    assert r.status_code == 200
    assert "hi Acme Studios" in r.text
    assert "Hi Jamie," in r.text
    assert session.query(EmailTemplate).count() == 0  # no template was created/required


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


def test_new_template_page_returns_ok_with_connected_mailboxes(client, session):
    mb = Mailbox(
        email="steven@gmail.com", access_token="at", refresh_token="rt",
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1), status="connected",
    )
    session.add(mb)
    session.commit()

    r = client.get("/templates/new", auth=AUTH)
    assert r.status_code == 200
    # Task 6's two-column template now actually includes the mailbox-picker
    # partial, so the connected mailbox's address should show up in the HTML.
    assert mb.email in r.text


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


def test_edit_page_msg_does_not_leak_into_test_send_status(client, session):
    t = EmailTemplate(name="Intro", subject="s", body="b")
    session.add(t)
    session.commit()

    r = client.get(f"/templates/{t.id}/edit?msg=Template saved.", auth=AUTH)
    assert r.status_code == 200
    assert r.text.count("Template saved.") == 1


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
    assert r.headers["location"] == f"/templates/{clone.id}/edit?msg=Template%20cloned."


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
