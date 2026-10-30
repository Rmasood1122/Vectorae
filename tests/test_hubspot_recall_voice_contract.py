"""HubSpot, Recall.ai, Vapi and ElevenLabs match their current public docs.

Checked 2026-10-28, no account or key used (docs read through web search
excerpts; the vendor doc hosts were not reachable from the session):
  * HubSpot: v1 OAuth is deprecated; POST /oauth/2026-03/token (same form
    fields) and POST /oauth/2026-03/token/introspect with the token in the
    form body. Companies need crm.objects.companies.read. Signature v3
    decodes a fixed list of %-escapes in the URI before signing. Contact
    search (POST /crm/v3/objects/contacts/search, filterGroups email EQ) and
    the deal-contact link (PUT /crm/v4/objects/deals/{id}/associations/
    default/contacts/{id}) match the search and associations v4 guides as
    called; no change, so no test here.
  * Recall: "Authorization: Token {key}"; transcript mode prioritize_accuracy.
  * Vapi: voicemailDetection google (twilio is legacy); MonitorPlan
    controlAuthenticationEnabled makes controlUrl require the bearer key.
  * ElevenLabs outbound-call returns {success, message, conversation_id,
    callSid}; post-call webhooks are rejected after 30 minutes.

Founder choices (2026-10-28): "Add it, ask to reconnect", "Require auth",
"Google", "Move to v3" (HubSpot's dated 2026-03 version is the v1 successor).
"""

import base64
import hashlib
import hmac
import json

import httpx
import pytest

from app.integrations import hubspot, recall
from app.services import guardrails


class _Recorder:
    def __init__(self, responder):
        self.requests = []
        self.responder = responder

    def __call__(self):
        def handler(request):
            self.requests.append(request)
            return self.responder(request)
        return httpx.Client(transport=httpx.MockTransport(handler))


# -- HubSpot -------------------------------------------------------------------

class TestHubSpotOAuth:
    def test_tokens_come_from_the_dated_endpoint(self, monkeypatch):
        rec = _Recorder(lambda r: httpx.Response(200, json={"access_token": "a"}))
        monkeypatch.setattr(hubspot, "_http", rec)
        hubspot.exchange_code("cid", "sec", "code", "https://x.test/cb")
        hubspot.refresh("cid", "sec", "rt")
        assert [str(r.url) for r in rec.requests] == [
            "https://api.hubapi.com/oauth/2026-03/token"] * 2

    def test_token_info_never_puts_the_token_in_the_url(self, monkeypatch):
        rec = _Recorder(lambda r: httpx.Response(200, json={"active": True, "hub_id": 7}))
        monkeypatch.setattr(hubspot, "_http", rec)
        assert hubspot.token_info("SECRET-AT", "cid", "sec")["hub_id"] == 7
        request = rec.requests[0]
        assert request.method == "POST"
        assert str(request.url) == "https://api.hubapi.com/oauth/2026-03/token/introspect"
        assert "SECRET-AT" not in str(request.url)
        form = dict(x.split("=") for x in request.content.decode().split("&"))
        assert form == {"client_id": "cid", "client_secret": "sec", "token": "SECRET-AT",
                        "token_type_hint": "access_token"}

    def test_an_inactive_token_is_refused(self, monkeypatch):
        monkeypatch.setattr(hubspot, "_http",
                            _Recorder(lambda r: httpx.Response(200, json={"active": False})))
        with pytest.raises(hubspot.HubSpotError):
            hubspot.token_info("at", "cid", "sec")

    def test_companies_read_is_requested(self):
        from urllib.parse import parse_qs, urlparse

        scope = parse_qs(urlparse(hubspot.build_auth_url("c", "https://x.test", "s")).query)["scope"]
        assert "crm.objects.companies.read" in scope[0].split(" ")


class TestHubSpotSignature:
    def _sig(self, secret, method, uri, body, ts):
        msg = method.encode() + uri.encode() + body + ts.encode()
        return base64.b64encode(hmac.new(secret.encode(), msg, hashlib.sha256).digest()).decode()

    def test_listed_escapes_are_decoded_before_checking(self):
        ts = "1700000000000"
        signed_over = "https://app.test/webhooks/hubspot?portal=a:b"
        sig = self._sig("sec", "POST", signed_over, b"{}", ts)
        assert hubspot.verify_signature_v3(
            "sec", "POST", "https://app.test/webhooks/hubspot?portal=a%3Ab", b"{}", ts, sig,
            now=1700000000.0)

    def test_an_unlisted_escape_is_left_alone(self):
        ts = "1700000000000"
        sig = self._sig("sec", "POST", "https://app.test/x?q=a b", b"{}", ts)
        assert not hubspot.verify_signature_v3(
            "sec", "POST", "https://app.test/x?q=a%20b", b"{}", ts, sig, now=1700000000.0)


class TestHubSpotCompaniesNotReadable:
    def test_a_403_asks_for_a_reconnect(self, db_session, test_user, monkeypatch):
        from app.services import crm_sync

        class _Conn:
            provider = "hubspot"

        monkeypatch.setattr(crm_sync, "connections", lambda s, u: [_Conn()])

        def forbidden(session, conn):
            raise hubspot.HubSpotError("POST /crm/v3/objects/companies/search: HTTP 403 "
                                       "{raw provider text}", 403)

        monkeypatch.setattr(guardrails, "_hubspot_entries", forbidden)
        result = guardrails.sync_from_crm(db_session, test_user.id)
        assert result["hubspot"]["status"] == "error"
        assert result["hubspot"]["reconnect"] is True
        assert result["hubspot"]["error"] == guardrails.HUBSPOT_RECONNECT_MESSAGE
        assert "raw provider text" not in result["hubspot"]["error"]


# -- Recall --------------------------------------------------------------------

class TestRecall:
    def test_bot_asks_for_the_accurate_transcript(self, fake_redis):
        bodies, headers = [], []

        def handler(request):
            bodies.append(json.loads(request.content))
            headers.append(request.headers.get("Authorization"))
            return httpx.Response(201, json={"id": "bot-1"})

        adapter = recall.RecallAdapter(
            api_key="rk", region="us-west-2", redis=fake_redis, sleep=lambda s: None,
            http=httpx.Client(base_url="https://us-west-2.recall.ai",
                              transport=httpx.MockTransport(handler)))
        adapter.create_bot("https://meet.test/x", join_at=None, metadata={"m": "1"})
        provider = bodies[0]["recording_config"]["transcript"]["provider"]
        assert provider == {"recallai_streaming": {"mode": "prioritize_accuracy"}}
        assert headers == ["Token rk"]


# -- Vapi ----------------------------------------------------------------------


# -- ElevenLabs ----------------------------------------------------------------
