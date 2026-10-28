# BUILD_DECISIONS.md

Decisions made autonomously during the overnight build (2026-09-13), per rule #1
of the build brief: where the brief left a choice open, the recommended option
was taken, recorded here with its reasoning, and work continued. Every entry is
reversible; the "Reverse it by" line says where.

---

## Section B — Identity & location verification

### B1. The flow applies only to accounts created after migration 0039
- New column `users.identity_required` (NOT NULL, server default false). Only
  `POST /auth/signup` sets it true.
- **Why:** migration 0015's lesson — a new gate that applies to existing rows
  locks out accounts (including the only admin) that have no way through it.
  No backfill is needed because the default already means "not required".
- **Reverse it by:** a data migration setting `identity_required = true` for
  the rows you want to force through.

### B2. A country mismatch flags for review; it never blocks
- Declared personal country vs IP geolocation → `geo_check_status`
  (`match | mismatch | unknown`). A mismatch sets
  `geo_review_status = "pending"`, writes an `account_security_events` row,
  and appears in `GET /admin/identity-reviews`.
- `unknown` (private IP, provider down, geolocation disabled) is recorded
  but **not** queued — a queue full of "could not tell" trains admins to
  ignore it.
- The detected country is **never echoed to the user** (it would be a free
  oracle for tuning a VPN exit).
- Resubmitting the form never clears a pending review; only an admin decision
  (`cleared` / `confirmed_risk`) does. A decision does not suspend anyone —
  suspension remains the separate explicit admin action.

### B3. Geolocation provider: ipapi.co by default, ipinfo.io supported
- `GEOLOCATION_PROVIDER=ipapi_co` (no key, ~1k lookups/day free) is the code
  default; `ipinfo` uses `GEOLOCATION_API_KEY` as its token; `disabled` for
  tests. Private/loopback/documentation IPs are never sent to a provider.
- Lookups never raise — "unknown" is a valid answer.

### B4. A separate `account_security_events` table for the audit
- The existing `compliance_audit_log` is per-lead/per-send (`channel` is NOT
  NULL); an account-level event does not fit it. The new table is append-only,
  nullable `user_id` (a blocked *signup* has no user yet; `email` records who
  tried), and survives user deletion with the id nulled.

### B5. Countries: a full ISO 3166-1 alpha-2 list served by the backend
- `app/services/countries.py` is the single source; the frontend fetches
  `GET /onboarding/countries` rather than shipping a second copy (this repo's
  recurring "two implementations drift" failure).

## Section C — Phone verification

### C1. What requires a verified phone (the gating decision)
Gated with `403 PHONE_NOT_VERIFIED` (only for post-0039 accounts, and only
while `REQUIRE_PHONE_VERIFICATION=true`):
1. **Starting outreach** — `POST /sequences/{id}/enroll` and
   `POST /sequences/{id}/approve`.
2. **Buying a plan** — billing checkout (Section E).
3. **Minting a public ROI share link** (Feature A6).

**Not gated:** browsing, strategy research, CRM work, onboarding itself.
**Why:** those three are the actions where a throwaway anonymous account costs
*someone else* money or reputation (spam sent from our infrastructure, card
testing, public pages). Gating the whole app would add friction without
adding protection.

### C2. Own the code, rent the pipe
- The app generates a 6-digit code (`secrets.randbelow`) and stores only an
  HMAC-SHA256 of it keyed by the JWT secret and bound to (user, number). The
  SMS provider only delivers text. **Why:** swapping Twilio for any other SMS
  API is one class, and a code proved for one number cannot verify another.
- Twilio Verify was not used for the same portability reason.

### C3. Limits (defaults)
| Limit | Default | Setting |
|---|---|---|
| Code lifetime | 10 min | `PHONE_OTP_TTL_SECONDS` |
| Wrong guesses per code | 5 | `PHONE_OTP_MAX_ATTEMPTS` |
| Resend cooldown | 60 s | `PHONE_OTP_RESEND_COOLDOWN_SECONDS` |
| Sends per hour, per account **and** per number | 5 | `RATE_LIMIT_OTP_SEND` |
| Verify attempts per 15 min per account | 10 | `RATE_LIMIT_OTP_VERIFY` |

