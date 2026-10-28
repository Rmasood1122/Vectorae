# Email network — warm B2B matches from the people you already email

Status: built 2026-10-12, behind `email_network_enabled` (default **off**), Growth plan and up.
Migration `0090_email_network`. Spec and code ship in the same PR (founder decision 2026-10-10).

## 1. Problem

Founder request (2026-10-12): *"every one upload his mails data same like LinkedIn — Vectorae
filters them and all everything like LinkedIn"*. A founder's best first customers are usually
people they already email. The LinkedIn network covers LinkedIn connections only.

## 2. Recon — this EXTENDS the LinkedIn network

| Existing | Reused how |
|---|---|
| `linkedin_network._profile` / `_score` / `seniority` / `function_of` | Same ICP scoring of title + company, via public aliases `icp_profile` / `icp_score` (aliases only; LinkedIn behaviour and tests unchanged). |
| `linkedin_network.analyze_user` / `refresh_all` / `erase_user_network` | Mirrored for contacts (`email_network.py`), same weekly beat slot style, same queue. |
| `api/linkedin_network._require_plan` | Same tiers (Growth, Scale, Pro, Enterprise), own PLANS key `email_network` (added to every plan) so the 402 names the right feature. |
| `network_messages` (draft → save → send, drafts never sent) | Mirrored as draft → save → **attach**; see §5 for why there is no one-off send. |
| `guardrails.CONSUMER_DOMAINS` | THE consumer-domain list; the email network adds ISP / regional providers on top (in its own module, so F05 behaviour does not change). |
| `lead_tasks.verify_emails` + `finalize_lead_batch` | "Add to campaign" makes a lead batch and runs exactly these stages (F01 verification, spend guard, scoring). |
| `outreach_tasks.send_message_impl` + `sequence_engine.build_compliant_email` | The only place a saved first email is ever sent. |
| `account_export.OWNERSHIP`, `account_erasure.DELETED`, `api/leads._gdpr_erase_lead` | Four new tables registered; a lead's GDPR erasure also erases that person's contact row. |
| `integrations/gmail.py` (scopes include `gmail.readonly`; `_gmail_http`, `get_valid_access_token`) | Gmail scan uses the existing grant and token refresh. |

## 3. Behaviour

### A. Imports — `POST /email-network/import` (one file, raw body)
* Formats detected by **content**: Google Contacts CSV (new and old headers, `:::` multi-address
  cells), Outlook CSV, any CSV with an e-mail column (`,` `;` or tab; UTF-8 / BOM / UTF-16 /
  cp1252), vCard 2.1/3/4 (folded lines, quoted-printable names, `item1.` groups, `mailto:`),
  mbox.
* **mbox is streamed** from `request.stream()` and parsed line by line; it is never held whole
  and never spooled to disk (no multipart parser is involved — Starlette spools multipart files
  over 1 MB to a temp file). The server reads through the file to find the header fields
  From, To, Cc, Date, List-Unsubscribe, List-Id, Precedence and Auto-Submitted and keeps only
  those — every other header (Subject, Message-ID, …) and every body line is discarded as it is
  read. A message separator must be a real `From <sender> <asctime date>` line, so an unescaped
  "From …" sentence in a body is never a new message. Parsing, storing and scoring run in the
  thread pool, never on the event loop; imports are rate-limited (`RATE_LIMIT_AI_ACTION`).
* **Bounds (review round H1).** 300 MB / 50,000 messages per mbox; contacts file 20 MB /
  50,000 rows / 100,000 addresses, at most 10 addresses per cell, a CSV cell over 128 KiB is a
  clean 422; at most 50 recipients counted per message (only the first 16 KB of a To/Cc is
  parsed); at most 50,000 people per upload, 200,000 hashes for counting excluded people, and
  100,000 contacts per account. Every cap that was hit is reported in `cap_note`. vCard
  unfolding is linear and PHOTO/LOGO/KEY/SOUND/NOTE and other unread properties are skipped
  without being assembled.
