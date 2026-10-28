# Feature Group 5 — LinkedIn outreach channel

## What it adds
- **`LinkedInChannel`** (`app/integrations/linkedin_channel.py`) implements the
  existing `OutreachChannel` interface over **Unipile**. It transports only;
  the sequence engine decides when a message may go. Like WhatsApp, it
  re-checks suppression immediately before every API call.
- **LinkedIn steps in sequences** (`channel: "linkedin"`, optional
  `linkedin_action`: `auto` | `connect` | `message` | `inmail`).
- **Multiple accounts per user**, connected through Unipile's hosted page
  (the user signs in to LinkedIn on Unipile; Vectorae stores only an account id).

## How a LinkedIn step resolves (`auto`)
| Lead state | Action |
|---|---|
| already connected | message |
| connection request pending | step **skipped** (sequence continues) |
| Premium profile | **InMail** (falls back to a connection request if no account has InMail credits) |
| otherwise | connection request (note ≤ 300 characters, never a pitch) |

The lead's Unipile id, Premium flag and connection state are re-read from
Unipile at send time, and updated by the `new_relation` webhook.

## Limits and rotation
- Per-account daily ceilings: **20 connection requests** and 50 messages
  (InMail counts as a message) — both admin-tunable in System Settings.
- Counters live in Redis per account per UTC day with a 36-hour TTL; a slot is
  reserved atomically before the send and released if the send fails.
- A lead's first touch goes to the least-used active account; **every later
  touch comes from the same account** (the relationship owner). If that
  account is disconnected, the send fails with a clear reason.
- When every eligible account is at its ceiling, the message is deferred to
  the next send window — never dropped.

## Compliance
- `linkedin_suppressions` holds opted-out profiles (normalised slug).
  `is_suppressed(..., linkedin=)` is checked at enrol time, at send time and
  in the channel guard. An unsubscribe on **any** channel — click, reply, GDPR
  delete — suppresses the LinkedIn profile too.
- A LinkedIn reply goes through the same classifier and stop rules as email
  and WhatsApp; an unsubscribe-style reply suppresses email, phone and profile.
- No unsubscribe footer in LinkedIn messages (it would get the account
  restricted); opting out by reply is honoured instead.

## Webhook
`POST /webhooks/unipile` (replies + new connections) is authenticated with the
`unipile.webhook_secret` system credential, either as
`X-LeadPilot-Signature: sha256=HMAC(secret, body)` or as the `Unipile-Auth`
header Unipile adds to deliveries (Unipile does not sign bodies itself). With
no secret set, every delivery is rejected. Deliveries are de-duplicated.

## Setup
1. Admin › Integrations › Unipile: `api_key`, `dsn`, and `webhook_secret`.
2. In Unipile, create a webhook to `https://<api>/webhooks/unipile` for
   messaging + users events, with header `Unipile-Auth: <webhook_secret>`.
3. Users connect accounts in Settings › Integrations › LinkedIn.
