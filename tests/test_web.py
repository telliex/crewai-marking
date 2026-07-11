"""Web layer: unsubscribe (GET + one-click POST), Resend webhook, and admin
auth/flow. Uses a shared in-memory SQLite via a get_db dependency override."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach.compliance import make_unsub_token
from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, Event, Lead, MailSequence, Suppression
from awkns_outreach.db.session import Base, get_db
from awkns_outreach.web.app import app
from awkns_outreach.web.stats import campaign_stats


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


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_unsubscribe_get_suppresses(client, session):
    token = make_unsub_token("a@b.com")
    r = client.get(f"/outreach/unsubscribe?token={token}")
    assert r.status_code == 200 and "unsubscribed" in r.text.lower()
    assert session.get(Suppression, "a@b.com") is not None


def test_unsubscribe_one_click_post(client, session):
    token = make_unsub_token("click@b.com")
    r = client.post(f"/outreach/unsubscribe?token={token}")
    assert r.status_code == 200
    assert session.get(Suppression, "click@b.com") is not None


def test_unsubscribe_invalid_token(client, session):
    r = client.get("/outreach/unsubscribe?token=garbage")
    assert r.status_code == 400
    assert session.query(Suppression).count() == 0


def test_webhook_bounce_suppresses_and_logs(client, session):
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.flush()
    session.add(Lead(campaign_id=c.id, email="bounce@x.com", company="X", status="active"))
    session.commit()

    r = client.post("/webhooks/resend", json={
        "type": "email.bounced",
        "data": {"to": ["bounce@x.com"], "email_id": "e1"},
    })
    assert r.status_code == 200
    assert session.get(Suppression, "bounce@x.com").reason == "bounce"
    assert session.query(Event).filter_by(type="bounce").count() == 1


def test_webhook_ignores_unknown_recipient(client):
    r = client.post("/webhooks/resend", json={"type": "email.opened", "data": {}})
    assert r.status_code == 200 and r.json().get("ignored")


def test_admin_requires_auth(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("admin", "wrong")).status_code == 401
    assert client.get("/", auth=("admin", "secret")).status_code == 200


def test_admin_create_and_view_campaign(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    r = client.post("/campaigns", auth=auth, data={
        "name": "JP studios", "titles": "creative director, head of content",
        "seed_text": '[{"name": "Toyota", "website": "https://www.toyota.co.jp", "priority": "A"}]',
        "angle_prompt": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    c = session.query(Campaign).one()
    assert c.name == "JP studios"
    assert c.target_titles == ["creative director", "head of content"]
    assert c.seed_companies == [{"name": "Toyota", "website": "toyota.co.jp", "priority": "A"}]

    detail = client.get(f"/campaigns/{c.id}", auth=auth)
    assert detail.status_code == 200 and "JP studios" in detail.text


def test_sequence_editor_saves_steps(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[], sequence=[], sender_identity={})
    session.add(c)
    session.commit()

    # Editor page renders (with the placeholder cheatsheet).
    page = client.get(f"/campaigns/{c.id}/sequence", auth=auth)
    assert page.status_code == 200 and "{angle}" in page.text

    # Save two steps; a fully-blank third row is dropped.
    r = client.post(f"/campaigns/{c.id}/sequence", auth=auth, follow_redirects=False, data={
        "step_key": ["intro", "bump", ""],
        "delay_days": ["0", "3", "0"],
        "subject": ["quick idea for {company}", "re: quick idea", ""],
        "body": ["Hi {first_name}, {angle}", "floating back up", ""],
    })
    assert r.status_code == 303
    session.refresh(c)
    assert len(c.sequence) == 2
    assert c.sequence[0] == {"key": "intro", "delay_days": 0,
                             "subject": "quick idea for {company}", "body": "Hi {first_name}, {angle}"}
    assert c.sequence[1]["key"] == "bump" and c.sequence[1]["delay_days"] == 3


def test_sequence_editor_clearing_all_steps(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[],
                 sequence=[{"key": "x", "delay_days": 0, "subject": "s", "body": "b"}],
                 sender_identity={})
    session.add(c)
    session.commit()
    r = client.post(f"/campaigns/{c.id}/sequence", auth=("admin", "secret"),
                    follow_redirects=False, data={"step_key": [], "delay_days": [],
                                                  "subject": [], "body": []})
    assert r.status_code == 303
    session.refresh(c)
    assert c.sequence == []


# --- dashboard: status filter, archive/pause lifecycle, edit, pagination --


def test_dashboard_default_hides_archived_status_filters_and_renders_badges(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    active = Campaign(name="Active Co", description="Warm JP leads",
                      target_titles=[], seed_companies=[], status="active")
    paused = Campaign(name="Paused Co", target_titles=[], seed_companies=[], status="paused")
    archived = Campaign(name="Archived Co", target_titles=[], seed_companies=[], status="archived")
    session.add_all([active, paused, archived])
    session.commit()

    default_page = client.get("/", auth=auth)
    assert default_page.status_code == 200
    assert "Active Co" in default_page.text and "Paused Co" in default_page.text
    assert "Archived Co" not in default_page.text
    # description second-line and status badges render
    assert "Warm JP leads" in default_page.text
    assert "bg-green-100" in default_page.text and "bg-amber-100" in default_page.text

    archived_page = client.get("/?status=archived", auth=auth)
    assert "Archived Co" in archived_page.text
    assert "Active Co" not in archived_page.text and "Paused Co" not in archived_page.text

    all_page = client.get("/?status=all", auth=auth)
    assert "Active Co" in all_page.text
    assert "Paused Co" in all_page.text
    assert "Archived Co" in all_page.text

    # Unknown status values behave as the default.
    unknown_page = client.get("/?status=bogus", auth=auth)
    assert "Active Co" in unknown_page.text and "Archived Co" not in unknown_page.text


def test_status_archive_and_unarchive_preserve_filter_and_page(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="Widgets", target_titles=[], seed_companies=[], status="active")
    session.add(c)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/status", auth=auth, follow_redirects=False,
                    data={"action": "archive", "status": "default", "page": "1"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/?status=default&page=1")
    session.refresh(c)
    assert c.status == "archived"

    r2 = client.post(f"/campaigns/{c.id}/status", auth=auth, follow_redirects=False,
                     data={"action": "unarchive", "status": "archived", "page": "2"})
    assert r2.status_code == 303
    assert r2.headers["location"].startswith("/?status=archived&page=2")
    session.refresh(c)
    assert c.status == "active"


def test_status_pause_resume_invalid_action_and_noop(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="Gadgets", target_titles=[], seed_companies=[], status="active")
    session.add(c)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/status", auth=auth, follow_redirects=False,
                    data={"action": "pause", "status": "default", "page": "1"})
    assert r.status_code == 303
    session.refresh(c)
    assert c.status == "paused"

    r2 = client.post(f"/campaigns/{c.id}/status", auth=auth, follow_redirects=False,
                     data={"action": "resume", "status": "default", "page": "1"})
    assert r2.status_code == 303
    session.refresh(c)
    assert c.status == "active"

    r3 = client.post(f"/campaigns/{c.id}/status", auth=auth,
                     data={"action": "bogus", "status": "default", "page": "1"})
    assert r3.status_code == 400

    # No-op transition (resume on an already-active campaign): redirects, no error.
    r4 = client.post(f"/campaigns/{c.id}/status", auth=auth, follow_redirects=False,
                     data={"action": "resume", "status": "default", "page": "1"})
    assert r4.status_code == 303
    session.refresh(c)
    assert c.status == "active"


def test_campaign_status_change_mirrors_onto_mail_sequence(client, session, monkeypatch):
    """The dashboard's own pause/resume/archive buttons must not leave a
    campaign's running/paused MailSequence status silently out of sync with
    the Tasks page's lifecycle state."""
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="Mirrors", target_titles=[], seed_companies=[], status="active")
    session.add(c)
    session.commit()
    seq = MailSequence(name="Live seq", campaign_id=c.id, status="running", steps=[])
    session.add(seq)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/status", auth=auth, follow_redirects=False,
                    data={"action": "pause", "status": "default", "page": "1"})
    assert r.status_code == 303
    session.refresh(c)
    session.refresh(seq)
    assert c.status == "paused" and seq.status == "paused"

    r2 = client.post(f"/campaigns/{c.id}/status", auth=auth, follow_redirects=False,
                     data={"action": "resume", "status": "default", "page": "1"})
    assert r2.status_code == 303
    session.refresh(c)
    session.refresh(seq)
    assert c.status == "active" and seq.status == "running"

    r3 = client.post(f"/campaigns/{c.id}/status", auth=auth, follow_redirects=False,
                     data={"action": "archive", "status": "default", "page": "1"})
    assert r3.status_code == 303
    session.refresh(c)
    session.refresh(seq)
    assert c.status == "archived" and seq.status == "stopped"


