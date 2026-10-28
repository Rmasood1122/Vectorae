# F05 — Guardrails Studio

**Flag** `guardrails_enabled` (system setting, default **off**) ·
**Migration** `0072_guardrails_studio` · **Service** `app/services/guardrails.py` ·
**API** `app/api/guardrails.py` · **UI** Settings → Deliverability →
"Guardrails Studio" (`frontend/src/components/settings/GuardrailsCard.tsx`,
`frontend/src/lib/guardrails.ts`, `frontend/src/lib/api/guardrails.ts`)

Status: built behind the flag (founder decision 2026-10-10: spec and code in the
same PR, reviewed at merge).

## 1. Problem

Competitor complaint (STAR A.6, F05): **brand damage — pitching current or
churned customers, ignoring opt-outs.** Scorecard row 5: *0 sends to excluded
entities.*

What Vectorae already had before F05 (recon):

| Building block | What it does | Gap F05 closes |
|---|---|---|
| `suppression_list` + `linkedin_suppressions`, `lead_tasks.is_suppressed` | Opt-outs (unsubscribe, STOP, GDPR erase). **Deployment-global**, contact-level only (email / phone / LinkedIn). Checked at sourcing, enrichment, email discovery, enrollment and at send time. | No per-account list, no **domain** or **company** level, no way to say "never pitch anyone at Acme, they are our customer". |
| `send_message_impl` (`app/workers/outreach_tasks.py`) | The send chokepoint — every sequence, follow-up and re-engagement send goes through it. | Nothing account-specific is checked. |
| `crm_sync` (HubSpot / Salesforce) | Two-way sync of records Vectorae **linked**; never imports CRM-only records. | The CRM knows who the customers are; Vectorae never asks. |
| `deals` table, lead `closed_won` | Local record of open and won deals. | A won deal at Acme does not stop a *different* lead at Acme being pitched in another campaign. |
| `claim_verification` (A1) | Strips unsupported claims **about the prospect**. | Explicitly ignores claims about **us** ("We helped 40 agencies grow 3x") — those are unchecked. |
| `adversarial_review` (A7, pre-launch outreach gate) + `send_review` (human queue) | Fixed spam/tone lists. | No per-product "never say this" list. |
| `compliance_audit_log`, `security_audit_events` | Per-send decision log; who-did-what log. | Nothing records exclusion-list changes or exclusion blocks. |

Less than half of F05 exists, so this is **new capability extending existing
chokepoints** — not a parallel send path. Opt-outs stay where they are
(`suppression_list`, global, always on); F05 adds the account-owned layer on top.

## 2. Behavior

### 2.1 Exclusion lists (per account)

An **exclusion** is `(kind, value, reason, source)` owned by one account
(`user_id` = the product owner, the same tenant key every other table uses).

| kind | value stored (normalised) | matches a lead when |
|---|---|---|
| `domain` | `acme.com` (lower-case; scheme, path, `www.`, `@` stripped) | the lead's **email domain** or its **company domain** (`enrichment_json.company_domain`) equals it **or is a subdomain of it** (`eu.acme.com` matches `acme.com`) |
| `company` | `acme` (lower-case, punctuation removed, trailing legal suffixes such as Inc / LLC / Ltd / GmbH removed) | the lead's company, normalised the same way, is **equal** (exact after normalisation — no fuzzy matching) |
| `contact` | `jane@acme.com`, `+15551234567`, or `linkedin:jane-doe` | the lead's email, phone (`+digits`) or LinkedIn profile slug is equal |

`reason` ∈ `customer`, `open_deal`, `churned`, `partner`, `competitor`,
`do_not_contact`, `other`. `source` ∈ `manual`, `csv`, `crm_local`,
`crm_hubspot`, `crm_salesforce`.

Removal is a **soft delete** (`removed_at`), so the production audit query can
tell whether an exclusion was active at the moment of a send. Re-adding a
removed value reactivates the same row and resets `active_since`.

**Sources**

