# F01 — Waterfall Enrichment + Bounce Shield

Status: **built behind two flags, both default OFF.** Spec and code ship in the
same PR (founder decision 2026-10-10); the founders review at merge.
Branch `feat/f01-waterfall-enrichment`. STAR Phase 4, row F01.

This is an **"extend X" feature**, not a new subsystem: about half of it
already existed (see [Recon](#recon--what-already-existed)). It extends the
lead pipeline's find/verify stages (`app/workers/lead_tasks.py`) and adds one
guard to the existing send chokepoint (`app/workers/outreach_tasks.py`
`send_message_impl`). It creates **no new send path**.

---

## Problem

Competitor complaint it answers (STAR row F01): *Apollo users report 15–35%
bounces on "verified" data; Clay charges per attempt.*

What that looks like in Vectorae today:

1. **No fall-through.** Apollo's email is verified by Hunter; if Hunter says
   it is undeliverable the lead is dropped. Hunter's email finder is only
   tried when Apollo returned *no* email at all, never when Apollo's email is
   bad — so a findable, deliverable address is thrown away.
2. **"Verified" is not what gets sent.** The send path sends to `VERIFIED`
   **and** `FLAGGED` leads (`outreach_tasks.py`: `lead.status in (VERIFIED,
   FLAGGED)`), and a sequence can be launched on `FLAGGED` leads
   (`api/sequences.py` `lead_statuses`). `FLAGGED` means risky / catch-all /
   unknown — exactly the addresses that bounce.
3. **No catch-all policy.** Hunter's accept-all signal is discarded; a
   catch-all address is "risky" like any other.
4. **Cost is invisible.** The Redis response cache already avoids paying
   twice, but nothing records how many paid attempts were made, which were
   cache hits, or what each provider contributed.
5. **No measurement of the claim.** Nothing can answer "what is the bounce
   rate on sends we called verified?".

## Recon — what already existed

| Part of F01 | Existing code | Verdict |
|---|---|---|
| Adapter interface | `app/integrations/base.py` `LeadSource` / `EmailVerifier` + registry; `apollo.py`, `hunter.py` | **Exists** — reused, not duplicated |
| Apollo → Hunter order | `lead_tasks.py` enrich → find → verify chain | **Half**: order exists, fall-through does not |
| 30-day result cache | `plumbing.py` `ResponseCache` (Redis), `CACHE_TTL_ENRICHMENT_SECONDS = 2592000` (30 days) on Apollo enrich, Hunter finder and Hunter verifier | **Exists** — reused; F01 makes hits visible in the ledger and adds verdict reuse on the lead row (survives a Redis flush) |
| Per-attempt cost ledger | structured `external_call` log lines only | **Missing** — new table |
| Verified-only send block | none (FLAGGED sends) | **Missing** — guard in the existing chokepoint |
| Catch-all policy | `provenance.py` maps `accept_all` → "risky" for display | **Missing** |
| Bounce auto-pause | `sequence_engine.check_bounce_rate`, `settings.bounce_rate_pause_threshold = 0.03`, per-workspace lowering via `compliance_rules.bounce_threshold`; `mailbox_health` warn 2% / bad 5% | **Exists — unchanged** (see [Reconciliation](#reconciliation-with-the-bounce-auto-pause-threshold)) |

---

## Behavior

### Flags (`app/services/system_settings.py` DEFAULTS)

| Key | Default | Meaning |
|---|---|---|
| `waterfall_enrichment_enabled` | `False` | Run the provider waterfall in the find/verify stages and write the cost ledger. |
| `bounce_shield_enabled` | `False` | **Safety guard.** Block an email send at the chokepoint unless the address is verified-valid (or overridden for that lead). |
| `bounce_shield_catch_all_policy` | `"block"` | `block` or `allow`. Any other stored value is treated as `block` (fails closed). |
| `bounce_shield_max_age_days` | `90` | A verdict older than this is not "verified" any more. |
| `waterfall_provider_order` | `"apollo,hunter"` | Ordered finder chain. Unknown keys are ignored and listed as disabled. |

Off = no behaviour change: with both flags off the pipeline and send path take
exactly the code paths they took before (tested).

Not plan-gated: the waterfall spends the same Apollo/Hunter credits the
pipeline already spends, and a safety guard must never be a paid tier.

### The waterfall (lead pipeline, `waterfall_enrichment_enabled`)

Finder steps run in `waterfall_provider_order`; every candidate is checked by
the **independent verifier** (Hunter's email-verifier). Apollo's own
"verified" label is recorded as `claimed_status` but **never trusted as a
verdict** — that label is precisely what the competitor complaint is about.

```
for step in [apollo, hunter, ...] (skipping disabled steps):
    candidate = step.find(lead)              # ledger: find_email attempt
    if candidate is suppressed  -> DROP lead ("suppressed"), stop
    if candidate already checked -> next step (never pay twice)
    verdict = verify(candidate)              # ledger: verify attempt
    if verdict == valid          -> STOP (first verified result wins)
keep the best non-valid: catch_all > unknown > invalid; none -> not_found
```

* **apollo** step: the email the Apollo search/enrichment already returned —
  no extra call, cost 0 (the enrich stage paid for it).
* **hunter** step: Hunter email-finder for name + company domain.
* **Further providers are disabled until credentials exist.** A step is
  enabled only if its adapter has credentials (`api_key` non-empty). No
  adapter is written for a vendor whose API has not been checked; adding one
  = implement a finder step and register it in `FINDER_STEPS`.
* **Suppression is per person, not per address.** If *any* candidate for the
  person is on the suppression list, the lead is dropped. Finding a second
  address for someone who opted out under the first is the exact behaviour a
  zero-tolerance posture forbids.
* **Verdict classification** (Hunter payload): `status` valid → valid;
  invalid/disposable → invalid; accept_all → catch_all; unknown → unknown;
  otherwise falls back to `result` (deliverable → valid, undeliverable →
  invalid, else unknown). A "valid" on a domain flagged `accept_all: true` is
  downgraded to catch_all (an accept-all server cannot prove a mailbox exists).
* **Cache.** Hunter/Apollo calls go through the existing 30-day Redis response
  cache; a cache hit is recorded in the ledger with `cached=true, cost_units=0`.
  A verdict already stored on the lead for the same address and younger than
  30 days is reused without any call.
* **Stage mapping** (unchanged meaning): valid → `VERIFIED`; catch_all /
  unknown → `FLAGGED`; invalid → `DROPPED`; no candidate → `DROPPED`
  (`no_email_found`). The legacy `enrichment_json.verification` key and the
  provenance tag are still written, so lead scoring and the provenance panel
  keep working.
* **Resumable.** The verify stage reads the verdict the find stage stored and
  never re-verifies it; ledger rows commit as they are written, so a crash
  mid-lead keeps the record of what was already paid.

### Verdict record (on the lead)

`leads.enrichment_json.email_verdict`:
`{verdict, email_sha256, provider, verifier, score, claimed_status, checked_at}`.
It is bound to the address by hash: if someone edits the lead's email, the
verdict no longer applies. GDPR delete already replaces `enrichment_json`
wholesale, so the verdict and any override go with it.

### Bounce Shield (send chokepoint, `bounce_shield_enabled`) — SAFETY GUARD

Inside `send_message_impl`, right after the suppression check, **email channel
only**. An email send proceeds only if, for the lead's *current* address:

1. the verdict is `valid` and no older than `bounce_shield_max_age_days`, or
2. the verdict is `catch_all`, fresh, and the policy is `allow`, or
3. a per-lead **override** exists for this address and the verdict is not
   `invalid` (an undeliverable address is never sendable, override or not).

Otherwise the message becomes `FAILED` with error
`bounce_shield: <reason>`, a `compliance_audit_log` row
`blocked_unverified_email` is written, the enrollment is **not** stopped, and
the task returns `blocked_unverified_email`. Nothing is claimed or rendered,
no model call is made, no daily-cap slot is used.

Blocked messages are **re-queued** (back to `SCHEDULED`, due now) when an
override is set or a re-verification comes back sendable; they then pass
through **every** gate again — the requeue is not a bypass.

A held message is still the message for its step: the follow-up sweep counts
it as pending (`app/services/send_holds.py` `pending_clause`, used by
`outreach_tasks._has_pending_message`), so no second copy of a held step is
created, and a requeue puts back **at most one** message per (sequence, step)
— extra held copies, and held copies of a step that already has a live
message, are cancelled (`send_holds.release`). Found by independent review
(2026-10-11); `tests/test_bounce_shield.py::TestHoldsAreNotDuplicated`.

Leads verified before the flag was switched on have no verdict record and are
blocked until re-verified or overridden. That is deliberate (a status set by
older code is not evidence about today's address) and is the main operational
consequence of turning the flag on: **turn on `waterfall_enrichment_enabled`
first**, then the shield.

When the shield is on, the `SENT` outcome's meta carries
`email_verdict` (`valid` | `catch_all` | `override`) — that tag is what the
success metric is measured on. With the shield off the meta is byte-identical
to before.

### Override

Per lead, by an owner or manager (an SDR cannot take deliverability risk for
the workspace). Requires a reason (5–300 chars). Refused when the address is
suppressed, missing, or verified `invalid`. Recorded on the lead
(`enrichment_json.bounce_shield_override = {by, at, reason, email_sha256,
verdict_at_override}`) and as a `compliance_audit_log` row
(`bounce_shield_override` / `bounce_shield_override_revoked`). Bound to the
address: an edited email voids it. A re-verification that returns `invalid`
removes it.

---

## Reconciliation with the bounce auto-pause threshold

**No threshold is changed.** The numbers measure different things:

| Threshold | Where | Population | Action |
|---|---|---|---|
| 3% (`BOUNCE_RATE_PAUSE_THRESHOLD`) | `sequence_engine.check_bounce_rate` | all sends of one campaign, ≥ 10 sends | auto-pause the campaign (brake) |
| lower, per workspace | `compliance_rules.bounce_threshold` (may only lower) | same | same |
| 2% / 5% | `mailbox_health` BOUNCE_WARN / BOUNCE_BAD | one mailbox, rolling window | throttle / pause mailbox |
| **< 2% (F01 target)** | `GET /bounce-shield/metrics` | sends tagged `email_verdict=valid` | **measurement only** — no brake |

The shield acts *before* a send; the brake acts *after* bounces. Blocked
messages never become SENT, so they do not enter the auto-pause denominator.
If the founders want the brake tighter during the pilot, the existing,
audited lever is a workspace compliance rule (`bounce_pause_threshold`) — a
deliberate, visible decision, not a side effect of this PR. A test pins
`bounce_rate_pause_threshold == 0.03` and shows `check_bounce_rate` behaves
identically with both F01 flags on.

---

## Non-goals

* No new data vendor adapters (Dropcontact, Findymail, ZeroBounce, …): none
  has credentials and none has been checked against current docs.
* No dollar pricing of attempts: the ledger records provider **units**. Dollar
  conversion, forecasts, caps and "charge only on verified" are **F07 Spend
  Guard**; one price table, not two.
* No change to Apollo search/enrich cost accounting (still structured logs).
* No automatic re-verification sweep of old leads; re-verification is per
  lead, on demand.
* No distinction between hard and soft bounces — the bounce path does not
  record one (see Unverified).
* No change to any bounce threshold.

## Data model and migration

Migration `0073_waterfall_enrichment` (`down_revision =
"0072_guardrails_studio"` -- re-chained after F05 so the history has one head), **create-only**: one table, nothing altered.

`enrichment_attempts` — one row per provider attempt:

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK users ON DELETE CASCADE, indexed | owner (tenancy) |
| strategy_id | UUID FK strategies ON DELETE CASCADE, nullable, indexed | |
| lead_id | UUID FK leads ON DELETE SET NULL, nullable, indexed | |
| batch_id | UUID FK lead_batches ON DELETE SET NULL, nullable | |
| step | String(32) | waterfall step key (`apollo`, `hunter`, `reverify`) |
| provider | String(50) | who was called (`apollo`, `hunter`, …) |
| operation | String(20) | `find_email` / `verify` |
| outcome | String(20) | find: `candidate`/`not_found`/`suppressed`/`duplicate`/`error`; verify: `valid`/`catch_all`/`invalid`/`unknown`/`error` |
| cached | Boolean | served from cache (no charge) |
| cost_units | Integer | provider units consumed (0 when cached / already paid) |
| claimed_status | String(40), nullable | the finder's own label, e.g. Apollo `email_status` |
| email_domain | String(255), nullable | domain only — **no address, no name** |
| created_at / updated_at | timestamps | |

No personal data: the ledger never stores an address or a name. Downgrade
drops the table (the ledger is lost; nothing else is touched — documented,
not lossless).

`account_export.OWNERSHIP` gains `enrichment_attempts: ["user_id"]`.
`scripts/activate_with_keys.py` `TARGET_REVISION` moves to the new head.

## API

All under the existing auth chokepoint; every lead is resolved with
`app/api/leads.py::_owned_lead` (another account's lead → 404).

| Method | Path | Notes |
|---|---|---|
| GET | `/leads/{id}/email-verdict` | verdict, override, send decision, this lead's ledger rows, shield settings |
| POST | `/leads/{id}/email-verdict/refresh` | re-verify the current address (spends a Hunter verify unless cached). 409 when both flags are off. Rate limited (`RATE_LIMIT_AI_ACTION` bucket `bounce_shield_refresh`). Re-queues blocked messages if now sendable. |
| POST | `/leads/{id}/email-override` | `{reason}`; owner/manager only; 409 when the shield is off, address missing/suppressed/invalid. Re-queues blocked messages. |
| DELETE | `/leads/{id}/email-override` | revoke; owner/manager only |
| GET | `/bounce-shield/metrics?days=30` | owner-scoped: sends and bounces by verdict tag, verified-send bounce rate with sample size, ledger totals (attempts, paid, cached, units, by provider), provider chain status, pause threshold for context |

## UI

Lead detail → Overview: **Email verification** panel
(`frontend/src/components/leads/EmailVerdictPanel.tsx`) — the verdict and who
found the address, whether the next email would send and why not, the
attempts with cost/cache, **Re-verify**, and **Allow sending anyway** (reason
required) / **Remove override**. Hidden when both flags are off and the lead
has no verdict. The metrics endpoint has no page yet; F09's pilot scorecard
is its intended consumer.

## Tenancy

* Ledger rows carry `user_id`; every read filters by it.
* Metrics join `Lead → Strategy → Product.user_id == current user`.
* Override/refresh/verdict resolve the lead through `_owned_lead`.
* Owner for ledger rows written by workers = `notifications.owner_of_strategy`.
* The Redis response cache is keyed on the request payload and shared across
  accounts, as it already was (single-tenant deployment per P0-D).

## Compliance review (CAN-SPAM / GDPR / CASL / PDPA)

* **Suppression first, per person.** Candidates are suppression-checked
  before any paid verification; one suppressed address drops the person.
  The send-time suppression check is untouched and runs **before** the shield.
* **No new send path.** The shield only *removes* sends. Re-queued messages
  re-enter `send_message_impl` and pass suppression, consent rules, send
  window, caps, mailbox health, client-domain isolation and the review queue
  again.
* **Override cannot reach an opted-out or undeliverable address**, is
  owner/manager-only, needs a reason and is audited.
* **GDPR minimisation.** The ledger stores no address or name (domain only).
  The verdict stores a SHA-256 of the address, not the address. Both verdict
  and override live in `enrichment_json`, which GDPR delete already wipes.
  The ledger is in the account export.
* **Lawful basis** for processing B2B contact data is unchanged by this
  feature (same vendors, same fields). Using a second vendor (Hunter finder)
  to *find* an address is already existing behaviour; F01 only applies it in
  more cases (when Apollo's address is bad).
* **Out-of-scope finding (reported, not fixed):** the existing Redis response
  cache holds provider payloads (which contain addresses) for 30 days and is
  not purged by GDPR delete.

## AI cost and budget impact

**None.** F01 makes no model call. No `usage_meter` scope is needed. The
data-provider spend it adds is bounded: per lead at most one Hunter finder +
one Hunter verify beyond today's path, and only when Apollo's address is not
verified-valid; stop-at-first-valid and the cache keep the common case at
today's cost. Every unit is in the ledger.

## Test plan (all offline: SQLite, fakes, fakeredis)

* `tests/test_waterfall_enrichment.py` — stop at first valid (Hunter never
  called); fall-through from invalid Apollo email to a valid Hunter email;
  catch-all kept as best fallback; suppressed candidate drops the person and
  is never verified; same candidate never verified twice; disabled steps
  (no credentials, unknown keys); classification table; ledger rows (cost,
  cached, claimed_status, no address); verdict reuse; resumability (verify
  stage makes zero verifier calls after the find stage); flag off =
  identical legacy behaviour and no ledger rows.
* `tests/test_bounce_shield.py` — guard off = legacy send; valid sends and
  tags meta; unverified/legacy/stale/edited-address/unknown blocked with audit
  row and enrollment kept; catch-all by policy (block, allow, garbage value
  fails closed); override allows non-invalid, never invalid; non-email
  channels untouched; requeue on override passes all gates; API tenancy (other
  account → 404), RBAC (SDR → 403), validation, refresh, metrics math and
  owner scoping; bounce threshold unchanged.
* `tests/test_waterfall_enrichment_migration.py` — migration table == model.
* `tests/test_account_export.py` (existing) — completeness covers the table.
* Frontend vitest `bounceShield.test.ts` — labels/decision text and the
  override write body.

## Success metric

*Hard-bounce rate on verified sends below 2% in the pilot* — **measured, not
promised.** Measured by `GET /bounce-shield/metrics`: bounces ÷ sends among
`SENT` outcomes tagged `email_verdict=valid`, over the window. Reported with
its sample size; under 100 verified sends it is flagged `sample_ok: false`.
**Current value: UNMEASURED** — no pilot has run.

## Unverified

* **Hunter response fields** (`status`, `result`, `accept_all`, `score`) and
  endpoints are from the existing adapter's `# TODO: verify` notes, not
  checked against current Hunter docs in this PR. Classification accepts both
  the `status` and the older `result` shapes for that reason.
* **Apollo `email_status`** field name/values (recorded as `claimed_status`
  only, never acted on) — unverified.
* **Credit cost per call** (1 unit per finder/verify call) is a unit count,
  not checked against current vendor billing.
* **Redis cache persistence on Upstash** (eviction policy, durability) —
  unverified; the lead-level verdict reuse is the fallback.
* **Hard vs soft bounce**: the bounce path (`reply_classification` "bounce")
  does not distinguish them, so the metric counts all recorded email bounces
  — an upper bound on the hard-bounce rate.
* **No live vendor run**: nothing here was exercised against real Apollo or
  Hunter, or in production.
