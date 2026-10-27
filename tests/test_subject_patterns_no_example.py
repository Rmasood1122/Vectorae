"""Subject-line patterns never carry another account's subject text.

`subject_line_patterns` is one aggregate for the whole install (no owner
column), read by every user with playbook access through
GET /playbook/subject-patterns. It used to store an `example`: the first
sent subject line of each pattern, "anonymized" by replacing only
{braces}/[brackets]. A sent subject is already filled in, so a prospect's
name or company from one account reached every other account.

Founder choice (2026-10-27, "Drop the example text"): no example is stored
or returned; the rates stay pooled and the card says so.

What must hold:
  * the nightly extraction keeps no subject text, and writing the patterns
    clears an example stored by the old code;
  * the API returns no `example`, even when an old row still holds one;
  * the prompt helper names no example.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db import models as m
from app.services import subject_intelligence as si

UTC = timezone.utc
SECRET_SUBJECT = "Quick question for Zyntrex, Priyanka?"


@pytest.fixture()
def paid_user(db_session, test_user):
    # playbook_access is off on the free plan (app/core/plans.py).
    test_user.plan = m.PlanTier.STARTER
    db_session.commit()
    return test_user


@pytest.fixture()
def other_accounts_sends(db_session, monkeypatch):
    """Another account sent SECRET_SUBJECT to 3 leads; one replied."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "PLAYBOOK_MIN_SAMPLE", 3)
    other = m.User(email="other@agency.test", email_verified=True)
    db_session.add(other)
    db_session.flush()
    product = m.Product(user_id=other.id, name="P", description="d",
                        type=m.ProductType.PRODUCT)
    db_session.add(product)
    db_session.flush()
    strategy = m.Strategy(product_id=product.id, flow_type=m.FlowType.NO_CLIENTS)
    db_session.add(strategy)
    db_session.flush()
    seq = m.Sequence(strategy_id=strategy.id, channel=m.ChannelType.EMAIL,
                     name="Outbound", status=m.SequenceStatus.ACTIVE)
    db_session.add(seq)
    db_session.flush()
    when = datetime.now(UTC) - timedelta(days=3)
    for n in range(3):
        lead = m.Lead(strategy_id=strategy.id, source="manual", external_id=f"sp{n}",
                      full_name=f"Lead {n}", company="Zyntrex",
                      email=f"lead{n}@zyntrex.test", status=m.LeadStatus.CONTACTED)
        db_session.add(lead)
        db_session.flush()
        msg = m.Message(sequence_id=seq.id, lead_id=lead.id, channel=m.ChannelType.EMAIL,
                        step_no=1, template="t", status=m.MessageStatus.SENT, sent_at=when,
                        scheduled_at=when, body="hello", subject=SECRET_SUBJECT)
        db_session.add(msg)
        db_session.flush()
        db_session.add(m.Outcome(lead_id=lead.id, message_id=msg.id,
                                 event=m.OutcomeEvent.SENT, channel="email", ts=when))
        if n == 0:
            db_session.add(m.Outcome(lead_id=lead.id, message_id=msg.id,
                                     event=m.OutcomeEvent.REPLIED, channel="email", ts=when))
    db_session.commit()


def _old_row_with_example(db_session):
    """A row as the old code stored it: real subject text in `example`."""
    db_session.execute(text(
        "INSERT INTO subject_line_patterns (id, pattern_type, channel, avg_reply_rate, "
        "avg_meeting_rate, sample_size, is_reliable, example, last_updated) "
        "VALUES (:id, 'question', 'email', 0.3, 0.1, 30, :t, :ex, :now)"),
        {"id": str(uuid.uuid4()), "t": True, "ex": SECRET_SUBJECT, "now": datetime.now(UTC)})
    db_session.commit()


def _stored_examples(db_session):
    return [r[0] for r in db_session.execute(
        text("SELECT example FROM subject_line_patterns")).all()]


def test_extraction_keeps_no_subject_text(db_session, other_accounts_sends):
    patterns = si.extract_patterns_from_outcomes(db_session)
    assert "question" in {p.pattern_type for p in patterns}
    for p in patterns:
        assert SECRET_SUBJECT not in repr(p)
        assert "Zyntrex" not in repr(p)


def test_writing_patterns_clears_an_old_example(db_session, other_accounts_sends):
    _old_row_with_example(db_session)
    si.persist_patterns(db_session, si.extract_patterns_from_outcomes(db_session))
    stored = _stored_examples(db_session)
    assert stored, "the nightly write must have produced rows"
    assert all(ex is None for ex in stored), stored


def test_api_returns_no_example_even_from_an_old_row(client, db_session, paid_user):
    _old_row_with_example(db_session)
    resp = client.get("/playbook/subject-patterns?channel=email")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [p["pattern_type"] for p in body] == ["question"]
    assert all("example" not in p for p in body)
    assert "Zyntrex" not in resp.text


def test_prompt_helper_names_no_example():
    pattern = si.SubjectLinePattern(pattern_type="question", avg_reply_rate=0.3,
                                    avg_meeting_rate=0.1, sample_size=30,
                                    is_reliable=True, channel="email")
    context = si.build_phase6_pattern_context([pattern])
    assert "Question" in context
    assert "Example" not in context


class _FailingSession:
    def __init__(self):
        self.rolled_back = False

    def execute(self, *_a, **_k):
        raise RuntimeError("query failed")

    def rollback(self):
        self.rolled_back = True


@pytest.mark.parametrize("call", [
    lambda s: si.extract_patterns_from_outcomes(s),
    lambda s: si.get_top_patterns(s, channel="email"),
])
def test_a_failed_query_rolls_the_session_back(call):
    # Both handlers called `db.rollback()`, an undefined name: the NameError
    # was swallowed and the caller's session stayed in a failed transaction.
    session = _FailingSession()
    assert call(session) == []
    assert session.rolled_back
