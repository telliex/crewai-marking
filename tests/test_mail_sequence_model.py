from sqlalchemy.orm import Session

from awkns_outreach.db.models import Campaign, MailSequence, Task


def _make_campaign(db_session: Session) -> Campaign:
    campaign = Campaign(name="Acme Q3", target_titles=[], seed_companies=[])
    db_session.add(campaign)
    db_session.commit()
    return campaign


def test_mail_sequence_round_trip_and_defaults(db_session: Session) -> None:
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

    sequence = MailSequence(name="Q3 Outbound", steps=steps)
    db_session.add(sequence)
    db_session.commit()

    db_session.expire_all()
    fetched = db_session.get(MailSequence, sequence.id)
    assert fetched is not None
    assert fetched.status == "active"
    assert fetched.created_at is not None
    assert fetched.updated_at is not None

    assert len(fetched.steps) == 2
    assert fetched.steps[0]["key"] == "step-1"
    assert fetched.steps[0]["attachments"][0]["filename"] == "deck.pdf"
    assert fetched.steps[0]["source_template_id"] == "tmpl-1"
    assert fetched.steps[1]["source_template_id"] is None


def test_task_round_trip_and_defaults(db_session: Session) -> None:
    campaign = _make_campaign(db_session)
    seq = MailSequence(name="Q3 Outbound", steps=[])
    db_session.add(seq)
    db_session.commit()

    task = Task(
        name="Q3 send", campaign_id=campaign.id, sequences={"A": seq.id, "B": seq.id},
    )
    db_session.add(task)
    db_session.commit()

    db_session.expire_all()
    fetched = db_session.get(Task, task.id)
    assert fetched is not None
    assert fetched.status == "draft"
    assert fetched.scheduled_start_at is None
    assert fetched.end_at is None
    assert fetched.started_at is None
    assert fetched.completed_at is None
    assert fetched.campaign_id == campaign.id
    assert fetched.sequences == {"A": seq.id, "B": seq.id}
    assert fetched.steps_by_tier == {}
    assert fetched.created_at is not None
    assert fetched.updated_at is not None
    assert fetched.campaign.id == campaign.id


def test_task_cascade_deletes_with_campaign(db_session: Session) -> None:
    campaign = _make_campaign(db_session)
    task = Task(name="Q3 send", campaign_id=campaign.id, sequences={})
    db_session.add(task)
    db_session.commit()
    task_id = task.id

    db_session.delete(campaign)
    db_session.commit()

    assert db_session.get(Task, task_id) is None


def test_mail_sequence_is_not_deleted_when_campaign_is_deleted(db_session: Session) -> None:
    """MailSequence is content-only now — it has no campaign_id at all, so
    deleting a Campaign must never cascade to it."""
    campaign = _make_campaign(db_session)
    sequence = MailSequence(name="Q3 Outbound", steps=[])
    db_session.add(sequence)
    db_session.commit()
    sequence_id = sequence.id

    db_session.delete(campaign)
    db_session.commit()

    assert db_session.get(MailSequence, sequence_id) is not None
