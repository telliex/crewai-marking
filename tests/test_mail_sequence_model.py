from sqlalchemy.orm import Session

from awkns_outreach.db.models import Campaign, MailSequence


def _make_campaign(db_session: Session) -> Campaign:
    campaign = Campaign(name="Acme Q3", target_titles=[], seed_companies=[])
    db_session.add(campaign)
    db_session.commit()
    return campaign


def test_mail_sequence_round_trip_and_defaults(db_session: Session) -> None:
    campaign = _make_campaign(db_session)

    steps = [
        {
            "key": "step-1",
            "delay_days": 0,
            "subject": "Hi {{company}}",
            "body": "<p>Hello</p>",
            "attachments": [
                {
                    "filename": "deck.pdf",
                    "stored_name": "abc123.pdf",
                    "content_type": "application/pdf",
                    "size": 1024,
                }
            ],
            "source_template_id": "tmpl-1",
        },
        {
            "key": "step-2",
            "delay_days": 3,
            "subject": "Following up",
            "body": "<p>Bump</p>",
            "attachments": [],
            "source_template_id": None,
        },
    ]

    sequence = MailSequence(name="Q3 Outbound", campaign_id=campaign.id, steps=steps)
    db_session.add(sequence)
    db_session.commit()

    db_session.expire_all()
    fetched = db_session.get(MailSequence, sequence.id)
    assert fetched is not None
    assert fetched.status == "draft"
    assert fetched.scheduled_start_at is None
    assert fetched.started_at is None
    assert fetched.completed_at is None
    assert fetched.campaign_id == campaign.id
    assert fetched.created_at is not None
    assert fetched.updated_at is not None

    assert len(fetched.steps) == 2
    assert fetched.steps[0]["key"] == "step-1"
    assert fetched.steps[0]["attachments"][0]["filename"] == "deck.pdf"
    assert fetched.steps[0]["source_template_id"] == "tmpl-1"
    assert fetched.steps[1]["source_template_id"] is None

    assert fetched.campaign.id == campaign.id


def test_mail_sequence_cascade_deletes_with_campaign(db_session: Session) -> None:
    campaign = _make_campaign(db_session)
    sequence = MailSequence(name="Q3 Outbound", campaign_id=campaign.id, steps=[])
    db_session.add(sequence)
    db_session.commit()
    sequence_id = sequence.id

    db_session.delete(campaign)
    db_session.commit()

    assert db_session.get(MailSequence, sequence_id) is None
