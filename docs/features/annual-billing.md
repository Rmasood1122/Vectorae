# Annual billing (+ pilot-safe trials, renewal notices, online cancel)

**Flag** `annual_billing_enabled` (System Settings, default **off**) ·
**Pricing constant** `ANNUAL_MONTHS_CHARGED = 10` in `app/core/billing_catalog.py`
(**founder pricing decision to confirm**) ·
**Service** `app/services/billing.py` (checkout, webhooks, cancel, renewal notices) ·
**Stripe** `app/integrations/stripe_billing.py` ·
**API** `app/api/billing.py` ·
**Worker** `app/workers/billing_tasks.py` (`send_renewal_notices`, daily 15:10 UTC) ·
**UI** `frontend/src/app/pricing/page.tsx` (monthly/annual toggle + disclosure),
`frontend/src/components/billing/PlanCard.tsx` (Profile → Plan: status, renewal
date, **Cancel plan**), helpers in `frontend/src/lib/pricing.ts` ·
**Migration** `0082_annual_billing` (also carries F04's `answer_earlier_replies`) ·
**Not plan-gated** (it is a way to pay for the existing plans).

Founder decision 2026-10-11: **"Add Annual Plan"**, in answer to: Vectorae has
no annual plan, and the monthly checkout's 14-day trial converts to paid before
a 30-day pilot (F09) ends.

## Problem

1. Buyers who have decided on Vectorae want to pay yearly for a discount; there
   was no way to do that (`BILLING_MODELS = (monthly, pay_per_meeting)`).
2. The F09 spec recorded a real defect: a monthly checkout carries a 14-day
   Stripe trial (`TRIAL_DAYS`), so an account that starts paying during a
   30-day pilot is **charged before the pilot's verdict** — exactly the
   "annual lock-in / pay before you know" complaint F09 exists to answer.
3. An annual plan is an automatic-renewal contract for a term of a year. Doing
   it without a pre-checkout disclosure, an acknowledgment, a reminder before
   renewal and an online cancel path is the kind of thing auto-renewal laws
   penalise. The web app had **no cancel button at all** (help article 22 told
   customers to contact support).

## Recon — this is "extend billing", not a parallel system

| Already existed | Where | What this change does with it |
|---|---|---|
| Price list as code (4 monthly tiers + pay per meeting), `plan_for`, `catalog()` | `app/core/billing_catalog.py` | Adds `ANNUAL` to `BILLING_MODELS`, `SUBSCRIPTION_MODELS = (monthly, annual)`, `ANNUAL_MONTHS_CHARGED`, `annual_price_cents()`, `annual_tiers()`; `catalog(annual_enabled=...)` adds an `annual` block only when the flag is on. |
| Plan entitlements | `app/core/plans.py` | **Unchanged.** `plan_for("annual", tier)` returns the same PLANS key as monthly, so limits are identical by construction. No PLANS key added. |
| Checkout (stub + Stripe), cancel, webhooks, `_sync_plan` | `app/services/billing.py` | Annual goes through the same functions. Cancel = at period end (live), immediately (stub) — the existing monthly semantics. |
| Stripe REST adapter | `app/integrations/stripe_billing.py` | `create_subscription_checkout` takes `billing_model`, `interval` (`month`/`year`), `unit_amount_cents` and an optional absolute `trial_end`. |
| Transactional email | `app/services/email_sender.py::send_email` | Acknowledgment, trial-ending and renewal notices to the account owner. Not the outreach send path. |
| Pilot record + verdict time | `app/services/pilot_mode.py` (`get_active`, `verdict_final_at`, `grace`) | Read **by billing** to push a trial past the verdict. `pilot_mode` still never imports or writes billing (test pinned). |
| Profile → Plan card | `frontend/src/app/(app)/profile/page.tsx` | Replaced by `PlanCard`, which also shows the subscription and the **Cancel plan** button (`POST /billing/cancel` existed; no UI called it). |

## Behavior

### 1. The annual price

For every monthly tier with a price (`starter`, `growth`, `scale`,
`enterprise`): one annual price = `monthly price × ANNUAL_MONTHS_CHARGED`
(default 10 = "2 months free"), Stripe recurring interval **`year`**.

| Tier | Monthly | Annual (×10) | Per-month equivalent | Saving vs 12 × monthly |
|---|---|---|---|---|
| Starter | $149 | $1,490 | $124.17 | $298 |
| Growth | $349 | $3,490 | $290.83 | $698 |
| Scale | $699 | $6,990 | $582.50 | $1,398 |
| Enterprise | $1,499 | $14,990 | $1,249.17 | $2,998 |

`ANNUAL_MONTHS_CHARGED` lives in **one place** (`billing_catalog.py`); the
pricing page, checkout, Stripe `price_data`, emails and tests all read it.
**Founder pricing decision to confirm:** the multiplier (10), and whether
pre-created Stripe Price ids (`STRIPE_PRICE_<TIER>_ANNUAL`, optional, like the
monthly ones) will be used — if they are, their amount must equal the catalog
(nothing checks a configured Price id against the catalog; see Unverified).

### 2. Checkout

`POST /billing/checkout {"billing_model": "annual", "tier": "growth",
"accept_renewal_terms": true, "disclosure_version": "<DISCLOSURE_VERSION>"}`.

* **Flag off** → `409 "Annual billing is not offered right now."`; the catalog
  has no `annual` block and the pricing page shows no toggle. Flag off never
  affects an annual subscription that already exists (webhooks, cancel and
  renewal notices keep working — the legal duties do not depend on the flag).
* **Consent (monthly AND annual — every auto-renewing plan)** — a checkout
  without `accept_renewal_terms: true` is `422`; one whose
  `disclosure_version` is not the current `billing_catalog.DISCLOSURE_VERSION`
  is `409` ("reload the page"). Pay per meeting needs neither. The consent is
  **recorded only when the checkout completes** (review F8): the checkout puts
  it in the Stripe Checkout Session's metadata (`accept_renewal_terms`,
  `disclosure_version`, `consented_at`, `consent_price_cents`,
  `consent_interval`) and `checkout.session.completed` copies it to the
  subscription (`renewal_consent_at`, `consent_disclosure_version`,
  `consent_price_cents`, `consent_interval`, `consent_checkout_session_id`).
  An abandoned checkout records nothing. A stub checkout completes at once and
  records it with no session id. The frontend constant
  `RENEWAL_DISCLOSURE_VERSION` must equal the backend's (a test pins it).