def test_campaign_status_change_unarchive_does_not_touch_mail_sequence(client, session, monkeypatch):
    """unarchive needs no sequence mirroring — a stopped sequence stays
    stopped even if its campaign is unarchived back to active."""
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="Unarchive me", target_titles=[], seed_companies=[], status="archived")
    session.add(c)
    session.commit()
    seq = MailSequence(name="Done seq", campaign_id=c.id, status="stopped", steps=[])
    session.add(seq)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/status", auth=auth, follow_redirects=False,
                    data={"action": "unarchive", "status": "archived", "page": "1"})
    assert r.status_code == 303
    session.refresh(c)
    session.refresh(seq)
    assert c.status == "active" and seq.status == "stopped"


def test_edit_campaign_get_and_post_saves_fields(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="Old name", target_titles=["cmo"], seed_companies=[], status="active")
    session.add(c)
    session.commit()

    page = client.get(f"/campaigns/{c.id}/edit", auth=auth)
    assert page.status_code == 200 and "Old name" in page.text

    r = client.post(f"/campaigns/{c.id}/edit", auth=auth, follow_redirects=False, data={
        "name": "New name", "description": "A new blurb",
        "titles": "creative director\nhead of content", "angle_prompt": "focus on X",
    })
    assert r.status_code == 303
    assert r.headers["location"] == f"/campaigns/{c.id}?msg=Campaign%20updated."
    session.refresh(c)
    assert c.name == "New name"
    assert c.description == "A new blurb"
    assert c.target_titles == ["creative director", "head of content"]
    assert c.angle_prompt == "focus on X"


