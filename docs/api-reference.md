# API Reference — Vectorae

Base URL (production): `https://api.yourdomain.com/api/v1`
Base URL (local dev): `http://localhost:8000/api/v1`

All endpoints except `/health` and `/auth/register` + `/auth/login` require:
```
Authorization: Bearer <access_token>
```

---

## Authentication

### POST /auth/register
Create a new account.
```json
Request:  { "email": "you@example.com", "password": "Str0ng!Pass" }
Response: { "access_token": "...", "refresh_token": "..." }
```

### POST /auth/login
```json
Request:  { "email": "you@example.com", "password": "Str0ng!Pass" }
Response: { "access_token": "...", "refresh_token": "..." }
```

### POST /auth/refresh
```json
Request:  { "refresh_token": "..." }
Response: { "access_token": "..." }
```

---

## Products

### POST /products
Create a product or skill to acquire clients for.
```json
Request: {
  "name": "Vectorae",
  "description": "AI-powered client acquisition automation",
  "type": "product"   // "product" | "service" | "skill"
}
Response: { "id": "...", "name": "...", "user_id": "..." }
```

### GET /products
List all products for the current user.

### GET /products/{id}
Get a single product. Includes `extracted_patterns_json` after past clients are submitted.

### POST /products/{id}/past-clients
Add a past client for pattern extraction.
```json
Request: {
  "details": "Acme Corp, 50 employees",
  "acquisition_story": "LinkedIn + email, closed in 2 weeks",
  "industry": "SaaS",
  "company_size": "11-50",
  "deal_size_usd": 12000,
  "acquisition_channel": "email",
  "trigger_event": "Series A announcement",
  "decision_maker_role": "VP Sales",
  "sales_cycle_days": 14
}
```

---

## Strategies

### POST /products/{product_id}/strategies
Create and run a strategy. Triggers the pipeline immediately.
```json
Request: { "flow_type": "with_clients" }   // "with_clients" | "no_clients"
Response: { "id": "...", "status": "pipeline_running", ... }
```

### GET /strategies/{id}
Get strategy status, documents, and verification results.
Key fields: `status`, `strategy_document`, `gtm_document`, `verified_passes_json`, `default_variant`.

### GET /strategies/{id}/steps
Get all research steps (72 or 144). Each step has `step_no`, `status`, `output`.

### POST /strategies/{id}/resume
Resume a stalled or partially-completed pipeline.

### GET /strategies/{id}/steps
Full step list. Filter by `?status=complete`.

---

## Leads

### POST /strategies/{id}/leads/source
Trigger Apollo sourcing + Hunter verification for the strategy's ICP.
Returns 422 if any verification pass has `result=FAIL`.

### GET /strategies/{id}/leads
List all verified leads for this strategy.

### GET /leads/{id}
Single lead detail including status, sequence status, outcomes.

### POST /leads/{id}/reply
Record/classify an inbound reply.
```json
Request: { "classification": "auto", "message": "Stop emailing me" }
Response: { "classification": "unsubscribe_request", ... }
```

### DELETE /leads/{id}
GDPR right-to-erasure. Wipes PII, creates tombstone. Returns 204.

### GET /leads/{id}/tombstone
Read the tombstone record for a deleted lead.

### GET /leads/{id}/outcomes
List all outcome events for this lead.

---

## Campaigns & Sequences

### GET /strategies/{id}/campaigns
List campaigns for this strategy.

### GET /strategies/{id}/sequences
List outreach sequences.

### POST /strategies/{id}/campaigns/{campaign_id}/pause
Pause a running campaign.

### POST /strategies/{id}/campaigns/{campaign_id}/resume
Resume a paused campaign.

---

## Playbook

### GET /playbook/scores
List playbook scores for the current user's strategies.
Query: `?strategy_id=...`

### GET /playbook/similar-strategies
Find strategies with similar ICPs.
Query: `?query=<product description>`

### POST /playbook/aggregate *(admin)*
Trigger nightly aggregation now.

### POST /playbook/promote-winners *(admin)*
Run A/B winner promotion.

---

## Meeting preparation & outcomes *(Feature Group 7)*

A brief is generated automatically when a lead reaches `meeting_booked`
(Calendly `invitee.created`, or a booking on the Vectorae calendar). All
routes are owner-scoped: another account's lead or brief is a **404**.
Routes that spend a model call are limited by `RATE_LIMIT_AI_ACTION`
(default 60/hour/user).