1. **Manual** — `POST /guardrails/exclusions`.
2. **CSV** — `POST /guardrails/exclusions/import` with the file's text. Header
   row required. Either `type,value[,reason,note]` columns, or any of
   `domain`, `company`, `email`, `phone`, `linkedin` columns (every non-empty
   cell becomes one exclusion). Max 10,000 rows / 2 MB. Invalid rows are
   reported by row number and skipped; valid rows are imported.
3. **CRM sync** — `POST /guardrails/exclusions/crm-sync` ("Sync now") and a
   daily Beat task (`app.workers.crm_tasks.guardrails_crm_sync_all`, 06:35
   UTC, only when the flag is on):
   * **local** (always, no vendor call): every lead of this account with status
     `closed_won` → `customer`; every lead with an **open** deal → `open_deal`;
     every lead with a **won** deal → `customer`. Each becomes a `domain`
     exclusion (from the lead's email/company domain) plus a `contact`
     exclusion (its email).
   * **HubSpot** (if connected): companies whose `lifecyclestage` is in the
     customer stages (default `customer`, `evangelist`) → `customer`; companies
     whose `hs_num_open_deals` > 0 → `open_deal`; companies whose
     `lifecyclestage` is in the connection's `churned_lifecycle_stages`
     setting (default none — HubSpot has no standard churn stage) → `churned`.
     Each company yields a `domain` (property `domain`) and a `company`
     (property `name`) exclusion.
   * **Salesforce** (if connected): Accounts whose `Type` is in the customer
     types (default `Customer - Direct`, `Customer - Channel`, `Customer`) →
     `customer`; Accounts with an open Opportunity (`IsClosed = false`) →
     `open_deal`; Accounts whose `Type` is in `churned_account_types`
     (default `Former Customer`) → `churned`. Each yields `domain` (from
     `Website`) and `company` (`Name`).
   * Overrides live in the existing `external_crm_connections.settings_json`
     (`customer_lifecycle_stages`, `churned_lifecycle_stages`,
     `customer_account_types`, `churned_account_types`).
   * **Sync only adds or refreshes** (`last_seen_at`). It never removes an
     exclusion: an account that stops being a customer in the CRM is still a
     former customer, and failing toward "do not pitch" is the safe direction.
     A person removes CRM-sourced rows by hand (the next sync re-adds them if
     the CRM still says so). A vendor error is recorded per provider and does
     not undo what the other sources added.
   * **Reason precedence on an existing row** (send-path review, 2026-10-11;
     `guardrails.upsert_exclusion`): `do_not_contact` is sticky (no later
     write downgrades it); a `do_not_contact` write upgrades any row; a
     manual write replaces a CSV- or CRM-sourced row's reason (a sync never
     overrides a manual row); otherwise the first writer stands. The result
     is `updated` when a row changed. The send-time block is unaffected (any
     exclusion blocks); the precedence matters to callers that exempt reasons
     (the meeting follow-up, which also checks EVERY matching row via
     `guardrails.send_blocks`).

**Enforcement points** (flag on):

| Where | What happens |
|---|---|
| `lead_tasks.source_leads_impl` (sourcing) | an excluded raw lead is **not created**; counted as `excluded` in the stage result |
| `lead_tasks.enrich_leads_impl` | enrichment that reveals an excluded email / domain / company → lead `DROPPED`, `dropped_reason = "excluded"`, `excluded_by = <exclusion id>` |
| `lead_tasks.find_missing_emails_impl` | a found email that is excluded → `DROPPED`, `dropped_reason = "excluded"` |
| `sequence_engine.enroll_leads` | an excluded lead is not enrolled (same place suppression is skipped) |
| **`send_message_impl` — the send chokepoint (SAFETY GUARD)** | checked immediately after suppression, before any other gate. Excluded → message `CANCELLED`, `error = "excluded by guardrails: <kind> (<reason>)"`, `compliance_audit_log` row with decision `blocked_excluded`, enrollment **stopped** (`suppressed_excluded`, counted as an authorised *Suppressed* stop by sequence completion). Returns `cancelled_excluded`. |
| `POST /leads/{id}/call` (manual AI call) | 409 "this contact is on your exclusion list" — it is the one human-initiated cold send that does not go through `send_message_impl` |

