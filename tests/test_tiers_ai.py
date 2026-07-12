"""writer/tiers.py: _parse_response (pure) and classify_campaign_tiers
(injected fake `classify`, no real Anthropic calls)."""
import pytest

from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, Lead
from awkns_outreach.writer.tiers import _parse_response, classify_campaign_tiers

# --- _parse_response -------------------------------------------------------


def test_parse_response_plain_json():
    result = _parse_response('{"1": "A", "2": "B"}', valid_ids={"1", "2"})
    assert result == {"1": "A", "2": "B"}


def test_parse_response_json_fenced():
    text = '```json\n{"1": "A", "2": "C"}\n```'
    result = _parse_response(text, valid_ids={"1", "2"})
    assert result == {"1": "A", "2": "C"}


def test_parse_response_bare_fenced_no_language_tag():
    text = '```\n{"1": "B"}\n```'
    result = _parse_response(text, valid_ids={"1"})
    assert result == {"1": "B"}


def test_parse_response_drops_unknown_ids():
    result = _parse_response('{"1": "A", "unknown": "B"}', valid_ids={"1"})
    assert result == {"1": "A"}


def test_parse_response_drops_invalid_tier_values():
    result = _parse_response('{"1": "A", "2": "Z", "3": "b"}', valid_ids={"1", "2", "3"})
    assert result == {"1": "A"}  # lowercase "b" and unknown "Z" both dropped


def test_parse_response_non_json_raises():
    with pytest.raises(Exception):
        _parse_response("not json at all", valid_ids={"1"})


# --- classify_campaign_tiers -------------------------------------------------


def _make_campaign(session, **kwargs):
    c = Campaign(
        name="Test campaign", target_titles=["VP Sales"], seed_companies=[],
        sequence=[], sender_identity={}, **kwargs,
    )
    session.add(c)
    session.flush()
    return c


def _make_lead(session, campaign, email, *, tier=None):
    lead = Lead(
        campaign_id=campaign.id, email=email, company="Acme", tier=tier,
        status="active",
    )
    session.add(lead)
    return lead


def _fake_classify_all_b(rows, *, campaign_context, **kwargs):
    return {row["id"]: "B" for row in rows}


def test_default_only_touches_null_tier_leads(db_session):
    c = _make_campaign(db_session)
    untouched = _make_lead(db_session, c, "a@x.com", tier="A")
    unclassified = _make_lead(db_session, c, "b@x.com")
    db_session.commit()

    summary = classify_campaign_tiers(db_session, c, classify=_fake_classify_all_b)

    assert summary.examined == 1
    assert untouched.tier == "A"  # never touched
    assert unclassified.tier == "B"


def test_reclassify_all_overwrites_existing_tiers(db_session):
    c = _make_campaign(db_session)
    lead_a = _make_lead(db_session, c, "a@x.com", tier="A")
    lead_none = _make_lead(db_session, c, "b@x.com")
    db_session.commit()

    summary = classify_campaign_tiers(
        db_session, c, reclassify_all=True, classify=_fake_classify_all_b
    )

    assert summary.examined == 2
    assert lead_a.tier == "B"
    assert lead_none.tier == "B"


def test_batch_boundary_respected(db_session):
    c = _make_campaign(db_session)
    for i in range(5):
        _make_lead(db_session, c, f"lead{i}@x.com")
    db_session.commit()

    call_sizes = []

    def fake_classify(rows, *, campaign_context, **kwargs):
        call_sizes.append(len(rows))
        return {row["id"]: "B" for row in rows}

    summary = classify_campaign_tiers(
        db_session, c, batch_size=2, classify=fake_classify
    )

    assert call_sizes == [2, 2, 1]
    assert summary.examined == 5
    assert summary.classified == 5


def test_one_failing_batch_increments_errors_and_others_still_process(db_session):
    c = _make_campaign(db_session)
    for i in range(4):
        _make_lead(db_session, c, f"lead{i}@x.com")
    db_session.commit()

    call_count = {"n": 0}

    def fake_classify(rows, *, campaign_context, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ValueError("boom")
        return {row["id"]: "A" for row in rows}

    summary = classify_campaign_tiers(
        db_session, c, batch_size=2, classify=fake_classify
    )

    assert summary.errors == 1
    assert summary.examined == 4
    assert summary.classified == 2  # only the second batch succeeded
    assert summary.per_tier == {"A": 2}


def test_summary_counts_skipped_for_missing_and_invalid_returns(db_session):
    c = _make_campaign(db_session)
    leads = [_make_lead(db_session, c, f"lead{i}@x.com") for i in range(3)]
    db_session.commit()
    ids = [l.id for l in leads]

    def fake_classify(rows, *, campaign_context, **kwargs):
        # Only classify the first id, and return an invalid tier for a
        # nonexistent id (which is dropped, not counted as classified).
        return {ids[0]: "A", "not-a-real-id": "Z"}

    summary = classify_campaign_tiers(db_session, c, classify=fake_classify)

    assert summary.examined == 3
    assert summary.classified == 1
    assert summary.per_tier == {"A": 1}
    assert summary.skipped == 2  # ids[1] and ids[2] were sent but not returned


def test_missing_api_key_raises_runtime_error(db_session, monkeypatch):
    c = _make_campaign(db_session)
    _make_lead(db_session, c, "a@x.com")
    db_session.commit()
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        classify_campaign_tiers(db_session, c, classify=_fake_classify_all_b)
