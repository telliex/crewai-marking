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
from awkns_outreach.db.models import Campaign, Event, Lead, MailSequence, Suppression, Task
from awkns_outreach.db.session import Base, get_db
from awkns_outreach.web.app import app
from awkns_outreach.web.routes import admin
from awkns_outreach.web.stats import campaign_stats
from awkns_outreach.writer.tiers import TierSummary


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
    # Legacy "priority" key in the pasted JSON is normalized to canonical "tier".
    assert c.seed_companies == [{"name": "Toyota", "website": "toyota.co.jp", "tier": "A"}]

    detail = client.get(f"/campaigns/{c.id}", auth=auth)
    assert detail.status_code == 200 and "JP studios" in detail.text


def test_create_campaign_blocks_on_malformed_seed_and_prefills_form(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    r = client.post("/campaigns", auth=auth, data={
        "name": "JP studios",
        "titles": "creative director",
        "seed_text": '[{"name":"Toyota","website":"toyota.jp,"country":"JP"}]',
        "angle_prompt": "",
    }, follow_redirects=False)

    assert r.status_code == 200  # re-rendered form, not a redirect
    assert session.query(Campaign).count() == 0  # nothing created
    assert "Seed import failed" in r.text
    assert 'value="JP studios"' in r.text  # name preserved
    assert "creative director" in r.text  # titles preserved
    assert "toyota.jp" in r.text  # seed_text preserved


def test_create_campaign_with_missing_name_row_blocks_creation(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    r = client.post("/campaigns", auth=auth, data={
        "name": "JP studios",
        "titles": "",
        "seed_text": '[{"website": "toyota.co.jp"}]',
        "angle_prompt": "",
    }, follow_redirects=False)

    assert r.status_code == 200
    assert session.query(Campaign).count() == 0
    assert "missing required field" in r.text


def test_seed_template_csv_download(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    r = client.get("/campaigns/seed-template.csv", auth=auth)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "seed_companies_template.csv" in r.headers["content-disposition"]
    first_line = r.text.splitlines()[0]
    assert first_line.split(",") == list(admin.SEED_FIELDS)


def test_edit_companies_form_renders_new_contact_columns(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp",
         "contact_name": "Jamie Rivera", "contact_title": "VP Finance"},
    ])
    session.add(c)
    session.commit()

    r = client.get(f"/campaigns/{c.id}/companies", auth=auth)
    assert r.status_code == 200
    assert 'value="jamie@toyota.co.jp"' in r.text
    assert 'value="Jamie Rivera"' in r.text
    assert 'value="VP Finance"' in r.text


def test_save_companies_persists_email_and_contact_fields(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/companies", auth=auth, data={
        "action": "save",
        "name": ["Toyota"], "website": [""], "country": [""], "category": [""],
        "tier": [""], "angle": [""],
        "email": ["jamie@toyota.co.jp"], "contact_name": ["Jamie Rivera"],
        "contact_title": ["VP Finance"],
    }, follow_redirects=False)
    assert r.status_code == 303
    session.refresh(c)
    assert c.seed_companies == [{
        "name": "Toyota", "email": "jamie@toyota.co.jp",
        "contact_name": "Jamie Rivera", "contact_title": "VP Finance",
    }]