* Plan, flag, role (owner/manager) and the attestation are checked **before** the body is read.
* Own addresses = account email + connected Gmail address + optional typed addresses (sent in
  the `X-Own-Addresses` header, never the URL, so they stay out of access logs); their
  non-consumer domains AND their subdomains count as colleagues.
* Per correspondent: email (lower-cased), name, domain → company guess (no website fetched,
  marked `company_from_domain`), title (contacts files only), sent / received counts, first /
  last seen, two-way. Outgoing = From is one of the user's own addresses (or Gmail `SENT`);
  incoming counts only the sender. Gmail `DRAFT` messages are skipped.
* Counts are kept **per source** (`mbox`, `gmail`) and combined by MAX, never summed, so a
  re-import of the same file, or a file plus a Gmail scan of the same mailbox, never inflates
  them. A contacts file and mailbox data merge by address.

### B. Gmail scan — `POST /email-network/gmail-scan`, `GET /email-network/gmail-scan`
* Celery task `leadpilot.network.gmail_scan_chunk`, one list page (100) per chunk, chunks 30 s
  apart; last 12 months (`q=after:<epoch seconds>`), max 5,000 messages; `format=metadata`,
  `metadataHeaders` = the eight headers above, `fields=id,internalDate,labelIds,payload/headers`
  (so not even the snippet is returned). No model calls.
* Idempotent: a queued / running scan is returned as-is (a running scan silent for 2 h is
  failed, its tallies cleared, and may be restarted). One active scan per account is enforced
  by the partial UNIQUE index `uq_email_network_imports_active_scan`. One new scan per hour
  after the last one STARTED, whatever became of it (done, failed, reconnect), plus the
  per-account `RATE_LIMIT_AI_ACTION` limiter. Duplicate chunk delivery is harmless: tally,
  page token and `chunk_seq` are committed with a conditional UPDATE on `chunk_seq`.
* Only the account OWNER may start a scan (it reads the owner's own mailbox); a workspace
  manager gets 403. Vectorae stores one Gmail connection per account; if there were ever
  several, the oldest (created_at, id) is used.
* Tenancy: the Gmail account is looked up by the scan row's own `user_id`; no parameter selects
  an account. No `gmail.readonly` (or modify / mail.google.com) in the stored grant → 409
  `reconnect_gmail`; a 401/403 mid-scan → status `reconnect_required`.
* While a scan runs, its running tallies sit in `email_network_imports.tally_json` (addresses
  that passed the fixed filters, plus SHA-256 prefixes of excluded addresses for counting);
  cleared when the scan finishes or fails on ANY path, and by the weekly sweeper for scans
  silent for 2 h. Every chunk re-checks the people it touched against suppression and the F05
  lists and drops them; a lead erasure or an unsubscribe also scrubs the address from running
  tallies.

### C. Filtering — `app/services/email_network_filter.py`, one reason per person
`invalid` → `own_address` (you + colleagues) → `personal_domain` → `role_inbox` → `automated`
(no-reply style local parts; senders seen only through List-Unsubscribe / List-Id /
Precedence bulk|list|junk / Auto-Submitted≠no / Gmail Promotions|Social|Updates|Forums mail
**that the user never wrote to**) → `suppressed` (global list: unsubscribed, bounced, erased)
→ `excluded` (this account's F05 lists by address, domain incl. parent domains, or company —
applied whether or not `guardrails_enabled` is on; hiding is always safe). Excluded people are
never stored; the import row keeps counts by reason; the UI shows
"1,240 people found, 610 excluded: 300 newsletters / automated senders, 120 personal email
domains, …". Re-checked at every analysis, at recommendation read time, and before
draft / add / attach.

### D. Scoring
`score = round(0.6 × LinkedIn ICP score) + relationship (0–40)`; relationship = two-way 20 +
recency (≤6 mo 12 / ≤12 mo 8 / ≤24 mo 4) + volume (log-scaled, 8 at ≥50 messages). A contact
with **no ICP fit scores 0** (a strong relationship alone is not a product match). Match
threshold 30, top 500 per product. Recommendations are ordered two-way first, then score
("people with no two-way exchange rank lowest"). The why is built only from counts and dates:
"You emailed each other 14 times, last in Aug 2026", "They emailed you 3 times, last in Mar
2025; you have not written to them", "In your contacts file; no emails between you were
found", then the ICP reasons. Weekly refresh Monday 04:25 UTC (flag on, eligible accounts only).

### E. Using matches
* **Add to campaign** (`POST /email-network/add-to-campaign`, ≤100): contacts become ordinary
  `Lead` rows (`source="email_network"`, status `email_found`, provenance `import` /
  `inferred` for a guessed company) in a lead batch; the normal **verify → finalize** chain
  runs on it (F01 / waterfall, spend guard, lead scoring). Only `verified` leads are enrollable
  (`ENROLLABLE_STATUSES`), through the campaign's **normal launch / enroll** (lawful-basis
  confirmation, review gate, SDR approval, plan, deliverability preflight). Plan lead room per
  strategy is honoured; duplicates in the campaign are skipped.