- Two send keys (like `/auth/login`): per account stops one user cycling
  numbers; per number stops SMS-pumping fraud spread across accounts.
- A resend kills every earlier outstanding code.

### C4. One number, one verified account
- A number already verified on another account is refused (409) *before* an
  SMS is spent. **Why:** otherwise one SIM verifies an unlimited account farm,
  which is what phone verification exists to prevent.

### C5. E.164 validation without a new dependency
- `phonenumbers` is not installed; a strict E.164 regex after stripping
  spaces/dashes/dots/parentheses (and `00` → `+`) is used. It does not validate
  that a number exists — the SMS round-trip does that.

## Section D — VPN / proxy blocking

### D1. On in production by default, off elsewhere
- `VPN_BLOCK_ENABLED` unset → active only when `APP_ENV` is
  production/prod/live. Explicit true/false always wins. Tests set it false and
  patch a fake provider in where they exercise it.

### D2. Blocks VPN, proxy, Tor **and** datacenter/hosting ranges
- Per the brief. Known trade-off: some corporate egress runs through hosting
  ranges; `VPN_ALLOWLIST_IPS` is the escape hatch.

### D3. Provider outage fails OPEN by default
- `VPN_DETECTION_FAIL_CLOSED=false`: when proxycheck/IPQS cannot answer, the
  request proceeds and the failure is logged. **Why:** failing closed turns a
  third-party outage (or an exhausted free quota) into "nobody can log in".

