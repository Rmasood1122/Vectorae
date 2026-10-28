"""Feature 7 — LIVE calls to Google Calendar and Zoom. Skipped by default.

Each test creates one real object and deletes it. They run only when the
credentials are provided in the environment, so CI and a developer machine
without credentials skip them:

  Google  LEADPILOT_LIVE_GOOGLE_ACCESS_TOKEN   an OAuth access token carrying
                                                calendar.events
  Zoom    LEADPILOT_LIVE_ZOOM_CLIENT_ID, _CLIENT_SECRET, _ACCOUNT_ID
          (optional LEADPILOT_LIVE_ZOOM_HOST_USER)

    LEADPILOT_LIVE_ZOOM_CLIENT_ID=... pytest tests/test_meeting_platforms_live.py -v

Until these have passed once against real accounts, Feature 7 is NOT verified
(FEATURES.md, Part 5).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

from app.integrations import google_meet, zoom

GOOGLE_TOKEN = os.environ.get("LEADPILOT_LIVE_GOOGLE_ACCESS_TOKEN")
ZOOM_CREDS = {
    "client_id": os.environ.get("LEADPILOT_LIVE_ZOOM_CLIENT_ID"),
    "client_secret": os.environ.get("LEADPILOT_LIVE_ZOOM_CLIENT_SECRET"),
    "account_id": os.environ.get("LEADPILOT_LIVE_ZOOM_ACCOUNT_ID"),
    "host_user": os.environ.get("LEADPILOT_LIVE_ZOOM_HOST_USER") or "me",
}


@pytest.mark.skipif(not GOOGLE_TOKEN, reason="LEADPILOT_LIVE_GOOGLE_ACCESS_TOKEN not set")
def test_google_creates_a_meet_event_and_deletes_it():
    class LiveGoogle(google_meet.GoogleMeetAdapter):
        def _auth(self) -> dict:
            return {"headers": {"Authorization": f"Bearer {GOOGLE_TOKEN}"}}

    adapter = LiveGoogle(None, None, redis=fakeredis.FakeRedis(decode_responses=True))
    assert adapter.health_check()
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=7)
    event = adapter.create_event(summary="Vectorae live test (auto-deleted)", description=None,
                                 start_at=start, end_at=start + timedelta(hours=1))
    try:
        assert google_meet.meet_link(event).startswith("https://meet.google.com/")
    finally:
        adapter.delete_event(str(event["id"]))


@pytest.mark.skipif(not all(ZOOM_CREDS[k] for k in ("client_id", "client_secret", "account_id")),
                    reason="LEADPILOT_LIVE_ZOOM_* not set")
def test_zoom_creates_a_meeting_and_deletes_it():
    adapter = zoom.ZoomAdapter(credentials=ZOOM_CREDS,
                               redis=fakeredis.FakeRedis(decode_responses=True))
    start = datetime.now(timezone.utc) + timedelta(days=7)
    meeting = adapter.create_meeting(topic="Vectorae live test (auto-deleted)",
                                     start_at=start, duration_minutes=30)
    try:
        assert str(meeting.get("join_url", "")).startswith("https://")
    finally:
        adapter.delete_meeting(str(meeting["id"]))
