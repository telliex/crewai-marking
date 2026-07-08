"""Writer backfill: fills Lead.angle using an injected (stub) generator, so the
LLM/CrewAI is never invoked in tests."""
from awkns_outreach.db.models import Campaign, Lead
from awkns_outreach.writer.angle import backfill_campaign_angles


def _campaign(session):
    c = Campaign(name="c", target_titles=[], seed_companies=[], angle_prompt="do X for {company}")
    session.add(c)
    session.flush()
    return c


def test_backfill_fills_only_missing_angles(db_session):
    c = _campaign(db_session)
    a = Lead(campaign_id=c.id, email="a@x.com", company="Acme", status="active")
    b = Lead(campaign_id=c.id, email="b@x.com", company="Beta", status="active", angle="already")
    db_session.add_all([a, b])
    db_session.commit()

    calls = []

    def stub(inputs, prompt, model):
        calls.append(inputs["company"])
        return f"angle for {inputs['company']}"

    n = backfill_campaign_angles(db_session, c, generate=stub)

    assert n == 1
    assert calls == ["Acme"]           # only the lead missing an angle
    db_session.refresh(a)
    db_session.refresh(b)
    assert a.angle == "angle for Acme"
    assert b.angle == "already"        # untouched


def test_backfill_survives_generator_error(db_session):
    c = _campaign(db_session)
    lead = Lead(campaign_id=c.id, email="a@x.com", company="Acme", status="active")
    db_session.add(lead)
    db_session.commit()

    def boom(inputs, prompt, model):
        raise RuntimeError("LLM down")

    n = backfill_campaign_angles(db_session, c, generate=boom)
    assert n == 0
    db_session.refresh(lead)
    assert lead.angle in (None, "")    # left empty; mailer will use a fallback
