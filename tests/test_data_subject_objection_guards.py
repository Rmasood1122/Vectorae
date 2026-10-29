"""SEALED legal guard (evals/compliance_testset.yaml): a person who objected to
data processing at Hunter is dropped and suppressed everywhere.

Hunter answers HTTP 451 when "the person asked not to have their personal
data processed" (GDPR Art. 21 objection). Every find/verify path (legacy and
waterfall) must drop the lead and suppress the person, never retry or keep
them. Founder choice (2026-10-27, "Suppress the person").

Moved byte-identically from tests/test_hunter_api_contract.py (#149) by the
2026-10-29 gate PR (docs/program/plans/phase5-seal-vendor-guards.md).
"""

import httpx
import pytest

from app.db import models as m
from app.integrations.hunter import DataSubjectOptedOut, HunterAdapter
from app.services import waterfall_enrichment as wf
from app.workers.lead_tasks import (
    enrich_leads_impl,
    find_missing_emails_impl,
    is_suppressed,
    source_leads_impl,
    verify_emails_impl,
)
from tests.conftest import make_raw


def _hunter(fake_redis, handler):
    return HunterAdapter(api_key="k", redis=fake_redis, sleep=lambda s: None,
                         http=httpx.Client(base_url="https://api.hunter.io/v2",
                                           transport=httpx.MockTransport(handler)))


class TestObjection:
    def test_451_is_a_data_subject_objection(self, fake_redis):
        handler = lambda r: httpx.Response(451, json={"errors": [{"id": "x"}]})
        with pytest.raises(DataSubjectOptedOut):
            _hunter(fake_redis, handler).verify("a@b.test")
        with pytest.raises(DataSubjectOptedOut):
            _hunter(fake_redis, handler).find_email("Jane Doe", "acme.test")


class _Objecting:
    """A verifier that reports a 451 for one address or one person."""

    provider = "fakeverifier"

    def __init__(self, inner, email=None, name=None):
        self.inner, self.email, self.name = inner, email, name

    def find_email(self, full_name, domain):
        if full_name == self.name:
            raise DataSubjectOptedOut("451")
        return self.inner.find_email(full_name, domain)

    def verify(self, email):
        if email == self.email:
            raise DataSubjectOptedOut("451")
        return self.inner.verify(email)

    def health_check(self):
        return True


@pytest.fixture(params=[False, True], ids=["legacy", "waterfall"])
def waterfall(request, monkeypatch):
    """Both the legacy find/verify stages and the F01 waterfall stages."""
    monkeypatch.setattr(wf, "enabled", lambda session: request.param)
    return request.param


@pytest.mark.usefixtures("waterfall")
class TestPipeline:
    def _enriched(self, db, batch, source, feed):
        source.feed = feed
        source_leads_impl(db, batch.id, source=source)
        enrich_leads_impl(db, batch.id, source=source)

    def _lead(self, db, batch, external_id):
        return db.query(m.Lead).filter_by(batch_id=batch.id, external_id=external_id).one()

    def test_verify_objection_drops_and_suppresses(self, db_session, lead_batch, fake_source,
                                                   fake_verifier):
        self._enriched(db_session, lead_batch, fake_source,
                       [make_raw(1, email="keep@a1.test"), make_raw(2, email="objects@a2.test")])
        verifier = _Objecting(fake_verifier, email="objects@a2.test")
        # The waterfall verifies inside its find stage, the legacy path in the
        # verify stage: the objection is caught in whichever runs it.
        found = find_missing_emails_impl(db_session, lead_batch.id, verifier=verifier)
        counts = verify_emails_impl(db_session, lead_batch.id, verifier=verifier)
        assert found["dropped"] + counts["dropped"] == 1 and counts["verified"] == 1
        lead = self._lead(db_session, lead_batch, "p2")
        assert lead.status is m.LeadStatus.DROPPED
        assert lead.enrichment_json["dropped_reason"] == "opted_out_at_data_provider"
        assert is_suppressed(db_session, email="objects@a2.test")
        assert not is_suppressed(db_session, email="keep@a1.test")

    def test_finder_objection_drops_the_lead(self, db_session, lead_batch, fake_source,
                                             fake_verifier):
        self._enriched(db_session, lead_batch, fake_source,
                       [make_raw(3, phone="+14155550123", domain="a3.test")])
        verifier = _Objecting(fake_verifier, name="Person 3")
        counts = find_missing_emails_impl(db_session, lead_batch.id, verifier=verifier)
        assert counts["dropped"] == 1
        lead = self._lead(db_session, lead_batch, "p3")
        assert lead.status is m.LeadStatus.DROPPED
        assert lead.enrichment_json["dropped_reason"] == "opted_out_at_data_provider"
        assert is_suppressed(db_session, phone="+14155550123")
