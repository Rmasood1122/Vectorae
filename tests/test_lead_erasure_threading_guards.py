"""SEALED legal guard (evals/compliance_testset.yaml): erasing a lead also
erases the Message-ID / References stored on its sent emails (migration 0096).

A References chain can name the prospect's mail host or address, so it is
personal data and goes with the rest of the person's correspondence (GDPR
Art. 17). Moved byte-identically from tests/test_gmail_threading.py (#157) by
the 2026-10-29 gate PR (docs/program/plans/phase5-seal-vendor-guards.md).
"""

# The two-lead world of the (sealed) lead-erasure test, reused read-only.
from tests.test_lead_erasure_correspondence import _erase, _messages, world  # noqa: F401

PARENT = "<CAparent123@mail.gmail.com>"
GRANDPARENT = "<CAfirst001@mail.gmail.com>"


def test_lead_erasure_clears_message_threading(client, db_session, world):  # noqa: F811
    user, jane, john = world
    for lead in (jane, john):
        sent = _messages(db_session, lead.id)[0]
        sent.rfc_message_id, sent.rfc_references = PARENT, GRANDPARENT
    db_session.commit()
    _erase(client, user, jane)
    db_session.expire_all()
    erased = _messages(db_session, jane.id)[0]
    kept = _messages(db_session, john.id)[0]
    assert (erased.rfc_message_id, erased.rfc_references) == (None, None)
    assert kept.rfc_message_id == PARENT
