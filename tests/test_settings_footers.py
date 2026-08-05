"""Settings → Footer web: footer library CRUD and the immutable default guard."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from awkns_outreach.config import settings
from awkns_outreach.db.models import FooterTemplate
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


def _default(session):
    f = FooterTemplate(
        name="Default (Pounds Network)", is_default=True,
        body_html="<div>Pounds Network · {unsubscribe_url}</div>",
        body_text="Pounds Network · {unsubscribe_url}",
    )
    session.add(f)
    session.commit()
    return f


def test_list_renders_default_footer(client, session):
    _default(session)
    r = client.get("/settings/footers", auth=AUTH)
    assert r.status_code == 200
    assert "Default (Pounds Network)" in r.text
    # The preview pane substitutes {unsubscribe_url} with a real link.
    assert "/outreach/unsubscribe?token=" in r.text


def test_create_and_edit_custom_footer(client, session):
    r = client.post("/settings/footers", auth=AUTH, follow_redirects=False, data={
        "name": "Promo", "body_html": "<div>Promo {unsubscribe_url}</div>",
        "body_text": "Promo {unsubscribe_url}",
    })
    assert r.status_code == 303
    f = session.scalars(select(FooterTemplate).where(FooterTemplate.name == "Promo")).one()
    assert not f.is_default

    r2 = client.post(f"/settings/footers/{f.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "Promo v2",
        "body_html": "<div>v2 {unsubscribe_url}</div>", "body_text": "v2 {unsubscribe_url}",
    })
    assert r2.status_code == 303
    session.expire_all()
    assert session.get(FooterTemplate, f.id).name == "Promo v2"


def test_default_footer_is_immutable(client, session):
    d = _default(session)
    # GET edit redirects away instead of rendering an editor.
    r = client.get(f"/settings/footers/{d.id}/edit", auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    # POST save is rejected and leaves the row unchanged.
    r2 = client.post(f"/settings/footers/{d.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "save", "name": "hacked", "body_html": "x", "body_text": "x",
    })
    assert r2.status_code == 303
    session.expire_all()
    assert session.get(FooterTemplate, d.id).name == "Default (Pounds Network)"


def test_delete_custom_footer(client, session):
    r = client.post("/settings/footers", auth=AUTH, follow_redirects=False, data={
        "name": "Temp", "body_html": "x", "body_text": "x",
    })
    f = session.scalars(select(FooterTemplate).where(FooterTemplate.name == "Temp")).one()
    r2 = client.post(f"/settings/footers/{f.id}/edit", auth=AUTH, follow_redirects=False, data={
        "action": "delete",
    })
    assert r2.status_code == 303
    session.expunge_all()
    assert session.get(FooterTemplate, f.id) is None


def test_footer_layout_column_persists(session):
    from awkns_outreach.db.models import FooterTemplate
    f = FooterTemplate(name="L", body_html="x", body_text="x", is_default=False,
                       layout={"rows": [{"columns": [{"blocks": []}]}]})
    session.add(f)
    session.commit()
    session.refresh(f)
    assert f.layout == {"rows": [{"columns": [{"blocks": []}]}]}
