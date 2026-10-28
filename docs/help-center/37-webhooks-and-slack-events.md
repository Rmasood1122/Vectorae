---
id: webhooks-and-slack-events
title: Webhook events, signatures and Slack alerts
audience: customer
last_verified: 2026-10-18
owner: help-center
sources:
  - app/services/webhook_delivery.py
  - app/api/webhook_targets.py
  - app/workers/webhook_tasks.py
  - app/services/event_bus.py
  - app/integrations/slack.py
  - app/workers/outreach_tasks.py
  - app/workers/lead_tasks.py
  - app/workers/calendar_tasks.py
  - app/api/webhooks.py
  - app/services/campaign_control.py
  - app/services/sequence_engine.py
  - app/services/phone_calls.py
  - app/services/strategy_mutation.py
  - app/services/meeting_followup.py
  - app/services/reply_sentiment.py
  - app/services/deliverability.py
  - app/services/mailbox_health.py
  - app/services/send_review.py
  - app/services/reengagement_memory.py
  - app/api/crm.py
  - frontend/src/components/settings/WebhooksCard.tsx
  - frontend/src/components/settings/SlackCard.tsx
  - docs/program/help-center-discoveries.md
  - docs/program/fixes/send-path-gaps.md
---

# Webhook events, signatures and Slack alerts

This is the technical reference for Vectorae's outbound webhooks and its Slack alerts: every event you can subscribe to, what each delivery contains, how to check its signature, how retries work, and which events reach Slack. Adding a webhook or connecting Slack is covered in [CRM and integrations](16-crm-and-integrations.md).

## Which events can a webhook subscribe to?

These are the events offered under **Events** when you click **Add webhook**:

| Event | Sent when |
|---|---|
| `meeting_booked` | A lead books a meeting (Calendly or the Vectorae calendar) |
| `reply_received` | A lead replies on any channel |
| `reply_interested` | A reply, or an AI phone call, is classified as interested |
| `lead_sourced` | A sourcing batch finishes with at least one verified lead |
| `campaign_paused` | A campaign is paused, automatically for bounce rate or by you |
| `strategy_mutated` | An idle campaign's strategy is rewritten |
| `deal_won` | You log a meeting outcome as closed won |
| `call_completed` | An AI call ends and has been analysed |
| `objection_spike` | A campaign's objection rate jumps week over week |
| `domain_blacklisted` | A sending domain is newly listed on a blocklist |
| `deliverability_warning` | A domain's health drops below the threshold, or a mailbox's spam-rate brake engages |
| `mailbox_paused` | A sending mailbox is paused for poor health |
| `send_needs_review` | A message is held in the review queue |
| `reengagement_due` | A "not now" prospect's return date arrives |
| `reply_draft_approved` | A suggested reply to a prospect is approved |

The last six used to be impossible to subscribe to, or never fired; that has been fixed. Through the API you may also use `lead_replied`, which is treated as `reply_received`. Each webhook can subscribe to up to 20 events, and an account can have up to 25 active webhooks.

## What does each delivery contain?

Every delivery is a `POST` with a JSON body:

```json
{"id": "<delivery id>", "event": "reply_received",
 "occurred_at": "<ISO 8601 time>", "data": { ... }}
```

Headers: `X-LeadPilot-Event` (the event name), `X-LeadPilot-Delivery` (the delivery id, the same on every retry, so use it to ignore duplicates), `X-LeadPilot-Signature`, and an older `X-ClientHunter-Signature` kept for existing integrations.

A "lead" object has `lead_id`, `strategy_id`, `full_name`, `email`, `company`, `title`, `phone`, `linkedin_url` and `status`. What `data` holds:

