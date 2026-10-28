# F03 — Deliverability Autopilot

**Flags** `deliverability_autopilot_enabled` (default **off**) and
`postmaster_import_enabled` (default **off**, and only acts when the first is
also on) · **Migration** `0075_deliverability_autopilot` (one nullable column) ·
**Service** `app/services/deliverability_autopilot.py` · **Adapter**
`app/integrations/google_postmaster.py` · **Task**
`app.workers.deliverability_tasks.import_postmaster_spam_rates` (daily 07:25 UTC)

## Problem

Competitor complaint (STAR A.6, F03): *deliverability damage is a top churn
cause; Google, Yahoo and Microsoft now reject non-compliant mail.* Since
February 2024 Gmail and Yahoo require bulk senders to authenticate with SPF
**and** DKIM, publish DMARC, support RFC 8058 one-click unsubscribe, and keep
the user-reported spam rate under 0.30% (0.10% recommended); Microsoft
followed for Outlook.com in 2025. A campaign that launches from an
unauthenticated domain does not merely underperform — it damages the sending
domain for months.

## This is "extend X", not a new system

Recon (rule 1) found more than half of the feature already built. F03 extends
it and adds no parallel implementation:

| Part of F03 | Already existed | F03 adds |
|---|---|---|
| SPF/DKIM/DMARC reading | `deliverability.dns_auth` + `_txt` (FG9), reused by `mailbox_health` | A **blocking** preflight with real pass/fail rules (Google include in the SPF chain, DKIM key not revoked, one valid DMARC record, alignment). It reads DNS through the same `deliverability._txt`, injectable for tests. |
| Launch check | `deliverability.launch_warning` — *warns*, never blocks | With the flag on, a failing preflight **blocks** enroll, manager approval and campaign resume with 409. The warning is unchanged. |
| One-click unsubscribe | `sequence_engine.build_compliant_email` builds `List-Unsubscribe` + `List-Unsubscribe-Post`; `POST /unsubscribe/{token}` honours it | A probe that pushes a synthetic message through `build_compliant_email` **and the Gmail MIME builder** (`gmail.build_mime`, extracted verbatim from `GmailChannel.send`) and parses the bytes that would go on the wire; plus a send-time guard that refuses an email whose outbound headers lack either header. |
| Warm-up ramp | `sequence_engine.daily_allowance`: 10 + 10/day since `GmailAccount.created_at`, capped at `gmail_daily_cap`, enforced in `send_message_impl` | With the flag on, `daily_allowance` also applies a weekly **stepped ceiling** anchored on the mailbox's **first actual send** (a mailbox connected a year ago that has never sent is new). Same function, same send-path call, `min()` only — it can never raise a cap. |
| Spam-rate brakes | `mailbox_health` deducts 35/65 points at 0.1%/0.3% of a **proxy** complaint rate | Explicit brakes (`brake_for`) that force **throttled** at ≥ 0.10% and **paused** at ≥ 0.30% regardless of the rest of the score, and the **real Google Postmaster spam rate** in place of the proxy when a fresh reading exists. |
| Complaint source label | `complaint_source` hard-coded `"proxy"` | Stored per mailbox row (`postmaster` or `proxy`), shown in the UI. |

## Behavior (flag on)

### 1. Pre-launch preflight — a safety guard

Runs on `POST /sequences/{id}/enroll`, `POST /sequences/{id}/approve` and
`POST /strategies/{id}/campaign/resume`, only when the sequence (or, for a
resume, any sequence of the strategy) has an email step. A LinkedIn/WhatsApp/
phone-only campaign is not checked. The mailbox checked is the one the send
path will use (`outreach_tasks._account_for_strategy`: the strategy owner's
connected Gmail account).