* **The trial the page states is the backend's** (review F3) —
  `GET /billing/checkout-terms` (also `checkout_terms` in `GET /billing`)
  returns `trial_eligible`, `trial_days`, `trial_ends_at`, `pilot_extended`,
  `pilot_verdict_at`, `first_charge_at`, `disclosure_version` — computed by the
  same `trial_end_for` that `start_checkout` uses. The disclosure and the
  button label are built from it; while it loads, the button is disabled.
  A signed-out visitor sees a new account's terms (14-day trial, no pilot) —
  they must sign up before any checkout.
* **Returning customers** — an account that already had its trial gets NO
  trial parameter at all (Stripe's `trial_period_days` minimum is 1; `0`
  was being sent).
* **Old sessions** — starting a checkout expires the previous Checkout
  Session if it is still open (`POST /v1/checkout/sessions/{id}/expire`; a
  Stripe refusal for an already-closed one is ignored; stub mode never calls
  Stripe).
* **Trial** — the same once-per-account trial as monthly (`TRIAL_DAYS = 14`),
  then the full annual amount. A trialing annual account gets a trial-ending
  notice (below) before that first charge.
* **Limits** — identical to the monthly tier (same PLANS key).
* **Switching** — follows the monthly semantics (a completed checkout replaces
  the plan; the replaced Stripe subscription is cancelled) **except** that an
  account whose annual year is **paid** (status `active`/`past_due`, including
  one set to cancel at period end) cannot start a different checkout online:
  `409` "Your annual plan is paid until <date>. Changing it before then needs a
  prorated refund, which is not available online yet — contact support, or
  cancel it and choose a new plan when the year ends." Reason: the existing
  switch path cancels the old subscription **immediately with no refund**,
  which for an annual term would forfeit up to 11 paid months. Proration is a
  **founder decision** (not built). A trialing annual account (nothing paid)
  can switch like a monthly one.
  **The same lock holds when a checkout COMPLETES** (review F2): a session
  opened earlier (e.g. while the annual plan was trialing) that completes
  after the year was paid does NOT cancel the paid year. The safest choice:
  keep **both** — the account stays on its paid annual plan; the new Stripe
  subscription is set to cancel at its own period end (so it never renews and
  nothing already paid for it is forfeited); `billing_subscriptions.support_note`
  is set and a `task_errors` row (`BillingNeedsSupport`) appears in the admin
  panel; the Plan card tells the customer a person will contact them about a
  refund or change. Cancelling either subscription immediately would forfeit
  money one way or the other; a person decides.