* **Warm first email**: `draft` (one model call, `metered(..., "drafting")`, F-AI-02
  delimiting of name / title / company), `save` (the user's words; wording that claims more
  relationship than the counts show is refused 422), `attach` (to the contact's lead in a
  chosen campaign; creates the lead + verification batch if needed), `detach`.

## 4. Non-goals
No shared pool; no body / subject / signature parsing (titles come only from contacts files);
no website fetching for company names; no IMAP / Outlook API; no one-off send (§5); no
auto-enrollment.

## 5. Decision: no one-off send — the saved email becomes the campaign's first email

The brief asked for "send" to create an email `Message` through the existing chokepoint, or the
next option if that is not clean. It is not clean: `send_message_impl` needs a lead, a sequence
and a live enrollment (`enrollment is None` → cancelled), and a one-off would need a fake
enrollment (a COMPLETED / stub enrollment and an `origin` would change the closed-lead and
review-reject semantics that key on `origin IS NULL`). Enrolling from this feature would skip
the launch gates of `POST /sequences/{id}/enroll`. So:

* **Nothing in this feature transmits or enrolls** (a guard test greps the six modules).
* A SAVED email attached to a lead is used by `send_message_impl` **as that lead's step-1
  EMAIL** (`email_network_messages.saved_opener`; origin NULL, step 1, email channel, owner
  match), rendered by `_render_saved_opener_outbound`: claim check, `build_compliant_email`
  (unsubscribe link + postal address + region notice + RFC 8058 headers), no tracking pixel.
  Region (review H2): the lead gets `country` from its ccTLD (`.de` → DE, `.co.uk` → GB;
  generic ccTLDs such as .io / .co are ignored). An `email_network` lead whose region stays
  unknown is treated as the STRICTEST regime on every step, follow-ups included: the GDPR
  right-to-object notice is added and no open-tracking pixel is used
  (`compliance_region.strictest_when_unknown`).
  Every gate before render (suppression, F05, departures, plan holds, Bounce Shield, consent
  rules, window, **daily send approval** — it counts as automatic — caps) and after it (review
  queue, forbidden phrases, erasure re-claim) applies unchanged. The SENT outcome is tagged
  `source=email_network_saved_opener`.
* The row is marked `used` and linked to the message by a conditional UPDATE (a concurrent
  detach wins); a draft row is never rendered. If that campaign message is later rejected,
  cancelled or failed, the contact can be detached / redrafted.
* Draft, save, attach and detach are conditional UPDATEs on the expected status (409 when the
  row changed underneath), so an unsaved AI draft can never end up attached.
* Deleting an ATTACHED (not yet used) first email — DELETE /email-network or the single-contact
  delete — marks the lead; when its step-1 email comes due it is HELD in the review queue with
  the campaign's own words (trigger "Your saved first email was deleted") instead of being
  silently written by AI. Said in the confirm dialog and the help center.
* For every email to an `email_network` lead, the chokepoint also (a) skips it when the CASL
  two-year rule now fails (`skipped_casl_window`, current contact row or the data recorded on
  the lead), and (b) refuses the FINAL words — including a reviewer's edit — when they contain a
  common over-claim phrase the email history does not support (handled like a forbidden
  phrase).

