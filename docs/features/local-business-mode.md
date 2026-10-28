# Local business mode + consent-based customer win-back

Founder request (2026-10-11, verbatim): "improve its capabilities: restaurants,
hospitals and local shops can also use it to get clients."

Status: built behind two flags (below). Spec and code ship in the same PR; the
founders review at merge. Nothing here is legal advice -- every legal reading is
marked **needs founder/legal confirmation**.

## Problem

Vectorae finds and messages people at companies. A restaurant, a clinic or a
corner shop asking "get me clients" usually means two different things:

* **(A) Business buyers near them.** Offices that order catering, employers that
  buy occupational-health screenings, property managers that buy in bulk. That
  is B2B outreach, which Vectorae already does -- but its ICP research, lead
  search and message prompts know nothing about "near me" or about local plays,
  so a café gets a nationwide ICP and copy written for a SaaS buyer.
* **(B) Their own past customers coming back.** That is consumer marketing, and
  it is only lawful on a consent basis (see the compliance review). Vectorae has
  no way to import a business's own customer list today (help-center article 02:
  "There is no way to import your own contact list as leads").

What the founder's request does NOT mean, and this feature refuses to build:
consumer prospecting (finding members of the public and cold-messaging them),
and any use of patient data for marketing.

## Recon -- what already existed (this feature EXTENDS, it does not duplicate)