* **Events in any order** (review F1) — `checkout.session.completed` reads the
  subscription's own state from Stripe (`GET /v1/subscriptions/{id}`: status,
  trial, period end, price). If that read fails it never downgrades a status a
  subscription event already set for the same subscription; its placeholder is
  `trialing` only when the session's own metadata says a trial was requested,
  else `active`. When a new subscription replaces an old one, the old trial
  dates, period end and price are cleared (`trial_used` keeps the account's
  one trial used), so no notice can state a stale date.
* **Never automatic.** Nothing converts a monthly, pay-per-meeting or pilot
  account to annual: the only way to annual is this checkout, made by the
  account. Webhooks decide the model from Stripe's own price interval
  (`items.data[0].price.recurring.interval`), falling back to the metadata
  the checkout wrote, falling back to `monthly` (the pre-existing behaviour).
  Tests pin that monthly renewals, `subscription.updated` events without
  metadata, the flag turning on, and a pilot ending all leave a monthly row
  monthly.

### 3. Trial vs pilot (monthly AND annual)

When the account has an **active F09 pilot** (`pilot_mode.get_active`) and its
verdict time `verdict_final_at = ends_at + grace` is in the future, a
subscription checkout's trial ends at

    trial_end = max(normal trial end, verdict_final_at)

where "normal trial end" is now + 14 days for a first trial and now (no trial)
for an account that already had one. **So no charge is taken during an active
pilot**, even for an account that already used its trial. Stripe requires a
`trial_end` at least 48 hours ahead, so a pilot-protected trial is never
shorter than **49 hours** (48 h + 1 h margin for the time the customer spends
on the checkout page, rounded **up** to a whole second — review F6). It can
only end later than the verdict, never earlier.
Stripe mode passes `subscription_data.trial_end` (unix) instead of
`trial_period_days`. Stub mode follows stub semantics: the row is `trialing`
with `trial_ends_at = trial_end`.

This applies whether or not `annual_billing_enabled` is on (it fixes the
monthly defect F09 recorded). It does not depend on `pilot_mode_enabled`: an
active pilot row is what matters. It does not change a subscription that was
already running when the pilot started (see Non-goals).

A pilot-extended trial lasts until the verdict: at most 30 days + the grace
period (`pilot_mode_verdict_grace_hours`, default 24 h, max 168 h). With the
default grace that is 30–31 days; with a longer grace it can exceed 31 days,
and such a trial gets the trial-ending notice below, monthly or annual (every
annual trial gets it anyway).

### 4. Notices (transactional email to the account owner)

