"""Live check of the Google Meet and Zoom adapters against the real APIs.

    python scripts/verify_meeting_platforms.py                 # dry run: config only
    python scripts/verify_meeting_platforms.py --zoom --execute
    python scripts/verify_meeting_platforms.py --google --user-email you@example.com --execute

Feature 7. Both adapters are written against current documentation and
exercised by mocked tests, but have never been called live. This script is the
missing step, for whoever holds the credentials.

WHAT --execute DOES
  Google  uses the connected Google account of --user-email (from DATABASE_URL)
          to create a one-hour event 7 days out WITH a Meet link and NO
          attendees, prints the link, then deletes the event (sendUpdates=none).
  Zoom    uses Admin > Integrations > Zoom (or the ZOOM_* env vars) to mint a
          token, create a scheduled meeting 7 days out, print the join URL,
          then delete it.
Without --execute nothing is sent: it reports what is configured.

It reads DATABASE_URL like the app does. Point it at the environment you mean
to verify; the only writes it can cause are an OAuth token refresh on the
Google account row.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.base import SessionLocal  # noqa: E402


def _google(db, email: str, execute: bool) -> bool:
    from app.db.models import GmailAccount, User  # noqa: PLC0415
    from app.integrations import google_meet  # noqa: PLC0415

    user = db.query(User).filter(User.email == email).one_or_none()
    account = (db.query(GmailAccount).filter_by(user_id=user.id).one_or_none()
               if user else None)
    state = google_meet.calendar_scope_state(account)
    print(f"[google] user={email} connected={account is not None} calendar_access={state}")
    if account is None or state == "missing":
        print("[google] FAIL: connect/reconnect Google in Settings first")
        return False
    if not execute:
        print("[google] dry run: pass --execute to create and delete a test event")
        return True

    adapter = google_meet.GoogleMeetAdapter(db, account)
    print(f"[google] health_check={adapter.health_check()}")
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=7)
    event = adapter.create_event(summary="Vectorae adapter verification (auto-deleted)",
                                 description=None, start_at=start,
                                 end_at=start + timedelta(hours=1))
    link = google_meet.meet_link(event)
    print(f"[google] created event {event.get('id')} meet_link={link or '(none)'}")
    if event.get("id"):
        adapter.delete_event(str(event["id"]))
        print("[google] deleted the test event")
    return bool(link)


def _zoom(db, execute: bool) -> bool:
    from app.integrations import zoom  # noqa: PLC0415

    creds = zoom.zoom_credentials(db)
    adapter = zoom.ZoomAdapter(credentials=creds)
    print(f"[zoom] configured={adapter.configured()} host_user={creds.get('host_user')}")
    if not adapter.configured():
        print("[zoom] FAIL: set Admin > Integrations > Zoom (or ZOOM_* env vars)")
        return False
    if not execute:
        print("[zoom] dry run: pass --execute to create and delete a test meeting")
        return True

    print(f"[zoom] health_check={adapter.health_check()}")
    start = datetime.now(timezone.utc) + timedelta(days=7)
    meeting = adapter.create_meeting(topic="Vectorae adapter verification (auto-deleted)",
                                     start_at=start, duration_minutes=30)
    print(f"[zoom] created meeting {meeting.get('id')} join_url={meeting.get('join_url')}")
    if meeting.get("id"):
        adapter.delete_meeting(str(meeting["id"]))
        print("[zoom] deleted the test meeting")
    return bool(meeting.get("join_url"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--google", action="store_true")
    parser.add_argument("--zoom", action="store_true")
    parser.add_argument("--user-email", help="Vectorae user whose Google account to use")
    parser.add_argument("--execute", action="store_true",
                        help="actually create and delete test objects")
    args = parser.parse_args()
    if args.google and not args.user_email:
        parser.error("--google needs --user-email")

    ok = True
    db = SessionLocal()
    try:
        if args.google:
            ok = _google(db, args.user_email, args.execute) and ok
        if args.zoom:
            ok = _zoom(db, args.execute) and ok
        if not (args.google or args.zoom):
            print("nothing selected: pass --google and/or --zoom")
    finally:
        db.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