| Need | Existing code | What this feature does with it |
|---|---|---|
| Product context in research prompts | `app/pipeline/engine.py::_product_block` (every step's prompt) | appends a local-business block |
| Messaging context in outreach prompts | `app/services/message_personalization.py::_playbook` (email + WhatsApp variables) | prepends the local-business block |
| ICP -> provider search criteria | `app/services/icp_extraction.py` (`titles/industries/locations/company_size_ranges/keywords`), `app/integrations/apollo.py::icp_to_apollo_params` (`locations` -> `person_locations`) | service area overrides `locations`; type templates fill empty `titles`/`industries` |
| Consent ledger | `app/services/consent.py` (`consent_events`, `record`, `record_grant`, `suppress_everywhere`) | every imported consent is a `granted` event |
| WhatsApp opt-in with evidence | `app/services/whatsapp_optin.py::record_opt_in(MANUAL_IMPORT, evidence=...)` | used verbatim for WhatsApp consent |
| Send chokepoint | `app/workers/outreach_tasks.py::send_message_impl` (suppression, F05 exclusions, F10, plan holds, WhatsApp opt-in/template/opt-out, compliance rules, send window, daily send approval, caps, send review, forbidden phrases, claim check, erasure re-claim) | win-back messages go through ALL of it, plus one new guard |
| Sequences/enrollment | `app/api/sequences.py`, `app/services/sequence_engine.py::enroll_leads` | a sequence gets an `audience`; enrollment never mixes audiences |
| Send-time windows | `app/services/send_windows.py` (engagement-learned); another branch builds `app/services/send_time.py` | this feature only EXPOSES `business_type` + recommended windows; it builds no scheduler |

Half or more of each part exists, so this is "extend X" throughout.

## Behavior

### Flags (`app/services/system_settings.py` DEFAULTS)

| Key | Default | Why that default |
|---|---|---|
| `local_business_mode_enabled` | **True** | It only changes targeting and prompt context for business buyers. Off = the business-type/service-area fields are refused (422) and stored values are ignored by prompts and sourcing -- exactly the previous behaviour. |
| `customer_winback_enabled` | **False** | Consumer marketing on a consent basis. Off until founder/legal confirm the readings below. Off = import and customer sequences answer 409 `CUSTOMER_WINBACK_DISABLED`; a queued customer message is cancelled at send time. |

### Part A -- local business mode (B2B local prospecting)

**Business type** (on the product, chosen at product creation, changeable with
`PUT /products/{id}/local-business`). Values:

| `business_type` | Label | Local B2B plays (templates in `app/services/local_business.py`) |
|---|---|---|
| `restaurant` | Restaurant, café or caterer | offices/companies in the service area (catering, team lunches, client meetings, corporate events); event planners and venues; hotels and serviced apartments (guest dining partnerships, overflow catering) |
| `healthcare` | Clinic, hospital or healthcare practice | employers (occupational health, corporate wellness, flu shots, screenings); schools and colleges; sports clubs and gyms (physicals, injury care); referring practices (B2B referral relationships); insurers and benefits brokers. **B2B only.** |
| `retail` | Retail or local shop | nearby offices (supplies, gifting); schools; property and facilities managers (bulk supply); hospitality (supply, gifting) |
| `local_service` | Salon, fitness or other local service | employers (staff wellness perks, corporate memberships); hotels and event venues (partner services); property managers (resident perks) |
| `other` | Other local business | offices and organizations in the service area; neighbouring businesses for partnerships |

NULL = not a local business (every existing product). `Strategy.business_type`
is a read-only property of the product's value, so the scheduler branch can read
`strategy.business_type` directly.

**Service area** (on the strategy, set at strategy creation, changeable with
`PUT /strategies/{id}/local-business`), stored in `strategies.service_area_json`:

```json
{"city": "Astoria, NY", "postal_code": "11103", "country": "US",
 "radius": 3, "unit": "mi", "timezone": "America/New_York"}
```

Validation (422 on any failure): `country` is a two-letter code; at least one of
`city` / `postal_code`; `city` <= 120 characters; `postal_code` 2-12 letters,
digits, spaces or hyphens; `unit` is `mi` or `km`; `radius` from 0.5 to 50 miles
(1 to 80 km) -- this is LOCAL mode; `timezone`, when given, is a real IANA zone.
A service area or offer needs the product to have a business type (422 otherwise).

**Local offer** (`strategies.local_offer`, <= 500 characters): what the business
offers business buyers ("catering packages for team lunches, 10-200 people").

**What changes when a strategy has local context** (flag on, product has a
business type):

1. *Research pipeline.* Every step's product block gets a `LOCAL BUSINESS CONTEXT`
   section: type, service area, offer, the type's B2B plays, messaging guidance,
   and the hard limits below. No extra model call.
2. *ICP -> sourcing.* `local_business.apply_to_criteria` runs on every criteria
   dict before a lead batch is created (and when the pipeline binds the playbook
   bucket): `locations` becomes the service area location (`"<city>, <country>"`,
   else `"<postal code>, <country>"`) unless the caller passed explicit criteria
   with their own `locations`; empty `titles` / `industries` are filled from the
   type's templates; a `local_business` key records the type, the full service
   area and `"radius_sent_to_provider": false`. The ICP extraction prompt also
   sees the local block. No extra model call.
3. *Messages.* The personalization playbook (email and WhatsApp variables) is
   prefixed with the same block. Claim verification and F05 guardrails run
   unchanged; the service-area location and the offer are added to the sender's
   own vocabulary in the claim engine (they are facts about the SENDER, so a
   sentence naming the sender's own neighbourhood is not an unsupported claim
   about the prospect).
4. *Send-time metadata.* `local_business.send_time_profile(business_type)` returns
   the recommended local windows (below). Nothing reads it yet.

**Hard limits written into every local prompt** (and enforced elsewhere where
code can enforce them):

* Business buyers only -- never consumers, households, patients or people buying
  for themselves. (Enforced: lead sourcing is a B2B people search; there is no
  consumer source.)
* Never state a distance or travel time to the prospect ("a 20-minute walk away")
  unless it is in the lead data -- Vectorae does not geocode prospects, so such a
  sentence would be invented. The business may say it is based in / serves its
  area.
* Offers and prices only from the offer text or the product description.
* Healthcare: never use, mention or imply patient information, conditions,
  treatments or health data; outreach is about services for the organization.

**Lead-source adapters -- what the location filter really does.** Recon of
`app/integrations/`:

| Adapter | Location support in our code | Radius |
|---|---|---|
| Apollo (`apollo.py`) | `locations` -> `person_locations` (already marked "verify against current Apollo docs" in the adapter) | **not sent.** No radius parameter is used by our adapter, and none is invented here. Whether Apollo's people search accepts a radius or a postal code in `person_locations` is **unverified against the live API**. |
| Hunter (`hunter.py`) | email finder/verifier only -- no search | n/a |
| Others (`signalforge.py`, `newsapi.py`, ...) | not lead sources for people search | n/a |

So the service area narrows sourcing to the city/postal-code string; the radius
is stored, shown, used in prompts and recorded on the batch, but is not applied
by the provider. Stated in the UI and the help center.

**Recommended local send windows** (`send_time_profile`), in the BUSINESS's local
time. They describe when the owner is busy serving customers and cannot answer a
reply -- a recommendation for the scheduler branch, not measured:

| Type | Avoid | Prefer |
|---|---|---|
| restaurant | 11:00-14:00, 17:00-21:00 | 09:00-11:00, 14:00-17:00 |
| healthcare | 08:00-10:00 (clinic opening rush) | 10:00-12:00, 14:00-16:00 |
| retail | 12:00-14:00 | 09:00-11:00, 14:00-17:00 |
| local_service | 17:00-20:00 | 09:00-12:00, 13:00-16:00 |
| other | -- | 09:00-17:00 |

The compliance send window (7:00-20:00 band, recipient local time) still applies
on top and always wins.

### Part B -- win-back for the business's OWN opted-in customers

Flag `customer_winback_enabled`, default **off**.

**Import.** `POST /strategies/{id}/customers/import` (multipart: `file` = CSV,
`attestation` = the exact sentence in `local_business.ATTESTATION`, version
`ATTESTATION_VERSION = "2"`). Version 2 (review 2026-10-12) adds: "This business
is not a HIPAA covered entity or business associate, and this file contains no
patients or patient information." The version is stored on every
`customer_imports` row and in the `customers.imported` security-audit event.
The strategy must be the caller's (404); BOTH flags must be on (409); its product
must have a business type (409) and must not be, or ever have been, `healthcare`
(403, HIPAA, below).

CSV columns (header row required, case-insensitive):

| Column | Required | Meaning |
|---|---|---|
| `name` | no | the customer's name |
| `email` | for email consent | |
| `phone` | for WhatsApp consent | E.164 with country code |
| `consent_channels` | **yes** | `email`, `whatsapp`, or both (`email;whatsapp`) -- what the person opted in to |
| `consent_date` | **yes** | ISO date the person opted in (not in the future) |
| `consent_source` | **yes** | how they opted in ("loyalty sign-up form at the till", "website newsletter box") |
| `consent_text` | no | the wording the person agreed to |
| `country` | no | two-letter country (region rules) |
| `timezone` | no | IANA zone (send window) |
| `linkedin` | no | profile URL -- used ONLY to check for an earlier opt-out, never stored |

Any other column is refused whole: **422** "unexpected column(s)". Nothing
unexpected is accepted even to be thrown away -- that is data minimisation, and
it means a disguised health column (`rx`, `notes`) cannot slip past the
header-word check.

File-level refusals (**422**, nothing stored): larger than 2 MB or more than
5,000 rows; not UTF-8; contains a NUL byte; unreadable by the CSV parser (a
field over Python's 131,072-character limit, broken quoting); a duplicate column
name; a header word that names health data; health or patient wording in ANY
row's `consent_source` or `consent_text` (`patient`, `clinic`, `appointment`,
`treatment`, `health`, `doctor`, `dental`, `pharmacy`, `therapy` ... --
`local_business.PHI_WORDING_TOKENS`), because such a description says the list
came from a clinical relationship.

Per-row limits, checked BEFORE anything is written (a refused row, never a
database error): `name` <= 200, `email` <= 320, `consent_source` 3-200,
`consent_text` <= 1,000, `phone` <= 50, `linkedin` <= 500, `timezone` <= 64.
Values stored from the file that start with `= + - @`, a tab or a carriage
return (spreadsheet formulas, OWASP CSV injection) are stored with a leading
apostrophe; an email starting with one of them is invalid.

**One transaction.** Every row is validated, then everything is written with no
inner commit (`whatsapp_optin.record_opt_in(..., commit=False)`,
`consent.record_grant(..., commit=False)`), and the import commits ONCE. Any
unexpected error rolls the whole import back and answers **500** "The import
failed and nothing was saved".
* A file without the three consent columns is refused whole: **422**.
* A file whose header names a health-data column (`patient`, `diagnosis`,
  `condition`, `treatment`, `prescription`, `medication`, `mrn`, `medical`,
  `insurance`, `health`, `dob`, `date_of_birth`, `appointment`) is refused whole
  for EVERY business type: **422** (no health data is ever accepted).
* A row without consent is **refused** (not stored): no `consent_channels`, no
  valid past `consent_date`, a `consent_source` under 3 characters, an unknown
  channel, an email channel without a valid email, a WhatsApp channel without a
  valid E.164 phone.
* **Decision (conservative): there is no "email without consent" path.** Every
  channel needs explicit recorded opt-in, email included. We do NOT rely on
  CAN-SPAM's opt-out model, the ePrivacy/PECR "soft opt-in" or CASL's
  existing-business-relationship implied consent for consumers -- each has
  conditions (same-product, 2-year windows, opt-out offered at collection) that
  Vectorae cannot verify from a CSV. A row that would only qualify under one of
  them is refused.
* **An opt-out is final (review H1).** The WHOLE row is refused when ANY
  identifier on it -- email, phone or LinkedIn, whether or not the row consents
  on that channel -- is on the suppression list, or has a `withdrawn`,
  `suppressed` or `erased` event in this account's consent ledger, or belongs
  to an existing record of this strategy that has such an event or was removed.
  The `consent_date` the file claims does not matter: an earlier date never
  revives consent and a later one does not either (a person who opted out
  re-subscribes themselves, e.g. through the hosted WhatsApp opt-in page -- an
  owner's spreadsheet cannot do it for them). An imported consent never lifts a
  suppression.
* **One person, one record (H1).** Existing records of the strategy are matched
  by email AND by normalised phone, prospects and suppressed ones included. A
  match updates that record; a row whose email and phone point at two different
  records is refused; nothing ever creates a second record for the same person.
* SMS and phone calls are not offered: Vectorae has no SMS sending channel, and
  AI calls to consumers are exactly what the TCPA restricts. `consent_channels`
  naming `sms` or `phone` is refused for that row.
* An email that already belongs to a B2B prospect in the same strategy is refused
  (one person is not both); one that already belongs to an imported customer gets
  the new consent recorded on that record.
* The plan's `max_leads_per_strategy` applies; rows past the room are refused
  with that reason. At most 5,000 data rows and 2 MB per file.

Accepted rows become `leads` with `source = "customer_import"`, status `sourced`,
`enrichment_json = {"customer_import_id", "country"?, "timezone"?}`. For each
accepted channel:

* email -> one `consent_events` row: kind `granted`, channel `email`, source
  `customer_import`, identifier = the email, detail
  `"opted in to marketing on <date> via <source>"`, meta
  `{import_id, consented_on, via, consent_text}`.
* whatsapp -> `whatsapp_optin.record_opt_in(source=MANUAL_IMPORT, evidence=
  "opted in to marketing on <date> via <source> (customer import <id>)",
  consent_text=...)`, which writes the WhatsApp opt-in row AND the ledger's
  `granted` event (existing behaviour).

One `customer_imports` row records the import: who, when, which strategy, the
business type, the attestation text and version, counts, and the refused lines with reasons
(line numbers and reasons only -- never the refused personal data, GDPR Art.
5(1)(c) data minimisation).

**Healthcare -- refused entirely (403).** A product whose business type is
`healthcare` cannot import a customer list at all, and a customer message whose
product is (or later becomes) healthcare is cancelled at send time. Message:
"Customer win-back is not available for healthcare businesses. A clinic's or
hospital's customer list is a patient list, and using patient information for
marketing needs each patient's written HIPAA authorization (45 CFR 164.508(a)(3)),
which Vectorae does not collect. Use local business mode to reach employers,
schools, sports clubs, referring practices and insurers instead."

**Customer sequences.** `POST /strategies/{id}/sequences` takes
`audience: "prospects" | "customers"` (default `prospects`, i.e. today's
behaviour). A `customers` sequence (flag on, non-healthcare business type):

* channels: email and WhatsApp only (422 for LinkedIn/phone);
* WhatsApp steps: approved-template steps only (no free-form `text` kind), with
  every variable mapped to a deterministic `lead.<field>` -- no model writes
  anything addressed to a consumer;
* email steps: the step `template` is the owner's own copy, sent as written. An
  optional first line `Subject: ...` sets the subject (default: the product
  name). Placeholders: `{first_name}`, `{business_name}` -- any other `{...}` is
  422. No AI rewriting, no prospect research, no open or click tracking;
* automatic no-reply follow-ups are switched off on every step.

**Enrollment never mixes audiences.** `enroll_leads` on a `customers` sequence
selects only `customer_import` leads (any status except dropped/disqualified);
on a `prospects` sequence it excludes them. F-P-T-A scoring is skipped for
customers.

**Send-time guard (SAFETY GUARD, every channel)** --
`local_business.customer_send_block`, placed in `send_message_impl` right after
the suppression gate. Cancels the message (audited `blocked_winback`, enrollment
stopped) when:

1. the lead's audience and the sequence's audience differ (a prospect in a
   customer sequence or a customer in a prospect sequence);
2. the lead is a customer and `customer_winback_enabled` is off;
3. the lead is a customer and the channel is not email or WhatsApp, or the
   message is an automatic re-engagement touch (`reengagement` /
   `notnow_memory` origins -- model-written B2B features);
4. the lead is a customer and the product's business type is `healthcare` or
   unset, or the product was ever healthcare (`healthcare_locked_at`);
5. the lead is a customer, the channel is email, and the ledger has no current
   email `granted` event for the lead (a later `withdrawn` / `suppressed` /
   `erased` event for email or all channels revokes it);
6. the lead is a customer and the ledger holds a `withdrawn` / `suppressed` /
   `erased` event for this record (on this channel or all channels) or for its
   email or phone in this account's ledger -- whatever a WhatsApp opt-in row
   still says (review H1, defence in depth).

WhatsApp consent is the existing opt-in gate further down (skip on no opt-in),
plus the existing approved-template and opt-out-wording checks. A lookup error
fails CLOSED (deferred one hour). Every other gate of the chokepoint applies
unchanged: suppression, F05 exclusions, F10 departures, plan holds, compliance
rules, send window, daily send approval, caps, send review, forbidden phrases,
claim verification, the unsubscribe footer and RFC 8058 headers, the erasure
re-claim.

Things that must never touch a customer (consumer) record, and where that is
enforced:

* F10 Champion Tracker (provider role checks would send consumer data to
  Apollo): `champion_tracker.staleness_hold` and `champion_lead_ids` skip
  `customer_import` leads.
* Lead scoring re-score sweep: excludes `customer_import` leads.
* Personalization research (LinkedIn posts, company news, Signal Radar): never
  called -- the win-back email path does not use the personalization renderer.

Known limitations (reported, not hidden):

* Imported customers are leads of the chosen strategy: they appear in its lead
  list (source `customer_import`) and count toward `max_leads_per_strategy`.
* A customer's reply goes through the normal inbox classification (an existing,
  metered model call), like any reply. A person may answer it with Reply Copilot
  (human-approved); Reply Copilot's auto-send never applies to a customer
  (`auto_send_decision` returns `customer_needs_a_person`).
* There is no in-app import screen yet (API only) while the flag is off by
  default.

## Non-goals

* Consumer prospecting of any kind (finding or buying consumer contacts).
* SMS or AI calls to consumers.
* Any healthcare patient/customer import, in any form.
* Geocoding prospects or computing distances; a provider radius filter.
* The send-time scheduler (another branch).
* Changing the home page (another branch owns it).

## Data model and migration -- `0088_local_business`

`down_revision = "0087_autopilot_launch"`; TARGET_REVISION in
`scripts/activate_with_keys.py` set to it. Types work on SQLite and PostgreSQL.

* `products.business_type` VARCHAR(30) NULL.
* `strategies.service_area_json` JSON NULL; `strategies.local_offer` VARCHAR(500) NULL.
* `sequences.audience` VARCHAR(20) NOT NULL, server default `'prospects'` (every
  existing sequence becomes a prospects sequence -- today's behaviour).
* `products.healthcare_locked_at` TIMESTAMPTZ NULL -- set the first time the
  product is (or leaves being) healthcare, never cleared (the healthcare lock).
* new table `customer_imports`: `id`, `user_id` (FK users, CASCADE), `strategy_id`
  (FK strategies, SET NULL), `business_type`, `attestation`,
  `attestation_version`, `accepted_count`,
  `rejected_count`, `rejected_json`, `created_at`. Index on `user_id`. Constraint
  names are <= 63 characters (PostgreSQL limit).

Downgrade drops the table and the five columns (the healthcare lock is lost too): business types, service areas,
offers and audiences are lost (customer sequences would become prospect
sequences -- so turn `customer_winback_enabled` off and stop customer sequences
BEFORE a downgrade). Imported customer leads and their consent events are not
touched by the downgrade.

`account_export.OWNERSHIP["customer_imports"] = ["user_id"]`;
`account_erasure`: `customer_imports` is DELETED (it is the account's own import
log; the proof of each consent lives in `consent_events`, which is KEPT as E2).

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/products` | + optional `business_type` |
| GET | `/products/{id}` | + `business_type` |
| PUT | `/products/{id}/local-business` | `{business_type}` (null clears) |
| POST | `/products/{id}/strategies` | + optional `service_area`, `local_offer` |
| PUT | `/strategies/{id}/local-business` | `{service_area, local_offer}` |
| GET | `/local-business/types` | the types, labels, plays, windows and both flag states |
| POST | `/strategies/{id}/customers/import` | multipart; see Part B |
| POST | `/strategies/{id}/sequences` | + `audience` |

`StrategyOut` gains `business_type`, `service_area`, `local_offer`.

## UI

* New-strategy wizard, first card: "Is this a local business?" selector (the five
  types, default "No"); when a type is chosen: city or postcode, country, radius
  + unit, and the offer. The radius line says plainly that it is used for
  research and messages and is not applied by the lead provider.
* No customer-import screen in this change (flag default off); the API is the
  flow until legal confirms. `frontend/src/lib/localBusiness.ts` holds the shared
  types, labels and the request builders (tested with vitest).

## Tenancy

* Product/strategy updates use the existing ownership checks (404 for another
  account's ids).
* The import resolves the strategy through `products.user_id == caller`; the
  duplicate-email lookup is scoped to that one strategy; the suppression check is
  the global list (as everywhere).
* `customer_imports` rows are written with the caller's id and read by nobody
  else. Consent events carry the owner's `user_id` (existing `_owner_id`).

## Compliance review (zero-tolerance posture)

Each reading below is **needs founder/legal confirmation**.

| Rule | Reading relied on | What the code does |
|---|---|---|
| US TCPA, 47 U.S.C. 227(b)(1); 47 CFR 64.1200(a)(2)-(3); FCC Declaratory Ruling FCC 24-17 (Feb 2024, AI voices are "artificial voice"); texts are "calls" under the TCPA | marketing calls/texts to consumers with an autodialer or artificial voice need prior express written consent | no SMS channel; no phone/AI-call channel for customers; no cold consumer outreach on any channel |
| CAN-SPAM, 15 U.S.C. 7704 | opt-out regime; requires identification, a working opt-out honoured within 10 business days | win-back email still requires explicit opt-in (stricter than the statute); footer + RFC 8058 one-click unsubscribe as for every email |
| GDPR Art. 6(1)(a), 7(1) and ePrivacy Directive 2002/58/EC Art. 13(1); UK PECR reg. 22 | electronic marketing to individuals needs prior consent the controller can demonstrate | consent date, source and wording recorded per contact in the ledger; no soft-opt-in reliance (Art. 13(2) / PECR 22(3) not used) |
| CASL, S.C. 2010 c. 23, s. 6(1), s. 10(9), s. 13 | a commercial electronic message needs consent; the sender bears the burden of proof | express consent required and recorded; implied consent via an existing business relationship NOT relied on |
| Singapore PDPA 2012, s. 13 and Part 9 (Do Not Call) | consent for collection/use; DNC registry for phone/SMS | consent required; no phone/SMS; the DNC check is still not performed (existing limitation) |
| HIPAA Privacy Rule, 45 CFR 164.501 ("marketing") and 164.508(a)(3) | using PHI (a patient list is PHI) for marketing needs written authorization | healthcare business type cannot import or message customers at all; health-data columns refused for every type; healthcare B2B outreach only |
| WhatsApp Business Policy (Meta) | opt-in before business-initiated messages; marketing via approved templates | existing opt-in gate, approved-template gate and opt-out-wording gate; customer WhatsApp steps template-only |
| GDPR Art. 5(1)(c) | data minimisation | refused rows are not stored; only line + reason kept |

LEGAL / SAFETY GUARDS ADDED (for the orchestrator's sealing decision):

1. `customer_send_block` in the send chokepoint (audience mismatch, flag off,
   channel via `channel_refusal`, healthcare now or ever, missing/withdrawn email
   consent, any person-level opt-out in the ledger, automatic re-engagement
   touches) -- fails closed.
2. Import consent requirement: 422 without consent columns; per-row refusal
   without consent; an opt-out on ANY identifier refuses the whole row whatever
   the consent date; never a second record (H1).
3. One channel question for every entry point (H2): `channel_refusal` /
   `refuse_channel` -- AI calls (`POST /leads/{id}/call`) and phone consent
   (`PUT /leads/{id}/phone-consent`) are 409 for a customer,
   `phone_calls.consent_ok` is False for one; API WhatsApp opt-ins
   (`POST /leads/{id}/whatsapp-optin`) are 409 and the opt-in CSV import never
   matches a customer -- a customer's consent comes only through this import.
4. Healthcare: import/sequence refusal (403), the healthcare lock, health-data
   column and wording refusal (422), attestation v2.
5. Enrollment audience separation.
6. Customer sequence shape: email/WhatsApp only, template-only WhatsApp,
   deterministic variables, no follow-ups (also refused later by
   `followup-settings`, 409, and skipped by the follow-up sweep), allowed
   placeholders only.
7. Consumers are never profiled (M1, below).

Tests: `tests/test_local_business.py` (Part A); `tests/test_customer_winback_guards.py`
(the send guard, H1, H2 -- the file to seal); `tests/test_customer_winback.py`
(import, sequences, M1-M3, L1-L4); `tests/test_local_business_migration.py`;
`tests/integration/test_customer_import_pg.py` (over-long fields, NUL bytes and
rollback on PostgreSQL).

### Healthcare lock (review M2)

The rule, as code (`local_business.change_business_type`, used by product
creation and `PUT /products/{id}/local-business`):

1. A product holding imported customers (any `customer_import` lead not removed)
   can neither BECOME healthcare nor LEAVE healthcare: 409 "remove them first".
   A healthcare product must never hold a marketing list, and a list must never
   be carried across the boundary.
2. The first time a product is healthcare -- set to it, or found to be it when
   it is changed away -- `products.healthcare_locked_at` is set and never
   cleared. Switching away keeps B2B targeting working, but customer win-back
   (import, customer sequences, every send) stays refused for that product
   forever (403 "This product was a healthcare business ...").
3. Every business-type change is audited in `security_audit_events`
   (`product.business_type_changed`, actor, product, `{from, to}`); every import
   is audited too (`customers.imported`, counts and attestation version).

**Residual risk, stated plainly.** Vectorae cannot know what a business really
is. A clinic that registers as a restaurant from day one, never selects
healthcare, uploads a CSV with no health column and no health wording, and
confirms an attestation that says it is not a HIPAA covered entity, will get
its list imported. The controls make that a deliberate false statement by the
owner (recorded with version, actor and time), not an accident: the attestation,
the lock, the column whitelist and the wording scan. Whether that allocation of
responsibility is acceptable is a founder/legal decision -- **needs founder/legal
confirmation**. The wording scan also has false positives (a gym's "health club
sign-up" is refused) by design.

### Consumers are never profiled (review M1)

Every path that sends a lead's data to a model or to an enrichment,
verification or social provider refuses or skips an imported customer:

| Entry point | Guard |
|---|---|
| `POST /leads/{id}/fpta/rescore`, `POST /leads/{id}/rescore` | 409 |
| `POST /strategies/{id}/leads/fpta/rescore`, re-score sweep | customers filtered out |
| `fpta_scoring.score_leads`, `lead_scoring.score_leads` (every caller) | customers dropped before any call |
| `POST /leads/{id}/personalization/refresh`, `PUT /leads/{id}/linkedin-url`, `POST /leads/{id}/loom/script` | 409 |
| `personalization_context.ensure_fresh` (send path, displacement monitor, Signal Radar) | returns `skipped_customer`, no fetch |
| `POST /leads/{id}/signals/refresh`, `signal_radar.refresh` | 409 / skipped |
| `POST /leads/{id}/meeting-prep`, `meeting_prep.generate_brief` (also booking-triggered briefs) | 409 / brief `failed`, no model call |
| `POST /leads/{id}/meeting-outcome` with `generate_followup` | 409 (logging alone is allowed) |
| `POST /leads/{id}/email-verdict/refresh` (verifier provider) | 409 |
| `POST /leads/{id}/role-check/refresh`, `champion_tracker.reverify`, staleness hold, champion list | 409 / skipped |
| displacement watchlist, post-sequence re-engagement candidates, "not now" plans, follow-up sweep, Reply Copilot auto-send | customers excluded |

`tests/test_customer_winback.py::test_every_lead_write_route_is_classified`
fails when a new `POST/PUT/PATCH /leads/{lead_id}...` route is added without
being put in the refused list or the documented safe list (manual overrides,
CRM notes/tags, consent withdrawal, status edits).

Not excluded, on purpose: classification of a customer's REPLY (an existing
inbox model call; a reply must be routed and an unsubscribe request honoured),
Reply Copilot DRAFTS a person approves, and the owner's OWN connected CRM
receiving the record when the owner moves it.

## AI cost and budget impact

Part A adds text to prompts that already run (pipeline steps, ICP extraction,
personalization): a few hundred input tokens per call, no new calls; all still
inside the existing usage scopes (`owner_scope(..., "pipeline" / "icp" /
"outreach")`, `metered(..., "icp")`). Part B makes NO model call to write
messages (owner copy, deterministic variables). The existing claim-extraction
model call (if `claim_model_extraction_enabled`) runs on win-back emails as on
every email, inside the send task's `owner_scope("outreach")`.

## Test plan (offline: SQLite, FakeClaude, FakeChannel, no network)

* business type at product creation; validation; PUT; flag off -> 422.
* service area validation (missing country, no city/postcode, radius bounds per
  unit, bad unit, bad timezone, no business type) -> 422; stored and returned.
* pipeline product block, ICP prompt and personalization playbook contain the
  local block when set and are byte-identical when not; flag off -> absent.
* apply_to_criteria: locations override, explicit locations kept, empty
  titles/industries filled, radius recorded and not passed to Apollo params.
* lead sourcing API stores the local criteria on the batch.
* send_time_profile for every type.
* import: flag off 409; healthcare 403; no business type 409; missing consent
  columns 422; health-data header 422; bad attestation 422; row refusals; consent
  recorded in the ledger and the WhatsApp opt-in table; suppressed contact refused;
  tenancy (another account's strategy 404; another account's prospect with the
  same email is not touched); plan room.
* sequences: audience validation; enrollment separation.
* send: a consented customer email sends with the footer, no tracking pixel, no
  model call; withdrawn consent / suppression / flag off / healthcare / audience
  mismatch -> never sent.
* account export/erasure completeness (existing tests cover the new table).
* migration: columns and table match the model; downgrade round-trips; names
  <= 63 characters.

## Success metric (measured, never promised)

Share of local-business strategies whose sourced leads fall inside the service
area (spot-checked by the founders on real batches), and replies per 100 sends
for local strategies versus the account's other strategies. Win-back: unsubscribe
rate per 100 win-back sends (should stay below the account's B2B rate) -- only
after legal confirmation switches it on.

## Unverified

* Apollo `person_locations` accepting a postal code, and any radius support:
  unverified against the live API; no radius is sent.
* The recommended local send windows: opinion, not measured.
* Every legal reading above: needs founder/legal confirmation.
* Whether a CSV "consent_source" sentence is enough evidence in a dispute
  (likely depends on the business's own records); Vectorae records what it is
  told and the owner's attestation.