One table, `billing_renewal_notices`, UNIQUE `(subscription_id, kind,
period_key)`; a row is **claimed before sending** (insert, or a
compare-and-set retry of a `failed` row, at most 3 attempts), exactly the F09
scorecard pattern — idempotent under re-runs and overlapping workers; a crash
mid-send leaves `sending`, which is never retried (a missing duplicate is
better than a double notice). **A notice that will not go out is raised**
(review F7): a row stuck in `sending` for over an hour, or `failed` 3 times, is
logged at ERROR and gets one `task_errors` row (`RenewalNoticeNotSent`) in the
admin panel, once (`escalated_at`). Stub subscriptions
(`is_stub`) get none: nothing is charged, and a notice saying it would be is
false. A suspended owner **is** mailed (they are still billed).

| Kind | When | For | `period_key` |
|---|---|---|---|
| `acknowledgment` | first sweep after an annual subscription grants a plan | annual | `<billing_model>:<tier>:<stripe subscription id>` |
| `trial_end` | from 7 days before `trial_ends_at` until it passes | trialing annual, or any trial longer than 31 days (pilot-extended) | `trial_ends_at`, UTC `YYYY-MM-DDTHH:MM:SSZ` |
| `renewal` | from **30 days** before `current_period_end` until it passes | annual, `active`/`past_due`, not cancelling | `current_period_end`, UTC `YYYY-MM-DDTHH:MM:SSZ` |

`period_key` is normalised to UTC whole seconds (review F9), so the same
instant gives the same key whatever timezone the database driver returns.

Each notice states: the plan, the amount that will be charged — **what Stripe
actually bills**, stored from the subscription webhook
(`items.data[0].price.unit_amount` / `recurring.interval` / `currency` →
`price_unit_amount_cents`, `price_interval`, `price_currency`; review F5). The
catalog is used only for stub rows. A live row whose Stripe price is not known
is read from Stripe once; if still unknown, the notice is held (`failed`,
retried, escalated after 3) — a guessed amount is never emailed. Then the
date, written with "(UTC)", that it renews automatically for another year (or
month) until cancelled, and **how to cancel online** (Profile → Plan →
Cancel plan, link included), and that cancelling keeps access to the end of the
paid period. A notice sent inside the last 15 days before the charge is
recorded with its `days_before` and logged at ERROR (it is outside the New
York window; see Compliance).

Worker: `app.workers.billing_tasks.send_renewal_notices`, daily at 15:10 UTC
(morning in the US), queue `default`. A missed day is caught up the next day
(the window is 15–30 days wide).

### 5. Online cancel

Profile → **Plan** (`PlanCard`) shows the plan, billing model, status, the
renewal / trial-end date, and — when a subscription grants the plan and is not
already cancelling — a **Cancel plan** button with one confirm step. It calls
the existing `POST /billing/cancel`: live monthly/annual → cancel at period end
(access continues until the date shown); **past due** (the renewal payment
failed) → cancelled **now** and every open invoice of the subscription is
**voided**, so a period the customer cancelled is never collected later
(review F4; the confirm step says so); pay per meeting and stub rows → ends now.
**Fail closed** — a real Stripe subscription (`is_stub` false) with no
`STRIPE_SECRET_KEY` configured is **not** cancelled locally (Stripe would keep
billing while the app said "cancelled"): `503`, nothing changes.
After cancelling, the card says "Cancelled — access until <date>". This is the
"easy online cancel path"; it is as easy as signing up (one page, one button,
one confirm).

### 6. Flag off

`annual_billing_enabled = false` (default): no `annual` block in the catalog,
no toggle on the pricing page, annual checkout `409`. Everything else in this
spec is on regardless: pilot-safe trials, notices for annual subscriptions
that exist, the cancel button, the webhook interval fix.

## Non-goals

* Proration / mid-term change of a paid annual plan (blocked with a support
  path; founder decision).
* Refunds (none are issued by code; Stripe dashboard).
* Changing a subscription that was already running when a pilot started (F09
  says the pilot never touches billing; a renewal of an existing monthly plan
  during a pilot still charges — reported, not changed).
* Annual pay-per-meeting (it has no fixed fee).
* Multi-currency, tax (Stripe Tax not configured — unchanged).