### D4. Order inside the handlers
- After the existing rate limiters (a blocked flood still spends its quota and
  cannot turn the provider lookup into the thing being flooded), before any
  account/password work (a VPN'd attacker learns nothing about credentials).

### D5. Provider: proxycheck.io default, IPQualityScore supported
- Verdicts cached in Redis per IP for 1 hour (`VPN_DETECTION_CACHE_SECONDS`);
  errors are never cached.

### D6. The error is a plain sentence plus a machine header
- `403` with detail "…Please disable your VPN or proxy and try again from your
  normal network." and header `X-Block-Reason: VPN_BLOCKED`. **Why:** the
  frontend's `normalizeError` only renders string details, so a structured
  detail would have shown "403 Forbidden" instead of the instruction.

## Section E — Pricing & billing

### E1. The price list (exact pricing was not specified)
Chosen for the ICP in CLAUDE.md (boutique agency owners, 3–30 staff) and
anchored on what the code already enforces per plan.

**Option 1 — Monthly subscription (marked Recommended).** Every tier has a
**14-day free trial** (once per account).

| Tier | Price | Campaigns | Leads / campaign | Steps | Channels | Testing | Analytics |
|---|---|---|---|---|---|---|---|
| Starter | **$149/mo** | 10 | 500 | 10 | Email, WhatsApp | A/B | 90 days |
| Growth | **$349/mo** | 25 | 2,500 | 15 | + LinkedIn | A/B + MV | 365 days |
| Scale | **$699/mo** | Unlimited | 10,000 | Unlimited | + AI calls | A/B + MV | Unlimited |
| Enterprise | **$1,499/mo** | Unlimited | Unlimited | Unlimited | All | A/B + MV | Unlimited |

**Option 2 — Pay per meeting booked.** **$0/month + $179 per booked meeting.**
Growth's channels with tighter volume caps (10 campaigns, 1,000 leads/campaign).

Reasoning:
- $149 sits under the price of one SDR-tool seat stack an agency already pays;
  Growth at ~2.3x adds the channel (LinkedIn) the ICP most asks for.
- $179/meeting: typical outsourced appointment-setting charges $150–$300 per
  qualified meeting. At $179, Growth ($349) breaks even at 2 meetings/month,
  so the per-meeting plan is genuinely cheaper for a low-volume or seasonal
  user and the monthly plan is cheaper for anyone booking consistently — which
  is why monthly is the one marked Recommended.
- **Tiers differ only by limits `app/core/plans.py` already enforces.** Seat
  limits, SLAs and "dedicated support" were deliberately NOT advertised: nothing
  in the code enforces them, and a pricing page must not promise what the
  product does not police.
- `pro` is no longer sold but kept in `PLANS` so existing accounts keep their
  entitlements. Enterprise gained `linkedin`/`phone` channels and a 10x API
  multiplier so it is strictly ≥ Scale.

**Reverse it by:** editing `app/core/billing_catalog.py` (prices) and
`app/core/plans.py` (limits). The pricing page reads both through
`GET /billing/catalog`, so nothing is hardcoded in the frontend.

### E2. Pay-per-meeting billing rules
- **Metered at the moment of booking** by a `before_flush` session listener
  that sees every `Outcome(event=BOOKED)` (Calendly webhook, native calendar)
  and adds a `billable_meetings` row in the **same transaction** — a booking and
  its billing record commit together or not at all. Same pattern as
  `crm_events.py`, for the same reason: BOOKED is written from several places.
- **Once per prospect**: `UNIQUE (user_id, dedupe_key = lead_id)`. Book →
  cancel → rebook is one charge.
- **48-hour grace**: charged by a sweep (every 30 min) only after 48h, and only
  if the lead is still at `meeting_booked` or later. A Calendly cancellation
  writes `REPLIED`, so a cancelled meeting is **waived** automatically.
- **Disputes** are allowed only inside the grace window (charged meetings go to
  support — refunds are out of scope); an admin resolves (waive / charge).
- **One invoice per meeting** (invoice item → invoice → finalize) with
  idempotency keys derived from the meeting id, so a retried sweep cannot
  double-charge. No prospect name appears on the invoice line.
- A metering failure is logged and **never breaks the booking**.
- PPM cancellation stops metering immediately; meetings already booked are
  still billed.

### E3. Stripe without the SDK, and stub mode
- The repo had no payment code and no `stripe` package; the integration uses
  httpx against Stripe's REST API (house integration style). Checkout uses
  inline `price_data` from the catalog unless `STRIPE_PRICE_<TIER>` ids are set.
- Monthly: Checkout Session in `subscription` mode with `trial_period_days`.
  PPM: Checkout Session in `setup` mode (saves a card).
- **Stub mode** (no `STRIPE_SECRET_KEY`): checkout activates the plan locally
  with `is_stub=true`; the sweep marks meetings charged with `is_stub=true`;
  every stub path logs `TODO: connect live Stripe keys`.
- Plan switching: a new Checkout; the webhook cancels the replaced subscription.
  An abandoned checkout never downgrades an existing plan.
- `past_due` keeps plan access while Stripe's dunning retries.
- `/webhooks/stripe` answers **503** without `STRIPE_WEBHOOK_SECRET` (never
  accepts unsigned events), verifies `Stripe-Signature` with a 5-minute replay
  tolerance, and dedupes via the existing `processed_webhooks` table.

### E4. Revenue analytics and access
- Pay-per-meeting fees (pending + charged) are a new `platform` cost kind in
  `revenue_analytics.report`, attributed to the campaign that booked the
  meeting, so cost-per-meeting and ROI include what Vectorae charged.
- `/billing` is a **personal** RBAC prefix: a workspace member buys and cancels
  only for their own account. Checkout is phone-gated (C1).

## Section A — Features

### A1. Fabrication-proof claim engine
- **Where it runs:** inside the send task, on the rendered content of every
  channel, *before* the message is persisted and transmitted — email (before
  the compliance footer is appended, so the footer is never touched), LinkedIn
  (body + InMail subject), WhatsApp free-form text and template **variables**
  (the Meta-approved template body itself is fixed), and the phone script
  (first message, voicemail, close, each talking point; the system prompt is
  rebuilt from the checked script). Follow-ups go through the same send path.
- **Evidence = only what is stored on the lead:** profile fields, every scalar
  in `enrichment_json` (Apollo person/org record, Hunter verification, any tool
  research stored there), `company_news_json` (NewsAPI) and
  `linkedin_posts_json`. Nothing is fetched at check time.
- **Detection:** category rules (funding, headcount, hiring, job change, news,
  metrics, expansion, "your post") applied **only to sentences about the
  prospect** (second person, their name or company) — claims about the sender's
  own results are out of scope. An optional model extraction pass
  (`claim_model_extraction_enabled`, default on, one extra Claude call per
  outbound message) adds recall; its failure degrades to rules.
- **Support = category evidence AND every specific token found:** numbers and
  money unit-normalised ("$20M" = "20 million"), "Series X", quoted phrases,
  proper nouns (except the lead's own name/company and the sender's product).
- **Strip vs rewrite:** mid-body sentence → removed; the opening hook → replaced
  with "I wanted to reach out to you at {company} directly."; subject →
  "Quick question"; template variable → "your team"; talking point → dropped.
  A message left empty gets the hook line.
- **Fails closed:** if verification errors, it re-runs with *no* evidence, so
  every detected claim is removed rather than sent unverified.
- Every decision (including verified claims, with their evidence source and
  excerpt) is logged to `claim_verification_log`; shown on the lead page under
  "Claim checks". Kill switch: `claim_verification_enabled`.
- **Known limit:** reply drafts and displacement DMs are human-sent drafts, not
  queued sends, so they are not gated (a human reads them first).

### A2. Tamper-evident audit trail
- **Recording** is a `before_flush` listener on new `Outcome` rows
  (sent / opened / clicked / replied / booked), in the same transaction —
  outcomes are written from a dozen places, so a listener is the only design
  where a future writer cannot forget. A recording failure is logged at ERROR
  and does **not** fail the transaction (by then the email has already left;
  failing the commit would lose the send record itself).
- **Chain:** `content_hash` at insert; `seq_no / prev_hash / chain_hash` assigned
  by a **separate sealer** (every 5 min via Celery, and on demand before any
  verify/export), serialised per account with a PostgreSQL advisory lock. Why
  not chain at insert: two concurrent sends would race for the same sequence
  number inside a business transaction.
- **Append-only, three layers:** ORM guard (all databases), PostgreSQL trigger
  (migration 0042, refuses DELETE / content UPDATE / re-seal even for raw SQL),
  and signed exports that pin the chain head. The hashed `*_ref` columns have
  **no foreign keys** — an `ON DELETE SET NULL` would rewrite hashed content on
  a GDPR erase and falsely report tampering. The payload holds ids, step,
  variant, classification and a click's *host* only — no PII.
- **Known limit (documented, not hidden):** removing the *newest* records
  leaves an internally valid, shorter chain; only a previously exported signed
  report (which records `head_seq_no`/`head_hash`) reveals that.
- **Signed reports:** Ed25519 over the canonical JSON (sorted keys, no
  whitespace). Key = `AUDIT_SIGNING_KEY` (base64 32-byte seed) or HKDF-derived
  from `SECRET_KEY`. `GET /audit/public-key` and `POST /audit/verify` are public
  (the buyer has no account); verify reads no database rows. PDF is rendered
  with Pillow (no new dependency) and states that the JSON is the signed
  artefact. "Workspace" = the owner account (`get_current_user` resolves the
  workspace owner).
- **Click events:** `OutcomeEvent.CLICKED` via `/t/c/{token}?u=`; the token is an
  HMAC over (message id, destination), so the redirect cannot be pointed
  anywhere else (no open redirector). **Off by default**
  (`click_tracking_enabled=false`) because rewritten links are a spam-filter
  signal; never applied where open tracking is off (EU/EEA/UK). Visible link
  text stays the real URL.

### A3. Reply-authenticity scoring
- **A third, separate verdict** stored on `inbound_replies`
  (`authenticity_kind`, `authenticity_score`, `buyer_intent_score`,
  `authenticity_confidence`, `authenticity_signals_json`) beside the existing
  routing class (`classification`) and next-action category
  (`reply_category`). Kinds: `genuine | out_of_office | auto_responder | bot |
  bounce` — separated because each wants a different action.
- **Real time, no extra model call:** scored in the same commit the reply is
  stored, in all three inbound routers (email, WhatsApp, LinkedIn). Evidence:
  RFC 3834 / auto-responder headers (Gmail now keeps *only* those headers in
  `raw`), sender/subject/body patterns from `reply_fraud.py` (extended with
  out-of-office and bounce detectors — the existing functions are unchanged),
  reply-within-45-seconds timing, the classifier's own verdict, and buying
  signals. Evidence combines as `1 − Π(1 − w)`, so one weak phrase alone never
  marks a person as a bot.
- Scoring never breaks routing (`safe_apply`). Existing replies are "not
  scored" (NULL), never a fabricated default; `POST
  /crm/replies/{id}/authenticity/rescore` scores history on demand.
- **UI:** there was no reply inbox in the frontend, so one was added as a CRM
  tab (`/crm/replies`) — kind + confidence badge, buyer-intent band, and the
  human-readable reasons, filterable (genuine / automated / each kind) and
  sortable by intent.

### A4. Unified conversation thread + channel stagnation
- **The thread is a read model, not a table.** `conversation_thread.py` merges
  messages, inbound replies (with authenticity), AI calls, calendar bookings,
  meetings and channel suggestions at read time; `ThreadItem` in
  `app/api/conversations.py` is its typed data model. A copy table would be a
  second source of truth that can drift from the send log — the failure this
  repo's handoff document records ten times. Shown as a "Conversation" tab on
  the lead page (inbound right-aligned, grouped by day).
- **Where the logic lives:** a new pipeline step,
  `app/pipeline/channel_orchestrator.py` (the brief allowed "a new pipeline
  step"), run hourly on the `outreach` queue and on demand
  (`POST /channel-suggestions/detect`). `engine.py` is the research pipeline
  and was left alone.
- **Stagnant** = N sends on one channel after the last *genuine* reply (OOO,
  auto-responders and bounces don't reset it), newest ≥ 48h old. Defaults:
  3 emails, 2 touches on other channels (system settings).
- **Next channel** must be allowed by the owner's plan, reachable (email
  address / LinkedIn URL + connected account / consented number with calling
  enabled / WhatsApp opt-in) and not itself stagnant. Order: Feature A5's
  outcome ranking when data exists, else email → LinkedIn → phone → WhatsApp.
- **Auto-switch is off by default** (`stagnation_auto_switch_enabled`). When
  on, it re-channels the lead's *already scheduled* next message in place —
  never schedules an extra one — so every send-time gate still applies.
  **WhatsApp is suggest-only**: a cold WhatsApp touch needs an approved
  template, which is a human choice. One open suggestion per lead; no repeat
  within 14 days of a decision.

### A5. Conversion probability + kill signals
- **Model:** an explainable odds-form Bayesian update (not a trained model —
  there is no labelled history to train on at launch). Prior = the lead's AI
  booking score, else 10%. Per-channel no-reply likelihood ratios (email 0.85,
  LinkedIn 0.8, WhatsApp 0.75, phone 0.8); genuine reply ×6 / ×3 / ×1.5
  (interested / question / objection); opens ×(1+0.5w), clicks ×(1+1.5w) with
  14-day decay; inactivity after 7 days can at most halve the odds. Every factor
  is stored and shown.
- **Kill signals** (bounce, unsubscribe/opt-out, genuine "not interested")
  archive regardless of probability. A booking/win is terminal "won".
- **Actions:** `< 5%` → **cooling** (enrollments paused 21 days, CRM tag
  "cooling"); `< 1.5%` or a kill signal → **archived** (enrollments stopped with
  `kill_signal:<reason>`, tag "archived"). Hysteresis: leaving cooling needs
  1.5× the threshold. Archive is sticky until a person reactivates.
- **Fairness guard:** probability alone never cools/archives a lead before
  **4 unanswered sends** — a lead with a low AI score still gets a fair first
  few touches (and existing 3-step-sequence behaviour is unchanged).
- **Where it runs:** a gate inside `send_message_impl` (the held message is
  never rendered or sent — result `held_cooling` / `held_archived`), and an
  hourly sweep on `learning` so the CRM shows a lead as cooling between sends.
- **Extensions to the named services:** `send_time_optimizer` gained
  outcome-based channel rates and ranking (a booking weighs 3× a reply, credited
  to the lead's most recent send, Laplace-smoothed) and booking-weighted slot
  scores; `score_decay` gained a public `engagement_weight`. The ranking is what
  the A4 stagnation step uses to pick the next channel.
- Reactivation resumes *paused* enrollments; *stopped* ones stay stopped (stops
  are irreversible by design in `sequence_engine`) — the UI says so.

### A6. Client-facing shareable ROI dashboard
- **Token = credential, handled like an API key:** 256-bit `secrets` token,
  shown once, only SHA-256 stored (+ an 8-char prefix for display). Every link
  **must** expire (1–365 days, default 30) and can be revoked instantly.
  "Signed link" is implemented as an unguessable hashed bearer token rather
  than an HMAC over parameters, because revocation needs a server-side record
  anyway and a stored hash revokes cleanly.
- **Public endpoint `GET /public/roi/{token}`:** per-IP rate limit
  (`RATE_LIMIT_PUBLIC_SHARE`, 120/h) *before* the lookup; unknown, expired and
  revoked all return the identical 404 (no enumeration oracle); `Cache-Control:
  no-store`, `X-Robots-Tag: noindex`, `Referrer-Policy: no-referrer`.
- **Content:** built on `roi_calculator` (the same six metrics as the in-app
  ROI card) + `roi_snapshots` trend + pipeline stage counts, last 90 days.
  **Aggregates only** — no lead name, email, company or message text (a test
  greps the payload). Scope: whole account/workspace or one campaign.
  White-label branding is applied when the workspace has it enabled.
- Page is `/share/roi?token=` (query param, not a path segment — the static
  export constraint documented in the repo). Creation is phone-gated (C1) and
  managed under Settings → Sharing.

### A7. Pre-send adversarial review
- **Only deterministic rules can block;** the AI red-team pass contributes
  *warnings only* and its failure is an `info` finding. A launch must never be
  stalled by model variance, and every block must be reproducible.
- **Blocking rules:** high-risk spam phrases ("guaranteed", "risk-free",
  "act now", …); a `RE:`/`FWD:` subject (deceptive under CAN-SPAM); a brief
  telling the writer to omit the unsubscribe/opt-out; a brief demanding a claim
  about the prospect in any of Feature A1's claim categories; a placeholder
  `SENDER_IDENTITY` **in production** (warn elsewhere so dev works).
- **Warnings:** softer spam, pressure/tone-deaf lines, ALL-CAPS (common
  industry acronyms excluded), 3+ exclamation marks, unapproved WhatsApp
  template, leads without WhatsApp opt-in, AI calling disabled / no call
  consent. **Info:** EU/UK lead count (GDPR notice + no tracking applied).
- **Gate placement:** both `POST /sequences/{id}/enroll` (before the SDR
  approval request, so blocked content can't be queued for a manager) and
  `POST /sequences/{id}/approve`. Response `409 SEQUENCE_REVIEW_BLOCKED`
  (string detail — the frontend only renders strings) + `X-Review-Id`.
- **Overrides:** owner/manager only, reason ≥ 10 characters, recorded on the
  review and in `account_security_events`. A review/override is bound to a
  SHA-256 **content hash** of everything a lead receives — editing the copy
  after an override re-arms the gate.
- UI: review panel on each sequence in Campaigns and under each pending launch
  on the Team approvals list (there is no other launch UI in the frontend).
