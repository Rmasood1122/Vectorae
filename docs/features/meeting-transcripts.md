# Feature 3 — Meeting transcripts from a recording bot

## What it does
In the meeting room, **Record & transcribe** sends a Recall.ai bot to the call.
When the call ends, Recall's `transcript.done` webhook tells Vectorae. The
transcript is pulled into the meeting, and the AI summary is rebuilt from the
transcript **and** the host's live notes.

## Why Recall.ai and not a browser extension
One integration covers Google Meet, Zoom and Teams. An extension would have to
be built, published, installed by every user and kept working against three
meeting UIs that change without notice. With Recall the surface is one API call
and one webhook.

## The summary never waits for the transcript
| Moment | What happens |
|---|---|
| End meeting | The summary is queued immediately from what exists, usually the notes (`summary_source = notes`). If a bot is recording, `transcript_deadline_at` = now + `meeting_transcript_wait_minutes` (admin setting, default 60). |
| `transcript.done` (before or after the deadline) | The transcript replaces any chunk-streamed text, and the summary is regenerated (`summary_source = transcript`). Ticked action items stay ticked. |
| `transcript.failed` | `transcript_status = failed`; the notes-only summary stays. |
| Deadline passes | The 10-minute sweep sets `timed_out`, so the panel stops implying a transcript is coming. A late transcript is still accepted. |

## Security and tenancy
- **Closed by default.** `POST /webhooks/recall` answers 503 until
  `recall.webhook_secret` is set under Admin › Integrations.
- **Svix verification**, as documented by Recall:
  - Headers: `webhook-id`, `webhook-timestamp`, `webhook-signature` (legacy
    `svix-*` also accepted).
  - Algorithm: HMAC-SHA256 over `<id>.<timestamp>.<raw body>`, keyed with the
    base64-decoded `whsec_` secret. The base64 digest is compared in constant
    time against every `v1,` signature.
  - Replay window: timestamps outside ±5 minutes are refused. Recall names no
    tolerance; this is the Svix default.
- **Once per delivery.** `processed_webhooks (recall, webhook-id)`.
- **Never guess a meeting.** A delivery is matched only by
  `meetings.recording_bot_id`, which Vectorae wrote when it created the bot.
  An unknown bot is acknowledged and matched to nothing. A payload whose
  `metadata.meeting_id` names a different meeting is refused.
- **One bot per meeting.** A conditional update claims the meeting before
  Recall is called, so a double click cannot start two bots. A failed call
  releases the claim.
- **Bounded.** Transcript downloads must be HTTPS, no redirects, at most 5 MB.
  Stored transcripts are capped at 250,000 characters. The region is validated
  as a slug before it becomes a hostname carrying the API key.

## Setup
1. Admin › Integrations › Recall.ai: `api_key`, `region` (e.g. `us-east-1`),
   and `webhook_secret`.
2. In the Recall dashboard, point a webhook at `https://<api>/webhooks/recall`
   for transcript events, and copy its verification secret into
   `webhook_secret`.
3. In a meeting with a join link: **Record & transcribe**.

## API
| Endpoint | Purpose |
|---|---|
| `POST /meetings/{id}/recording-bot` | Send a bot (host only; 404 otherwise). 503 not configured, 422 no join URL, 409 meeting over, 502 provider declined |
| `GET /meetings/{id}/recording-bot` | `recording`, `transcript_status`, `transcript_deadline_at`, `summary_source` |
| `POST /webhooks/recall` | `transcript.done` → fetch queued; `transcript.failed` → failed; other events acknowledged and ignored |

Meeting responses also carry `transcript_status`, `transcript_deadline_at` and
`summary_source`.

## Limits
- **Never called live.** No Recall credentials exist in any environment. The
  signing scheme, `transcript.done` payload and transcript JSON format were
  checked against docs.recall.ai on 2026-09-13. The `Authorization: Token`
  header (the quickstart's form; the API reference suggests bearer), the
  create-bot body and the bot-level transcript shortcut carry
  `# TODO: verify`.
- **No recording consent is collected.** A bot records everyone on the call.
  Two-party-consent jurisdictions and GDPR can require every participant's
  consent. The UI tells the host to announce it; Vectorae does not enforce it.
- **A late transcript costs a second model call:** the notes-only summary, then
  the transcript summary.
- **The provider transcript replaces chunk-streamed text** for the same
  meeting; mixing the two would feed the summary every sentence twice.

## Files
`app/integrations/recall.py`, `app/services/meeting_recording.py`,
`app/api/recording.py`, `app/workers/calendar_tasks.py`
(`fetch_meeting_transcript`, `expire_transcript_waits`), migration
`0049_meeting_recording.py`, `frontend/src/components/meetings/RecordingControl.tsx`.
Tests: `tests/test_meeting_recording.py`, `tests/test_meeting_recording_migration.py`.