## Data model and migration (`0082_annual_billing`)

`down_revision = "0081_daily_send_approval"` (the orchestrator re-chains numbers).
SQLite- and PostgreSQL-safe types (JSON not used; `sa.Uuid`, booleans with
server defaults).

* `billing_subscriptions` + `trial_started_at` (DateTime tz, null) — from
  Stripe `trial_start` / stub checkout; used to tell a >31-day trial.
  + `trial_used` (Boolean, NOT NULL, default false; **backfilled true where
  `trial_ends_at` is set**) — the account's one trial survives a replaced
  subscription.
  + consent: `renewal_consent_at` (DateTime tz), `consent_disclosure_version`
  (String 40), `consent_price_cents` (Integer), `consent_interval` (String 10),
  `consent_checkout_session_id` (String 128) — all null until a checkout with
  consent completes.
  + Stripe price: `price_unit_amount_cents` (Integer), `price_interval`
  (String 10), `price_currency` (String 3).
  + `support_note` (String 500) — a state a person must resolve.
  `billing_model` stays `String(20)` (`annual` fits).
* **new** `billing_renewal_notices`: `id`, `user_id` (FK users CASCADE),
  `subscription_id` (FK billing_subscriptions CASCADE), `kind` (String 20),
  `period_key` (String 120), `charge_at` (DateTime tz, null), `amount_cents`
  (Integer, null), `days_before` (Integer, null), `status`
  (`sending`/`sent`/`failed`), `attempts`, `sent_at`, `error` (String 300),
  `escalated_at` (DateTime tz, null), timestamps; UNIQUE `(subscription_id, kind, period_key)`
  `uq_billing_renewal_notice`; index on `user_id`.
* `reply_copilot_settings` + `answer_earlier_replies` (Boolean, NOT NULL,
  server default **true**) — F04, see `docs/features/f04-reply-copilot.md`.

**Downgrade** drops the table and the added columns: the notice log (evidence
of notices sent), consent records, stored Stripe prices, support notes and
trial starts are lost; F04 accounts fall
back to the default (answer earlier replies = on). Annual subscriptions remain
rows with `billing_model = 'annual'` — code from before this change would read
them as an unknown model (`plan_for` → free) — so **do not downgrade while an
annual subscription exists**. `TARGET_REVISION` in
`scripts/activate_with_keys.py` → `0082_annual_billing`.
`billing_renewal_notices` is in `account_export.OWNERSHIP` (`user_id`).

## API

| Method | Path | Change |
|---|---|---|
| `GET` | `/billing/catalog` (public) | `annual` block (tiers with `annual_price_cents`, `monthly_equivalent_cents`, `savings_cents`, `months_charged`) and `annual_available` when the flag is on |
| `POST` | `/billing/checkout` | `billing_model` accepts `annual`; new `accept_renewal_terms` (bool) and `disclosure_version` — both required for monthly and annual (422 / 409) |
| `GET` | `/billing/checkout-terms` | new: the trial a checkout would get now (F3) |
| `POST` | `/billing/cancel` | annual = at period end (live); past due = now + open invoices voided; real subscription with no Stripe key = 503 |
| `GET` | `/billing` | `subscription.billing_model` may be `annual`; `subscription.price_cents` is Stripe's price once known; `subscription.needs_support`; `checkout_terms` |

Webhooks: `customer.subscription.created/updated` store status, period end
(top level, or on the subscription items from Stripe API `2025-03-31.basil`
on), trial, cancel flag, price (amount, interval, currency) and `trial_start`. Renewal (`invoice.paid` on a new period) arrives as
`subscription.updated` with the new `current_period_end` — the next year's
notice keys on that, so each renewal gets its own notice.

## UI

