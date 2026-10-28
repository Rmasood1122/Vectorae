# Vectorae — What It Is, Everything It Does, and Why It Is Built This Way

**Vectorae** takes a product or a skill as
input and carries it to a booked meeting, on its own, running 24/7 in the
cloud. You describe what you sell. It researches the market, writes and
verifies a go-to-market strategy, sources and verifies real leads, writes and
sends personalised outreach across email and WhatsApp, follows up when nobody
replies, takes the booking on its own calendar, runs the meeting room, writes
the summary, and files all of it in a CRM that learns from the result.

This document is the complete map: every feature, the whole pipeline end to
end, and — in [Part 4](#part-4--why-this-is-built-differently) — an honest
account of what makes this architecture different from the usual stack, and
what it deliberately does not claim.

Written for someone who has never seen the codebase. Every claim below is
traceable to a file, and the files are named.

---

## Contents

- [Part 0 — The 60-second version](#part-0--the-60-second-version)
- [Part 1 — The pipeline, end to end](#part-1--the-pipeline-end-to-end)
  - [Stage 1 · Intake](#stage-1--intake)
  - [Stage 2 · Research (72 / 144 steps)](#stage-2--research-72--144-steps)
  - [Stage 3 · Verification (10 passes)](#stage-3--verification-10-passes)
  - [Stage 4 · Lead sourcing (5-stage chain)](#stage-4--lead-sourcing-5-stage-chain)
  - [Stage 5 · Sequences & personalisation](#stage-5--sequences--personalisation)
  - [Stage 6 · Sending (the compliance chokepoint)](#stage-6--sending-the-compliance-chokepoint)
  - [Stage 7 · Replies & routing](#stage-7--replies--routing)
  - [Stage 8 · Automated follow-up](#stage-8--automated-follow-up)
  - [Stage 9 · Booking](#stage-9--booking)
  - [Stage 10 · The meeting](#stage-10--the-meeting)
  - [Stage 11 · CRM](#stage-11--crm)
  - [Stage 12 · The learning loop](#stage-12--the-learning-loop)
- [Part 2 — Every feature, by area](#part-2--every-feature-by-area)
- [Part 3 — How it is built](#part-3--how-it-is-built)
- [Part 4 — Why this is built differently](#part-4--why-this-is-built-differently)
- [Part 5 — What it does NOT do](#part-5--what-it-does-not-do)

---

## Part 0 — The 60-second version

```
   YOU                          VECTORAE                            RESULT
   ───                          ─────────                            ──────

   "I sell local SEO      ┌──────────────────────────┐
    audits to fire        │  1. Intake               │
    protection cos."      │     + past-client story  │
        │                 └────────────┬─────────────┘
        │                              ▼
        │                 ┌──────────────────────────┐
        │                 │  2. Research             │  72 steps (or 144)
        │                 │     8 phases × 9 steps   │  ICP · market · rivals
        │                 └────────────┬─────────────┘  channels · messaging
        │                              ▼
        │                 ┌──────────────────────────┐
        │                 │  3. Verify               │  10 independent passes
        │                 │     PASS or fix & retry  │  legal · KPI · ICP fit
        │                 └────────────┬─────────────┘  ← ships nothing until
        │                              ▼                    all 10 are green
        │                 ┌──────────────────────────┐
        │                 │  4. Source leads         │  Apollo → enrich →
        │                 │     verify every email   │  Hunter verify → drop
        │                 └────────────┬─────────────┘   the undeliverable
        │                              ▼
        │                 ┌──────────────────────────┐
        │                 │  5. Write & send         │  per-lead copy, not
        │                 │     email + WhatsApp     │  mail-merge tokens
        │                 └────────────┬─────────────┘
        │                              ▼
        │                 ┌──────────────────────────┐
        │                 │  6. Follow up            │  auto, per-step delay,
        │                 │     Reply? Stop.         │  never twice
        │                 └────────────┬─────────────┘
        │                              ▼
        │                 ┌──────────────────────────┐
        │                 │  7. Book                 │  own calendar, own
        │                 │     own booking page     │  domain, own database
        │                 └────────────┬─────────────┘
        │                              ▼
        │                 ┌──────────────────────────┐
        ▼                 │  8. Meet                 │  join · live notes ·
   "Meeting with Sara,    │     room + AI summary    │  transcript · actions
    Thursday 10:00,       └────────────┬─────────────┘
    here's the summary                 ▼
    and 3 action items"   ┌──────────────────────────┐
                          │  9. Learn                │  every outcome feeds
                          │     playbook + A/B       │  the next strategy
                          └──────────────────────────┘
```

**The whole thing runs on a server.** Close your laptop; the sequences keep
sending, the follow-ups keep firing, the booking page keeps taking bookings.
The web app, the Android app and the CLI are three windows onto one backend,
not three copies of the logic.

---

# Part 1 — The pipeline, end to end

## Stage 1 · Intake

**What you give it:** a product or a skill, in plain language. Optionally, the
details of a past client and the story of how you won them.

**What it does with the past client:** `app/services/pattern_recognition.py`
extracts seven structured patterns from that free-text story — industry,
company size, buyer role, deal size, acquisition channel, trigger event, sales
cycle length. Those seven become the anchor for everything downstream.

**Why this matters more than it sounds.** There are two flows:

| | Flow 1 — **WITH_CLIENTS** | Flow 2 — **NO_CLIENTS** |
|---|---|---|
| You have | at least one won client | nothing yet |
| Pipeline | 72 research steps | 72 GTM + 72 strategy = **144** |
| Anchored on | your proven pattern | a positioning hypothesis it builds first |
| Verification pass #10 | consistency with your past-client pattern | consistency with the GTM plan |

A system that only supports Flow 1 cannot help anyone launching. A system that
only supports Flow 2 throws away the single most valuable input a founder has —
the shape of the client they already won. This does both, and the difference
propagates all the way to the final verification pass.

*Files:* `app/api/products.py`, `app/services/pattern_recognition.py`,
`app/db/models.py::PastClient`

---

## Stage 2 · Research (72 / 144 steps)

Eight phases, nine steps each. The ninth step of every phase is a **synthesis**
step that consumes the eight before it — so the pipeline is a funnel, not a
list.

### The 8 strategy phases

| Phase | What it settles |
|---|---|
| **1 · Product decomposition** | core offering, cost of inaction, value prop, differentiators, feature→benefit map, pricing logic, delivery model, proof inventory |
| **2 · ICP** | firmographics, personas, decision-makers, pain ranked by urgency, buying triggers, **disqualifiers and anti-ICP**, watering holes |
| **3 · Market sizing** | TAM/SAM/SOM with stated assumptions, segmentation, segment scoring, two deep segment profiles, entry sequencing |
| **4 · Competitors** | direct rivals, indirect alternatives, **the do-nothing option**, messaging audit, pricing landscape, positioning gaps, win/loss hypotheses |
| **5 · Channels** | cold email viability, WhatsApp opt-in reality, LinkedIn, referral, inbound — each scored against the ICP, **each with per-country compliance constraints** |
| **6 · Messaging** | narrative arc, hooks per persona, offer design, subject-line bank, CTA design, personalisation variables, sequence arcs, A/B variant plan |
| **7 · Objections** | objection inventory by frequency, rebuttal scripts, proof gaps, case-study angles, ROI math, risk reversal, social-proof plan |
| **8 · Execution** | sequence blueprints, cadence and send times, **volume ramp and warm-up**, KPI targets, budget, tooling, team, risk register, 90-day plan |

### The 8 GTM phases (Flow 2 only, run after the strategy pipeline)

*(Corrected 2026-10-11: this heading used to say "run first". The code runs the
strategy pipeline first and GTM second: `app/pipeline/engine.py:37-40`
`pipelines_for_flow` returns `[STRATEGY, GTM]` for Flow 2.)*

Positioning · Pricing · Launch sequencing · Channel budget · Content plan ·
Partnerships · Sales motion · Funnel metrics.

### Three things about how this is built

**1. The steps are DATA, not code.** `app/pipeline/registry.py` declares all 144
steps as plain structures. `app/pipeline/engine.py` never changes when a step
is reworded. Tests (`tests/test_registry.py`) enforce the invariants: exactly
8 × 9 = 72 per pipeline, globally unique step ids, `step_no` running 1..72 in
order.

**2. Every step is resumable.** `research_steps` carries a
`UNIQUE (strategy_id, pipeline, step_no)` constraint — **the schema itself
guarantees "resume never duplicates a step."** A worker killed at step 47
resumes at 47, not at 1, and cannot write 47 twice.

**3. The prompt forbids invention.** The system prompt is explicit: *"You never
invent statistics; when a figure is an estimate you label it as an
assumption."* Verification pass #1 then checks that it obeyed.

*Files:* `app/pipeline/registry.py`, `app/pipeline/engine.py`,
`app/workers/tasks.py`

---

## Stage 3 · Verification (10 passes)

**No strategy reaches a real person until ten independent checks pass.**

Claude acts as a strict reviewer against exactly one criterion at a time, and
must answer in JSON — `PASS`, or `FAIL` plus a concrete instruction for what to
change. On `FAIL`, a fixer rewrites the document to resolve that one finding
and the pass runs again, up to `VERIFICATION_MAX_RETRIES_PER_PASS`. Exhaust the
retries and the strategy is marked `needs_human_review` — it does **not** ship.

| # | Pass | What it refuses to let through |
|---|---|---|
| 1 | **Factual accuracy** | invented statistics; unlabelled estimates |
| 2 | **ICP fit** | drift toward audiences the ICP explicitly excludes |
| 3 | **Channel–message fit** | a 400-word essay assigned to WhatsApp |
| 4 | **Legal & compliance** | missing CAN-SPAM identity or unsubscribe, no GDPR lawful basis for EU targets, cold WhatsApp outside approved templates, ToS-violating scraping. **"A strategy cannot ship without this pass."** |
| 5 | **Deliverability risk** | day-one mass blasting, no warm-up ramp, no bounce threshold |
| 6 | **Competitive realism** | "we beat X" claims unsupported by the competitor analysis |
| 7 | **Resource feasibility** | plans needing headcount or budget the user does not have |
| 8 | **KPI realism** | reply/meeting/win rates outside credible cold-outreach benchmarks |
| 9 | **Personalisation quality** | generic blast copy wearing a `{first_name}` token |
| 10 | **Consistency** | Flow 1: departure from the proven past-client pattern. Flow 2: contradiction with the GTM plan. |

### The precedence rule — a real bug, fixed in the prompt

The research pipeline deliberately refines its own estimates: Phase 1 might say
"$2,500–$7,500/mo" and Phase 2 finalise "$3,000 Foundation / $5,500 Growth".
Without an explicit rule, the judge treated every phase as equally
authoritative, failed the document for using either value, then for using
both — an unsatisfiable criterion that made the fix loop oscillate until it
exhausted its retries. *(Observed in the 2026-08-19 run.)*

`PRECEDENCE_RULE` in `app/verification/passes.py` now states the rule to the
judge explicitly: **later phases supersede earlier ones; the highest-numbered
phase is authoritative.** Deterministic across runs, rather than left to
per-call inference.

### The truncation guard

`app/services/anthropic_client.py` raises `TruncatedResponseError` when the
model stops because it hit `max_tokens` rather than because it finished. This
is not theoretical: on 2026-08-19, seven of eight fixer calls returned exactly
`out_tokens=8000` against an ~8,234-token document, and each truncated result
was written straight over `strategy_document`. The document still ends
mid-sentence at `**Pricing range:** $3,000-`. **A length cutoff is not a
shorter answer — it is an answer with the end missing**, and the gateway now
refuses to return one as if it were complete.

*Files:* `app/verification/passes.py`, `app/verification/loop.py`,
`app/services/anthropic_client.py`

---

## Stage 4 · Lead sourcing (5-stage chain)

Five Celery stages, each resumable, each recording its own state on
`lead_batches.stage`:

```
SOURCING ──▶ ENRICHING ──▶ FINDING_EMAILS ──▶ VERIFYING ──▶ FINALIZED
 Apollo       per-lead        Hunter            Hunter        suppression
 search       enrichment      email finder      verify        + dedupe
```

Each lead walks its own status ladder:

```
sourced → enriched → email_found → verified ──┐
                                  → flagged ──┼──▶ contacted → replied → meeting_booked
                                  → dropped ──┘
```

**Deduplication is enforced by the database, not by application code.** `leads`
carries `UNIQUE (strategy_id, email)` and
`UNIQUE (strategy_id, source, external_id)`. A retried batch physically cannot
create the same person twice.

**Undeliverable addresses are dropped, not sent to.** Hunter's verdict maps
straight onto status: deliverable → `verified`, risky → `flagged`,
undeliverable → `dropped`. Nothing in the send path will pick up a `dropped`
lead.

**Every external call is protected.** `app/integrations/plumbing.py` gives
every adapter, for free: retry with exponential backoff honouring
`Retry-After`, a Redis response cache with per-call TTL (*"we never pay twice
for the same search"* — enrichment is cached 30 days), a **circuit breaker
whose state is shared across every worker via Redis**, and one structured JSON
log line per call carrying provider, endpoint, status, latency, cost units,
cache hit and attempt number.

**F01 — Waterfall Enrichment (flag `waterfall_enrichment_enabled`, default
off).** With the flag on, the find stage walks an ordered provider chain
(`waterfall_provider_order`, Apollo → Hunter; providers without credentials
are skipped) and every candidate goes through the independent verifier: the
first address Hunter confirms wins and nothing after it is called. A bad
Apollo address now falls through to Hunter's finder instead of dropping the
person; a suppressed candidate drops the person outright. Every attempt —
including 30-day cache hits at cost 0 — is a row in `enrichment_attempts`
(domain only, never the address). Apollo's own "verified" label is recorded,
never trusted. Spec: `docs/features/f01-waterfall-enrichment.md`.

*Files:* `app/workers/lead_tasks.py`, `app/integrations/apollo.py`,
`app/integrations/hunter.py`, `app/integrations/plumbing.py`,
`app/services/waterfall_enrichment.py`

---

## Stage 5 · Sequences & personalisation

A **sequence** is a named cadence of steps. A **step** carries a *brief*, not
copy — "open on their hiring post, ask one question about onboarding" — and the
final message is generated per lead, at send time.

**Steps are multi-channel.** A step's `channel` is nullable: `NULL` means "use
the sequence's channel", so every sequence written before multi-channel
existed still works, and a non-null value overrides per step. Step 1 email,
step 3 WhatsApp template follow-up, in one sequence.

**The two-stage split is deliberate.** Because a step holds a brief, the
message is rendered *after* the compliance gates, against the messaging
playbook *as it stands today* — not against whatever it said when the sequence
was written. The rendered subject and body are persisted on the message row
**before** transmission, so what was sent is always recoverable.

**WhatsApp variables are filled honestly.** `lead.<field>` mappings fill
deterministically with no model call. Anything else is a brief Claude fills
from enrichment plus the playbook, with an explicit instruction: *"never
invented facts, names, or statistics."* A variable that cannot be filled
**fails the send** rather than transmitting a template with an empty slot.

**"Why now" signals carry a date and a source (F02, flag `signal_radar_enabled`,
off by default).** Stored company news, the lead's own LinkedIn posts, Apollo
funding/headcount fields and strategy market intel naming the company become
dated `lead_signals` rows with an expiry (≤ 90 days) and a source link, shown on
the lead. An email may be offered the one freshest linked signal, and it counts
as *cited* only when the claim engine verified the sentence against that
signal's own stored source. No new vendor, no new fetch, no new model call.
See `docs/features/f02-signal-radar.md`.

**Autopilot launch: research to first message without a person in between
(flag `autopilot_launch_enabled` offers it; each strategy opts in, off by
default).** When a strategy opted in -- with a recorded lawful-basis
confirmation (who, when, the exact text; audited) -- and verification passes,
Autopilot runs its own lead search through the ordinary sourcing chain, writes
one sequence per channel from the strategy's own messaging research, runs the
Launch button's own gates, and enrolls each VERIFIED lead of its OWN batch on
one channel (never hand-added, earlier or job-change-tracking leads). It can
only be on while daily send approval covers every channel it uses; otherwise it
pauses and its messages are held. A failed or
needs-review verification is never bypassed; a channel whose prerequisite is
missing (Gmail, LinkedIn account, plan, approved WhatsApp template) waits while
the ready ones launch, the owner is told by email + push, and a 15-minute
sweep resumes it. No new send path: every message still passes the whole send
chokepoint, daily send approval included.

**Best time to send (flag `best_time_scheduling_enabled`, default on).** Each
step is placed at the best slot in the prospect's local time inside sending
hours: channel and industry priors ("priors, not promises"), refined per
strategy when its pipeline completes (one metered call, priors on failure),
then the account's own reply rates once it has 30 sends on a channel (Bayesian
smoothing toward the prior). A person (owner or manager) can set their own window, which is hard: nothing
is placed outside it, and one that never overlaps the permitted hours is 422.
Learned rates are refreshed into Redis every six hours, so placing a message
costs at most one query. The strategy
page's **Why isn't this sending?** panel lists, per channel, every condition
stopping sends, each read from the gate the send path itself runs. Each lead's
timezone now comes from its own location (country, and state/province for the
US, Canada, Australia and Brazil), written at enrichment, so sending hours open
in the prospect's business day, not UTC's; leads with no location use the
strategy's target region, then the account's zone. A manual launch now needs a
server-checked, recorded lawful-basis confirmation (422 without it). See
`docs/features/autopilot-launch.md`.

*Files:* `app/services/message_personalization.py`,
`app/services/sequence_engine.py`, `app/api/sequences.py`,
`app/services/signal_radar.py`, `app/services/autopilot.py`,
`app/services/send_time.py`, `app/services/send_diagnostics.py`,
`app/services/lead_timezone.py`, `app/services/launch_consent.py`

---

## Stage 6 · Sending (the compliance chokepoint)

> *"The send task is the LAST LINE of compliance enforcement: it re-checks the
> suppression list, the campaign state, the enrollment state, the send window
> and the daily allowance immediately before transmitting — no matter what was
> true when the message was scheduled. **There is no bypass flag anywhere in
> this codebase, by design.**"*
> — `app/workers/outreach_tasks.py`, module docstring

Every single send runs this gauntlet, in this order:

1. **Idempotency claim.** `SCHEDULED → SENDING` before anything is transmitted.
   A message already `SENT` returns `already_sent`. One stuck in `SENDING` is
   flagged for review and **never automatically resent** — because it may have
   transmitted before the worker crashed.
2. **Campaign state.** A paused campaign sends nothing.
3. **Enrollment state.** `STOPPED` cancels the message outright. `PAUSED` defers.
4. **Suppression, re-checked now.** Not at scheduling time. Now.
   Right after it, **customer win-back** (local business mode, safety guard,
   every channel): a business prospect in a customer (win-back) sequence, or an
   imported customer in a prospect sequence, is cancelled; a customer message
   also needs `customer_winback_enabled` (default **off**), email or WhatsApp,
   a non-healthcare local business (HIPAA), and for email a current opt-in in
   the consent ledger. Cancelled = audited `blocked_winback`, sequence stopped;
   a lookup error defers an hour (fails closed)
   (`app/services/local_business.py::customer_send_block`).
   Then, **F01 Bounce Shield** (flag `bounce_shield_enabled`, default off; a
   safety guard): an email goes only to an address verified valid within
   `bounce_shield_max_age_days`, a catch-all one under an explicit `allow`
   policy, or one an owner/manager overrode for that lead with an audited
   reason. Anything else is held as `FAILED` (`bounce_shield: …`), audited,
   and released back through every gate on override or re-verification. No
   bounce threshold changes (`app/services/bounce_shield.py`).
   Before it, **F10 Champion Tracker** (safety guard, every channel): a
   prospect confirmed to have **left** the company is never messaged at the
   old lead or the old address again — cancelled, audited, sequence stopped,
   and this holds even with the flag off once a departure is recorded. With
   `champion_tracker_enabled` on (default off), a message whose title/company
   was last observed more than `champion_staleness_days` (90) ago is held as
   `FAILED` (`champion_tracker: …`) — no provider call in the send path — and
   a 15-minute sweep re-verifies the role through the existing Apollo
   enrichment (budget-capped, F01 ledger): still in role → released through
   every gate; moved → old address closed and, for a past positive replier or
   customer, a new verified lead at the new company with a "Changed jobs"
   re-engagement plan (never auto-enrolled). Spec:
   `docs/features/f10-champion-tracker.md` (`app/services/champion_tracker.py`).
   After those cancelling gates and before Bounce Shield, **plan entitlement,
   re-checked now** (`app/services/plan_holds.py`): a message on a channel the
   owner's *current* plan lacks, or a sequence step past its
   `max_sequence_steps`, is held as `FAILED` (`plan_limit: …`), audited, never
   sent; the enrollment is not stopped. This is what a downgrade does to
   messages scheduled before it (launch already checked the plan). An owner
   that cannot be resolved defers the message an hour (fails closed).
   Grandfathered: enrollments created before `PLAN_HOLD_EFFECTIVE_AT`
   (2026-10-12 UTC), admin owners, person-approved Reply Copilot answers. A
   15-minute beat sweep (`leadpilot.plan_holds.release`) releases a hold as
   soon as the owner's current plan covers it again. Holds are not counted as
   delivery failures in the campaign's per-channel stats, and the campaign
   overview reports them (`plan_holds`: count + reasons).
5. **WhatsApp rules** (defence in depth — the adapter checks again):
   no opt-in → the message is *skipped* and the sequence continues on its other
   channels; a free-form message whose 24-hour customer-service window closed
   **fails closed** into `needs_template`; a template that lost Meta approval
   since scheduling is refused.
6. **Send window.** Business hours in the *lead's own* timezone, weekends
   skipped, with a configurable fallback zone.
7. **Daily send approval** (safety guard, every channel, flag
   `daily_send_approval_enabled` **on by default** — founder decision
   2026-10-11). An *automatic* send (sequence step, automatic follow-up,
   re-engagement, "not now" return, Reply Copilot auto-send) goes out only on a
   day the account owner or a workspace owner/manager approved automatic
   sending **for that channel** (`daily_send_approval_channels`, default
   email, LinkedIn, WhatsApp, phone). Otherwise it stays `SCHEDULED`, moved to
   the next local midnight with a `daily_send_approval: …` reason — never
   failed, never dropped — and approval releases it back through every gate.
   Messages a person approved one by one (send-review queue, approved Reply
   Copilot answers) are not gated. The LinkedIn network **bulk** send (queued
   Celery jobs through Unipile) is automatic LinkedIn sending and waits for the
   same LinkedIn approval; a single interactive send, "Call now" and the
   meeting follow-up email are a person's action. Only a person decides: a
   personal API key cannot approve (403). A decision is valid until its stored
   `valid_until` instant (a later timezone change cannot stretch it). The first due send of the day with no
   decision notifies the owner once — transactional email (never the outreach
   path), FCM push, and an in-app banner on every page (there is no web push).
   LinkedIn approval is a separate, never-pre-ticked choice with an explicit
   acknowledgement that LinkedIn may restrict or suspend accounts that use
   automation (422 without it); every decision is audited in
   `compliance_audit_log` with approver, time, channels, counts and the warning
   version and text. The day is the account's local day (calendar-availability
   timezone, else UTC). Lookup error fails closed (deferred an hour). Spec:
   `docs/features/daily-send-approval.md` (`app/services/daily_send_approval.py`,
   `/send-approval/*`, page `/send-approval`).
8. **Daily cap and warm-up ramp**, per channel. Over the allowance, the message
   is **deferred to tomorrow's window — never dropped**.
9. **Render, persist, then transmit.** In that order.

### Compliance that is structural, not procedural

- **CAN-SPAM**: every email gets a visible footer carrying the configured
  sender identity and a working unsubscribe link.
- **RFC 8058 one-click**: `List-Unsubscribe` and
  `List-Unsubscribe-Post: List-Unsubscribe=One-Click` headers on every send.
- **Unsubscribe is instant and total**: one click suppresses both email and
  phone, writes the outcome, and hard-stops every enrollment for that lead. A
  reply *phrased* as an unsubscribe is classified and treated identically.
- **Bounce-rate circuit breaker**: above `BOUNCE_RATE_PAUSE_THRESHOLD` (3% by
  default, after a minimum sample so tiny samples cannot trip it) **the whole
  campaign auto-pauses** and the owner gets a push notification. A human has to
  resume it.
- **A stopped enrollment can never send again.** Irreversible, by design.

*Files:* `app/workers/outreach_tasks.py`, `app/services/sequence_engine.py`,
`app/integrations/gmail.py`, `app/integrations/whatsapp.py`

---

## Stage 7 · Replies & routing

Inbound mail is polled per connected Gmail account; WhatsApp arrives by
webhook. Every inbound message gets **one Claude call** to classify it, and the
class decides the routing:

| Class | What happens |
|---|---|
| `interested` / `question` / `objection` / `not_interested` | sequence **stops**, lead → `replied`, reply stored, **owner gets a push notification** |
| `unsubscribe_request` | treated **exactly** like an unsubscribe click: suppression on both identifiers + hard stop, immediately |
| `out_of_office` | sequence **pauses** and reschedules past the return date — it does not stop |
| `bounce` | bounce outcome, lead dropped, campaign bounce-rate check runs |

**A WhatsApp reply stops the whole sequence, including pending cold email
steps** — because `stop_enrollment` stops *enrollments*, not channels. Someone
who replied on one channel is not still cold on another.

**Routing never replies to a human.** By default nothing answers a reply but a
person. **Reply Copilot (F04, flag `reply_copilot_enabled`, off by default)**
drafts a grounded, claim-verified answer to interested / question / objection
replies and queues it in the inbox for approve / edit / discard (LinkedIn:
copy-only); never for opt-outs, suppressed or excluded contacts. An approved
email goes through the ordinary send chokepoint. The only automatic answer is
the opt-in "send me a time" email reply, off at two levels (admin
`reply_copilot_auto_send_allowed` and the account's own opt-in). Each account
also decides whether an answer to a prospect's later reply may still go out
when their earlier reply stopped the sequence (`answer_earlier_replies`, on
by default) (`docs/features/f04-reply-copilot.md`).

### The cross-tenant bug this code is built around

Inbound matching used to be `select(Lead).where(Lead.email == addr)` with no
tenancy filter — first row wins, across the entire database. Two customers
prospecting the same person is routine in B2B, so a reply landing in one
account's inbox flipped the *other* customer's lead to `replied`, stopped their
sequences, wrote a `REPLIED` outcome into their learning loop, and on an
unsubscribe suppressed their lead.

Every lookup is now scoped to the leads owned by the user whose account
received the message. The same class of fix was applied to the Calendly webhook
(see Stage 9) and to WhatsApp opt-in.

*Files:* `app/services/reply_classification.py`,
`app/workers/outreach_tasks.py`, `app/api/webhooks_whatsapp.py`

---

## Stage 8 · Automated follow-up

*(Engagement Hub, Feature 1 — see `docs/features/engagement-hub.md`.)*

A sweep runs every 30 minutes looking for enrollments where a real prospect was
contacted, said nothing, and **the system quietly stopped**.

**The design decision that matters is what it skips.** `schedule_next_step`
already queues step N+1 the moment step N sends. A sweep that also queued one
would send the same prospect the same message twice — the failure nobody sees
in staging and every customer sees in a recipient's inbox. So the sweep
**skips any enrollment that already has a pending message**, and what is left
is the population that was genuinely being dropped:

- a send that failed permanently — the enrollment sat `ACTIVE` with nothing
  queued behind it, forever;
- a WhatsApp step stuck on `needs_template` because the window closed or a
  template lost approval;
- a step deleted or renumbered after its message was scheduled.

**If the sequence has no step with the next number**, the follow-up brief is
generated from the lead's *own* message and outcome history plus the product
brief — then rendered and sent through the ordinary path, so it is
indistinguishable from a step a human wrote. This path is narrow: when the last
step of a sequence sends, the engine marks the enrollment COMPLETED
(`app/services/sequence_engine.py:446-451`), and the sweep only looks at ACTIVE
enrollments (`app/workers/outreach_tasks.py` `check_followup_due_impl`). So it
fires only when a step was deleted or renumbered after the enrollment's last
send, not as an extra touch after a finished sequence (that is the separate,
opt-in re-engagement, `docs/features/reengagement.md`). *(Corrected 2026-10-11,
help-center-discoveries D5.)*

**Idempotency is two layers, and the weaker one fails open.** A Redis lock
(`followup:{enrollment}:{step}`, 2h TTL) stops two workers acting at once; the
message row's `SCHEDULED → SENDING` claim is the actual guarantee. The lock
fails **open** when Redis is down — failing closed would silently stop every
follow-up in the product during an outage with nothing surfacing that it had.

Per step, in the UI: an on/off toggle and a delay in hours (default 72). In the
CRM grid, a per-lead badge — **Follow-up due · Replied · Scheduled** — computed
from the same inputs the sweep uses, so the badge and the automation cannot
disagree about who is overdue.

*Files:* `app/workers/outreach_tasks.py`, migration `0020_followup_delay.py`

---

## Stage 9 · Booking

Two paths, and they behave differently on purpose.

### Path A — Calendly (existing, untouched)

Links are **tenant-tagged**. A Calendly delivery carries no account identity of
its own: one deployment has one webhook subscription and one signing key, and
the payload describes the invitee, not which customer owns the campaign. So the
tenant id is written into the booking link as `utm_content` (chosen because a
user's own `utm_source`/`utm_campaign` may already be in use, and overwriting
their attribution to smuggle an internal id would corrupt their reporting), and
read back off the webhook.

**A booking that comes back without a usable tenant id is quarantined** —
acknowledged so Calendly stops retrying, logged loudly, matched to nothing.
Falling back to a global email lookup is precisely the cross-tenant bug this
replaced. *Never guess a tenant.*

### Path B — Vectorae's own calendar (new)

Availability, booking pages and bookings on our own tables, on our own domain.
The difference is where a booking **lands**: a booking made here already knows
its page, and the page knows its owner, so **ownership is a foreign key rather
than an inference**.

- **Availability**: recurring weekly windows, several per day if you like,
  each storing **wall-clock times plus their IANA zone**. "I take calls at 9am"
  is a fact about your morning; stored as UTC it would drift by an hour twice a
  year and start offering 8am on its own. Conversion happens per offered day,
  so DST resolves against the date being booked.
- **Booking pages**: several per user ("30-min intro", "60-min deep dive") over
  the same availability. Slug, duration (15/30/45/60), buffer, per-day cap,
  custom questions. The slug is the public URL and is globally unique.
- **Public page**: timezone selector → month picker with available dates
  marked → slot list → form → confirmation. No auth, no account, no login wall.
- **Slots are computed, never stored.** A materialised slot table would have to
  regenerate a month of rows on every availability edit, and the same Tuesday
  morning is four 15-minute slots on one page and one 60-minute slot on
  another — there is no single set of slots to store.
- A slot is withheld when it is in the past or inside the minimum-notice
  window, when it overlaps **anything on the host's whole calendar** (every
  live booking on *any* of their pages, plus every non-cancelled meeting — a
  host with two booking pages is still one person), or when the page has hit
  its per-day cap.

### Double-booking is prevented by a constraint, not a query

Two people can load the same page and submit the same 10:00 slot in the same
second. A check-then-insert loses that race in production and passes in tests.

`calendar_bookings.slot_key` holds the slot's UTC start while a booking is
live and is `NULL` once cancelled. `UNIQUE (booking_page_id, slot_key)` blocks
a second live booking and still lets a cancelled slot be re-booked, because
NULLs never collide in a unique constraint. The obvious alternative — a partial
unique index `WHERE status <> 'cancelled'` — is PostgreSQL-only, which would
mean the test suite exercising a constraint production does not have.

**One insert, no read, no race, one behaviour in tests and in production.**

### What a booking triggers

Lead → `meeting_booked` · one `BOOKED` outcome · one CRM timeline entry ·
confirmation emails to both parties in **their own** timezones · a push
notification to the owner · and the sequence **paused**.

Paused, not stopped — see [Stage 11](#stage-11--crm).

*Files:* `app/api/calendar.py`, `app/services/calendar_service.py`,
`app/workers/calendar_tasks.py`, `app/integrations/calendly.py`,
migration `0021_calendar.py`

---

## Stage 10 · The meeting

A **meeting room** inside Vectorae:

```
┌──────────────┬───────────────────────────────┬──────────────────┐
│  Who         │  Live notes                   │  Join            │
│              │                               │                  │
│  Sara Khan   │  ┌─────────────────────────┐  │  [Open in Zoom]  │
│  Acme Fire   │  │ typing…                 │  │                  │
│  Owner       │  │                         │  │  https://…  [⧉]  │
│              │  │ autosaves every 10s     │  │                  │
│  30 min      │  │ and on close            │  │  they can join   │
│  confirmed   │  └─────────────────────────┘  │  from anything   │
├──────────────┴───────────────────────────────┴──────────────────┤
│  ● 12:04   [Start meeting]              [End meeting]           │
└─────────────────────────────────────────────────────────────────┘
                              │ End
                              ▼
              AI summary · key points · action items
              (checkboxes) · next steps · sentiment
```

- **Platform-agnostic.** Google Meet, Zoom, Teams, or *any* URL you paste. The
  join panel is deliberately **not an iframe**: Meet, Zoom and Teams all send
  `X-Frame-Options: DENY`, so an embed renders a blank box — worse than no
  embed, because it looks broken rather than absent. Every platform gets the
  same two things: open in the tool, and the raw link shown in full, for the
  moment on a call when the prospect cannot get in and you need to read it out.
- **Notes autosave** on a 10-second debounce and on unmount, with a visible
  saving/saved state. Somebody types into that box for forty minutes while
  talking; a save on blur alone loses it to a closed tab.
- **Transcripts** arrive via a signed webhook (browser extension or a recording
  provider). **With no secret configured the endpoint answers 503, not 200** —
  an unauthenticated write path into the verbatim contents of a private sales
  call should not appear by default.
- **AI summary** returns `{summary, key_points, action_items, next_steps,
  sentiment}`. The prompt is explicit that it must not invent action items: *if
  nobody committed to anything, return an empty list.*
- **`raw_notes` is never written by the model.** Your notes and the AI's notes
  are separate columns, so a generated summary can never destroy your own
  record of what was said. Regenerating **merges** action items, so a box you
  already ticked stays ticked.
- **Two time pairs, deliberately.** Scheduled start/end, and actual start/end.
  The summary prompt uses the *actual* duration, because "we booked 60 minutes
  and it ran 12" is a signal about the call, and averaging it away erases it.
- **A stale-meeting sweep** runs hourly. A meeting nobody pressed End on stays
  `in_progress` forever — blocking calendar slots and keeping its lead's
  enrollment paused. One that was *started* closes as completed; one that was
  never started becomes a **no-show** and releases its slot, because recording
  it as completed would put a call that did not happen into the conversion
  numbers.

*Files:* `app/api/meetings.py`, `app/services/meeting_ai.py`,
`app/integrations/google_meet.py`, `app/integrations/zoom.py`,
migration `0022_meetings.py`

---

## Stage 11 · CRM

Not a link out to HubSpot. A CRM over the same rows the pipeline writes.

- **Data grid** — spreadsheet-shaped, built from scratch. **No spreadsheet
  library, no CSV round-trip.** The grid *is* the editing surface; CSV leaves
  as a download and never comes back, because an export/edit/import loop means
  every edit is stale the moment it is made and two people working the same
  list silently overwrite each other.
  - server-side paging, sorting and filtering (the browser never holds 5,000
    rows), virtualised rendering (~30 DOM rows per page of 200), fixed row
    height so virtualisation is arithmetic rather than measurement;
  - a real `role="grid"` with `aria-rowindex`/`aria-colindex` on virtualised
    cells — a screen reader has no other way to know the DOM is a window onto
    a larger set;
  - full keyboard navigation, inline editing with optimistic updates and
    rollback, multi-column shift-click sort, column resize/reorder/hide, bulk
    actions across up to 500 leads in **one** request.
- **Four dashboards** — pipeline funnel and conversion, lead velocity and
  source quality, campaign performance by channel, and an account-wide activity
  feed.
- **Notes, tags, saved views, custom fields**, and now a **Meetings tab** per
  lead with the summary and action items inline.
- **Real-time** — Server-Sent Events over Redis pub/sub, one channel per user.

### Three schema decisions worth understanding

**1. `crm_activities` is not `outcomes`.** `outcomes` is the learning loop's
immutable event log; the nightly aggregation, the A/B sweep and the playbook
scorer all compute *rates from its row counts*. Writing "note added" and "tag
removed" into it would change those denominators silently, in a table three
milestones of code already depend on. A reply legitimately writes one row to
each.

**2. The CRM added no columns to `leads`.** Two shortcuts were available — an
`owner_user_id` and a `custom_fields_json` on `leads` — and both were rejected.
The first is a column only the CRM UI writes, sitting in the row that the
sourcing chain, the sequence engine, the learning loop and the published SDK
all load. The second cannot be sorted or filtered server-side by a query
spelled the same way in SQLite and PostgreSQL, which would mean the test suite
exercising a different query shape than production.

**3. Stage history starts empty, and says so.** `stage_entered_at` is *not*
backfilled, because the information does not exist: `leads.updated_at` moves on
any write at all. Copying it in would manufacture a stage-entry time that is
simply wrong for an unknowable share of rows, and the velocity dashboard would
then report a precise-looking average built on it. Leads keep `NULL` until
their next status change, and **the dashboard labels the figure estimated.**

### Pause, not stop

A booking made on Vectorae's own calendar **pauses** the enrollment. The M3
rule is that a booked meeting is a hard stop — correct for Calendly, where
nothing reliably tells us the meeting fell through. But we *own* this row: we
know the moment it is cancelled, because the cancellation is a request to this
API. So the honest state is paused, and a cancelled meeting puts the lead back
into the sequence where it left off instead of stranding a warm lead in a state
the engine defines as irreversible.

The resume only undoes pauses *this* feature made — a cancelled meeting cannot
un-pause an out-of-office hold and send into an inbox the engine has been told
is unattended.

*Files:* `app/api/crm.py`, `app/services/crm_service.py`,
`app/services/crm_events.py`, `frontend/src/components/crm/`,
migration `0019_m9_crm.py`

---

## Stage 12 · The learning loop

Every outcome is a row. Every night, those rows become the next strategy's
starting point.

| Component | What it does |
|---|---|
| **Nightly aggregation** | rolls `outcomes` into `playbook_scores`, keyed on `(pattern_key, variant)` |
| **Playbook injection** | high-scoring patterns are injected into **Phase 6 (messaging)** and **Phase 8 (execution)** of every new strategy — so run #100 starts from what actually worked, not from zero |
| **A/B testing** | two-proportion z-test, **stdlib only** (`math.erfc`) — no scipy |
| **Multi-variate** | Kruskal-Wallis H-test → pairwise Mann-Whitney U with **Bonferroni correction**, for A/B/C/D. Non-parametric, because conversion data with small per-group samples has no business assuming normality |
| **Score decay** | exponential time decay, `λ = ln2 / half_life`. Crucially it computes an **effective sample size** (`Σ weights`, not a raw count) — *a pattern with 100 old outcomes may have `effective_n` below the minimum* |
| **Subject intelligence** | eight structural patterns (question, number, curiosity gap, social proof, direct offer, length bands…) extracted from winning subject lines. A/B tells you **which** won; this tells you **why** |
| **Send-time optimiser** | per-slot reply rates by channel and ICP, cached 25h, fed back into the scheduler |
| **Personalisation scorer** | measures how much available enrichment each message actually used, then computes the **Pearson correlation** with replies. Above 0.3, a prompt injection is added to Phase 6 |
| **Strategy similarity** | TF-IDF cosine similarity, pure Python, **user-scoped** so cross-tenant search is impossible |

### Auto-promotion has four gates, and all four must pass

1. **Sample size** — each variant has at least `PLAYBOOK_MIN_SAMPLE` sends
2. **Significance** — two-sided p below `AB_SIGNIFICANCE_THRESHOLD`
3. **Minimum lift** — relative lift at least `AB_MIN_LIFT`
4. **Harm guard** — `AB_HARM_CEILING`

A "winner" that clears significance on eleven sends is noise, and promoting it
teaches the playbook something false — permanently, because the playbook feeds
every future strategy.

**On a fresh install this behaves identically to run #1000.** With no outcomes
yet, `PlaybookService.get_insights` returns an explicit "no playbook data yet"
block. Graceful empty handling is a hard requirement, not an afterthought.

*Files:* `app/services/playbook_service.py`, `ab_testing.py`,
`multi_variate.py`, `score_decay.py`, `subject_intelligence.py`,
`send_time_optimizer.py`, `personalization_scorer.py`, `similarity.py`,
`app/workers/learning_tasks.py`

---

# Part 2 — Every feature, by area

<details open>
<summary><strong>Strategy & research</strong></summary>

- Two intake flows (with / without past clients)
- Seven-pattern extraction from a free-text client story
- 72-step strategy pipeline · 144-step for zero-history products
- Steps declared as data; engine untouched when steps change
- Per-step resume, guaranteed unique by database constraint
- 10-pass verification with automated fix-and-retry
- `needs_human_review` terminal state — no silent shipping
- Truncation detection on every model call
- Full strategy document, viewable and exportable
- Similar-strategy search (TF-IDF, user-scoped)
- Quickstart (F06, flag `quickstart_enabled`, off by default): website URL +
  one-line offer → ICP → up to 25 verified leads and **review-only** first
  drafts while the full pipeline runs; nothing is sent from it
  (`docs/features/f06-quickstart.md`)
</details>

<details open>
<summary><strong>Leads</strong></summary>

- Apollo sourcing from ICP criteria extracted by the pipeline
- Per-lead enrichment
- Hunter email finding and verification
- Deliverability triage: verified / flagged / dropped
- Database-enforced dedupe on two keys
- Global + per-user suppression list
- GDPR lead erasure: personal fields, the messages written to the person and
  their replies erased (counts' metadata kept), identifiers suppressed, proof
  kept in the consent ledger
- Kanban pipeline board with validated status transitions
- Manual lead CRUD; batch resume from any stage
- Local business mode (flag `local_business_mode_enabled`, on by default):
  a product can be a restaurant/café/caterer, clinic/hospital/healthcare
  practice, retail/local shop, salon/fitness/local service or other local
  business, and a strategy can carry a service area (city or postcode,
  country, 0.5-50 mi / 1-80 km) and an offer. Research steps, ICP extraction
  and outreach prompts get the local B2B plays and hard limits (business
  buyers only, no invented distances, no patient data); lead search uses the
  service area as the location (the radius is not sent to Apollo).
  `strategy.business_type` and `send_time_profile()` expose recommended local
  send windows for a scheduler. Customer win-back (flag
  `customer_winback_enabled`, off by default): a local business's OWN
  customers imported from CSV with a recorded opt-in per channel (email /
  WhatsApp only; no SMS or calls; rows without consent refused; healthcare
  refused outright), sent owner-written copy through every send gate
  (`docs/features/local-business-mode.md`)
</details>

<details open>
<summary><strong>Outreach</strong></summary>

- Multi-channel sequences (Gmail, WhatsApp) with per-step channel override
- Per-lead AI-generated copy from a step brief plus the messaging playbook
- A/B and multi-variate variants per step
- Gmail OAuth with encrypted, auto-refreshing tokens
- WhatsApp Business Cloud: template lifecycle (draft → submitted → approved),
  Meta status sync, 24-hour window tracking, opt-in capture with evidence
- Business-hours send window in the **lead's** timezone
- Per-channel daily caps with warm-up ramps; over-cap sends deferred, never lost
- Bounce-rate auto-pause with owner notification
- CAN-SPAM footer + RFC 8058 one-click unsubscribe on every email
- Reply classification and routing; **no auto-reply to humans by default**
- Reply Copilot (F04, flag `reply_copilot_enabled`, off by default): grounded,
  claim-verified answer drafts queued in the inbox (approve / edit / discard;
  LinkedIn copy-only), sent only through the send chokepoint; opt-in
  "send me a time" auto-send, off at two levels; per-account
  `answer_earlier_replies` (on by default; off = an answer passes only the
  stop its own reply caused)
- Automated follow-up with per-step delay and enable/disable
- Opt-in post-sequence re-engagement: one email per completed, neutral
  enrollment, per-campaign toggle (off by default), own daily/weekly caps under
  admin ceilings, re-gated at send time
- Configurable compliance rules: send window, weekend skipping, daily cap,
  consent requirement and bounce-pause threshold per workspace, recipient
  region and channel. Resolved at send time, and fail-closed: an empty table is
  the old baseline, and an invalid or unreadable rule only ever tightens
- Voice twin (flag `voice_clone_calls_enabled`, off by default; needs AI
  calling on): the account owner's OWN voice as an ElevenLabs Professional
  Voice Clone (written consent first, recordings never stored, revoke deletes
  it at ElevenLabs) for AI calls, AI answering of inbound calls (book, take a
  message, transfer), and live Take over. Every call opens "this is
  <owner>'s AI assistant, using <owner>'s voice. This call is recorded.";
  concealment is refused on save and a post-call check pauses the account if
  the assistant ever claims to be human (`docs/features/voice-twin.md`)
</details>

<details open>
<summary><strong>Calendar & meetings</strong></summary>

- Own weekly availability, wall-clock times with IANA zones
- Multiple public booking pages per user, own domain, no login for the visitor
- Custom booking questions, per-day caps, buffers
- Computed slots; constraint-enforced no double-booking
- Custom-built week grid and month picker (**no calendar library**)
- Booking drawer, cancellation with email, no-show handling
- Meeting room: live notes, timer, platform launcher
- Google Meet / Zoom / Teams / any URL
- Transcript ingestion (HMAC-signed; closed by default)
- Recall.ai recording bots: transcript pulled in on `transcript.done` and the
  summary upgraded from notes to transcript + notes. The summary never waits
  for it: a missing transcript times out and leaves the notes-only summary
- AI summary: summary, key points, action items, next steps, sentiment
- Searchable transcript viewer with TXT and PDF export
- Action-item checkboxes that survive regeneration
- Calendly integration retained and tenant-safe
</details>

<details open>
<summary><strong>CRM</strong></summary>

- Data grid with server-side paging/sort/filter and row virtualisation
- Inline editing, optimistic with rollback; bulk edit up to 500 rows
- Notes, tags, saved views, user-defined custom fields
- Per-lead activity timeline; per-lead meetings tab
- Four dashboards (pipeline, leads, campaigns, activity)
- Real-time SSE stream with a polling fallback and a kill switch
- `followup_status` badge per lead
- Lead assignment to workspace members: per-lead picker, bulk assign, and
  least-loaded round-robin across SDRs (idempotent — only unassigned leads move)
- CSV export (one-way, by design)
</details>

<details open>
<summary><strong>Learning</strong></summary>

- Nightly outcome aggregation into playbook scores
- Playbook injection into Phases 6 and 8 of every new strategy
- A/B (z-test) and multi-variate (Kruskal-Wallis + Bonferroni), stdlib only
- Four-gate auto-promotion with a harm guard
- Exponential score decay with effective sample size
- Subject-line pattern extraction
- Send-time optimisation
- Personalisation-depth scoring with reply correlation
- Anonymised benchmarks: nightly per-account reply / meeting / bounce rate
  percentiles by industry and channel, published only above a minimum account
  count, shown next to the user's own numbers
</details>

<details open>
<summary><strong>Platform</strong></summary>

- JWT auth with refresh; email verification with a kill switch
- Plan tiers (free / starter / pro / enterprise) with limits and feature gates
- Billing (Stripe): monthly subscriptions, pay per meeting, and an **annual
  subscription** (flag `annual_billing_enabled`, off by default; monthly price
  x `ANNUAL_MONTHS_CHARGED` = 10, same limits) with a pre-checkout auto-renewal
  disclosure + consent, acknowledgment / trial-ending / 30-day renewal emails
  to the owner, an online **Cancel plan** button in Profile, and trials that
  never end before an active pilot's verdict (`docs/features/annual-billing.md`)
- Spend Guard (F07, flag `spend_guard_enabled`, off by default): a cost
  forecast before every strategy, lead-sourcing and Quickstart run, an
  account-set monthly cap (on top of the plan's daily AI limit) enforced
  before each run and before every paid AI or data call, 50/80/100% alerts,
  and a usage page with forecast vs actual (`docs/features/f07-spend-guard.md`)
- Per-endpoint rate limiting, applied **inside** handlers so a malformed
  payload cannot burn a slot
- Encrypted-at-rest OAuth tokens and API keys (Fernet, with key rotation)
- GDPR for account holders: full data export (Profile › Download your data)
  and self-serve account deletion (`DELETE /me`, password + typed DELETE):
  plan cancelled at once, every owned row erased per `account_export.OWNERSHIP`,
  billing kept anonymised 7 years, consent/suppression kept as opt-out proof
  (`docs/features/gdpr-erasure.md`; retention defaults await founder/legal
  confirmation)
- Circuit breakers per provider, shared across workers, with an admin reset
- Structured JSON logging with request IDs end to end
- `/health`, `/health/channels`, `/health/learning-loop`
- Admin: users, plans, suspension, suppression, task errors, circuit breakers,
  webhook deliveries, tutorials, support tickets
- Outbound webhooks with retry
- Push notifications (FCM) for replies, bookings and campaign pauses
- In-app AI support chat, grounded in a curated FAQ, with a ticket fallback
- Tutorial catalogue with progress tracking
- Theme engine — every colour a CSS variable, user-configurable
- Offline banner, pull-to-refresh, safe-area handling, deep links (mobile)
- 30-Day Pilot Mode (F09, flag `pilot_mode_enabled`, off by default): success
  criteria locked on day 1, weekly scorecard email to the owner, day-30 verdict
  from the meetings-per-dollar ledger, verdict PDF/CSV + full account export;
  never changes a plan or billing, and a plan bought during a pilot is not
  charged before the verdict (`docs/features/f09-pilot-mode.md`)
</details>

**Totals:** 166 API routes · 24 migrations · ~30,000 lines of backend Python
across 124 modules · 147 frontend TypeScript files · 50 backend test modules.

---

# Part 3 — How it is built

```
┌───────────────────────────────────────────────────────────────────┐
│  CLIENTS — three windows, one brain                               │
│  Next.js 14 (static export)  ·  Capacitor Android  ·  pip CLI     │
└──────────────────────────────┬────────────────────────────────────┘
                               │ HTTPS / REST + SSE
┌──────────────────────────────▼────────────────────────────────────┐
│  FastAPI  ·  166 routes                                           │
│  RequestIDMiddleware → CORS → auth → rate limit → handler         │
└─────┬──────────────────────────────────────────┬──────────────────┘
      │ SQLAlchemy 2.x                            │ Redis
┌─────▼──────────────┐         ┌──────────────────▼─────────────────┐
│  PostgreSQL        │         │  Celery                            │
│  All state.        │         │  ├ pipeline   72/144-step research │
│  UUID PKs.         │         │  ├ outreach   dispatch · send ·    │
│  VARCHAR enums.    │         │  │             replies · follow-up │
│  24 migrations,    │         │  ├ learning   nightly aggregation  │
│  advisory-locked.  │         │  └ default    calendar · webhooks  │
└────────────────────┘         │  Beat: 8 scheduled jobs            │
                               └────────────────────────────────────┘
```

**Every enum is `VARCHAR`, not a native Postgres enum.** Adding a value later
is a data-free migration, and SQLite behaves identically — which is what lets
the 1,000-test suite run in-process, in seconds, with no containers.

**Migrations are serialised by a Postgres advisory lock.** Measured with four
migrators started together against a fresh database: *without* the lock, 3 of 4
exited non-zero with an `IntegrityError` on the `alembic_version` insert — a
container crash-loop on deploy. *With* it, all four exited 0 and the schema
landed exactly once.

**Queue routing is a tested invariant.** `tests/test_celery_routing.py` asserts
that every registered task routes to a queue some worker in
`docker-compose*.yml` and `railway/*.json` actually consumes, and that no
worker listens to a queue nothing publishes to. This exists because it once
caught a **total production outage**: with no `task_routes`, every task went to
Celery's implicit `celery` queue while the workers were bound to
`-Q pipeline / outreach / learning,default`. Nothing consumed `celery`, nothing
was published to the four declared queues, and the entire async backend would
have silently done nothing in production. It passed in dev only because the dev
worker is started without `-Q`.

### The test suite

**1,066 passing, 5 skipped** in the in-process SQLite suite (plus a separate
integration suite under `tests/integration/` that needs real PostgreSQL and
Redis), and **276 frontend tests**.

A meaningful share of them are **architecture tripwires** rather than unit
tests — they exist because a specific class of bug already shipped once:

| Test file | The bug it prevents recurring |
|---|---|
| `test_celery_routing.py` | tasks published to a queue nobody consumes |
| `test_config_paths_match_routes.py` | config URLs pointing at routes that do not exist (`GMAIL_REDIRECT_URI` was `/api/v1/...` when **0 of 78 routes** used that prefix — Google would have redirected every user to a 404) |
| `test_migration_lock.py` | migrations racing on multi-replica deploys |
| `test_crm_migration.py`, `test_engagement_migrations.py` | a column added to a model and forgotten in the migration — invisible to every other test, fatal in production |
| `test_rate_limit_ordering.py` | a 422 burning a rate-limit slot |
| `test_compliance.py` | any bypass of the send-time guards |
| `test_production_guard.py` | booting production with an unsafe secret |

**The migration parity tests deserve a note.** The suite builds its database
with `create_all()`; production builds it by running migrations. Those are
disjoint code paths, so a column added to a model but missed in the migration
passes every test in the repository while being absent from the deployed
database — surfacing as a "no such column" on a query nobody changed. The
parity tests run the migration against a real database and diff the result
against the models, column by column, index by index, constraint by constraint.

---

# Part 4 — Why this is built differently

> **A note on what follows.** These are architectural comparisons grounded in
> what *this* codebase does — every claim on the left is traceable to a file
> named earlier in this document. They are **not** benchmark results, and they
> are not claims about any named competitor's internals. Where a comparison is
> made, it is against the *shape* of the common alternative: a stack of point
> tools wired together, which is what most teams actually run.

## 4.1 · The structural argument: one system, not five

The normal way to do this is a stack:

```
  sending tool  →  data provider  →  scheduler  →  CRM  →  notetaker
      │                 │               │           │          │
      └── Zapier ───────┴───────────────┴───────────┴──────────┘
                    (and a spreadsheet, and hope)
```

Every arrow in that diagram is a place where state goes stale, identity gets
lost, or an event silently fails to arrive. Concretely, in a stack:

- The scheduler does not know the person booking is a lead in the CRM. It knows
  an email address, and something downstream has to guess.
- The sending tool does not know a meeting was booked, so it keeps sending —
  the single most embarrassing failure in outbound.
- The CRM does not know why a message worked, so nothing gets better.
- The notetaker writes a summary into a fourth product that the sequence engine
  will never read.

**In Vectorae these are foreign keys.** A booking knows its page; the page
knows its owner; the booking knows its lead; the lead knows its strategy; the
strategy knows its outcomes; the outcomes feed the next strategy's prompt.
There is no integration layer to fail, because there is no gap to integrate
across.

That is not a marketing claim — it is why Stage 9 can pause a sequence, and why
Stage 12 can make Stage 2 better.

## 4.2 · Twelve decisions that are unusual, and why

| # | Decision | The alternative, and why it loses |
|---|---|---|
| 1 | **Compliance is re-checked at send time, with no bypass flag** | Checking at *scheduling* time is cheaper and is what a queue naturally does. But a lead who unsubscribes on Tuesday still gets Wednesday's message, because it was already approved. There is no flag, anywhere, that skips these checks — not for testing, not for admins. |
| 2 | **A strategy cannot ship until 10 passes are green** | Generating a strategy and showing it to the user is one model call. Verifying it is eleven or more. The pass that earns the cost is #4: a plan that ships without CAN-SPAM identity or a GDPR basis is a legal problem the user did not know they had. |
| 3 | **Steps carry briefs, not copy** | Storing final copy is simpler and faster. But it freezes the message at authoring time, outside the compliance gates and outside today's playbook. Rendering at send time is what lets Stage 12 improve Stage 6 without anyone rewriting a sequence. |
| 4 | **Double-booking prevented by a UNIQUE constraint on a nullable key** | A check-then-insert reads correct and loses the race. A partial unique index is PostgreSQL-only, so tests would exercise a constraint production does not have. The `slot_key`-goes-NULL trick behaves identically on both. |
| 5 | **Availability stores wall-clock time + IANA zone** | UTC is the reflex answer and is wrong here: "9am" is a fact about the user's morning, and stored as UTC it silently becomes 8am in November. There is a test pinning this across the October 2026 DST change. |
| 6 | **Auto-promotion needs four gates including a harm guard** | Promoting on significance alone is standard and is how a playbook learns something false from eleven sends — permanently, because it feeds every future strategy. |
| 7 | **Score decay uses effective sample size, not row count** | Counting rows lets a pattern with 100 outcomes from last year outvote 12 from last week. `Σ weights` makes "how much evidence do we actually have *now*" the number that gates promotion. |
| 8 | **CRM activity is a separate table from outcomes** | One table is obviously simpler. But the learning loop computes *rates from row counts*, so "note added" rows would move the denominators of every metric, silently, in a table three milestones already depend on. |
| 9 | **The follow-up sweep skips enrollments the engine already owns** | The naive sweep finds every overdue lead — including the ones with a message already queued — and sends twice. The guard is the entire design; what is left is the genuinely stalled population. |
| 10 | **The follow-up lock fails OPEN when Redis is down** | Failing closed is the safe-looking default and it silently stops every follow-up in the product during an outage, with nothing surfacing that it has. The message row's state claim is the real guarantee; the lock is an optimisation. |
| 11 | **Transcript ingestion is closed (503) without a secret** | Shipping it open "for now" would put an unauthenticated write path into the verbatim contents of private sales calls, on by default, on day one. |
| 12 | **Stage history is not backfilled, and the dashboard says "estimated"** | Copying `updated_at` in would produce a full column and a precise-looking average built on a value that is wrong for an unknowable share of rows. A gap you can see beats a number you cannot trust. |

## 4.3 · Built from scratch where it counts

| Built here | Why not a library |
|---|---|
| **Week grid + month picker** | FullCalendar and react-big-calendar bring a rendering model, a theme system and a data shape you then fight. The geometry is ~200 lines of `Date` arithmetic in `src/lib/calendar/grid.ts`, unit-tested against the awkward cases (a week crossing a month, a 31st that must not skip April, a booking starting before the first rendered hour). |
| **Data grid** | The grid *is* the editing surface. A spreadsheet library or a CSV round-trip means every edit is stale the moment it is made. |
| **A/B + multi-variate statistics** | scipy is ~30 MB of wheel to compute an erfc and a rank-sum. Both are implemented directly against the stdlib. |
| **TF-IDF similarity** | scikit-learn for a cosine similarity over a few hundred documents, in a user-scoped query. `math` + `collections`. |
| **Subject-line pattern extraction** | Eight regex/heuristic patterns beat a dependency on an NLP stack for a job that is genuinely eight regexes. |
| **Modal, badges, buttons, the whole UI kit** | Every colour is a CSS variable so the theme engine can recolour the entire app. A component library that ships its own hex values is the one element ignoring the user's chosen preset. |

**No FullCalendar. No react-big-calendar. No date-fns. No scipy. No
scikit-learn. No spreadsheet library.** What the frontend actually imports:
React, Next, Tailwind, TanStack Query, Recharts (charts), lucide (icons),
`sonner` (admin toasts) and the Capacitor plugins. 15 `@radix-ui` packages
sit in `package.json` and are imported **nowhere in `src/`** — every primitive
on screen, modal and grid and calendar and badges and buttons, is in this
repository, so that the theme engine can recolour all of it from CSS variables.
*(Those unused declarations are dead weight in the lockfile and worth
pruning.)*

## 4.4 · Multi-tenancy taken seriously, because it was once wrong

Three separate cross-tenant bugs were found and fixed in this codebase, all the
same shape — a lookup by email with no owner filter:

1. **Inbound replies** — a reply in one customer's inbox flipping another
   customer's lead, stopping their sequences and suppressing their contact.
2. **Calendly bookings** — a booking landing on the wrong tenant's lead.
3. **WhatsApp opt-in** — the same, on phone numbers.

Two customers prospecting the same person is *routine* in B2B, so this is not a
rare edge. The fixes are structural, not spot patches: ownership is expressed
once, in a base selectable (`owned_leads_select`, `owned_leads_subquery`), so
it cannot be forgotten at a call site; every "not yours" answers **404, never
403**, so existence cannot be probed by status code; and the Calendly path
**quarantines** a booking it cannot attribute rather than guessing.

`tests/integration/test_security.py` asserts cross-tenant isolation across five
resource types.

## 4.5 · Failure modes are chosen, not inherited

Every degradation path in this system is a decision someone wrote down:

- **Redis down** → the follow-up lock fails open, the CRM live feed drops to
  polling, the rate limiter fails open. *None of these stop the product.*
- **Anthropic down** → the follow-up brief falls back to a generic one and
  still sends; the meeting summary returns empty with an error rather than
  leaving the meeting stuck `in_progress`; the support chat serves the curated
  FAQ instead of showing an error.
- **A mail relay down** → signup still creates the account (a resend button is
  better than a 500); a booking is still made and still shows on the calendar.
- **Google or Zoom down** → the meeting is still created, with `platform_error`
  in the response, so there is somewhere to attach notes for the call that
  happens anyway on a link pasted into Slack.
- **A worker killed mid-send** → the message is flagged, never auto-resent,
  because it may have transmitted before the crash.
- **A provider failing repeatedly** → its circuit breaker opens for every
  worker at once, and calls fail fast instead of piling up.

The rule visible throughout: **the feature is allowed to degrade; the write is
not.**

## 4.6 · The code explains itself

This is unusual enough to state plainly. Comments in this codebase do not say
*what* the line does — they say **why it is that line and not the obvious
alternative**, and where a bug is the reason, they say when it happened and
what it cost:

> *"7 of 8 fixer calls in the 2026-08-19 run returned exactly out_tokens=8000,
> and the loop wrote each truncated result straight over strategy_document. The
> document still ends mid-sentence at `**Pricing range:** $3,000-`."*

> *"Measured 2026-08-20 with four migrators started together against a fresh
> database: WITHOUT the advisory lock 3 of 4 exited non-zero…"*

> *"Verified live on 2026-08-20 — two 422s followed by two 429s, with zero
> strategies created."*

That is what makes the next engineer able to change this safely, and it is why
the tripwire tests in Part 3 exist as tests rather than as folklore.

---

# Part 5 — What it does NOT do

Stated plainly, because a feature list that only lists strengths is a sales
page, not a document.

**Deliberate product limits**

- **No auto-replying to humans by default.** Replies are classified and
  routed; a person writes or approves the answer. The single exception is
  Reply Copilot's opt-in "send me a time" email auto-send (F04), off at two
  levels.
- **LinkedIn is a sequence channel, not yet proven live.** LinkedIn steps send
  through Unipile (`app/integrations/linkedin_channel.py`,
  `docs/features/linkedin-channel.md`) on the Growth plan and above
  (`app/core/plans.py` `channels`), behind per-account daily ceilings. The
  Unipile client is still marked `# TODO: verify` against the live service
  (`app/integrations/unipile_linkedin.py`). *(Corrected 2026-10-11: this line
  used to say LinkedIn "is not implemented"; help-center-discoveries D5.)*
- **Data ownership is still one account per workspace.** Workspaces
  (`docs/features/workspaces.md`) let members act on the owner's records with
  roles, and leads can now be **assigned** to members (see below), but leads,
  strategies and campaigns were deliberately NOT migrated to an
  `organization_id`. Every tenant-scoped query still scopes by the owner's
  `user_id`, which is the property the cross-tenant fixes in 4.4 rely on.
- **Cold WhatsApp requires an approved Meta template and a recorded opt-in.**
  There is no free-form cold path, by design.
- **Completed sequences are re-engaged only on request, once, and capped.**
  Opt-in re-engagement (`docs/features/reengagement.md`) is off per campaign
  until an owner or manager turns it on. It has its own daily and weekly caps
  under admin ceilings, reaches only leads whose last outcome was neutral, sends
  at most one email per enrollment (enforced by a UNIQUE constraint), and runs
  every send through the same Stage 6 gauntlet. Nothing sweeps completed
  sequences otherwise.
- **CSV export is one-way.** See Stage 11.

**Known gaps, with the reason**

- **Compliance rules govern the send moment, not every surface.**
  (`docs/features/compliance-rules.md`)
  - **Scheduling:** it still places messages with the baseline window, and the
    send-time check reschedules anything a rule forbids. The smart-send-time
    heatmap's "schedulable" shading is the same baseline.
  - **Caps:** rule caps apply to Gmail and WhatsApp. LinkedIn and phone keep
    their own per-account limits in System Settings.
  - **Dashboards:** the campaign overview and the CRM campaigns dashboard still
    display the baseline bounce threshold, not a workspace's lower one. The
    pause itself uses the rule.
  - **Consent:** a consent rule on email or LinkedIn stops those sends, because
    Vectorae records no consent for either channel.
  - **Region:** it is only as good as the lead's enrichment country or timezone.
    Leads with neither fall under `unknown`, which only `*` or `unknown` rules
    match.
- **Re-engagement is email-only, and the conversion gate will hold many of its
  leads.** WhatsApp needs an approved template no step names. LinkedIn's `auto`
  action would send a second connection request. An unprompted AI call is a
  TCPA question. Every re-engagement send also passes the live
  conversion-probability gate. A lead who finished a sequence with
  `conversion_min_unanswered_sends` or more unanswered touches is often judged
  cold, and the send is held (`held_cooling` / `held_archived`), not sent. That
  is the gate working, not a bypass to add. The "would qualify" count in the
  panel is computed before that gate runs, so it is an upper bound. A message
  deferred by the cap and later sent by the dispatcher leaves its attempt row
  at `held` with the first result. The message row is the record of what
  actually went out.
- **Lead assignment is who works a lead, not who sends.** Outreach still goes
  out from the workspace owner's connected Gmail / LinkedIn accounts and voice
  profile. An assignee gets no notification when a lead is handed to them, the
  grid cannot yet filter by assignee, and a member removed from the workspace
  keeps their assignments (shown as "Former member") until a manager
  reassigns them. Round-robin in the UI works on the grid selection; the
  whole-campaign form (`strategy_id`) is API-only and capped at 500 leads per
  call, with `more_remaining` in the response.

- **OPEN — Google Meet and Zoom are still NOT verified against the live APIs.**
  No credentials exist yet. Built without them (Feature 7):
  - **Adapters checked against current docs (2026-09-13):**
    - Google: `conferenceDataVersion=1`, the `hangoutsMeet` type, the Meet link
      read from `hangoutLink` or `conferenceData.entryPoints`, and one re-read
      when conference creation is `pending`.
    - Zoom: the server-to-server token request.
  - **Fixed:** the Google health check used `calendarList`, which needs a scope
    the app never requests, so it reported every connected account unhealthy.
  - **Cleanup:** both adapters can delete what they create. The shared plumbing
    now accepts an empty 204 body.
  - **Zoom credentials:** move to Admin › Integrations, with the old `ZOOM_*`
    environment variables as fallback. The host is configurable (`host_user`),
    because Zoom's docs do not say whether `me` works for a server-to-server
    app.
  - **Reconnect flow:** the stored Google grant is checked before any call. An
    account without `calendar.events` gets `platform_action: reconnect_google`
    on meeting create and a "Reconnect for Calendar" prompt in Settings. The
    auth URL now sends `include_granted_scopes=true`, so the reconnect keeps
    Gmail access.

  Still unverified, pending credentials:
  - The Zoom create-meeting path, body fields and `users/me` for a
    server-to-server app (`# TODO: verify`).
  - That both providers accept these requests at all.

  To close this item: `scripts/verify_meeting_platforms.py --google
  --user-email … --execute` and `--zoom --execute`, or
  `tests/test_meeting_platforms_live.py` with the `LEADPILOT_LIVE_*` variables.
  Until one of them passes, `platform=custom` is the only verified path. A
  platform failure still returns a created meeting, not an error.
- **The `calendar.events` Google scope is newer than some connections.**
  Accounts connected before it shipped are told to reconnect: in Settings, and
  when they choose Meet. An account whose grant was stored without any scopes
  (`calendar_access: unknown`) is not blocked; Google decides, and a 403 maps
  to the same reconnect error.
- **Recall.ai transcripts are built and tested with mocks, never called live.**
  A host can send a Recall.ai bot to a call. Its `transcript.done` webhook
  (Svix-signed, de-duplicated, matched to the meeting only by the stored bot
  id) pulls the transcript in and upgrades the summary
  (`docs/features/meeting-transcripts.md`). No Recall credentials exist in any
  environment. The signing scheme, event payload and transcript format were
  checked against docs.recall.ai on 2026-09-13; the auth header and endpoint
  paths carry `# TODO: verify`, because Recall's quickstart and API reference
  disagree on `Token` vs `Bearer`. The docs name no webhook replay tolerance,
  so five minutes (the Svix default) is a choice, not a documented value.
- **Vectorae does not collect consent to record.** Sending a bot records
  everyone on the call. Some US states, and GDPR for EU/UK participants,
  require every party's consent. The meeting room tells the host to announce
  it; nothing enforces that.
- **`/book/:slug` needs a hosting rewrite.** `frontend/vercel.json` ships it
  for the documented target; on another host, links use `/book/?slug=…` until
  an equivalent rewrite exists. This is a consequence of `output: 'export'`,
  which is what makes the Android build possible.
- **The mobile tab bar scrolls** now that it carries nine destinations. Worth
  revisiting if a tenth is added.
- **Zoom meetings are hosted by one configured account** (server-to-server
  OAuth). Correct while the user *is* the host; wrong for a future team plan,
  and `app/integrations/zoom.py` is the module that would change.
- **No public benchmark numbers.** Reply rates, booking rates and deliverability
  depend on the sender, the list and the offer. This document makes
  architectural claims, not performance claims. The in-app benchmark panel
  (`docs/features/benchmarks.md`) is not an exception. It shows the spread
  across *this deployment's* accounts, labelled as exactly that, and publishes
  nothing for a bucket with fewer than 10 accounts (never fewer than 5, whatever
  the setting says).
- **Benchmark anonymity has a limit.** Only rounded percentiles across accounts
  are stored, and an account needs 30 sends to count. But in a small bucket a
  percentile is, by construction, one account's rounded rate, and an operator
  who controls several accounts could stack a bucket. Industry comes from the
  strategy's ICP: a strategy targeting several industries lands in `multiple`,
  not in each of them.

---

## Where to go next

| You want | Read |
|---|---|
| Run it locally | `README.md` |
| The newest subsystem in depth | `docs/features/engagement-hub.md` |
| Deploy it | `DEPLOY.md`, `DEPLOY_RENDER_FREE.md`, `DEPLOY_RAILWAY.md` |
| Compliance detail | `docs/compliance.md`, `SECURITY.md` |
| The learning loop in depth | `docs/learning-loop.md`, `docs/learning-loop-advanced.md` |
| API reference | `docs/api-reference.md`, or `/docs` on a running server |
| Architecture | `docs/architecture.md` |
| Production checklist | `docs/PRODUCTION_READINESS.md`, `LAUNCH_CHECKLIST.md` |
| Other shipped features | `docs/features/` |

---

*Every claim in this document is traceable to the file named beside it. Where
something is unverified, it is listed in [Part 5](#part-5--what-it-does-not-do)
rather than omitted.*