def test_legacy_sequence_editor_redirects_to_sequences(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[], sender_identity={})
    session.add(c)
    session.commit()

    r = client.get(f"/campaigns/{c.id}/sequence", auth=auth, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/sequences"


def test_legacy_sequence_editor_404s_for_missing_campaign(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    r = client.get("/campaigns/does-not-exist/sequence", auth=("admin", "secret"),
                    follow_redirects=False)
    assert r.status_code == 404


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


def test_status_delete_blocked_unless_archived(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="Widgets", target_titles=[], seed_companies=[], status="active")
    session.add(c)
    session.commit()
    campaign_id = c.id

    r = client.post(f"/campaigns/{campaign_id}/status", auth=auth, follow_redirects=False,
                    data={"action": "delete", "status": "default", "page": "1"})
    assert r.status_code == 303
    assert session.get(Campaign, campaign_id) is not None


def test_status_delete_removes_archived_campaign_and_cascades(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="Widgets", target_titles=[], seed_companies=[], status="archived")
    session.add(c)
    session.commit()
    campaign_id = c.id
    lead = Lead(campaign_id=campaign_id, email="jamie@toyota.co.jp", company="Toyota")
    session.add(lead)
    session.commit()

    r = client.post(f"/campaigns/{campaign_id}/status", auth=auth, follow_redirects=False,
                    data={"action": "delete", "status": "archived", "page": "1"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/?status=archived&page=1")
    session.expunge_all()
    assert session.get(Campaign, campaign_id) is None
    assert session.get(Lead, lead.id) is None


def test_campaign_status_change_mirrors_onto_task(client, session, monkeypatch):
    """The dashboard's own pause/resume/archive buttons must not leave a
    campaign's running/paused Task status silently out of sync with the
    Tasks page's lifecycle state."""
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="Mirrors", target_titles=[], seed_companies=[], status="active")
    session.add(c)
    session.commit()
    task = Task(name="Live task", campaign_id=c.id, status="running", sequences={})
    session.add(task)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/status", auth=auth, follow_redirects=False,
                    data={"action": "pause", "status": "default", "page": "1"})
    assert r.status_code == 303
    session.refresh(c)
    session.refresh(task)
    assert c.status == "paused" and task.status == "paused"

    r2 = client.post(f"/campaigns/{c.id}/status", auth=auth, follow_redirects=False,
                     data={"action": "resume", "status": "default", "page": "1"})
    assert r2.status_code == 303
    session.refresh(c)
    session.refresh(task)
    assert c.status == "active" and task.status == "running"

    r3 = client.post(f"/campaigns/{c.id}/status", auth=auth, follow_redirects=False,
                     data={"action": "archive", "status": "default", "page": "1"})
    assert r3.status_code == 303
    session.refresh(c)
    session.refresh(task)
    assert c.status == "archived" and task.status == "stopped"


def test_campaign_status_change_unarchive_does_not_touch_task(client, session, monkeypatch):
    """unarchive needs no task mirroring — a stopped task stays stopped even
    if its campaign is unarchived back to active."""
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="Unarchive me", target_titles=[], seed_companies=[], status="archived")
    session.add(c)
    session.commit()
    task = Task(name="Done task", campaign_id=c.id, status="stopped", sequences={})
    session.add(task)
    session.commit()

    r = client.post(f"/campaigns/{c.id}/status", auth=auth, follow_redirects=False,
                    data={"action": "unarchive", "status": "archived", "page": "1"})
    assert r.status_code == 303
    session.refresh(c)
    session.refresh(task)
    assert c.status == "active" and task.status == "stopped"


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


# --- AI classify route, inline tier edit, and campaign_detail tier filter --


def test_classify_route_redirects_with_summary_msg(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    canned = TierSummary(examined=10, classified=8, per_tier={"A": 3, "B": 4, "C": 1}, skipped=2, errors=1)
    monkeypatch.setattr(admin, "classify_campaign_tiers", lambda *a, **kw: canned)

    r = client.post(f"/campaigns/{c.id}/classify", auth=auth, data={}, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    location = unquote(r.headers["location"])
    assert location.startswith(f"/campaigns/{c.id}?msg=")
    assert "Classified 8/10" in location
    assert "A 3" in location and "B 4" in location and "C 1" in location
    assert "skipped 2" in location and "failed batches 1" in location


def test_enrich_route_surfaces_runtime_error(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    def boom(*a, **kw):
        raise RuntimeError("Apollo /mixed_people/api_search 403: not accessible on a free plan.")

    monkeypatch.setattr(admin, "enrich_campaign", boom)

    r = client.post(f"/campaigns/{c.id}/enrich", auth=auth, data={}, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    location = unquote(r.headers["location"])
    assert "Enrich failed" in location
    assert "free plan" in location


def test_classify_route_surfaces_runtime_error(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    def boom(*a, **kw):
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    monkeypatch.setattr(admin, "classify_campaign_tiers", boom)

    r = client.post(f"/campaigns/{c.id}/classify", auth=auth, data={}, follow_redirects=False)
    assert r.status_code == 303
    assert "ANTHROPIC_API_KEY" in r.headers["location"]


def test_classify_route_404_for_unknown_campaign(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    r = client.post(
        "/campaigns/does-not-exist/classify", auth=("admin", "secret"),
        data={}, follow_redirects=False,
    )
    assert r.status_code == 404


def test_convert_seed_companies_no_seed_companies(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={}, follow_redirects=False,
    )
    assert r.status_code == 303
    from urllib.parse import unquote
    assert "No seed companies to convert." in unquote(r.headers["location"])
    assert session.query(Lead).count() == 0


def test_convert_seed_companies_preview_writes_nothing(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp"},
    ])
    session.add(c)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={}, follow_redirects=False,
    )
    assert r.status_code == 303
    from urllib.parse import unquote
    location = unquote(r.headers["location"])
    assert "Preview: would convert 1 seed company to leads (Toyota)" in location
    assert "Nothing saved" in location
    assert session.query(Lead).count() == 0


def test_convert_seed_companies_preview_caps_company_name_list(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": f"Company {i}", "email": f"c{i}@x.com"} for i in range(7)
    ])
    session.add(c)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={}, follow_redirects=False,
    )
    assert r.status_code == 303
    from urllib.parse import unquote
    location = unquote(r.headers["location"])
    assert "Company 0, Company 1, Company 2, Company 3, Company 4" in location
    assert "and 2 more" in location
    assert "Company 5" not in location
    assert "Company 6" not in location
    assert session.query(Lead).count() == 0


def test_convert_seed_companies_blocks_when_missing_email(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp"},
        {"name": "Sony"},
    ])
    session.add(c)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={}, follow_redirects=False,
    )
    assert r.status_code == 303
    from urllib.parse import unquote
    location = unquote(r.headers["location"])
    assert "Convert failed" in location and "Sony" in location
    assert session.query(Lead).count() == 0


def test_convert_seed_companies_blocks_on_duplicate_email(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp"},
        {"name": "Toyota JP", "email": "JAMIE@toyota.co.jp"},
    ])
    session.add(c)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={}, follow_redirects=False,
    )
    assert r.status_code == 303
    from urllib.parse import unquote
    location = unquote(r.headers["location"])
    assert "duplicate email" in location and "jamie@toyota.co.jp" in location
    assert session.query(Lead).count() == 0