### GET /leads/{lead_id}/meeting-prep
The brief to show (the next live meeting's, else the most recent) plus up to
20 earlier briefs.
```json
Response: {
  "brief": {
    "id": "...", "lead_id": "...",
    "source": "calendly" | "leadpilot_calendar" | "manual",
    "status": "pending" | "generating" | "ready" | "failed",
    "meeting_start_at": "2026-09-14T15:00:00Z", "meeting_url": "https://...",
    "content_md": "# Meeting prep: ...",
    "sections_json": {
      "company_overview": "...", "recent_activity": "...", "why_they_booked": "...",
      "pain_points": ["..."], "likely_objections": [{"objection": "...", "response": "..."}],
      "talking_points": ["..."], "discovery_questions": ["..."],
      "competitive_landscape": "...", "next_steps": ["..."],
      "deal_structure": "...", "opening_60_seconds": "..."
    },
    "profile_json": { "name": "...", "email": "...", "linkedin_url": "...",
                      "linkedin_posts": [...], "company_news": [...] },
    "opening_script": "...", "error": null,
    "generated_at": "...", "reminder_24h_sent_at": null, "reminder_1h_sent_at": null,
    "cancelled_at": null, "created_at": "..."
  },
  "history": [{ "id": "...", "source": "...", "status": "...", "meeting_start_at": "...",
                "cancelled_at": null, "created_at": "..." }]
}
```
`profile_json` is copied from the lead record, never written by the model.

### POST /leads/{lead_id}/meeting-prep → 202
Prepare a brief on demand. Reuses the lead's manual brief if one exists.
```json
Request:  { "meeting_id": "..." | null }   // ties the reminders to that meeting's time
Response: <brief>  (status "pending"; poll GET until "ready" or "failed")
```

### GET /meeting-prep/{brief_id}
One brief.

### POST /meeting-prep/{brief_id}/regenerate → 202
Re-queue generation. **409** while the brief is already `generating`.

### POST /leads/{lead_id}/meeting-outcome → 201
"Log Meeting Outcome". Commits the outcome FIRST, then (unless
`generate_followup` is false) writes a follow-up email and saves it as a Gmail
draft. The draft is never sent automatically.
```json
Request: {
  "outcome": "interested" | "needs_follow_up" | "not_a_fit" | "closed_won" | "closed_lost",
  "notes": "...",                // optional, ≤ 20,000 chars
  "meeting_id": "..." | null,
  "deal_value": 4500.5,          // closed_won only; creates a won Deal
  "currency": "USD",
  "deal_name": "..." | null,
  "generate_followup": true
}
Response: {
  "id": "...", "lead_id": "...", "meeting_id": null, "deal_id": "..." | null,
  "outcome": "closed_won", "notes": "...",
  "previous_status": "meeting_booked", "new_status": "closed_won",
  "followup_subject": "...", "followup_body": "...",
  "draft_status": "draft_saved" | "not_connected" | "reauth_required" | "suppressed"
                | "no_address" | "generation_failed" | "failed" | "pending" | "sent",
  "draft_error": null, "gmail_draft_id": "...", "sent_at": null,
  "created_at": "...", "gmail_drafts_url": "https://mail.google.com/mail/u/0/#drafts"
}
```
Status mapping: interested / needs_follow_up → `opportunity` (CRM next action in
2 / 3 days); not_a_fit → `disqualified`; closed_won → `closed_won` (+ Deal,
`won` outcome); closed_lost → `closed_lost` (`lost` outcome). Every live
sequence enrollment for the lead is stopped.

### GET /leads/{lead_id}/meeting-outcomes
All outcomes for the lead, newest first.

### PUT /meeting-outcomes/{outcome_id}/draft
Save the user's edits and update the Gmail draft. **409** once sent.
```json
Request: { "subject": "...", "body": "..." }
```

### POST /meeting-outcomes/{outcome_id}/draft/regenerate
Rewrite the follow-up with the model. **409** once sent.

### POST /meeting-outcomes/{outcome_id}/send
Send the saved Gmail draft. **409** with the reason when it is not sendable
(already sent, contact suppressed, no saved draft, Gmail not connected).
The suppression list is re-checked at send time.

---

## Deals

Revenue linked to a lead and a campaign (`strategy_id`). Money crosses the API
as a decimal `value` and is stored as integer cents (`value_cents`).

### POST /deals → 201
```json
Request: {
  "value": 1200, "currency": "USD", "stage": "won" | "open" | "lost",
  "name": "..." | null,          // defaults to the lead's company
  "close_date": "2026-09-11" | null,   // defaults to today for won deals
  "lead_id": "..." | null,       // strategy_id is derived from it when omitted
  "strategy_id": "..." | null,   // 422 if it contradicts lead_id
  "notes": "..." | null
}
Response: { "id": "...", "name": "...", "value": 1200.0, "value_cents": 120000,
            "currency": "USD", "stage": "won", "close_date": "2026-09-11",
            "lead_id": "...", "strategy_id": "...", "source": "manual",
            "external_ref": null, "notes": null, "created_at": "..." }
```

### GET /deals
Query: `strategy_id`, `stage`, `lead_id`, `limit` (≤ 200), `offset`.
Response: `{ "total": 3, "items": [<deal>, ...] }`

### GET /deals/{id} · PATCH /deals/{id} · DELETE /deals/{id} → 204
PATCH accepts any subset of `name`, `value`, `currency`, `stage`,
`close_date`, `notes`.

---

## AI intelligence *(Feature Group 1)*

Owner-scoped (404 for another account's strategy or lead). Model- or
paid-API-spending routes are limited by `RATE_LIMIT_AI_ACTION`.

### GET /strategies/{strategy_id}/intelligence
Everything shown next to the strategy document.
```json
Response: {
  "consensus_status": "complete" | "partial" | null,
  "zones": [{ "id": "...", "pipeline": "strategy", "phase": 6, "step_no": 54,
              "section_title": "...", "topic": "Primary channel",
              "claude_position": "...", "gpt_position": "...",
              "severity": "medium" | "high", "similarity": 0.41 }],
  "market_signals": { "queries": ["fire protection"], "fetched_at": "...",
                      "signals": [{ "type": "funding" | "hiring" | "launch" | "news",
                                    "title": "...", "company": "...", "source": "google_news" | "apollo",
                                    "url": "...", "published_at": "..." }],
                      "errors": [] } | null,
  "market_signals_fetched_at": "..." | null,
  "versions": [<version summary>, ...]
}
```
Consensus runs on the 8 phase-synthesis steps per pipeline when the admin
setting `consensus_enabled` is on and an OpenAI key is set. Claude's answer is
always the one in the document.

### GET /strategies/{strategy_id}/model-outputs?step_no=9&pipeline=strategy
Both models' raw answers for one section: `[{provider, model, output, error, latency_ms}]`.

### POST /strategies/{strategy_id}/market-signals/refresh → 202
Re-fetch live signals (Google News RSS + Apollo). Used by the next pipeline run.

### GET /strategies/{strategy_id}/versions · GET /strategies/{strategy_id}/versions/{version_id}
Version summaries (`version_no`, `status`: original/proposed/applied/superseded/dismissed,
`trigger`: original/idle_no_replies/manual, `changes_json`, `outcome_snapshot_json`);
the single-version route adds `document`.

### POST /strategies/{strategy_id}/mutate → 201
Propose a mutation now. **502** if the model fails (no version is written),
**409** if the strategy has no document yet.

### POST /strategies/{strategy_id}/versions/{version_id}/apply
Make the version live: `strategy_document` is replaced and the version's
messaging is injected into every future outreach prompt. **409** if already applied.

### POST /strategies/{strategy_id}/versions/{version_id}/dismiss
Only a `proposed` version (else **409**).

### POST /strategies/{strategy_id}/leads/rescore → 202
Queue rescoring of every scorable lead in the strategy.

### POST /leads/{lead_id}/rescore
Score one lead now: `{lead_id, ai_booking_likelihood, ai_score_reason, ai_score_factors, ai_scored_at}`.

### Lead fields
`GET /strategies/{id}/leads` now returns `ai_booking_likelihood` and
`ai_score_reason` on every item and sorts by score (highest first, unscored
last) unless `?sort=created`. `GET /leads/{id}` adds `ai_score_factors`
and `ai_scored_at`. The CRM grid accepts `ai_booking_likelihood` as a sort key.

---

## Personalization *(Feature Group 2)*

### GET /me/style-profile
`{ "profile": {tone, formality (1-5), vocabulary_level, sentence_length,
avg_words_per_sentence, humor, greeting_style, signoff_style,
signature_habits[], do[], dont[], summary} | null, "samples": [...], "updated_at": "..." }`

### PUT /me/style-profile
`{"samples": ["...", ...]}` — 1 to 5 samples, each ≥ 40 characters. Claude
extracts the profile (rate-limited, `RATE_LIMIT_AI_ACTION`). **422** on invalid
samples, **502** if the analysis fails. The profile is appended to the system
prompt of every outreach generator; the samples never are.

### DELETE /me/style-profile → 204

### GET /leads/{lead_id}/personalization
```json
{ "linkedin_url": "https://www.linkedin.com/in/...",
  "linkedin_posts": [{"text": "...", "posted_at": "...", "url": "..."}] | null,
  "linkedin_posts_fetched_at": "..." | null,
  "company_news": [{"headline": "...", "summary": "...", "url": "...",
                    "source": "...", "published_at": "..."}] | null,
  "company_news_fetched_at": "..." | null,
  "loom": {"status": "suggested" | "recorded" | "skipped", "title": "...",
           "script": "...", "on_screen": "...", "share_url": "...", "embed_id": "..."},
  "loom_page_url": "https://app.../v?t=..." | null }
```
`null` posts/news = never fetched; `[]` = fetched, nothing found.

### POST /leads/{lead_id}/personalization/refresh
Force-refetch posts and news (rate-limited). Normally they refresh at send time
(posts cached 7 days, news 3 days).

### PUT /leads/{lead_id}/linkedin-url
`{"linkedin_url": "https://www.linkedin.com/in/..." | null}` — **422** if not a
`linkedin.com/in/` URL. Changing it discards the previously fetched posts.

### POST /leads/{lead_id}/loom/script
Write or rewrite the personal-video recording script (rate-limited, **502** on failure).

### PUT /leads/{lead_id}/loom
`{"share_url": "https://www.loom.com/share/<32 hex>"}` — marks the video
recorded; sequence step 2 then links to the video page. **422** if not a Loom link.

### POST /leads/{lead_id}/loom/skip

### GET /public/video?t=<token> *(public, no auth)*
IP-rate-limited (`RATE_LIMIT_PUBLIC_VIDEO`). Returns only
`{first_name, company, title, embed_url}`; **404** for a bad token or a video
that is not (or no longer) recorded.

### Message field
Sent messages record `personalization_json`:
`{linkedin_post_url, news_url, style_profile: bool, loom_cta: bool}`.

---

## LinkedIn channel *(Feature Group 5)*

### GET /integrations/linkedin/accounts
```json
[{ "id": "...", "unipile_account_id": "...", "display_name": "...", "profile_url": "...",
   "has_premium": true, "inmail_credits": 12, "inmail_sent_total": 3,
   "is_active": true, "status": "ok", "last_used_at": "...",
   "usage_today": {"connect": 7, "message": 12, "inmail": 2},
   "limits": {"connect": 20, "message": 50} }]
```

### POST /integrations/linkedin/connect
`{"url": "https://account.unipile.com/..."}` — Unipile's hosted sign-in page.
**503** if Unipile is not configured.

### POST /integrations/linkedin/accounts → 201
`{"unipile_account_id": "..."}` — link an existing Unipile account (**422** if
Unipile does not know it, **409** if another user owns it).

### PATCH /integrations/linkedin/accounts/{id}
`{"is_active": false}` — paused accounts are skipped by rotation.

### POST /integrations/linkedin/accounts/{id}/refresh
Re-read Premium status and InMail credits.

### DELETE /integrations/linkedin/accounts/{id} → 204

### GET /leads/{lead_id}/linkedin
`{linkedin_url, provider_id, is_premium, connection_status: null|"pending"|"connected",
invited_at, connected_at, account_id, has_conversation}`

### Sequences
`POST /strategies/{id}/sequences` accepts `channel: "linkedin"` on the
sequence or per step, with optional `linkedin_action`
(`auto` default | `connect` | `message` | `inmail`). `linkedin_action` on a
non-LinkedIn step is **422**. A sequence whose first step is LinkedIn enrols
leads that have a LinkedIn URL (no email required).

### POST /integrations/linkedin/unipile/notify?state=… *(public)*
Unipile's hosted-auth callback. The encrypted `state` identifies the user;
invalid or expired state is **400**.

### POST /webhooks/unipile *(public, signed)*
Events `message_received` (a reply — classified and routed like any other
channel) and `new_relation` (connection accepted). Authenticated by
`X-LeadPilot-Signature: sha256=<HMAC>` or `Unipile-Auth: <secret>` against the
`unipile.webhook_secret` credential; **401** otherwise (including when no
secret is configured). Idempotent per event.

---

## AI phone calls *(Feature Group 6)*

AI calling is off until an admin enables `phone_calling_enabled`, and (by
default) only leads with recorded phone consent are called — see
`docs/features/ai-calling.md` for the legal reason.

### GET /strategies/{strategy_id}/calls
`[{id, lead_id, lead_name, provider, to_number, status, outcome, duration_seconds,
recording_url, voicemail_audio_url, ended_reason, analysis_json, error, created_at, ended_at}]`

### GET /leads/{lead_id}/calls · GET /calls/{call_id}
As above plus `script_json`, `voicemail_text`, `transcript`.
`outcome`: `voicemail_dropped` | `voicemail` | `no_answer` | `answered` |
`interested` | `not_interested` | `failed`.

### POST /leads/{lead_id}/call → 201
"Call now" (rate-limited). `{"brief": "..."}` optional. **409** when calling is
disabled, the contact is suppressed or there is no consent; **422** for an
undialable number; **503** with no provider configured; **429** at the daily
call limit; **502** if the provider refuses.

### PUT /leads/{lead_id}/phone-consent
`{"source": "signed web form 2026-09-01"}` — records prior consent to AI-voice
calls. **DELETE** revokes it (→ 204).

### Sequences
`channel: "phone"` on a sequence or step places an AI call at that step.

### POST /webhooks/vapi *(public, signed)*
`status-update` and `end-of-call-report`. Authenticated by `x-vapi-secret`
(set as the serverUrlSecret on every call) or `X-LeadPilot-Signature:
sha256=<HMAC>` against `vapi.webhook_secret`. Voicemail/no-answer settle
immediately; conversations are queued for analysis. Idempotent per call.

### POST /webhooks/elevenlabs *(public, signed)*
`post_call_transcription`. Authenticated by
`ElevenLabs-Signature: t=<ts>,v0=HMAC_SHA256(secret, "<ts>.<body>")` against
`elevenlabs.webhook_secret`.

---

## Analytics & revenue *(Feature Group 3)*

See `docs/features/revenue-analytics.md` for how each figure is computed.
Money is integer cents.

### GET /analytics/revenue?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
Defaults to the last 90 days; **422** if `date_from` > `date_to` or the span
exceeds 3 years.
`{currency, date_from, date_to, campaigns: [{strategy_id, name, campaign_state, sends,
meetings, deals_won, revenue_cents, direct_cost_cents, api_cost_cents, voice_cost_cents,
allocated_cost_cents, total_cost_cents, cost_per_meeting_cents, cost_per_deal_cents, roi}],
totals: {revenue_cents, cost_cents, meetings, deals_won, sends, cost_per_meeting_cents,
cost_per_deal_cents, roi, open_pipeline_cents, unattributed_revenue_cents,
unallocated_cost_cents}, monthly: [{month, revenue_cents, cost_cents}],
notes: {excluded_deals, excluded_costs, usd_costs_excluded, api_cost_usd_cents,
voice_cost_usd_cents}, api_usage: [{provider, purpose, calls, input_tokens, output_tokens,
cost_usd_cents}]}`

### GET /costs?strategy_id=&limit=&offset=
`{total, items: [{id, strategy_id, category, description, amount_cents, amount, currency,
hours, incurred_on, created_at}]}`

### POST /costs → 201
`{"strategy_id": null | "<id>", "category": "data|tools|ai|time|ads|other",
"description": "...", "amount": 49.99}` — or `"hours": 2.5, "hourly_rate": 60` instead
of `amount`. `currency` defaults to USD, `incurred_on` to today. No `strategy_id` =
account-wide. **PATCH /costs/{id}** edits; **DELETE /costs/{id}** → 204.

### GET /strategies/{strategy_id}/funnel
`{sequences: [{id, name, channel, steps: [{step_no, channel, variant, sent, opened,
replied, booked, dropped, in_progress, open_rate, reply_rate, booking_rate,
drop_off_rate}]}], best_step: {sequence_id, sequence_name, step_no, reply_rate} | null,
best_step_min_sent}`

### GET /strategies/{strategy_id}/send-time
`{smart_send_time, windows: [{dow, hour, score, share, opens, replies}], computed_at,
opens, min_opens, max_delay_hours, heatmap: [{dow, hour, opens, replies, score,
schedulable}]}` — `dow` Monday = 0, lead local time.

### PUT /strategies/{strategy_id}/send-time
`{"smart_send_time": true}` → the status above plus `rescheduled` (steps moved).

### POST /strategies/{strategy_id}/send-time/recompute
Rate-limited. Status plus `result`: `computed` | `insufficient_data` |
`no_schedulable_engagement`.

### GET /strategies/{strategy_id}/sentiment?weeks=12
`{weeks: [{week_start, total, interested, question, objection, not_interested,
unsubscribe, objection_rate, interested_rate, alerted, partial}], threshold, min_replies}`

### GET /t/o/{token}.gif *(public)*
Email open pixel. Always returns the same 1×1 GIF; records an open only for a
validly signed token. Rate-limited per token (`RATE_LIMIT_OPEN_PIXEL`).

---

## CRM & ecosystem *(Feature Group 4)*

See `docs/features/crm-ecosystem.md`. Every route below except the callbacks
and inbound webhooks accepts a personal API key (`Authorization: Bearer lpk_…`
or `X-API-Key`), except admin routes, which refuse API keys (403).

### API keys
- `GET /me/api-keys` → `[{id, name, prefix, created_at, last_used_at, revoked}]`
- `POST /me/api-keys` `{"name": "Zapier"}` → **201** with `key` (shown once); **409** at 10 active keys
- `DELETE /me/api-keys/{id}` → **204** (revoke)

### Outbound webhooks
- `POST /webhooks/outbound/register` `{"url": "https://…", "events": ["meeting_booked"],
  "description"?, "secret"? (≥16 chars), "source"?: "api|zapier|make"}` → **201**
  `{id, url, events, active, description, source, disabled_reason, created_at, secret}`.
  **422** for a non-https or private URL, an unknown event, or more than 25 active targets.
- `GET /webhooks/outbound` → targets (no secrets)
- `DELETE /webhooks/outbound/{id}` → **204**
- `POST /webhooks/outbound/{id}/test` → **202** `{delivery_id, event}` (sample payload, `"test": true`)
- `GET /webhooks/outbound/deliveries?limit=50` → `[{id, target_id, event, status, attempts,
  last_status_code, last_error, created_at, last_attempt_at, next_attempt_at, delivered_at,
  target_url}]`
- `GET /webhooks/outbound/events` → `[{event, description, sample, zapier}]`
- Legacy: `POST /webhooks/targets` `{url, secret, event_types, description?}`,
  `GET /webhooks/targets`, `DELETE /webhooks/targets/{id}`

Delivery body `{id, event, occurred_at, data}`; headers `X-LeadPilot-Event`,
`X-LeadPilot-Delivery`, `X-LeadPilot-Signature: t=<ts>,v1=<HMAC-SHA256(secret, "<ts>.<body>")>`.

### Slack
- `GET /integrations/slack` → `{configured, connected, team_name?, channel_id?, channel_name?}`
- `GET /integrations/slack/auth-url` → `{auth_url}` (**503** until an admin configures the Slack app)
- `GET /integrations/slack/callback` *(public, OAuth redirect → `/settings?tab=integrations&slack=connected|error`)*
- `GET /integrations/slack/channels` → `[{id, name, is_private}]`
- `PUT /integrations/slack/channel` `{"channel_id": "C123"}`
- `POST /integrations/slack/send-test` · `DELETE /integrations/slack` → **204**

### HubSpot / Salesforce
- `GET /integrations/crm` → `[{provider, configured, connected, status, account_id,
  account_name, last_push_at, last_pull_at, last_error, linked_leads, linked_deals, settings}]`
- `GET /integrations/{hubspot|salesforce}/auth-url` → `{auth_url}` (**503** if the app is not configured)
- `GET /integrations/{hubspot|salesforce}/callback` *(public, OAuth redirect)*
- `POST /integrations/{hubspot|salesforce}/sync` → **202** `{queued}` (full push + pull; rate-limited)
- `PUT /integrations/{hubspot|salesforce}/settings` `{sync_new_leads?, lead_status_map?,
  deal_stage_map?, pipeline?}` — map keys are Vectorae statuses / `open|won|lost`
- `DELETE /integrations/{hubspot|salesforce}` → **204** (tokens and links removed)

### POST /webhooks/hubspot *(public, signed)*
HubSpot webhook batch; `X-HubSpot-Signature-v3` + `X-HubSpot-Request-Timestamp`
(5-minute window) against the HubSpot app's client secret. **401** otherwise.

### POST /webhooks/salesforce *(public, signed)*
`{"org_id", "records": [{"sobject": "Lead"|"Opportunity", "id", "fields"}]}` with
`X-LeadPilot-Timestamp` and `X-LeadPilot-Signature: sha256=<HMAC-SHA256(webhook_secret,
"<ts>.<body>")>`. **401** otherwise.

---

## Workspaces & roles *(Feature Group 8)*

See `docs/features/workspaces.md`. Send `X-Workspace-Id: <id>` to act in a
workspace you belong to (404 if you do not; 400 if malformed). With it, every
data route acts on the workspace owner's records and the role is enforced:
viewers get **403** on any write; SDRs get **403** on DELETE and on writes under
`/integrations`, `/webhooks/outbound`, `/webhooks/targets`, `/costs`, `/playbook`.

- `GET /workspaces` → `[{id, name, slug, role, is_personal, brand_name}]`
- `GET /workspaces/current` → `{id, name, slug, role, is_personal, approval_required, branding}`
- `PATCH /workspaces/current` `{name?, approval_required?}` *(owner)*
- `GET /workspaces/current/members` → `[{user_id, email, role, joined_at}]`
- `PATCH /workspaces/current/members/{user_id}` `{"role": "manager|sdr|viewer"}`
- `DELETE /workspaces/current/members/{user_id}` → **204** (remove, or leave yourself)
- `GET /workspaces/current/invitations` *(manager+)*
- `POST /workspaces/current/invitations` `{email, role}` → **201** `{id, email, role, expires_at, link}`;
  **409** if already a member
- `DELETE /workspaces/current/invitations/{id}` → **204**
- `POST /workspaces/invitations/accept` `{token}` → `{workspace_id, name, role}`;
  **403** wrong address, **409** used, **410** expired
- `GET /workspaces/current/branding` · `PUT /workspaces/current/branding`
  `{white_label_enabled?, brand_name?, primary_color? "#rrggbb", support_email?, custom_domain?}` *(owner)*
- `POST /workspaces/current/branding/logo` multipart `file` *(owner; PNG/JPEG/WebP ≤ 1 MB; 413/415)*
- `POST /workspaces/current/domain/verify` → `{verified, ...branding}` *(owner)*
- `GET /branding?host=<host>` *(public)* → `{white_label, brand_name, logo_url, primary_color, support_email}`

### Approvals
- `POST /sequences/{id}/enroll` by an SDR (approval required) → **202**
  `{"enrolled": 0, "status": "pending_approval"}`
- `POST /sequences/{id}/approve` *(owner/manager)* → `{status, enrolled}`; **409** if not pending
- `POST /sequences/{id}/reject` `{note}` *(owner/manager)* → `{status: "draft"}`
- `GET /approvals` → `[{sequence_id, sequence_name, strategy_id, campaign, status,
  state: "pending|declined", requested_by, requested_at, note, lead_statuses}]`

---

## Trust & deliverability *(Feature Group 9)*

See `docs/features/trust-deliverability.md`.

- `GET /deliverability` → `{threshold, monitoring_enabled, domains: [{domain, address,
  health: {score, source, checked_at, details: {spf, dmarc, dmarc_policy, dkim, bounce_rate,
  reasons}}, blacklist: {listed_on, unknown, source, checked_at}, history: [{checked_at, score}]}]}`
- `POST /deliverability/check` → the same, after running the checks now (rate-limited)
- `GET /compliance/audit?lead_id=&limit=100` → `[{id, lead_id, message_id, channel, region,
  regime, decision, checks, ts}]`; `decision`: `sent` | `blocked_suppressed` |
  `blocked_no_address` | `skipped_no_consent`
- `GET /leads/{lead_id}/compliance` → `{region, regime, legal_basis, open_tracking, audit}`
- `POST /sequences/{id}/enroll` adds `"warning"` when a sending domain's health is below the threshold.
- Inbound replies may now be classified `automated_response` (stored, never counted).
- Campaign state `paused_blacklist`: set when a sending domain is newly listed; resume
  with `POST /strategies/{id}/campaign/resume`.

---

## Vectorae agents

Five advisory agents (Rust and C++ cores) and the master supervisor. They read
only the caller's own data and change nothing. Spec:
[features/vectorae-agents.md](features/vectorae-agents.md).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/agents` | Master status, then each agent's language, backend (`native` / `python`) and health (`idle`, `healthy`, `degraded`, `down`; circuit `closed`, `open`, `half_open`) |
| POST | `/agents/{name}/run` | Run `lead_priority`, `followup_timing`, `deliverability_guard`, `reply_triage` or `claim_check`. Body `{"params": {...}}`. **404** unknown agent, **422** bad parameter, **503** circuit open, **502** agent failed. Limited by `RATE_LIMIT_AGENT_RUN` (120/hour) |
| POST | `/agents/claim_check/check` | Body `{"subject", "body", "first_touch"}`: check one draft's wording; reads no data |

## Webhooks (no auth — signed)

### POST /webhooks/whatsapp
Receive WhatsApp messages and status updates. Requires `X-Hub-Signature-256` header.

### POST /webhooks/calendly
Receive Calendly booking events. Requires `Calendly-Webhook-Signature` header.

---

## Health

### GET /health *(public)*
```json
{ "status": "ok", "components": { "database": {...}, "redis": {...}, "celery": {...} } }
```

### GET /health/learning-loop *(admin)*
Last aggregation run status.

### GET /health/channels *(auth required)*
Gmail, WhatsApp, Calendly integration health.

---

## Admin *(all require is_admin=True)*

See [app/api/admin.py](../app/api/admin.py) for full schema.

| Method | Path | Purpose |
|---|---|---|
| GET | /admin/users | List all users |
| POST | /admin/users/{id}/suspend | Suspend user |
| POST | /admin/users/{id}/unsuspend | Unsuspend user |
| GET | /admin/suppression-list | Global suppression list |
| POST | /admin/suppression-list | Add entry |
| DELETE | /admin/suppression-list/{email} | Remove entry |
| GET | /admin/task-errors | Celery task error log |
| POST | /admin/task-errors/{id}/resolve | Mark resolved |
| GET | /admin/circuit-breakers | Circuit breaker states |
| POST | /admin/circuit-breakers/{name}/reset | Reset to CLOSED |
| GET | /admin/playbook/scores | All playbook scores |
| POST | /admin/playbook/recompute | Trigger aggregation |
| GET | /admin/celery-stats | Queue depths + workers |
| POST | /admin/rotate-encryption-key | Re-encrypt all tokens |
| GET | /admin/integrations | System credential catalogue + which keys are set (values never returned) |
| PUT | /admin/integrations/{provider} | `{"values": {"api_key": "..."}}` — empty string clears a key; 422 on unknown keys or user-scope providers |
| DELETE | /admin/integrations/{provider} | Remove every system key for a provider |
| GET | /admin/system-settings | Every feature switch/limit with value, default, type, description |
| PUT | /admin/system-settings/{key} | `{"value": ...}` — 422 when the type does not match the default's |

### System credentials

Deployment-wide API keys for the feature expansion (OpenAI, NewsAPI, Unipile,
Vapi, ElevenLabs, MXToolbox, Mailreach, and the Slack/HubSpot/Salesforce app
credentials) are stored through `TokenStore` (Fernet-encrypted) with a NULL
owner — never in environment variables. A user's own credential for the same
provider, where the provider allows one, takes precedence.

---

## Error Response Format

```json
{
  "error": "compliance_error",
  "compliance_code": "MISSING_UNSUBSCRIBE",
  "message": "Email body must include a valid unsubscribe link",
  "request_id": "a1b2c3d4..."
}
```

All error responses include `request_id` for log correlation.

HTTP status codes:
- `200/201` — success
- `204` — success (no body, e.g. GDPR delete)
- `400` — bad request
- `401` — missing or invalid JWT
- `403` — forbidden (insufficient permissions or wrong owner)
- `404` — not found
- `422` — validation error or compliance error
- `500` — internal server error (details in logs, not in response)
- `503` — service unavailable (DB or Redis down)