**Fail-closed.** If the exclusion lookup itself errors at send time, the
message is **deferred one hour** (`deferred_guardrails_unavailable`), never
sent. At sourcing an error aborts the stage (Celery retries; the stage is
resumable). An exclusion added *after* a message was scheduled still blocks it,
because the check runs at send time.

### 2.2 Approved-claims and forbidden-phrases library (per product)

A **library entry** is `(kind, text)` owned by an account and scoped to one
product, or to **all** of the account's products (`product_id` null).

* `forbidden_phrase` — matched case-insensitively, whitespace-normalised, on
  word boundaries ("guarantee" does not match "guaranteed"; add both if both
  are banned).
* `approved_claim` — a statement about **us** the account has signed off,
  with an optional `source_url` (the evidence) for the reviewer.

**Forbidden phrases feed the outreach gate:**

1. **Pre-launch** (`adversarial_review.rule_findings`, the A7 gate): a step
   brief containing a forbidden phrase is a **BLOCK** finding
   `guardrail:forbidden:<phrase>`. (A library edit after a review passed is not
   re-reviewed — the review's content hash covers the sequence, not the
   library — which is why point 2 exists.)
2. **At send** (`send_message_impl`), over the **final words** — after the
   human send-review gate has applied any reviewer edit, so an approved edit
   cannot reintroduce a banned phrase. Hit → message `FAILED`,
   `error = "forbidden phrase: …"`, `compliance_audit_log` decision
   `blocked_forbidden_phrase`, LinkedIn/phone reservations released. Returns
   `blocked_forbidden_phrase`. All channels (subject and body).