def test_convert_seed_companies_creates_leads(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp", "contact_name": "Jamie Rivera",
         "contact_title": "VP Finance", "country": "JP", "category": "automotive",
         "tier": "A", "angle": "cars", "website": "toyota.co.jp"},
    ])
    session.add(c)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={"confirm": "1"}, follow_redirects=False,
    )
    assert r.status_code == 303
    from urllib.parse import unquote
    assert "Converted 1 seed company to leads." in unquote(r.headers["location"])
    lead = session.query(Lead).one()
    assert lead.email == "jamie@toyota.co.jp"
    assert lead.company == "Toyota"
    assert lead.contact_name == "Jamie Rivera"
    assert lead.contact_title == "VP Finance"
    assert lead.country == "JP"
    assert lead.category == "automotive"
    assert lead.tier == "A"
    assert lead.angle == "cars"
    assert lead.website == "toyota.co.jp"
    assert lead.step == 0 and lead.status == "active"


def test_convert_seed_companies_maps_blank_optional_fields_to_none(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp"},
    ])
    session.add(c)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={"confirm": "1"}, follow_redirects=False,
    )
    assert r.status_code == 303
    lead = session.query(Lead).one()
    assert lead.contact_name is None
    assert lead.contact_title is None
    assert lead.country is None
    assert lead.category is None
    assert lead.tier is None
    assert lead.angle is None
    assert lead.website is None


def test_convert_seed_companies_blocked_when_leads_already_exist(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[
        {"name": "Toyota", "email": "jamie@toyota.co.jp"},
    ])
    session.add(c)
    session.flush()
    session.add(Lead(campaign_id=c.id, email="existing@x.com", company="X", status="active"))
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/from-seed-companies", auth=auth,
        data={}, follow_redirects=False,
    )
    assert r.status_code == 303
    from urllib.parse import unquote
    assert "already has leads" in unquote(r.headers["location"])
    assert session.query(Lead).count() == 1