## 6. Data model (migration 0090) and data minimisation

| Table / field | Why it is needed |
|---|---|
| `email_contacts.email`, `domain` | Identify the person, dedupe, filter, become a lead's address. |
| `full_name` | Show who it is; personalise a draft. |
| `company`, `company_from_domain` | ICP scoring; the flag tells the user (and provenance) it is a guess. |
| `title` (contacts files only) | ICP scoring (seniority / function). |
| `sent_to_count`, `received_from_count`, `two_way` | Relationship strength; honest wording; ranking. |
| `first_seen`, `last_seen` | Recency; CASL two-year check; "last in Aug 2026". |
| `in_contacts_file`, `source_counts_json` | Source flags; MAX-merge without double counting. |
| `email_contact_matches.*` | Score, parts, segment, reasons per product (no new personal data). |
| `email_network_imports.*` | Attestation time + version, counts, excluded counts by reason (numbers only), cap note; Gmail page token / chunk seq; transient `tally_json`. |
| `email_network_messages.subject`, `body` | The **user's own** draft; links to strategy / lead / message it became. |

Never stored: subjects, bodies, Message-IDs, References, snippets, attachments, signatures,
other headers, excluded people (only hashes, transiently, during a running scan). FKs:
`user_id` CASCADE; matches / messages → contact CASCADE; messages → strategy / lead / message
SET NULL. Downgrade drops the four tables (re-importable; campaign leads are untouched).

## 7. API
See the module docstring of `app/api/email_network.py`. Status, recommendations, messages list
and DELETE work with the flag off (DELETE is never plan- or flag-gated). Import needs owner or
manager (an SDR cannot import a mailbox); Gmail scan needs the OWNER. Viewers and SDRs can read
the contacts list and recommendations, exactly as with the LinkedIn network.
`DELETE /email-network/contacts/{id}` deletes one contact (owner-scoped, 404 otherwise) with
its scores and saved first email.

## 8. UI
Network page → tabs **LinkedIn** (default, unchanged) | **Email contacts**
(`frontend/src/components/network/EmailNetworkPanel.tsx`): attestation tick (enables upload
and scan), format help, own-addresses field, file picker, Gmail scan button + live status,
import summary with exclusion reasons, per-product warm matches (score, relationship badge,
why, CASL note), multi-select **Add to campaign**, draft / save / **Use as their first email**
/ detach, **Delete my email contacts** (shown whenever contacts exist, any plan).

## 9. Tenancy
Every query filters on `user_id` (contacts, matches, messages, imports) or on the product's
`user_id` (strategies). Foreign ids answer 404. The chokepoint render requires the row's
`user_id` to equal the strategy owner. A Gmail scan only ever uses the scanning account's own
`GmailAccount`.

## 10. Compliance review — every reading below needs founder/legal confirmation

* **GDPR (EU/UK).** Controller of the contacts' data = the account (Vectorae processes on its
  behalf). Lawful basis relied on: the account's legitimate interest (Art. 6(1)(f)) in
  contacting **existing business contacts** about relevant offers; the attestation records the
  user's statement that they are their own business contacts. Data minimisation (Art. 5(1)(c)):
  headers only, filtered before storage (§6). Art. 14 information duty on first contact: the
  existing region notice / footer that `build_compliant_email` adds to every email to an EU/UK
  lead (`compliance_audit.region_notice`) — whether that notice is a sufficient Art. 14 notice
  is itself unconfirmed. Art. 21 objection = the unsubscribe link (suppression is global and
  checked at send time). Art. 15/17: export and erasure cover the four tables; a lead's erasure
  removes the contact row. The privacy policy (`docs/PRIVACY_POLICY.md`) does **not** yet
  describe this feature — not edited here (legal text, out of scope).
* **PECR / ePrivacy.** Unsolicited marketing email to individual subscribers needs consent or the
  soft opt-in; corporate subscribers are outside reg. 22. Personal-domain addresses are
  excluded so only corporate addresses remain. A sole trader on a business domain is an
  individual subscriber under PECR — not detectable; relies on the attestation.