**Approved claims feed claim verification** (`claim_verification.enforce`):
when the flag is on **and the product has at least one approved claim**, a
sentence that is about **us** (we / our / us / the product's name) and makes a
**proof claim** — a metric (`%`, `$`, `3x`, revenue/ARR…), a count of
customers/clients/companies/teams/users/years, or a proof phrase (*trusted by,
used by, customers like, clients include, award, certified, #1, number one,
leading, guaranteed, proven, rated, ranked*) — must be backed by **one**
approved claim that contains every number (unit-normalised, the A1 rules),
every proper noun and every proof phrase in the sentence. Otherwise the
sentence is **stripped** (subject / template slot: rewritten to the generic
value), exactly like an unsupported prospect claim, and logged to
`claim_verification_log` with `category = "sender_claim"`,
`extractor = "library"`, `evidence_source = "approved_claim:<id>"` when it
passed. A product with **no** approved claims is unaffected (opt-in by
existence), so turning the flag on does not silently rewrite every account's
copy. Respects `claim_verification_enabled` (it is a layer of that engine).
Errors fail closed: every sender proof claim is stripped.

**Service API for F04 Reply Copilot** (`app/services/guardrails.py`):

```python
guardrails.enabled(session) -> bool
guardrails.approved_claims(session, user_id, product_id) -> list[dict]   # id, text, source_url, product_id
guardrails.forbidden_phrases(session, user_id, product_id) -> list[dict]
guardrails.forbidden_hits(texts, phrases) -> list[str]                    # pure
guardrails.check_copy(session, user_id, product_id, subject, body) -> list[str]
guardrails.verify_sender_claims(text, claims, *, field_name, lead, sender_vocab, short, empty)
    -> tuple[str | None, list[claim_verification.Decision]]               # pure
guardrails.exclusion_for_lead(session, lead, *, user_id=None) -> Match | None
```

### 2.3 Audit-trail entries

| Event | Where it is recorded |
|---|---|
| exclusion added / removed / CSV import / CRM sync | `security_audit_events` (`guardrails.exclusion_added`, `guardrails.exclusion_removed`, `guardrails.exclusions_imported`, `guardrails.crm_synced`), same transaction as the change; details carry counts, kind and reason — never the CSV text |
| library entry added / removed | `security_audit_events` (`guardrails.library_added`, `guardrails.library_removed`) |
| send blocked by an exclusion | `compliance_audit_log` decision `blocked_excluded`, `checks.exclusion = {id, kind, reason, source}` |
| send blocked by a forbidden phrase | `compliance_audit_log` decision `blocked_forbidden_phrase`, `checks.forbidden_phrases = [...]` |
| sender claim stripped / verified | `claim_verification_log` (`sender_claim`) |
| lead dropped at enrichment | `leads.enrichment_json.dropped_reason = "excluded"`, `excluded_by` |

The hash-chained `activity_audit_events` trail (A2) records *activity*
(sent/opened/replied…); a block is the absence of activity, so it is not added
there — no change to the chain format.

## 3. Non-goals

* No fuzzy company matching ("Acme Corp" vs "ACME Corporation Holdings" match;
  "Acme" vs "Acme Labs" do not). Use a domain exclusion for certainty.
* No change to the global suppression list or its semantics; opt-outs remain
  global and always on regardless of this flag.
* Exclusions do not delete or rewrite existing leads; already-sourced leads are
  stopped at enrollment and at send.
* Not enforced on: the meeting follow-up email (`meeting_followup.send_followup`
  — a reply to someone the user just met) and LinkedIn network messages
  (`network_messages.deliver` — the user's own 1st-degree connections, not
  leads). Both are human-initiated, non-prospecting sends. Reported, not
  changed.
* No CRM write-back of exclusions; no CRM webhook trigger (daily + "Sync now").
* No AI. No model calls anywhere in F05.

## 4. Data model and migration

`0072_guardrails_studio` (down: `0071_webhook_schema_sync`; create-only, two
tables; downgrade drops both — **lossy**: the lists are lost, documented).

`guardrail_exclusions`: `id` uuid pk · `user_id` → users CASCADE (index) ·
`kind` varchar(10) · `value` varchar(320) · `display_value` varchar(320) ·
`reason` varchar(20) · `source` varchar(20) · `external_ref` varchar(100) null ·
`note` varchar(300) null · `created_by_user_id` → users SET NULL null ·
`active_since` timestamptz · `removed_at` timestamptz null · `last_seen_at`
timestamptz null · `created_at` / `updated_at`. **UNIQUE (user_id, kind,
value)** — the lookup index.

`guardrail_library`: `id` · `user_id` → users CASCADE (index) · `product_id` →
products CASCADE null (index) · `kind` varchar(20) · `text` text ·
`source_url` varchar(500) null · `created_by_user_id` → users SET NULL null ·
`created_at` / `updated_at`.

Both types are portable (no JSONB, no arrays). Both tables are in
`account_export.OWNERSHIP` (`user_id`) so the GDPR export stays complete.

## 5. API (authenticated; every query filtered by `user_id = current_user.id`)

| Method | Path | Notes |
|---|---|---|
| GET | `/guardrails/status` | `{enabled, counts}` — always available so the UI can explain the flag |
| GET | `/guardrails/exclusions?kind=&source=&q=&include_removed=&limit=&offset=` | |
| POST | `/guardrails/exclusions` | `{kind, value, reason, note}`; 422 on an invalid value |
| DELETE | `/guardrails/exclusions/{id}` | soft delete; 404 for another account's row |
| POST | `/guardrails/exclusions/import` | `{csv, default_reason}` |
| POST | `/guardrails/exclusions/crm-sync` | runs synchronously for this account |
| POST | `/guardrails/exclusions/check` | `{email, domain, company, phone, linkedin}` → the match, if any ("would this be blocked?") |
| GET | `/guardrails/products` | this account's products, for the library selector (no product listing existed) |
| GET | `/guardrails/library?product_id=` | entries for that product plus account-wide ones |
| POST | `/guardrails/library` | `{kind, text, product_id?, source_url?}`; product must be owned (404) |
| DELETE | `/guardrails/library/{id}` | hard delete (the library is not a send record) |

Writes require the flag on (**409** `guardrails_disabled` when off) so nothing
can be configured that silently does nothing; reads work either way. Writes are
rate-limited with the existing `RATE_LIMIT_CRM_WRITE` bucket. Not plan-gated:
it is a safety control, and safety is not a paid upgrade.

## 6. UI

Settings → Deliverability → **Guardrails Studio** card (the tab that already
holds the compliance audit log; a new tab would change the pinned tab list):
flag state; exclusion table with kind/source filters and search; add form;
CSV upload (file read in the browser, text posted); "Sync from CRM" button with
per-source counts; per-product library editor (approved claims with source
URL, forbidden phrases). The claim-check panel labels `sender_claim` as
"Claim about us" and `approved_claim:` sources as "Approved claim".

## 7. Tenancy

Every read and write filters on `user_id`; library writes additionally verify
the product's `user_id`. Matching at sourcing/send resolves the owner through
strategy → product → `user_id` (`notifications.owner_of_strategy`) and only
looks at that owner's rows — another account's exclusion of `acme.com` never
blocks your send to Acme. Tests pin both directions (visibility and
enforcement).

## 8. Compliance review (CAN-SPAM / GDPR / CASL / PDPA)

* **Never weakens an existing control.** Exclusions are an additional block
  evaluated *after* suppression; nothing is skipped or reordered. The flag
  only adds blocks.
* **Opt-outs.** Remain global and unconditional (`suppression_list`). An
  exclusion is not a substitute for an unsubscribe; a `do_not_contact` contact
  exclusion is account-scoped by design.
* **GDPR.** Contact exclusions hold third-party identifiers (email/phone/
  LinkedIn) for the purpose of **not** contacting them — a legitimate-interest
  do-not-contact record, the same basis as a suppression list. They are in the
  account's Art. 15/20 export, erased with the account (CASCADE), and the audit
  details never carry the identifier itself (ids, kinds, counts only). CSV text
  is parsed in memory and not stored.
* **CRM data.** Only identifiers needed for matching are stored (company name,
  domain, contact email). Read-only CRM scopes already granted for sync.
* **Claims.** Stripping unapproved "about us" proof claims reduces deceptive-
  advertising exposure (FTC Act §5 / CAN-SPAM "deceptive content"); it never
  adds content.
* Not legal advice; enforces the operator's own policy.

## 9. AI cost and budget impact

**Zero.** No model calls. Per send with the flag on: one settings read, one
indexed exclusion lookup (UNIQUE (user_id, kind, value)) and one library read
for the forbidden phrases. With the flag off: one settings read per check
point, nothing else.
CRM sync: ≤ 3 HubSpot search calls (paged, 100/page, capped at 50 pages) or 3
SOQL queries per connection per day.

## 10. Test plan (all offline: SQLite, fakes, no network)

`tests/test_guardrails.py`:
* normalisation (domains, companies, contacts) and subdomain matching;
* flag off = no behaviour change (sourcing, send, claims, review, API writes 409);
* sourcing skips excluded raw leads; enrichment drops leads revealed as excluded;
* enrollment skips excluded leads;
* **send-time guard**: exclusion added after scheduling → `cancelled_excluded`,
  nothing transmitted, audit row, enrollment stopped; domain, subdomain,
  company and contact variants; lookup error → deferred, not sent;
* forbidden phrase at send → `blocked_forbidden_phrase`, nothing transmitted;
  reviewer-edited copy is the copy checked; pre-launch BLOCK finding;
* approved claims: unapproved sender claim stripped and logged, approved one
  kept, product without a library untouched, prospect-claim engine unchanged;
* tenancy: another account's exclusions neither visible nor enforced;
  another account's product cannot be used for the library;
* CSV import (both layouts, errors by row, limits); CRM sync (local always,
  HubSpot/Salesforce through fake clients, vendor error isolated, never removes);
* audit: security_audit rows for every list change;
* manual call endpoint refuses an excluded lead;
* the production audit query below runs on SQLite against seeded data
  (PostgreSQL-only functions are avoided where the dialects differ — see note).

`tests/test_guardrails_migration.py`: migration creates exactly the model's
tables; downgrade round-trips.

## 11. Success metric — measured, never promised

**Zero sends to excluded entities.** Measured two ways:

1. The send-time guard tests above (red before the guard existed, green after).
2. This **read-only** production audit query (PostgreSQL). Run it, do not
   modify data. It lists every message that was **sent** while an exclusion
   of the **same account** covering that recipient was active. Expected: 0
   rows. It has **not** been run against production.

```sql
-- F05 audit: sends to entities excluded at send time. READ-ONLY.
-- Expected result: zero rows.
WITH sent AS (
    SELECT m.id                         AS message_id,
           m.sent_at,
           m.channel,
           p.user_id,
           lower(l.email)               AS email,
           lower(split_part(l.email, '@', 2))                    AS email_domain,
           lower(l.enrichment_json ->> 'company_domain')         AS company_domain,
           btrim(regexp_replace(lower(coalesce(l.company, '')), '[^a-z0-9]+', ' ', 'g')) AS company_norm,
           '+' || regexp_replace(coalesce(l.phone, ''), '[^0-9]', '', 'g')       AS phone_norm,
           lower(regexp_replace(coalesce(l.linkedin_url, ''),
                 '^.*linkedin\.com/(in|pub)/([^/?#]+).*$', '\2'))              AS linkedin_slug
    FROM messages m
    JOIN leads l       ON l.id = m.lead_id
    JOIN strategies s  ON s.id = l.strategy_id
    JOIN products p    ON p.id = s.product_id
    WHERE m.status = 'sent'
      AND m.sent_at IS NOT NULL
)
SELECT sent.message_id, sent.sent_at, sent.channel, sent.user_id,
       e.id AS exclusion_id, e.kind, e.reason, e.source
FROM sent
JOIN guardrail_exclusions e
  ON e.user_id = sent.user_id
 AND e.active_since <= sent.sent_at
 AND (e.removed_at IS NULL OR e.removed_at > sent.sent_at)
WHERE (e.kind = 'domain'  AND (sent.email_domain   = e.value OR sent.email_domain   LIKE '%.' || e.value
                            OR sent.company_domain = e.value OR sent.company_domain LIKE '%.' || e.value))
   OR (e.kind = 'contact' AND (e.value = sent.email
                            OR e.value = sent.phone_norm
                            OR e.value = 'linkedin:' || sent.linkedin_slug))
   OR (e.kind = 'company' AND e.value = btrim(regexp_replace(sent.company_norm,
        '( (inc|incorporated|llc|ltd|limited|corp|corporation|co|company|gmbh|plc|sa|ag|bv|pty|llp|lp))+$', '')))
ORDER BY sent.sent_at DESC;
```

Notes: the company branch approximates `guardrails.normalize_company` in SQL
(`&` → `and` and non-ASCII letters are not reproduced), so it can under-report
a company-only match on such names; the domain and contact-email branches
mirror the Python normalisation; the phone branch assumes stored lead phones
are digits with punctuation, as `_phone_key` does. The guard
only exists for sends after the flag was turned on; restrict with
`AND sent.sent_at >= '<flag-on timestamp>'` to measure the feature rather than
history. Manual AI calls (`calls` table) are not covered by this query; the
API refuses them (test-pinned).

## 12. Unverified

* **HubSpot search API** request/response shape for companies
  (`POST /crm/v3/objects/companies/search`, properties `lifecyclestage`,
  `hs_num_open_deals`, `domain`, `name`, paging via `after`) — written to the
  public docs, exercised only against a fake client. Not run against a live
  portal; scope `crm.objects.companies.read` may not be in the scopes the
  existing OAuth app requests (the current sync uses contacts/deals). **Check
  the granted scopes before turning the CRM source on.**
* **Salesforce SOQL** (`Account.Type`, `Opportunity.IsClosed`,
  `Account.Website`) — standard objects/fields, but picklist values for
  `Type` vary by org; defaults are Salesforce's shipped values. Not run against
  a live org.
* The production audit query has **not** been run; it is syntax-checked only
  by reading. Its SQLite-portable subset is exercised in the tests.
* Real-world false-positive rate of company-name matching and of the sender
  proof-claim detector on production copy — unmeasured.
* Help-center article: `docs/help-center/` does not exist on this branch
  (Phase 2 not merged here) — to be added when it lands.
* `scripts/gate/e2e_journeys.py` (journey J3 "suppression and exclusions
  enforced") does not exist on this branch — journey not added.
