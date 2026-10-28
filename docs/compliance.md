# Compliance Documentation — Vectorae

## Enforcement Architecture

Compliance is enforced in two places. The **send chokepoint** (`app/workers/outreach_tasks.py` `send_message_impl`) re-checks suppression, consent, send windows and caps for every channel, and builds every email through `sequence_engine.build_compliant_email` (headers + visible unsubscribe footer). The **WhatsApp adapter** additionally refuses a non-compliant message itself (`app/integrations/whatsapp.py` raises `ComplianceError`); the Gmail adapter only transports what the send path built. The API layer catches `ComplianceError` and returns a 422 with a `compliance_code` field. *(Corrected 2026-10-11: this said every adapter enforces compliance; the Gmail adapter does not.)*

---

## Email (CAN-SPAM / GDPR)

*Corrected 2026-10-11 against the code (platform-gaps item 8,
help-center-discoveries D6). The earlier rows named a `{unsubscribe_link}`
template token, a `GMAIL_DAILY_SEND_CAP` of 500, an HMAC token, an adapter-level
`ComplianceError` and a 20/day warm-up start; none of those is what the code
does. Line numbers are as of that date.*

| Rule | Implementation |
|---|---|
| Unsubscribe headers on every email | `sequence_engine.build_compliant_email` (`app/services/sequence_engine.py:515-531`) adds `List-Unsubscribe` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058). Since 2026-10-12 they are transmitted raw on one line (`build_mime` uses `mime_policy.OUTBOUND_POLICY`; before, the default email policy RFC 2047 encoded the long List-Unsubscribe value), asserted on the raw bytes by `tests/test_unsubscribe_headers_raw.py`. The send path always builds the email through it (`app/workers/outreach_tasks.py` `_render_email_outbound`). `GmailChannel.send()` only transports the pre-built headers (`app/integrations/gmail.py:284-287`); it does not itself raise `MISSING_UNSUBSCRIBE` — that refusal exists only on the debug send endpoint (`app/api/debug.py:269-276`). |
| Unsubscribe link in body | Not a template token. `build_compliant_email` appends a visible footer, `compliance_footer()` (`sequence_engine.py:507-512`): the configured `SENDER_IDENTITY` plus "Don't want these emails? Unsubscribe instantly: <url>". GDPR/UK and CASL recipients also get a region notice before it (`compliance_audit.region_notice`). |
| Unsubscribe token | `make_unsubscribe_token` (`sequence_engine.py:496-497`) encrypts `{"lead_id", "purpose": "unsubscribe"}` with the app's Fernet key (`app/core/crypto.py`), so non-users can unsubscribe without signing in. Route: `GET`/`POST /unsubscribe/{token}` (`app/api/unsubscribe.py:54,59`), path parameter, not `?token=`. |
| Suppression checked at send time | `send_message_impl` re-checks `is_suppressed` immediately before sending (`app/workers/outreach_tasks.py:205`), for every channel. |
| Bounce rate auto-pause | Above `BOUNCE_RATE_PAUSE_THRESHOLD` (`bounce_rate_pause_threshold`, default 0.03, `app/config.py:113`), after `bounce_min_sends`, the campaign state becomes `paused_bounce_rate` (`sequence_engine.py:618`). A workspace compliance rule may lower the threshold, never raise it. (The old `GMAIL_BOUNCE_PAUSE_THRESHOLD` name is not a setting; it was removed from `app/core/config.py` and the env examples on 2026-10-11.) |
| Daily send cap | `GMAIL_DAILY_CAP` (`gmail_daily_cap`, **default 100**, `app/config.py:106`), read by `daily_allowance` (`sequence_engine.py:166`), `compliance_rules` and `mailbox_health`; a compliance rule or a throttled mailbox can only lower it. Sends above the allowance are deferred to the next day's window, not dropped. (The old `GMAIL_DAILY_SEND_CAP` name, default 500, is not a setting; removed 2026-10-11.) |
| Warm-up | `gmail_warmup_start_sends` (10) plus `gmail_warmup_daily_increment` (10) per day since the account was connected, capped at the daily cap (`sequence_engine.py:165-167`, `app/config.py:107-108`). With the Deliverability Autopilot on, a stepped ramp can lower it further. (The old `GMAIL_WARMUP_START_DAILY` name is not a setting; removed 2026-10-11.) |
| Reply classification | "stop emailing me", "unsubscribe", "remove me" → classified `unsubscribe_request` → suppression + sequence stop |