* **CAN-SPAM.** A commercial email to an existing contact still needs the opt-out and the
  postal address: every send goes through `build_compliant_email`. Common phrases that claim a
  meeting, a call or a conversation that the email history does not show are refused at save
  and again on the final words at send (a pattern list, not a guarantee against every
  phrasing).
* **CASL (Canada).** Implied consent for an existing business relationship (s.10(10)) lasts two
  years from the purchase / contract / inquiry; the tool cannot see purchases. Decision: a
  `.ca` address is refused for campaign use unless there was a **two-way** email exchange within
  the last two years (and it is shown with the reason), and the same rule is re-checked AT SEND
  for every email to an email-network lead. Two-way email is only a proxy for a business
  relationship; Canadians on non-`.ca` domains are not detected. Both limits are stated next to
  every CASL notice on the Email contacts tab and in the help center.
* **PDPA (Singapore and others).** DNC registry rules apply to phone numbers, not email; the
  feature stores no phone numbers. Consent / deemed-consent questions for email marketing
  under PDPA are not specifically handled beyond the attestation and the unsubscribe path.

## 11. Plan and flag
Plan: same tiers as the LinkedIn network (Growth, Scale, Pro, Enterprise; not Pay per meeting) —
it is the same feature for another source; the email channel itself is on all plans, so no
reason to differ. Flag `email_network_enabled` (system_settings, default **False**) because
the legal readings above are unconfirmed. Flag off: import / scan / draft / save / attach /
add refuse with 409; status, stored recommendations, messages list and DELETE keep working; a
saved email already attached is still sent by the normal path with every check (the flag gates
entry points, not sends); the weekly refresh does nothing.

## 12. AI cost
Only drafting calls a model: one `complete_json` per draft, `max_tokens` 700, prompt ≈ 400
input tokens → ≈ 400 × $3/M + ≈ 250 × $15/M ≈ **$0.005 per draft**, metered under the capped
purpose `drafting` (account daily AI cap applies). Import, scan, filtering, scoring and the
weekly refresh call no model. Verification of added leads costs what any lead's verification
costs, through the existing spend guard.

## 13. Test plan (all offline)
* `tests/test_email_network_guards.py` — the legal / safety guards (headers only incl. a Gmail
  reply carrying a snippet, Subject and Message-ID; no temp files; filters; suppression /
  exclusion / erasure; tenancy incl. Gmail; chokepoint footer + headers, daily approval hold,
  suppression cancel; no transmit code in the feature; draft never attachable or rendered;
  relationship wording; CASL; attestation; export / erasure coverage; plan / flag / role).
  Proposed for sealing.
* `tests/test_email_network.py` — parsers with quirks (BOM, `;`, UTF-16, cp1252, folded and QP
  vCards, mbox CRLF, From_ escaping, a "From " body line, chunk boundaries 1/7/64 bytes,
  RFC 2047 names), aggregation and MAX-merge, scoring and ordering, Gmail paging / duplicate
  chunk / cap / 403 / 429 / idempotency / cooldown / task requeue, add-to-campaign, attach /
  detach, API.
* `tests/test_email_network_migration.py`, `tests/integration/test_migration_0090_email_network_pg.py`.
* `frontend/src/tests/emailNetwork.test.ts`, `emailNetworkPage.test.tsx`.

## 14. Success metric (measured, not promised)
Share of imported accounts that add ≥1 contact to a campaign within 7 days; reply rate of
`email_network` leads vs. sourced leads in the same campaigns (SENT outcomes tagged
`email_network_saved_opener` for saved first emails).

## 15. Unverified (not proven against a live vendor or production)
* Gmail: repeated `metadataHeaders` encoding (`metadataHeaders=From&metadataHeaders=To…`, the
  standard Google convention), the error codes for an expired / narrowed grant (401 and 403 are
  both treated as reconnect), and that `fields` actually removes `snippet` in practice (the code
  ignores it either way). Reference pages read 2026-10-12: users.messages.get / list, Format,
  guides/filtering (`after:` seconds), guides/performance (`fields`), reference/quota (6,000
  units / user / minute; get 20, list 5), guides/labels (`SENT`, `DRAFT`, `CATEGORY_*`).
