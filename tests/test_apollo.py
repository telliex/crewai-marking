"""Apollo client + enrich: HTTP mocked with respx, DB on in-memory SQLite."""
import httpx
import pytest
import respx

from awkns_outreach.apollo.client import (
    bulk_match,
    domain_from_website,
    has_real_email,
    search_people,
)
from awkns_outreach.apollo.enrich import enrich_campaign
from awkns_outreach.db.models import Campaign, Lead

BASE = "https://api.apollo.io/api/v1"


def test_has_real_email():
    assert has_real_email("k.tanaka@toyota.co.jp")
    assert not has_real_email("kenji@email_not_unlocked@toyota.co.jp")
    assert not has_real_email("x@domain.com")
    assert not has_real_email("")
    assert not has_real_email(None)


def test_domain_from_website():
    assert domain_from_website("https://www.toyota.co.jp") == "toyota.co.jp"
    assert domain_from_website("toyota.co.jp") == "toyota.co.jp"
    assert domain_from_website(None) is None


@respx.mock
def test_search_people_parses_masked():
    respx.post(f"{BASE}/mixed_people/api_search").mock(
        return_value=httpx.Response(
            200,
            json={
                "people": [
                    {
                        "id": "abc123",
                        "name": "Kenji Tanaka",
                        "title": "Creative Director",
                        "email": "kenji@email_not_unlocked@toyota.co.jp",
                        "email_status": "verified",
                        "organization": {"name": "Toyota", "website_url": "https://toyota.co.jp"},
                    }
                ],
                "pagination": {"total_entries": 1},
            },
        )
    )
    people, total = search_people(domains=["toyota.co.jp"], titles=["creative director"])
    assert total == 1
    assert people[0].id == "abc123"
    assert not has_real_email(people[0].email)  # still masked


@respx.mock
def test_bulk_match_unlocks():
    respx.post(f"{BASE}/people/bulk_match").mock(
        return_value=httpx.Response(
            200,
            json={"matches": [{"id": "abc123", "name": "Kenji Tanaka",
                               "email": "k.tanaka@toyota.co.jp", "email_status": "verified",
                               "organization": {"name": "Toyota"}}]},
        )
    )
    matched = bulk_match(["abc123"])
    assert matched[0].email == "k.tanaka@toyota.co.jp"
    assert has_real_email(matched[0].email)


def _campaign(session):
    c = Campaign(name="Test", target_titles=["creative director"],
                 seed_companies=[{"website": "toyota.co.jp"}])
    session.add(c)
    session.flush()
    return c


@respx.mock
def test_enrich_preview_spends_no_credits(db_session):
    search = respx.post(f"{BASE}/mixed_people/api_search").mock(
        return_value=httpx.Response(200, json={
            "people": [{"id": "abc123", "name": "Kenji", "title": "Creative Director",
                        "email": "kenji@email_not_unlocked@toyota.co.jp",
                        "organization": {"name": "Toyota"}}],
            "pagination": {"total_entries": 1}})
    )
    match = respx.post(f"{BASE}/people/bulk_match")
    c = _campaign(db_session)

    summary = enrich_campaign(db_session, c, reveal=False, limit=10)

    assert search.called
    assert not match.called          # preview must NOT unlock
    assert summary.total_found == 1
    assert summary.created == 0
    assert db_session.query(Lead).count() == 0