---

## WhatsApp (Meta Policy)

| Rule | Implementation |
|---|---|
| Template-only for cold contact | `WhatsAppChannel.send()` raises `ComplianceError(WHATSAPP_NO_FREE_FORM_COLD)` for cold contacts |
| Template must be approved | Before sending, template status is checked via Meta API; `REJECTED` → `ComplianceError(WHATSAPP_TEMPLATE_NOT_APPROVED)` |
| Opt-in required | `WhatsAppChannel.send()` checks `whatsapp_optins` table; no opt-in → `ComplianceError(WHATSAPP_NO_OPTIN)` |
| 24-hour window for free-form | Customer-service window tracked per phone number; free-form outside window → `ComplianceError(WHATSAPP_WINDOW_CLOSED)` |
| STOP handling | `STOP` inbound message triggers atomic transaction: suppression + opt-out audit + sequence stop. All three or none (rollback on failure) |

---

## GDPR

*Re-checked against the code 2026-10-11 (gaps-sweep-2 #13). The earlier rows
named `first_name`/`last_name`/`raw_data` columns, a `deleted_at` column, a
`lead_tombstones` table, `lead_service.source_leads()` and a
`?format=csv` export; none of those exists. Legal characterisation (which
Article each mechanism satisfies) needs founder/legal confirmation.*

| Right | Implementation |
|---|---|
| Erasure of a prospect | `DELETE /leads/{id}` (`app/api/leads.py` `gdpr_delete_lead` / `_gdpr_erase_lead`): writes a consent-ledger erasure event first, then clears `full_name`, `title`, `email`, `phone`, `company`, the LinkedIn URL and profile fields, posts/news, scores and their reasons and provenance values; sets `enrichment_json = {"gdpr_deleted": true}` and status `dropped`; scrubs the correspondence (reply sender/subject/body/AI draft, Message-ID headers, every message subject/body; anything still scheduled is cancelled) via `account_erasure.erase_lead_correspondence`, plus signals, drafts and Champion Tracker records; the same person's Champion-Tracker-linked lead rows in the same account are erased too. Every live enrollment of the lead is stopped (reason `lead_removed`) in the same commit (`sequence_engine.stop_for_status`, since 2026-10-12). |
| Tombstone | The lead row is kept with its id and provider `external_id` only; there is no `deleted_at` column. The `(strategy_id, source, external_id)` unique key stops the same provider record being inserted again in that strategy. |
| No re-contact, no re-sourcing | The erased person's email and phone go onto the suppression list, and their LinkedIn profile onto `linkedin_suppressions` (reason `gdpr_delete`). The sourcing chain checks suppression before inserting (`app/workers/lead_tasks.py` `is_suppressed`), and every send path re-checks all three identifiers at send time. |
| Access / portability (account holder) | `POST /me/data-export` (password re-typed; `app/api/account_data.py`, `app/services/account_export.py`): one JSON file of every row the account owns. There is no CSV lead export. |
| Access (a prospect asking what is held about them) | No self-serve endpoint for the prospect. The account owner can read the lead (`GET /leads/{id}`, `GET /leads/{id}/provenance`) and answer the request. |
| Erasure of the account | `DELETE /me` with the password and `confirm: "DELETE"` (`app/services/account_erasure.py`, spec `docs/features/gdpr-erasure.md`). Billing records are kept for `BILLING_RETENTION_YEARS` (7) and consent and suppression records are kept, by founder decisions E1–E3 (retention periods awaiting founder/legal confirmation). |

---

## Verification Gating

Before lead sourcing can begin on a strategy, **all 10 verification passes must be PASS**. If any pass has `result=FAIL`, `POST /strategies/{id}/leads/source` returns 422. This prevents unresearched or non-compliant strategies from going live.

---

## Suppression List

The suppression list is checked:
1. At lead sourcing time (pre-import filter)
2. At email send time (last-moment guard)
3. At WhatsApp send time

An email or phone number added to the suppression list is never contacted again. Admin can add entries globally; users can add entries for their own campaigns.

---

## Audit Trail

Every compliance-relevant action is an event in the `outcomes` table:
- `unsubscribed` — from email link or reply
- `opted_out` — WhatsApp STOP
- `bounced` — email bounce
- `suppressed` — blocked at send time
- `gdpr_deleted` — right-to-erasure exercise

The `whatsapp_opt_out_audit` table (separate from outcomes) stores the full STOP message payload for regulatory purposes.