* Whether existing production Gmail grants all include `gmail.readonly` (it is in
  `GMAIL_SCOPES`; the stored `scopes` string is checked).
* Real Google Takeout / Outlook / iCloud exports beyond the fixtures in the tests.
* Throughput of a 300 MB mbox in the async request (parsing is CPU-bound in the event loop,
  chunk by chunk); request timeouts at the proxy (Render) for very large uploads.
* Every legal reading in §10.

## 16. Limitations
* A sender who is both a person and a list sender stays only if the user wrote to them.
* Contacts-file-only people have no relationship evidence and rank with one-way contacts.
* "Colleagues" = the user's own non-consumer domains only; a colleague on another company
  domain is not detected.
* A saved first email is only used if the campaign's sequence opens with an EMAIL step and the
  lead is enrolled before another step-1 email exists for it.
* An mbox upload that hits a cap stops reading the request body early.

## 17. Review round (2026-10-12)

The reviewer's probes are in `scratchpad/review-en/wt/tests/test_probe_en_review.py`. Every finding below has a
ported test. "Pre-fix" is that test's result on the snapshot of the tree taken before this
round (`scratchpad/prefix`). Some tests only kill mutations: they assert behaviour that was
already right, so they PASS pre-fix and FAIL under their mutation. I re-ran the reviewer's
`mutate.py`, with strings updated only where this round rewrote the line (M11, M21): 24 of 24
mutations are killed.

