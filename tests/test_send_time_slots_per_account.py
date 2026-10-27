"""Send-time slots (GET /playbook/send-times) are per account.

The nightly job (update_send_time_scores) cached one slot list per
(channel, ICP industry, ICP size) for the whole install, so one account's
send hours and reply rates were what every other account was shown for the
same ICP. Founder choice (2026-10-27, "Send-time per account"): each
account gets its own slots, built from its own sends only.

What must hold:
  * the nightly job writes one cache entry per account, from that account's
    sends only;
  * the API returns the caller's slots, never another account's, and falls
    back to the default hours when the caller has no data of their own.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models as m
from app.services import send_time_optimizer as sto

UTC = timezone.utc
MONDAY_10 = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
THURSDAY_15 = datetime(2026, 9, 17, 15, 0, tzinfo=UTC)


@pytest.fixture()
def paid_user(db_session, test_user):
    # playbook_access is off on the free plan (app/core/plans.py).
    test_user.plan = m.PlanTier.STARTER
    db_session.commit()
    return test_user


@pytest.fixture()
def other_user(db_session):
    other = m.User(email="other@agency.test", email_verified=True)
    db_session.add(other)
    db_session.commit()
    return other


def _sends(db, owner, when, n, replies, tag):
    """`n` email sends by `owner` at `when`, the first `replies` replied."""
    product = m.Product(user_id=owner.id, name="P", description="d",
                        type=m.ProductType.PRODUCT)
    db.add(product)
    db.flush()
    strategy = m.Strategy(product_id=product.id, flow_type=m.FlowType.NO_CLIENTS)
    db.add(strategy)
    db.flush()
    seq = m.Sequence(strategy_id=strategy.id, channel=m.ChannelType.EMAIL,
                     name="Outbound", status=m.SequenceStatus.ACTIVE)
    db.add(seq)
    db.flush()
    for i in range(n):
        lead = m.Lead(strategy_id=strategy.id, source="manual", external_id=f"{tag}{i}",
                      full_name=f"Lead {i}", email=f"{tag}{i}@x.test",
                      status=m.LeadStatus.CONTACTED)
        db.add(lead)
        db.flush()
        msg = m.Message(sequence_id=seq.id, lead_id=lead.id, channel=m.ChannelType.EMAIL,
                        step_no=1, template="t", body="hello", status=m.MessageStatus.SENT,
                        sent_at=when)
        db.add(msg)
        db.flush()
        db.add(m.Outcome(lead_id=lead.id, message_id=msg.id, event=m.OutcomeEvent.SENT,
                         channel="email", ts=when))
        if i < replies:
            db.add(m.Outcome(lead_id=lead.id, message_id=msg.id,
                             event=m.OutcomeEvent.REPLIED, channel="email",
                             ts=when + timedelta(hours=3)))
    db.commit()


@pytest.fixture()
def two_accounts(db_session, paid_user, other_user):
    _sends(db_session, paid_user, MONDAY_10, 4, 1, "mine")
    _sends(db_session, other_user, THURSDAY_15, 6, 6, "theirs")
    return paid_user, other_user


def _slots(rec):
    return [(s["day_of_week"], s["hour_utc"], s["expected_reply_rate"])
            for s in rec.recommended_slots]


def test_nightly_job_keeps_each_accounts_slots_apart(db_session, two_accounts):
    mine, theirs = two_accounts
    assert sto.update_send_time_scores(db_session) == 2
    # Sunday=0: Monday is 1, Thursday is 4.
    assert _slots(sto.get_send_time_recommendation(
        "email", None, None, owner_id=mine.id)) == [(1, 10, 0.25)]
    assert _slots(sto.get_send_time_recommendation(
        "email", None, None, owner_id=theirs.id)) == [(4, 15, 1.0)]


def test_api_returns_only_the_callers_slots(client, db_session, two_accounts):
    sto.update_send_time_scores(db_session)
    resp = client.get("/playbook/send-times?channel=gmail")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fallback_used"] is False
    assert [(s["day_of_week"], s["hour_utc"]) for s in body["recommended_slots"]] == [(1, 10)]


def test_caller_without_data_gets_the_defaults(client, db_session, paid_user, other_user):
    _sends(db_session, other_user, THURSDAY_15, 6, 6, "theirs")
    sto.update_send_time_scores(db_session)
    body = client.get("/playbook/send-times?channel=gmail").json()
    assert body["fallback_used"] is True
    assert body["confidence"] == "no_data"
    assert (4, 15) not in [(s["day_of_week"], s["hour_utc"])
                           for s in body["recommended_slots"]]
