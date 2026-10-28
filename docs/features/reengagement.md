# Feature 5 — Opt-in post-sequence re-engagement

## What it does
When a campaign turns it on, Vectorae sends **one** email to each lead who
finished that campaign's sequence without replying, unsubscribing or bouncing,
once a delay has passed. It is off for every campaign until an owner or manager
turns it on in **Campaigns › Re-engagement**.

This was deliberately unbuilt before (FEATURES.md Part 5): sweeping completed
sequences is an unbounded mass-send. The opt-in, the caps and the
once-per-enrollment rule are the design, not settings added afterwards.

## Who qualifies
All of these, checked by the hourly sweep and again when the task runs:

| Rule | Why |
|---|---|
| Enrollment `completed`, never re-engaged, nothing pending | An active enrollment belongs to the sequence engine and follow-up sweep |
| Lead status still `contacted` | Replied, booked, closed, dropped and disqualified leads are not neutral |
| No reply / booking / win / loss since this sequence first sent | A reply to a campaign two years ago does not disqualify |
| Never unsubscribed, opted out or bounced; not suppressed | On any campaign, ever |
| Not archived by the conversion gate | The system already judged the lead dead |
| Last send was **email**, at least `delay_days` ago | See Limits |

## Limits and caps
| Setting | Default | Where |
|---|---|---|
| `reengagement_enabled` | off | per campaign |
| `reengagement_delay_days` | 30 | per campaign, never below `reengagement_min_delay_days` (14) |
| `reengagement_daily_cap` | 10 | per campaign, never above `reengagement_daily_cap_ceiling` (25) |
| `reengagement_weekly_cap` | 25 (rolling 7 days) | per campaign, never above `reengagement_weekly_cap_ceiling` (100) |
| `reengagement_allowed` | on | Admin › System Settings — the deployment kill switch |

The API refuses values outside the ceilings (422). If an admin lowers a
ceiling later, it is applied at every check without rewriting stored campaign
values. A re-engagement send **also counts against the channel's ordinary daily
cap**.

## Guarantees
- **At most once per enrollment.** `reengagement_attempts` has
  `UNIQUE (enrollment_id)`, and the attempt row is committed before any
  message exists. A retried task, overlapping sweeps or a crash after the
  claim cannot produce a second message.
- **No bypass.** The message goes through `send_message_impl`: suppression,
  campaign state, enrollment state, the conversion-probability gate, the send
  window, the daily cap. On top of those, for `messages.origin =
  'reengagement'` only, it re-checks:
  - the switch turned off since scheduling → **cancelled**
  - the lead engaged since scheduling → **cancelled**
  - the re-engagement cap is exhausted → **deferred to the next day's window,
    never dropped**
- **Tenant-scoped.** Candidates are found per campaign through both
  `leads.strategy_id` and `sequences.strategy_id`. An enrollment whose lead and
  sequence belong to different campaigns is refused (`tenant_mismatch`).
- **No invented facts.** The brief prompt carries the same rules as the
  follow-up brief (no invented triggers, connections, statistics, deadlines or
  names; no guilt or urgency) and tells the writer to make "not now" easy. A
  failed model call falls back to a fixed brief.
- **Audit.** The SENT outcome carries `meta_json.source = "reengagement"`.

## API
- `GET /strategies/{id}/reengagement` — settings, effective limits, ceilings,
  sent today and this week, and `eligible_now`. That count is computed as if the
  feature were on, before the send-time conversion gate, so it is an upper bound.
- `PUT /strategies/{id}/reengagement` `{enabled?, delay_days?, daily_cap?,
  weekly_cap?}` — owners and managers only (403 otherwise). 409 when the
  deployment kill switch is off. 404 for a campaign that is not yours.

## Limits
- **Email only.** WhatsApp needs an approved template, and there is no step to
  name one. LinkedIn's `auto` action would send a second connection request. An
  unprompted AI call raises TCPA consent questions.
- **The conversion gate holds many of these leads.** A lead with
  `conversion_min_unanswered_sends` or more unanswered touches is often judged
  cold. The send ends `held_cooling` or `held_archived`. That is intended.
- **The attempt row records the first result.** A message deferred by a cap and
  sent later by the dispatcher leaves its attempt at `held`. The message row is
  the record of what went out.

## Files
`app/services/reengagement.py`, `app/api/reengagement.py`,
`app/workers/outreach_tasks.py` (sweep, send, send-time hold),
`app/services/message_personalization.py` (`build_reengagement_brief`),
`alembic/versions/0048_reengagement.py`,
`frontend/src/components/campaigns/ReengagementPanel.tsx`.
Tests: `tests/test_reengagement.py`, `tests/test_reengagement_migration.py`.