def test_campaign_page_shows_convert_card_only_when_no_leads(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.commit()

    r = client.get(f"/campaigns/{c.id}", auth=auth)
    assert "Convert seed companies to leads" in r.text

    session.add(Lead(campaign_id=c.id, email="x@y.com", company="X", status="active"))
    session.commit()

    r2 = client.get(f"/campaigns/{c.id}", auth=auth)
    assert "Convert seed companies to leads" not in r2.text


def test_campaign_page_hides_convert_card_on_tier_filter_even_if_that_tier_is_empty(client, session, monkeypatch):
    # The card must gate on the campaign's TOTAL lead count, not the
    # tier-filtered `leads` list — otherwise viewing an empty tier tab on a
    # campaign that already has leads (just none in that tier) would show
    # the card again even though conversion is blocked server-side either way.
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.flush()
    session.add(Lead(campaign_id=c.id, email="x@y.com", company="X", tier="B", status="active"))
    session.commit()

    r = client.get(f"/campaigns/{c.id}?tier=A", auth=auth)
    assert "x@y.com" not in r.text  # tier=A view shows none of this campaign's (tier B) leads...
    assert "Convert seed companies to leads" not in r.text  # ...but the card must still stay hidden


def test_inline_tier_sets_and_clears_value(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.flush()
    lead = Lead(campaign_id=c.id, email="a@b.com", company="X", status="active")
    session.add(lead)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/{lead.id}/tier", auth=auth, data={"tier": "A"},
    )
    assert r.status_code == 200
    assert '<option value="A" selected>A</option>' in r.text
    session.refresh(lead)
    assert lead.tier == "A"

    r2 = client.post(
        f"/campaigns/{c.id}/leads/{lead.id}/tier", auth=auth, data={"tier": ""},
    )
    assert r2.status_code == 200
    assert '<option value="" selected>—</option>' in r2.text
    session.refresh(lead)
    assert lead.tier is None


def test_inline_tier_400_on_bad_value(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.flush()
    lead = Lead(campaign_id=c.id, email="a@b.com", company="X", status="active")
    session.add(lead)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/{lead.id}/tier", auth=auth, data={"tier": "Z"},
    )
    assert r.status_code == 400


def test_inline_tier_404_when_lead_belongs_to_another_campaign(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c1 = Campaign(name="c1", target_titles=[], seed_companies=[])
    c2 = Campaign(name="c2", target_titles=[], seed_companies=[])
    session.add_all([c1, c2])
    session.flush()
    lead = Lead(campaign_id=c1.id, email="a@b.com", company="X", status="active")
    session.add(lead)
    session.commit()

    r = client.post(
        f"/campaigns/{c2.id}/leads/{lead.id}/tier", auth=auth, data={"tier": "A"},
    )
    assert r.status_code == 404


def test_inline_status_pauses_and_resumes_lead(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.flush()
    lead = Lead(campaign_id=c.id, email="a@b.com", company="X", status="active")
    session.add(lead)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/{lead.id}/status", auth=auth, data={"status": "paused"},
    )
    assert r.status_code == 200
    assert "paused" in r.text
    assert "resume" in r.text
    session.refresh(lead)
    assert lead.status == "paused"

    r2 = client.post(
        f"/campaigns/{c.id}/leads/{lead.id}/status", auth=auth, data={"status": "active"},
    )
    assert r2.status_code == 200
    assert "pause" in r2.text
    session.refresh(lead)
    assert lead.status == "active"


def test_inline_status_400_on_bad_value(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.flush()
    lead = Lead(campaign_id=c.id, email="a@b.com", company="X", status="active")
    session.add(lead)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/{lead.id}/status", auth=auth, data={"status": "completed"},
    )
    assert r.status_code == 400


def test_inline_status_400_when_lead_in_system_driven_status(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.flush()
    lead = Lead(campaign_id=c.id, email="a@b.com", company="X", status="bounced")
    session.add(lead)
    session.commit()

    r = client.post(
        f"/campaigns/{c.id}/leads/{lead.id}/status", auth=auth, data={"status": "active"},
    )
    assert r.status_code == 400
    session.refresh(lead)
    assert lead.status == "bounced"


def test_inline_status_404_when_lead_belongs_to_another_campaign(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c1 = Campaign(name="c1", target_titles=[], seed_companies=[])
    c2 = Campaign(name="c2", target_titles=[], seed_companies=[])
    session.add_all([c1, c2])
    session.flush()
    lead = Lead(campaign_id=c1.id, email="a@b.com", company="X", status="active")
    session.add(lead)
    session.commit()

    r = client.post(
        f"/campaigns/{c2.id}/leads/{lead.id}/status", auth=auth, data={"status": "paused"},
    )
    assert r.status_code == 404


def test_campaign_detail_tier_filter_and_counts(client, session, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("admin", "secret")
    c = Campaign(name="c", target_titles=[], seed_companies=[])
    session.add(c)
    session.flush()
    session.add_all([
        Lead(campaign_id=c.id, email="a@x.com", company="X", status="active", tier="A"),
        Lead(campaign_id=c.id, email="b@x.com", company="X", status="active", tier="B"),
        Lead(campaign_id=c.id, email="c1@x.com", company="X", status="active", tier="C"),
        Lead(campaign_id=c.id, email="d@x.com", company="X", status="active", tier=None),
        Lead(campaign_id=c.id, email="e@x.com", company="X", status="active", tier=None),
    ])
    session.commit()

    all_page = client.get(f"/campaigns/{c.id}", auth=auth)
    assert all_page.status_code == 200
    assert "All (5)" in all_page.text
    assert "A (1)" in all_page.text and "B (1)" in all_page.text and "C (1)" in all_page.text
    assert "unclassified (2, sends as B)" in all_page.text
    for email in ("a@x.com", "b@x.com", "c1@x.com", "d@x.com", "e@x.com"):
        assert email in all_page.text

    a_page = client.get(f"/campaigns/{c.id}?tier=A", auth=auth)
    assert "a@x.com" in a_page.text
    for email in ("b@x.com", "c1@x.com", "d@x.com", "e@x.com"):
        assert email not in a_page.text

    unclassified_page = client.get(f"/campaigns/{c.id}?tier=unclassified", auth=auth)
    assert "d@x.com" in unclassified_page.text and "e@x.com" in unclassified_page.text
    for email in ("a@x.com", "b@x.com", "c1@x.com"):
        assert email not in unclassified_page.text