@respx.mock
def test_enrich_reveal_creates_leads_idempotently(db_session):
    respx.post(f"{BASE}/mixed_people/api_search").mock(
        return_value=httpx.Response(200, json={
            "people": [{"id": "abc123", "title": "Creative Director",
                        "email": "kenji@email_not_unlocked@toyota.co.jp",
                        "organization": {"name": "Toyota"}}],
            "pagination": {"total_entries": 1}})
    )
    respx.post(f"{BASE}/people/bulk_match").mock(
        return_value=httpx.Response(200, json={"matches": [
            {"id": "abc123", "name": "Kenji Tanaka", "email": "K.Tanaka@Toyota.co.jp",
             "title": "Creative Director", "organization": {"name": "Toyota"}}]})
    )
    c = _campaign(db_session)

    s1 = enrich_campaign(db_session, c, reveal=True, limit=10)
    assert s1.unlocked == 1 and s1.created == 1
    lead = db_session.query(Lead).one()
    assert lead.email == "k.tanaka@toyota.co.jp"  # lowercased
    assert lead.status == "active" and lead.step == 0

    s2 = enrich_campaign(db_session, c, reveal=True, limit=10)  # re-run
    assert s2.created == 0 and s2.updated == 1
    assert db_session.query(Lead).count() == 1


@respx.mock
def test_enrich_carries_seed_metadata_and_apollo_overwrites(db_session):
    # Apollo reports a different company name than the seed; Apollo wins.
    respx.post(f"{BASE}/mixed_people/api_search").mock(
        return_value=httpx.Response(200, json={
            "people": [{"id": "p1", "title": "Creative Director",
                        "email": "kenji@email_not_unlocked@toyota.co.jp",
                        "organization": {"name": "Toyota"}}],
            "pagination": {"total_entries": 1}})
    )
    respx.post(f"{BASE}/people/bulk_match").mock(
        return_value=httpx.Response(200, json={"matches": [
            {"id": "p1", "name": "Kenji Tanaka", "email": "k@toyota.co.jp",
             "title": "Creative Director",
             "organization": {"name": "Toyota Motor Corp",
                              "website_url": "https://global.toyota"}}]})
    )
    c = Campaign(
        name="Test", target_titles=["creative director"],
        seed_companies=[{
            "name": "Toyota", "website": "toyota.co.jp", "country": "JP",
            "category": "automotive", "priority": "A", "angle": "seed angle",
        }],
    )
    db_session.add(c)
    db_session.flush()

    enrich_campaign(db_session, c, reveal=True, limit=10)
    lead = db_session.query(Lead).one()
    # Apollo facts overwrite the seed's overlapping fields.
    assert lead.company == "Toyota Motor Corp"
    assert lead.website == "https://global.toyota"
    # Seed-only metadata carries through (Apollo has no priority/angle here).
    assert lead.country == "JP"
    assert lead.category == "automotive"
    assert lead.priority == "A"
    assert lead.angle == "seed angle"


@respx.mock
def test_reenrich_refreshes_facts_but_keeps_existing_angle(db_session):
    respx.post(f"{BASE}/mixed_people/api_search").mock(
        return_value=httpx.Response(200, json={
            "people": [{"id": "p1", "title": "Creative Director",
                        "email": "kenji@email_not_unlocked@toyota.co.jp",
                        "organization": {"name": "Toyota"}}],
            "pagination": {"total_entries": 1}})
    )
    respx.post(f"{BASE}/people/bulk_match").mock(
        return_value=httpx.Response(200, json={"matches": [
            {"id": "p1", "name": "Kenji Tanaka", "email": "k@toyota.co.jp",
             "title": "Head of Content",
             "organization": {"name": "Toyota Motor Corp"}}]})
    )
    c = Campaign(name="Test", target_titles=["creative director"],
                 seed_companies=[{"website": "toyota.co.jp", "angle": "seed angle"}])
    db_session.add(c)
    db_session.flush()

    enrich_campaign(db_session, c, reveal=True, limit=10)
    lead = db_session.query(Lead).one()
    lead.angle = "AI-generated angle"  # simulate the writer having filled it
    db_session.flush()

    s = enrich_campaign(db_session, c, reveal=True, limit=10)
    assert s.updated == 1
    db_session.refresh(lead)
    assert lead.company == "Toyota Motor Corp"      # fact refreshed
    assert lead.contact_title == "Head of Content"  # fact refreshed
    assert lead.angle == "AI-generated angle"       # not clobbered