Pricing page: a **Monthly / Annual** segmented toggle (shown only when the
catalog has `annual`). Annual shows each tier's annual total and per-month
equivalent and the saving. Above the checkout button, a disclosure box built
from the backend's checkout terms: "<Tier> annual: <$X> is charged on <date>
(UTC), when your 14-day free trial ends [— it runs until your 30-day pilot's
verdict | or: is charged today (this account has already had its free
trial)], then <$X> every year on that date until you cancel. It renews
automatically. Cancel anytime online in Profile → Plan → Cancel plan; you keep
your plan until the end of the period you paid for. Cancelling stops the next
renewal; a year already paid is not refunded." Monthly gets the same text per
month (no refund sentence). A required checkbox "I agree to these renewal
terms" for **both** monthly and annual (unticked again when the tier or period
changes). The button says what happens: "Start 14-day trial of Growth", "Start
free trial of Growth until <date>", or "Subscribe to Growth". Profile → Plan:
`PlanCard` (above).

## Tenancy

Billing routes are PERSONAL (`app/services/rbac.py`) — unchanged. Every
notice row carries `user_id`; the sweep reads the subscription's own user;
the pilot lookup is `pilot_mode.get_active(db, user.id)` (filtered by the
account). No cross-account reads.

## Compliance review — auto-renewal (zero tolerance; **needs founder/legal confirmation**)

I am not a lawyer; the readings below are what the code was built against,
not settled law. Vectorae sells to businesses; several of these statutes
protect "consumers" and may not apply to B2B sales at all — the build complies
anyway, because the posture is zero tolerance.

| Rule relied on | What it requires (as read here) | Where the code meets it |
|---|---|---|
| **California Automatic Renewal Law**, Bus. & Prof. Code §§17600–17606 (as amended, incl. AB 2863, eff. 1 July 2025) | Applies to every automatic renewal or continuous service offer — **monthly included** (§17602(a)(1)–(2)). Clear and conspicuous auto-renewal terms before purchase, near the request for consent; affirmative consent; an acknowledgment with the terms and how to cancel; online cancellation where sign-up was online; for a term of **1 year or longer**, a notice **15–45 days** before renewal; for a free trial longer than 31 days, a notice **3–21 days** before the trial ends | Pricing-page disclosure + required checkbox for monthly and annual (`accept_renewal_terms` + `disclosure_version`, stored at completion with price, interval and session); `acknowledgment` notice; Profile → Cancel plan; `renewal` notice at 30 days; `trial_end` notice at 7 days for trials >31 days and every annual trial |
| **New York General Obligations Law §5-903** | For certain automatically renewing service contracts, written notice **15–30 days** before the date the customer must give notice of non-renewal | `renewal` notice at 30 days (inside both the NY and CA windows); late notices (<15 days) logged at ERROR with `days_before` |
| **New York General Business Law §527-a** | Clear auto-renewal disclosure, consent, acknowledgment, cancellation mechanism | Same as California above |
| **ROSCA**, 15 U.S.C. §8403 | For negative-option online sales: disclose material terms before billing information, express informed consent before charging, simple cancellation | Disclosure before the Stripe redirect, checkbox, online cancel |
| FTC Negative Option ("click-to-cancel") Rule, 16 CFR 425 (2024) | Vacated by the 8th Circuit in July 2025 — **not relied on**; the cancel path meets its intent anyway | — |

Exact windows chosen: renewal notice at **30 days** (inside CA 15–45 and NY
15–30); trial notice at **7 days** (inside CA 3–21). Both are constants in
`billing_catalog.py` (`RENEWAL_NOTICE_DAYS`, `TRIAL_NOTICE_DAYS`).

Notices are Vectorae's own **transactional** account mail to the customer (no
prospect, no outreach chokepoint, no marketing content) — CAN-SPAM's
transactional/relationship category; GDPR basis: performance of the contract.
CASL/PDPA: transactional messages about an existing subscription. The notice
log stores no message body, only the facts of the notice.

## AI cost and budget impact

None. No model calls.

## Test plan (offline: SQLite, stub mode, recorded Stripe calls, captured email)

`tests/test_annual_billing.py`:
* catalog: no `annual` with the flag off; with it on, every priced monthly
  tier has an annual price = monthly × `ANNUAL_MONTHS_CHARGED`, same plan key,
  identical limits; the constant is defined once (grep);
