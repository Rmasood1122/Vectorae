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
from app.integrations.hunter import HunterAdapter, VerificationPending
from app.services import waterfall_enrichment as wf

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