def test_edit_archived_campaign_blocked_get_and_post(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="Frozen", target_titles=[], seed_companies=[], status="archived")
    session.add(c)
    session.commit()

    get_r = client.get(f"/campaigns/{c.id}/edit", auth=auth, follow_redirects=False)
    assert get_r.status_code == 303
    assert get_r.headers["location"].startswith("/?msg=")

    post_r = client.post(f"/campaigns/{c.id}/edit", auth=auth, follow_redirects=False, data={
        "name": "Should not save", "description": "", "titles": "", "angle_prompt": "",
    })
    assert post_r.status_code == 303
    assert post_r.headers["location"].startswith("/?msg=")
    session.refresh(c)
    assert c.name == "Frozen"  # unchanged


def test_dashboard_pagination(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    for i in range(25):
        session.add(Campaign(name=f"Campaign {i:02d}", target_titles=[], seed_companies=[], status="active"))
    session.commit()

    page1 = client.get("/?status=all&page=1", auth=auth)
    assert "Showing 1–20 of 25 campaigns" in page1.text
    page2 = client.get("/?status=all&page=2", auth=auth)
    assert "Showing 21–25 of 25 campaigns" in page2.text

    # Out-of-range page clamps to the last valid page.
    out_of_range = client.get("/?status=all&page=99", auth=auth)
    assert "Showing 21–25 of 25 campaigns" in out_of_range.text


def test_campaign_stats_sent_total_counts_lifetime(session):
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.flush()
    lead = Lead(campaign_id=c.id, email="a@b.com", company="X", status="active")
    session.add(lead)
    session.flush()

    now = datetime.now(timezone.utc)
    session.add(Event(lead_id=lead.id, type="sent", created_at=now))
    session.add(Event(lead_id=lead.id, type="sent", created_at=now - timedelta(hours=48)))
    session.commit()

    stats = campaign_stats(session, c, now=now)
    assert stats["sent_total"] == 2
    assert stats["sent_last_24h"] == 1