* checkout: flag off 409; missing consent 422; stub annual first trial
  (14 days, trialing, plan granted, consent stored); stub annual with a
  previous trial → active for 365 days; live annual → Stripe interval `year`,
  amount ×10, metadata `annual`, configured annual Price id used;
* paid annual cannot switch online (409), trialing annual can; cancel live
  annual → at period end; stub → now;
* **pilot**: active pilot → stub and live trial ends at the verdict (monthly and
  annual), even with a previous trial; ≥48 h floor; ended pilot / another
  account's pilot → normal trial;
* **never auto-convert**: monthly renewal events (`subscription.updated` new
  period, `invoice.paid`) keep `monthly`; an event without metadata stays
  `monthly`; a `year` interval event flips nothing that was not an annual
  checkout's subscription id; turning the flag on changes no row; a pilot
  sweep/end leaves a monthly row monthly;
* webhooks: annual lifecycle (completed → trialing → active with a year-long
  period → renewal → next year's notice key);
* notices: acknowledgment once; renewal due at 30 days, not at 31, once per
  renewal (re-run, concurrent claim), a new period gets a new notice; not
  for cancelling / canceled / monthly / stub subs; trial_end for annual and for
  a >31-day monthly trial, not for a 14-day monthly trial; failure retried,
  capped at 3; email names amount, date and the cancel link; late notice
  records `days_before`; Celery task routed + on the beat schedule;
* tenancy: notices only to the subscription's own owner; billing routes stay
  personal.
* `tests/test_annual_billing_review.py` — the independent review of 60b786d
  (F1–F9 + returning customers, Stripe `basil` period end, cancel without a
  key, monthly consent); the reviewer's probes are ported with their
  assertions the right way round (P1–P6). 33 of its 34 tests fail on 60b786d
  (the 34th pins that pay per meeting still needs no renewal consent).
* `tests/test_annual_billing_migration.py`: table + columns match the models;
  downgrade round-trips.
* `frontend/src/tests/pricing.test.ts`: per-month equivalent, saving, disclosure
  text, checkout payload; `PlanCard` helpers.

## Success metric (measured, never promised)

* Share of new paid subscriptions that choose annual (from
  `billing_subscriptions.billing_model`), and
* zero charges during an active pilot (count of subscriptions whose first
  charge precedes an active pilot's `verdict_final_at` — should be 0).

Neither is measured yet: no live Stripe traffic.

## Unverified

* Stripe calls added by the review — `GET /v1/subscriptions/{id}`,
  `POST /v1/checkout/sessions/{id}/expire`, `GET /v1/invoices?subscription=&status=open`
  and `POST /v1/invoices/{id}/void` — built from Stripe's documented API, only
  recorded in tests. Our requests pin `Stripe-Version: 2024-06-20`; webhook
  payloads follow the endpoint's configured version, hence both
  `current_period_end` shapes are read.
* Stripe Checkout `subscription_data.trial_end` and `price_data.recurring.interval
  = year` against the live API (built from Stripe's documented parameters;
  only recorded calls in tests). Stripe's 48-hour minimum trial is from its docs.
* That a configured `STRIPE_PRICE_<TIER>_ANNUAL` matches the catalog amount
  (not checked; the founder must create it at monthly × 10 or leave it unset).
* Stripe sends its own receipts; whether Stripe's emails duplicate or conflict
  with the acknowledgment wording.
* Whether the cited statutes apply to Vectorae's B2B customers, and whether
  the exact wording satisfies them — **founder/legal confirmation needed**.
* Email deliverability of the notices (Resend/SPF/DKIM for
  `calendarharvest.com` is itself unverified, GAP-007).

## Out of scope (reported, not fixed)

* The existing monthly→monthly switch cancels the old subscription immediately
  without proration (small amounts; unchanged).
* Pay-per-meeting disputes still have no web UI.
