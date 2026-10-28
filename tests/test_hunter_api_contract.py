"""The Hunter adapter matches Hunter's current API docs, and a person who
objected to data processing at Hunter is never pursued.

Checked against hunter.io/api-documentation/v2 on 2026-10-27:
  * email-verifier: `result` (deliverable / risky / undeliverable) is
    deprecated; use `status`: valid, invalid, accept_all, webmail,
    disposable, unknown;
  * 202: verification still in progress, call again; 222: the remote SMTP
    server failed, retry later;
  * 451: the person asked not to have their personal data processed;
  * the key may be sent in the X-API-KEY header.

Founder choices (2026-10-27): webmail is risky (flagged, never auto-verified);
a 451 drops the lead and suppresses the person.
"""

import httpx
import pytest

from app.db import models as m
from app.integrations.base import EmailVerificationStatus
from app.integrations.hunter import DataSubjectOptedOut, HunterAdapter, VerificationPending
from app.services import waterfall_enrichment as wf
from app.workers.lead_tasks import (
    enrich_leads_impl,
    find_missing_emails_impl,
    is_suppressed,
    source_leads_impl,
    verify_emails_impl,
)
from tests.conftest import make_raw

S = EmailVerificationStatus


def _hunter(fake_redis, handler):
    return HunterAdapter(api_key="k", redis=fake_redis, sleep=lambda s: None,
                         http=httpx.Client(base_url="https://api.hunter.io/v2",
                                           transport=httpx.MockTransport(handler)))


def _answer(**data):
    return lambda request: httpx.Response(200, json={"data": {"email": "a@b.test", **data}})


class TestStatusField:
    @pytest.mark.parametrize("status, expected", [
        ("valid", S.DELIVERABLE), ("invalid", S.UNDELIVERABLE),
        ("disposable", S.UNDELIVERABLE), ("accept_all", S.RISKY),
        ("webmail", S.RISKY), ("unknown", S.UNKNOWN)])
    def test_status_is_read(self, fake_redis, status, expected):
        assert _hunter(fake_redis, _answer(status=status)).verify("a@b.test").status is expected

    def test_status_wins_over_the_deprecated_result(self, fake_redis):
        result = _hunter(fake_redis, _answer(status="webmail", result="deliverable")).verify("a@b.test")
        assert result.status is S.RISKY

    def test_result_alone_still_works(self, fake_redis):
        assert _hunter(fake_redis, _answer(result="deliverable")).verify("a@b.test").status \
            is S.DELIVERABLE

    def test_neither_field_fails_closed(self, fake_redis):
        assert _hunter(fake_redis, _answer(score=99)).verify("a@b.test").status is S.UNKNOWN

    def test_webmail_is_flagged_in_the_waterfall(self, fake_redis):
        result = _hunter(fake_redis, _answer(status="webmail", result="deliverable", score=50)
                         ).verify("a@b.test")
        verdict = wf.classify(result)
        assert verdict == wf.UNKNOWN
        assert wf.LEAD_STATUS_FOR[verdict] is m.LeadStatus.FLAGGED


class TestNotFinalAnswers:
    @pytest.mark.parametrize("code", [202, 222])
    def test_is_raised_and_never_cached(self, fake_redis, code):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(code, json={"data": {}})

        adapter = _hunter(fake_redis, handler)
        for _ in range(2):
            with pytest.raises(VerificationPending):
                adapter.verify("a@b.test")
        assert len(calls) == 2  # the second call was not served from cache

    def test_a_final_answer_is_cached(self, fake_redis):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": {"status": "valid"}})

        adapter = _hunter(fake_redis, handler)
        adapter.verify("a@b.test")
        adapter.verify("a@b.test")
        assert len(calls) == 1


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