| Finding | Fix (file:line) | Test | Pre-fix |
|---|---|---|---|
| H1 no people cap; event loop blocked | `email_network_parse.py:47-53` caps, `Tally._touch` / `_exclude` bounds; `email_network.py:43` account cap + batched `store`; `api/email_network.py:168` rate limit, `:213`/`:224` `run_in_threadpool` (feed / close too) | `test_email_network.py::TestBoundsH1` (6) | FAIL 6/6 |
| H2 no region for email-network leads | `email_network_messages.py:492` ccTLD → `country`; `compliance_region.py:141` `strictest_when_unknown`; `compliance_audit.py:67` notice | `guards::TestRegionH2` (3: .de, .com unknown, US control) | FAIL 3/3 |
| M1 body "From …" taken as a separator | `email_network_parse.py:423` `is_from_line` (used by `_line` and `detect`) | `guards::TestRolesAndPrivacy::test_a_from_sentence_in_a_body_never_makes_contacts`; `TestRobustness::test_detect_needs_a_real_from_line` | FAIL |
| M2 stale / failed scan keeps its tally | `email_network_gmail.py:145` `_mark` on stale; `:166` `sweep_stale` (weekly); `:300` per-chunk drop; `email_network.py:415` `scrub_scan_tallies` (erasure, unsubscribe) | `guards::TestErasureScrub` (stale sweep, restart, running-scan suppression + erasure) | FAIL |
| M3 CASL checked only at add | `email_network_messages.py:253`; `outreach_tasks.py:535` (`skipped_casl_window`, lookup error defers) | `guards::TestCaslAtSend::test_a_canadian_contact_past_two_years_is_skipped_at_send` | FAIL |
| M4 draft/attach race | `email_network_messages.py:117` `_transition` (draft, save, attach, detach); conditional claim in `saved_opener` | `guards::TestRaceM4` | FAIL |
| M5 thin over-claim check | `email_network_messages.py:169` wider `_SPOKEN` / `_RECENT`; `:242` + `outreach_tasks.py:906` re-check on the final words (reviewer edits) | `guards::TestHonestWordingReview` | FAIL for the 6 phrases + the reviewer-edit test; the 2 controls pass |
| M6 overlong domain → PostgreSQL DataError leaking parameters | `email_network_filter.py:137` RFC limits; `app/db/base.py:37` `hide_parameters=True` | `guards::TestRolesAndPrivacy::test_overlong_domains_are_rejected_and_sql_parameters_hidden` | FAIL |
| M7 CSV cell > 128 KiB → 500 | `email_network_parse.py:601` `_safe_rows` (+ the header read) | `TestRobustness::test_a_csv_cell_over_128k_is_a_422_not_a_500` | FAIL |
| M8 quadratic vCard unfolding | `email_network_parse.py:621` linear unfold; unread properties skipped | `TestRobustness::test_vcard_unfolding_is_linear_and_skips_photos` | FAIL |
| LOW role / automated variants | `email_network_filter.py:181` | `guards::TestBlockedEverywhere::test_role_inbox_variants`, `test_automated_dot_variants` | FAIL (the 2 dot-forms of role inboxes were already caught) |
| LOW consumer domains | `email_network_filter.py:95` brand pattern + list | `guards::…::test_consumer_domain_variants` (+ a business-domain control) | FAIL |
| LOW own subdomains | `email_network_filter.py:238` | `guards::…::test_own_subdomains_are_colleagues` | FAIL |
| LOW owner-only Gmail scan | `api/email_network.py:259`; oldest account deterministic, `email_network_gmail.py:88` | `guards::TestRolesAndPrivacy::test_only_the_owner_can_scan_gmail` | FAIL |
| LOW rejected opener blocks the contact | `email_network_messages.py:546` `opener_dead` (draft, detach) | `guards::TestDeletes::test_a_rejected_first_email_does_not_block_the_contact` | FAIL |
| LOW DELETE swaps the opener for AI copy | `email_network.py:353` flag; `email_network_messages.py:267` hold; `outreach_tasks.py:852`; confirm dialog `ERASE_CONFIRM` | `guards::TestDeletes::test_delete_all_holds_an_attached_first_email_for_review` | FAIL |
| LOW no single-contact delete | `email_network.py:403`; `api/email_network.py:236` | `guards::TestDeletes::test_delete_one_contact_is_owner_scoped` | FAIL |
| LOW unsubscribed / erased rows linger | `email_network.py:447` `purge_blocked` in `refresh_all` (any flag or plan); `:473` `purge_address` from `sequence_engine.py:741` and `api/unsubscribe.py:60` | `guards::TestErasureScrub::test_an_unsubscribe_removes_the_contact_at_once`, `test_the_weekly_refresh_purges_whatever_the_flag_or_plan` | FAIL |
| LOW help text not true (F05 at send, "never reads", CASL limits) | `docs/help-center/11-linkedin.md`; this spec; `EmailNetworkPanel.tsx` `CASL_LIMITS` | help-center suite (recall 0.966) | n/a (docs) |
| LOW own addresses in the URL | `api/email_network.py:146` header; `frontend/src/lib/api/emailNetwork.ts` | `guards::TestRolesAndPrivacy::test_own_addresses_are_read_from_a_header_not_the_url`; vitest wire test | FAIL |
| LOW cooldown / concurrent scans | `email_network_gmail.py:149`; partial UNIQUE index `models.py:5074`, `0090_email_network.py:99` | `TestScanControl` (3) | FAIL |
| LOW migration docstring claim | `0090_email_network.py` docstring (51 characters) | `test_email_network_migration.py` | n/a |
| Surviving mutations M1–M24 | — | `guards::TestOpenerRender` (M24, M3, M19), `TestCaslAtSend` (M6), `TestBlockedEverywhere` (M1, M2, M7, M8, M11, M16, M17, M18), `TestErasureScrub` (M4), `TestHonestWordingReview` (M5), `TestRobustness` (M13, M14) | PASS pre-fix; each FAILS under its mutation |

**Not reproduced by the reviewer, and not changed:** body text reaching `full_name` because a
header/body blank line is missing (getaddresses drops the malformed value).

**Probe that still "passes" on the fixed tree:** the reviewer's
`test_casl_window_not_rechecked_at_send`. It sends at `NOW + 63 days` (2026-10-13) but
evaluates `casl_block` at real-now + 63 days. At the time it actually sends, the last two-way
exchange is 700 days old, which is inside the 730-day window, so "sent" is correct. The ported
test uses a single clock, and there the send is skipped as required.