| Check | Pass rule | Blocks? |
|---|---|---|
| mailbox | a Gmail account is connected | yes |
| SPF | exactly one `v=spf1` record, and `include:_spf.google.com` is reachable through its `include:`/`redirect=` chain within RFC 7208's 10-lookup limit (the send path is the Gmail API, so Google's servers must be authorised) | yes |
| DKIM | a key with a non-empty `p=` under one of the selectors in `deliverability_dkim_selectors` (default `google`; comma-separated) | yes |
| DMARC | exactly one `v=DMARC1` record at `_dmarc.<domain>`, falling back to the organisational domain (last two labels); `p=` one of none/quarantine/reject | yes; `p=none` passes with a warning (Gmail's rule is "publish DMARC", any policy) |
| alignment | DMARC passes only if SPF or DKIM is aligned with the From domain. Gmail sends with envelope-from and DKIM `d=` equal to the mailbox domain once SPF/DKIM are set up for that domain, so alignment holds iff DMARC exists **and** (SPF ok **or** DKIM ok) | yes |
| one-click unsubscribe | the bytes `gmail.build_mime` produces carry `List-Unsubscribe: <https://…/unsubscribe/<token>>` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`; the URL is HTTPS (RFC 8058 §3.1); the token decodes back to the lead; `POST /unsubscribe/{token}` is a registered route; and in the RAW transmitted bytes neither header is RFC 2047 encoded (`=?`) or folded across lines (added 2026-10-12: the parsed check passed while `build_mime`'s default email policy encoded the ~230-character List-Unsubscribe value on every send; `build_mime` now uses `app/core/mime_policy.OUTBOUND_POLICY`, max line 998) | yes |

A consumer mailbox (gmail.com etc., `deliverability.CONSUMER_DOMAINS`) is
signed and authorised by its provider; SPF/DKIM/DMARC are reported as
"provider-managed" and pass. The header check still runs.

A DNS lookup that fails (timeout, SERVFAIL) is indistinguishable from "no
record" through `_txt`, so it **blocks** — the guard fails closed and the
message says "not found (or the DNS lookup failed)".

Every preflight that gates a launch writes one `deliverability_checks` row
(`kind="preflight"`) per mailbox domain with the full report, pass or fail —
that is the measurement for the success metric.

### 2. Send-time header guard

In `send_message_impl`, right after the email is rendered, an outbound email
without both one-click headers raises `ComplianceError` → the message is
`FAILED` with `blocked_compliance`, never transmitted (the existing
`ComplianceError` branch). Defence in depth: `build_compliant_email` always
sets them today; this stops a future change from silently removing them.

### 3. New-mailbox ramp (stepped, anchored on first send)

`sequence_engine.RAMP_SCHEDULE`: days since the mailbox's first send
0–6 → ≤ 10/day, 7–13 → ≤ 20, 14–20 → ≤ 30, 21–27 → ≤ 40, 28+ → no extra
ceiling. Combined with the existing linear ramp and every cap by `min()`. A
mailbox that has never sent from Vectorae starts at day 0 — Vectorae cannot
see its history elsewhere, and the cost of guessing wrong is the domain.
Sends over the allowance are **deferred, never dropped** (existing rule).
`GET /strategies/{id}/stats` shows the same cap the send path enforces.

### 4. Spam-rate brakes

`deliverability_autopilot.brake_for(rate)`:

| Spam rate | Brake | Effect (existing mailbox_health machinery) |
|---|---|---|
| ≥ 0.10% | throttled | daily cap cut to 25% of the ramped allowance (min 2); `deliverability_warning` alert when newly engaged |
| ≥ 0.30% | paused | no sends; messages deferred, never cancelled; `mailbox_paused` alert; resuming is a human decision |

The brake is the floor on severity: the score can make a state worse, never
better. The rate used is the Postmaster reading when one exists (below),
otherwise the existing proxy.

### 5. Google Postmaster Tools import — built, disabled, Unverified

`google_postmaster.GooglePostmasterV2` behind the `SpamRateSource` adapter
interface. Daily task: for each non-consumer sending domain of each user with
a Postmaster grant, query the last 7 days of `SPAM_RATE`, store the latest
dated reading as a `deliverability_checks` row (`kind="postmaster"`,
`source="google_postmaster"`), then re-score that user's mailboxes
(`check_dns=False`) so a brake applies the same day. A reading is used for
5 days after its date; older, the proxy returns.

What was verified on 2026-10-11 (documentation only — no live call):

| Fact | Source |
|---|---|
| v1 `trafficStats` is replaced by v2 `domains.domainStats.query`; v1 "will stop working" when v2 launches | [Migrate to v2](https://developers.google.com/workspace/gmail/postmaster/guides/migration-v2?hl=en); [Postmastery](https://www.postmastery.com/gmail-postmaster-tools-v1-is-retiring-what-you-need-to-know/) |
| `POST https://gmailpostmastertools.googleapis.com/v2/{parent=domains/*}/domainStats:query`; body `metricDefinitions[]`, `timeQuery`, `pageSize` (≤ 200), `pageToken`, `aggregationGranularity` (default DAILY) | [domains.domainStats.query](https://developers.google.com/workspace/gmail/postmaster/reference/rest/v2/domains.domainStats/query) |
| Scopes: `https://www.googleapis.com/auth/postmaster` or `https://www.googleapis.com/auth/postmaster.traffic.readonly` (we request the read-only one) | same page; [API setup](https://developers.google.cn/workspace/gmail/postmaster/guides/setup?authuser=4) |
| `MetricDefinition{name, baseMetric{standardMetric}, filter}`; `standardMetric` `SPAM_RATE` exists | [MetricDefinition](https://developers.google.com/workspace/gmail/postmaster/reference/rest/v2/MetricDefinition) |
| `timeQuery` is `dateList{dates[]}` or `dateRanges{dateRanges[{start,end}]}` (inclusive); response `domainStats[]{date, metric, value{doubleValue|floatValue|intValue|stringValue}}` + `nextPageToken` | [python client v2 reference](https://chromium.googlesource.com/external/github.com/google/google-api-python-client/+/65bbea2587587b7336eaca55989a8b82c8900a4e/docs/dyn/gmailpostmastertools_v2.domainStats.html) |
| v1's `userReportedSpamRatio` was a 0–1 ratio over DKIM-authenticated traffic | [v1 python reference](https://googleapis.github.io/google-api-python-client/docs/dyn/gmailpostmastertools_v1.domains.trafficStats.html) |

## Non-goals

- **No peer warm-up network** (STAR row: third-party inbox pools carry
  reputation and terms-of-service risk). The ramp is Vectorae's own volume.
- No connect flow for the Postmaster grant in this PR (see Unverified).
- No post-launch **block** on auth regressions: mailbox health already
  re-reads DNS every 4h and throttles (−55 points) — unchanged.
- No idle-mailbox re-ramp (a mailbox dormant for months is not re-ramped).
- No Yahoo/Microsoft complaint feeds (no comparable per-domain API).
- No change to the bounce auto-pause, the blacklist pause or the score bands.

## Data model and migration

`0075_deliverability_autopilot` (down_revision `0074_signal_radar`):
adds `mailbox_health.complaint_source VARCHAR(16) NULL`. NULL means "proxy"
(every row written before the migration, and every row written with the flag
off), so the API keeps answering `"proxy"` exactly as before. Downgrade drops
the column; the only information lost is which source a stored rate came
from. Preflight and Postmaster readings reuse `deliverability_checks`
(`kind` is `String(20)`; `"preflight"` and `"postmaster"` fit) — no new table.

## API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/deliverability/preflight?sequence_id=` | The report for one owned sequence (404 otherwise). Flag off → `{"enabled": false}` and no DNS. Rate-limited like `/deliverability/check`. Not recorded. |
| `POST` | `/sequences/{id}/enroll`, `/sequences/{id}/approve`, `/strategies/{id}/campaign/resume` | Flag on + failing preflight → **409**, `detail` = "Deliverability preflight failed: …" (a string — the frontend renders string details). |
| `GET` | `/deliverability/mailboxes` | `complaint_source` is now the stored source (`proxy` / `postmaster`). |

## UI

`MailboxHealthCard` labels the complaint rate from `complaint_source`
("(proxy)" or "(Google Postmaster)") instead of a hard-coded "(proxy)". The
409 from a blocked launch is shown by the existing launch error handling.

## Tenancy

Every query filters by owner: preflight reads the strategy owner's Gmail
account; Postmaster readings and preflight rows are written and read with
`user_id`; the preflight endpoint resolves the sequence through
`_owned_sequence`. Tests cover another account's sequence (404) and another
account's Postmaster reading never reaching this account's mailboxes.

## Compliance review (CAN-SPAM / GDPR / CASL / PDPA)

- Strengthens, never weakens: the preflight and the header guard only ever
  stop or defer mail. No new send path; the ramp and brakes act inside
  `send_message_impl` on the existing defer-never-drop path; suppression,
  consent, review queue and outreach gate are untouched.
- RFC 8058 one-click unsubscribe is now verified on the wire format before a
  launch and enforced per message — CAN-SPAM's opt-out and GDPR/CASL's
  withdrawal routes keep working by construction.
- Postmaster data is aggregate per domain (no personal data). The OAuth grant
  is the customer's own, stored encrypted in `integration_tokens`.
- Not legal advice; the preflight checks mailbox-provider requirements, not
  every regime's.

## AI cost and budget impact

None. No model calls. Cost is DNS lookups (≤ ~15 per preflight) and one
Postmaster API call per domain per day when enabled.

## Test plan

`tests/test_deliverability_autopilot.py` — offline, injected TXT resolver,
FakeChannel, fake HTTP for Postmaster:
SPF chain walking (direct, nested, redirect, missing Google, multiple records,
lookup limit); DKIM selectors and revoked keys; DMARC (missing, org-domain
fallback, p=none warning, invalid policy, duplicates); alignment; consumer
domains; header probe on the real MIME bytes (and failure when the base URL
is not HTTPS or a header is stripped); preflight rows recorded; launch blocked
on enroll/approve/resume with the flag on and identical with it off;
non-email sequences not checked; send-time header guard; stepped ramp
(anchor on first send, min with linear ramp and caps, flag-off identity);
brakes at 0.10%/0.30% overriding a high score; Postmaster reading replacing
the proxy and labelled; stale reading ignored; importer disabled by default,
parsing the v2 response, pagination, permission errors, tenancy; endpoint
404/flag-off/tenancy; settings defaults. `tests/test_mailbox_health_migration.py`
lists the column under `_ADDED_LATER` (precedent:
`tests/test_meeting_prep_migration.py`); `tests/test_deliverability_autopilot_migration.py`
covers 0072 up/down. Frontend: `mailboxHealth.test.ts` for the label.

## Success metric (measured, never promised)

- **Zero launches with failing auth**: count of `deliverability_checks` rows
  `kind='preflight' AND ok=false` whose sequence nevertheless has an
  enrolment created after `checked_at` — must be 0; and the share of launches
  with a `kind='preflight'` row at all (coverage). Read-only query, run in
  staging/pilot.
- **Real spam-rate data for connected domains**: share of non-consumer
  sending domains with a `kind='postmaster'` row in the last 5 days. **0%
  until the Postmaster connect flow ships** (see Unverified) — reported as 0,
  not estimated.

## Unverified

- **Postmaster Tools v2 against a live account.** Request/response shapes come
  from Google's reference pages above; no call has been made. In particular
  the **unit of `SPAM_RATE`** (0–1 ratio vs percent) is not stated on the
  MetricDefinition page. The adapter assumes a ratio
  (`SPAM_RATE_UNIT = "ratio"`, matching v1); if it is a percent every reading
  would be 100× too high and every domain would brake (fail-safe direction,
  but wrong). Confirm with one live call before enabling.
- **The grant.** There is no connect flow: the importer reads a user-scope
  refresh token from `integration_tokens` (provider `google_postmaster`, key
  `refresh_token`) and mints access tokens with the existing Google OAuth app
  (`GOOGLE_CLIENT_ID/SECRET`). The Postmaster Tools API must be enabled in
  that Google Cloud project, `postmaster.traffic.readonly` added to its
  consent screen (Google may classify it as sensitive and require app
  verification — not checked), and the Google user must have the domain
  verified in Postmaster Tools. Follow-up: an "Connect Postmaster Tools"
  OAuth flow.
- **Postmaster data availability**: Google only reports a domain with enough
  daily Gmail volume; a small pilot may never get a reading. The proxy then
  stays in use, labelled.
- **DKIM selectors**: custom Google Workspace prefixes or other providers'
  selectors must be listed in `deliverability_dkim_selectors`, or the
  preflight blocks (fails closed).
- **Organisational domain** for DMARC fallback uses the last two labels (no
  Public Suffix List); a `.co.uk` subdomain sender is looked up at
  `_dmarc.co.uk` and blocks until DMARC is published at the exact domain.
- Ramp numbers are conventional industry guidance, not measured on
  Vectorae traffic.
- Help-center article: `docs/help-center/` does not exist on this branch
  (Phase 2 not merged); the article update is a follow-up.