- `meeting_booked`: `lead`, `source` (`calendly` or `leadpilot_calendar`), and `calendly_event` or `booking_id`.
- `reply_received` and `reply_interested`: `lead`, `channel`, `classification`. An interested AI phone call instead sends `lead_id`, `call_id`, `channel` (`phone`) and `classification`.
- `lead_sourced`: `batch_id`, `strategy_id`, `counts`, and `leads` (the batch's verified leads, up to 200).
- `campaign_paused`: `strategy_id`, `trigger` (`bounce_rate` or `manual`), `reason`, plus `bounce_rate` for an automatic pause.
- `strategy_mutated`: `strategy_id`, `version_id`, `version_no`, `trigger`, `changes`.
- `deal_won`: `lead_id`, `deal_id`, `value_cents`, `currency`.
- `call_completed`: `call_id`, `lead_id`, `outcome`, `duration_seconds`.
- `objection_spike`: `strategy_id`, `week_start`, `objection_rate`, `previous_objection_rate`, `replies`.
- `domain_blacklisted`: `domain`, `listed_on`, `campaigns_paused`.
- `deliverability_warning`: `domain`, `score`, `reasons`; from a mailbox brake also `mailbox_ref` and `complaint_source`.
- `mailbox_paused`: `mailbox_ref`, `address`, `score`, `reasons`.
- `send_needs_review`: `review_id`, `lead_id`, `triggers`.
- `reengagement_due`: `plan_id`, `lead_id`, `reason_kind`, `status`.
- `reply_draft_approved`: `reply_id`, `lead_id`, `category`, `next_action`, `draft_response`.

**Send test** queues a sample of one of the webhook's events with `"test": true` in `data`. The samples for `meeting_booked`, `deal_won` and `strategy_mutated` (no `version_id` or `changes`) do not match the live payloads exactly, so build on the fields listed above.

## How do I confirm a delivery really came from Vectorae?

Use the signing secret shown once when you added the webhook (it starts with `whsec_`). The header looks like:

`X-LeadPilot-Signature: t=<unix timestamp>,v1=<hex signature>`

To check it:

1. Read the raw request body exactly as received. Do not parse and re-serialise the JSON first.
2. Split the header on commas and take `t` and `v1`.
3. Compute HMAC-SHA256 with your secret as the key over the timestamp, a full stop, then the raw body (`"<t>.<body>"`), and hex-encode it.
4. Compare it with `v1` using a constant-time comparison.
5. Reject the request if `t` is more than five minutes (300 seconds) from your current time.

```python
import hashlib, hmac, time

def is_from_vectorae(secret: str, body: bytes, header: str) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    ts = int(parts.get("t", "0"))
    if abs(time.time() - ts) > 300:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))
```

The timestamp is signed fresh on every attempt, so a retry carries a new signature. The older `X-ClientHunter-Signature` is `sha256=` plus HMAC-SHA256 of the body alone, with no timestamp; prefer `X-LeadPilot-Signature`.

## What happens if my endpoint is down or returns an error?

Any 2xx response counts as delivered. Vectorae waits up to 10 seconds for an answer. A timeout, a connection error or any other status (apart from 410) is a failure, and the delivery is retried:

| Attempt | When |
|---|---|
| 1 | Straight away |
| 2 | 30 seconds after attempt 1 |
| 3 | 2 minutes after attempt 2 |
| 4 | 10 minutes after attempt 3 |
| 5 | 30 minutes after attempt 4 |
| 6 | 2 hours after attempt 5 |

After the sixth failed attempt the delivery is marked `exhausted` and recorded for your administrator. There is no button to resend it. An earlier version stopped after five attempts, so the 2-hour retry never ran; that has been fixed.

Other outcomes:

- **410 Gone** turns the webhook off with the reason "the receiver answered 410 Gone" and stops retrying. To start again, add the webhook again.
- If your host does not resolve to a public address, the delivery fails at once without retries. Redirects are not followed.
- Deleting a webhook, or one turned off by a 410, cancels its pending deliveries.

Check **Recent deliveries** on the webhook card for each delivery's status, attempt count and last response.

## Which events are posted to Slack?

Slack has no per-event settings: once connected with a **Channel** chosen, Vectorae posts these to that one channel:

- Meeting booked, meeting prep brief ready, and meeting reminders a day and an hour before.
- Interested reply (email, other channels and AI phone calls).
- Campaign paused automatically for bounce rate. A pause you make yourself is not posted.
- Strategy rewritten on an idle campaign; campaign awaiting approval; approval decision.
- Objection spike, deal won, AI call completed.
- Domain blacklisted, email health warning, mailbox paused.
- Message held for review, re-engagement due, reply draft approved.
- Daily send approval needed, Autopilot updates, personal video suggested.
- AI call in progress, AI call paused for a disclosure incident, message taken by your AI assistant.

Every reply (`reply_received`) and leads sourced (`lead_sourced`) go to webhooks only, never to Slack. Each post has a title, a short text and, when there is a screen to open, an "Open in Vectorae" button. If Slack is not connected, no channel is chosen, or Slack fails, the post is skipped and nothing else is affected. The Slack connection has not been tested against a live Slack workspace.

## Related
- [CRM and integrations](16-crm-and-integrations.md)
- [HubSpot and Salesforce sync in detail](36-crm-sync-field-mapping.md)
- [The send-review queue and approvals](12-send-review-queue.md)
- [Deliverability and mailbox health](19-deliverability-and-mailbox-health.md)
- [Daily send approval and notifications](39-daily-send-approval-and-notifications.md)
- [Known limitations](29-known-limitations.md)
