"""Reply detection: poll a mailbox, match From address against leads, mark
replied + log a reply Event; idempotent watermark advance + overlap dedupe."""
from datetime import datetime, timedelta, timezone

import httpx
import respx

from awkns_outreach.db.models import Campaign, Event, Lead, Mailbox
from awkns_outreach.gmail.replies import poll_mailbox_replies

_LIST_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
_GET_URL_TPL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/{id}"


def _mailbox(**kw):
    base = dict(
        provider="gmail", email="steven@gmail.com", access_token="valid", refresh_token="rt1",
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1), status="connected",
    )
    base.update(kw)
    return Mailbox(**base)


def _mock_message(message_id: str, from_header: str):
    return respx.get(_GET_URL_TPL.format(id=message_id)).mock(
        return_value=httpx.Response(200, json={
            "id": message_id, "threadId": f"t-{message_id}",
            "payload": {"headers": [{"name": "From", "value": from_header}]},
        })
    )


@respx.mock
def test_reply_matches_lead_marks_replied_and_logs_event(db_session):
    respx.get(_LIST_URL).mock(return_value=httpx.Response(200, json={"messages": [{"id": "m1"}]}))
    _mock_message("m1", "Kenji Tanaka <k@toyota.co.jp>")

    c = Campaign(name="c", target_titles=[], seed_companies=[])
    db_session.add(c)
    db_session.flush()
    lead = Lead(campaign_id=c.id, email="k@toyota.co.jp", company="Toyota", status="active")
    db_session.add(lead)
    mb = _mailbox()
    db_session.add(mb)
    db_session.commit()

    summary = poll_mailbox_replies(db_session, mb)
    assert summary.matched == 1 and summary.considered == 1 and summary.error is None

    db_session.refresh(lead)
    assert lead.status == "replied"
    assert lead.replied_at is not None
    assert lead.next_action_at is None

    events = db_session.query(Event).filter_by(lead_id=lead.id, type="reply").all()
    assert len(events) == 1 and events[0].detail == "m1"
    assert mb.last_poll_at is not None


@respx.mock
def test_repoll_does_not_duplicate_event(db_session):
    respx.get(_LIST_URL).mock(return_value=httpx.Response(200, json={"messages": [{"id": "m1"}]}))
    _mock_message("m1", "Kenji Tanaka <k@toyota.co.jp>")

    c = Campaign(name="c", target_titles=[], seed_companies=[])
    db_session.add(c)
    db_session.flush()
    lead = Lead(campaign_id=c.id, email="k@toyota.co.jp", company="Toyota", status="active")
    db_session.add(lead)
    mb = _mailbox()
    db_session.add(mb)
    db_session.commit()

    poll_mailbox_replies(db_session, mb)
    second = poll_mailbox_replies(db_session, mb)  # overlap window re-scans the same message id
    assert second.matched == 0  # already-replied lead + dup Event, both skip

    events = db_session.query(Event).filter_by(lead_id=lead.id, type="reply").all()
    assert len(events) == 1


@respx.mock
def test_non_lead_sender_ignored(db_session):
    respx.get(_LIST_URL).mock(return_value=httpx.Response(200, json={"messages": [{"id": "m1"}]}))
    _mock_message("m1", "Stranger <stranger@example.com>")

    mb = _mailbox()
    db_session.add(mb)
    db_session.commit()

    summary = poll_mailbox_replies(db_session, mb)
    assert summary.matched == 0 and summary.considered == 1
    assert db_session.query(Event).count() == 0


@respx.mock
def test_watermark_advances_and_overlap_window_used_on_next_poll(db_session):
    list_route = respx.get(_LIST_URL).mock(return_value=httpx.Response(200, json={"messages": []}))
    mb = _mailbox(last_poll_at=None)
    db_session.add(mb)
    db_session.commit()
    assert mb.last_poll_at is None

    poll_mailbox_replies(db_session, mb)
    first_query = list_route.calls[0].request.url.params["q"]
    assert "newer_than:2d" in first_query  # first-ever poll: short backfill, no watermark yet
    watermark_1 = mb.last_poll_at
    assert watermark_1 is not None

    poll_mailbox_replies(db_session, mb)
    second_query = list_route.calls[1].request.url.params["q"]
    assert "after:" in second_query  # subsequent poll: watermark-based query
    assert mb.last_poll_at >= watermark_1
