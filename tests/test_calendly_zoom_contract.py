"""Calendly and Zoom match their current public docs.

Checked on 2026-10-27 (no account used):
  * Calendly event_types: `count` defaults to 20, paged by
    pagination.next_page_token / page_token; webhook signature
    "t=<ts>,v1=<hex HMAC-SHA256 of '<t>.<body>'>".
  * Zoom meetings: userId is "the user's user ID or email address. For
    user-level apps, pass the `me` value" -- a Server-to-Server app is
    account-level; duration 1-1440 minutes.

Founder choice (2026-10-27, "Require a host email"): no default Zoom host.
"""

import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.integrations import calendly, zoom

START = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)
CREDS = {"client_id": "cid", "client_secret": "sec", "account_id": "acc"}


def _zoom(creds, api_handler, fake_redis):
    return zoom.ZoomAdapter(
        credentials=creds, redis=fake_redis, sleep=lambda s: None,
        http=httpx.Client(base_url=zoom.ZOOM_API_BASE,
                          transport=httpx.MockTransport(api_handler)),
        token_http=httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"access_token": "zt"}))))


class TestZoomHost:
    def test_no_host_is_configured_by_default(self, db_session):
        assert zoom.zoom_credentials(db_session)["host_user"] == ""

    def test_no_host_refuses_before_any_call(self, fake_redis):
        calls = []
        adapter = _zoom(CREDS, lambda r: calls.append(r) or httpx.Response(201, json={}),
                        fake_redis)
        with pytest.raises(zoom.ZoomNotConfigured) as caught:
            adapter.create_meeting(topic="x", start_at=START, duration_minutes=30)
        assert "host_user" in str(caught.value)
        assert caught.value.action == "configure_zoom"
        assert calls == []

    def test_no_host_is_unhealthy(self, fake_redis):
        adapter = _zoom(CREDS, lambda r: httpx.Response(200, json={}), fake_redis)
        assert adapter.health_check() is False

    def test_a_configured_host_is_used(self, fake_redis):
        paths = []

        def handler(request):
            paths.append(request.url.path)
            return httpx.Response(201, json={"id": 1, "join_url": "https://zoom.us/j/1"})

        _zoom({**CREDS, "host_user": "host@x.test"}, handler, fake_redis).create_meeting(
            topic="x", start_at=START, duration_minutes=30)
        assert paths == ["/v2/users/host@x.test/meetings"]

    def test_duration_is_capped_at_1440(self, fake_redis):
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(201, json={"id": 1})

        _zoom({**CREDS, "host_user": "h@x.test"}, handler, fake_redis).create_meeting(
            topic="x", start_at=START, duration_minutes=5000)
        assert bodies[0]["duration"] == 1440


class TestCalendlyEventTypes:
    def test_every_page_is_read(self, fake_redis):
        seen = []

        def handler(request):
            if request.url.path.endswith("/users/me"):
                return httpx.Response(200, json={"resource": {"uri": "https://api.calendly.com/users/U"}})
            params = dict(request.url.params)
            seen.append(params)
            if "page_token" not in params:
                return httpx.Response(200, json={
                    "collection": [{"name": "A", "uri": "a"}],
                    "pagination": {"next_page_token": "T2"}})
            return httpx.Response(200, json={"collection": [{"name": "B", "uri": "b"}],
                                             "pagination": {"next_page_token": None}})

        adapter = calendly.CalendlyAdapter(
            api_token="t", redis=fake_redis, sleep=lambda s: None,
            http=httpx.Client(base_url=calendly.CALENDLY_API_BASE,
                              transport=httpx.MockTransport(handler)))
        assert [e["name"] for e in adapter.event_types()] == ["A", "B"]
        assert seen[0]["count"] == "100" and seen[1]["page_token"] == "T2"


class TestCalendlySignature:
    def _header(self, key, t, body: bytes):
        sig = hmac.new(key.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
        return f"t={t},v1={sig}"

    def test_a_valid_signature_verifies(self):
        body = b'{"event": "invitee.created"}'
        header = self._header("k", 1_000, body)
        assert calendly.verify_webhook_signature(body, header, "k", now=1_010,
                                                 tolerance_seconds=180)

    def test_a_non_utf8_body_is_refused_not_a_crash(self):
        body = b"\xff\xfe not utf-8"
        header = self._header("k", 1_000, b"something else")
        assert calendly.verify_webhook_signature(body, header, "k", now=1_010,
                                                 tolerance_seconds=180) is False

    def test_a_non_utf8_body_signed_correctly_verifies(self):
        body = b"\xff\xfe"
        header = self._header("k", 1_000, body)
        assert calendly.verify_webhook_signature(body, header, "k", now=1_010,
                                                 tolerance_seconds=180)
