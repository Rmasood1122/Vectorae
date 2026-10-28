# F02 — Signal Radar ("why now")

**Flag** `signal_radar_enabled` (system setting, **default off**) ·
**Migration** `0074_signal_radar` (creates `lead_signals`) ·
**Service** `app/services/signal_radar.py` ·
**API** `app/api/signals.py` ·
**UI** `frontend/src/components/leads/SignalRadarPanel.tsx`,
`frontend/src/lib/signals.ts`, `frontend/src/lib/api/signals.ts`

## Problem

Competitor complaint (STAR A.6, F02): *buyers rank signal freshness with a
verifiable source as the top differentiator.* Tools that write "congrats on
the raise" do it from data nobody can trace, and often from news that is
months old.

Vectorae already **fetches** most of what a "why now" needs, but never
treats it as a signal with an age and a source:

| Already exists | What it lacks |
|---|---|
| `market_intel.py` — Google News + Apollo org funding/headcount, once per **strategy** | Strategy-scoped; never attached to a lead; never reaches outreach copy |
| `personalization_context.py` — the lead's LinkedIn posts (7-day cache) and NewsAPI company news (3-day cache) | No per-item age or expiry in the prompt; posts are not age-filtered at all |
| Apollo enrichment on `leads.enrichment_json` (org `latest_funding_round_date`, headcount growth) | Never surfaced as a dated event |
| `claim_verification.py` — every claim must be backed by a stored evidence item | Does not know which evidence item was a "why now" signal, so nobody can measure it |
| `provenance.py` — confidence per source, age bands | Per field, not per event |

**Recon verdict: extend, not build.** Over half the inputs exist. Signal Radar
is a normalising layer over data the system already stores (no new vendor, no
new fetch, no new model call), plus one prompt block, plus the link from a
claim-verification decision back to the stored signal.

## Behaviour

### 1. Derivation (no network)

`signal_radar.refresh(session, lead, owner_id=..., strategy=..., now=...)`
reads only what is already stored and upserts one `lead_signals` row per
distinct event:

| Source (`source`) | Read from | Type | Date (`observed_at`) | Link (`source_url`) | Confidence |
|---|---|---|---|---|---|
| `newsapi` | `leads.company_news_json` | funding / hiring / launch / news (the deterministic `market_intel.classify_headline` rules) | article `published_at` | article URL | 0.70 (provenance `company_news`) |
| `linkedin_post` | `leads.linkedin_posts_json` | `post` | `posted_at` | post URL | 0.80 (provenance `linkedin`) |
| `apollo` | the organisation record inside `leads.enrichment_json` | funding (`latest_funding_round_date`), hiring (`organization_headcount_six_month_growth` ≥ 10%) | the round date; for headcount growth, the moment Apollo reported it to us (the lead's `created_at`) | none — Apollo has no public page for the fact | 0.75 (provenance `apollo`) |
| `market_intel` | `strategies.market_signals_json`, **only** items naming this lead's company (Google News: whole-word company name in the headline; Apollo: normalised org name equal to the lead's company) | the item's own type | item `published_at` (items without a date are skipped) | the headline link (Google News); none for Apollo | 0.60 (headline only, name match) / 0.75 (Apollo) |

Rules:
- An item **without a parseable date is skipped**, except Apollo headcount
  growth (dated as above, documented). A signal whose age is unknown cannot be
  fresh, and freshness is the point.
- A date **in the future** is a data error: the row is stored but never citable.
- Rows are upserted on `(lead_id, dedupe_key)` where `dedupe_key` is a SHA-256
  of the URL (or source + type + title when there is no URL). Re-deriving is
  idempotent.
- Rows are never deleted by refresh — an article that falls out of the 30-day
  news cache keeps its row and simply expires.
- `refresh` never raises into the send path (same contract as
  `personalization_context.ensure_fresh`).

Runs: in the email send path right after `personalization_context.ensure_fresh`
(so the newest posts/news are included), and on demand via
`POST /leads/{id}/signals/refresh` (re-derive only — **no vendor call**).

### 2. Freshness decay

Each type has a lifetime (`TTL_DAYS`): funding 90, launch 60, hiring 60,
news 45, post 30. `expires_at = observed_at + TTL`. No lifetime exceeds 90
days, so every citable signal is under 90 days old by construction (the
success metric's bar).

`freshness = 0.5 ** (age_days / half_life)` with `half_life = TTL / 2`, and 0
at or after expiry. `score = confidence × freshness`. Age is shown separately
from confidence (same reasoning as `provenance.staleness`: a weak source and an
old fact need different fixes); `score` is only used to rank.

Status bands on the lead: `fresh` (freshness ≥ 0.5), `aging` (> 0, < 0.5),
`expired`.

### 3. Citability

A signal is **citable** only when all hold:
1. not expired, and not dated in the future;
2. it has a public `http(s)` `source_url` — "verifiable" means a person can
   open the source. Apollo-derived signals are therefore **shown but never
   cited** (`not_citable_reason: "no public source link"`);
3. confidence ≥ 0.5;
4. claim verification is enabled (`claim_verification_enabled`). With the
   claim engine off nothing can be tied to a source, so nothing is offered.

### 4. Personalization (email only)

When the flag is on and the lead has a citable signal, the email prompt gets
one extra block naming **the single highest-scoring citable signal**: its type,
date, age, publisher, and its text — the text and publisher delimited with
`prompt_safety` (F-AI-02), because a headline or a post body is written outside
this system. The instruction: it MAY be given as the reason for writing now,
stating only what the text says; skip it if it does not concern this company.

WhatsApp, LinkedIn and call scripts are unchanged (non-goal for v1).

### 5. Tie to the stored source (the guard)

`claim_verification.enforce` gains an optional `decisions_out` list (existing
callers unaffected) so the send path can see the decisions it just logged.
After verification, `signal_radar.resolve_citation` looks for a **VERIFIED**
decision whose evidence source is exactly the evidence key of one of the lead's
citable signals:

| Signal source | Evidence key the claim engine must have used |
|---|---|
| `newsapi` | `newsapi:<article url>` |
| `linkedin_post` | `linkedin_post:<post url>` |
| `market_intel` (Google News) | `market_intel:<headline url>` |

Market-intel headlines are not in the claim engine's evidence ledger today (it
reads only lead columns). With the flag on, `enforce` adds the lead's
**unexpired, linked `market_intel` signals** as `news` evidence with key
`market_intel:<url>`. These rows are data stored for that lead, which is the
claim engine's existing rule; with the flag off the ledger is byte-for-byte
what it was. With the flag on, this extra evidence applies to every channel the
claim engine checks (email, WhatsApp, LinkedIn, call scripts), because the
ledger is per lead, not per channel — only the *offering* of a signal in the
prompt is email-only.

Only a tied, verified decision counts as a cited signal. A message that
mentions a signal the engine could not tie is **not counted** — and its
unsupported claim was already stripped or rewritten by the claim engine as
before. The citation is recorded on `messages.personalization_json.signal_radar`:

```json
{"offered": {"id": "…", "type": "funding", "source": "newsapi",
             "source_url": "https://…", "observed_at": "…", "age_days": 11},
 "cited": {"id": "…", "type": "funding", "source_url": "https://…",
           "observed_at": "…", "age_days": 11, "evidence_source": "newsapi:https://…",
           "claim": "Congrats on the Series B …"} | null}
```

Known conservative bias: when two LinkedIn posts both support a claim with no
distinguishing proper noun, the claim engine attributes it to the first stored
post; if the offered signal was the second post, the message is counted as
**not** citing a signal. The metric can undercount; it cannot overcount.

## Non-goals

- No new data vendor, no new fetch, no scraping. Sources are only what
  `market_intel` and `personalization_context` already fetch and Apollo already
  returned.
- No new model call. No change to the claim engine's verdict rules.
- No signal block for WhatsApp / LinkedIn / phone in v1.
- No automatic campaign triggering on a new signal ("signal-based sequences").
- No age filter retrofitted onto the existing LinkedIn-post prompt block (see
  out-of-scope discovery in the PR report).

## Data model and migration

`0074_signal_radar` creates `lead_signals`:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK users, CASCADE, not null, indexed | owner (denormalised for ownership filters and the GDPR export) |
| `lead_id` | UUID FK leads, CASCADE, not null | |
| `signal_type` | String(20) | funding / hiring / launch / news / post |
| `source` | String(30) | newsapi / linkedin_post / apollo / market_intel |
| `source_url` | String(1000), null | |
| `source_name` | String(200), null | publisher |
| `title` | String(500) | |
| `excerpt` | Text, null | ≤ 1,000 chars of summary / post body |
| `observed_at`, `expires_at`, `first_seen_at`, `last_seen_at` | DateTime(tz) | not null |
| `confidence` | Float | |
| `dedupe_key` | String(64) | SHA-256 hex |
| `created_at` | DateTime(tz), server default now | |

Unique `(lead_id, dedupe_key)` (also serves the per-lead read). Portable types
only (UUID, JSON-free); downgrade drops the table — **lossy by design**: the rows
are derived from data that stays on `leads` / `strategies`, so
`POST /leads/{id}/signals/refresh` (or the next send) rebuilds them after a
re-upgrade.

## API

| Route | Behaviour |
|---|---|
| `GET /leads/{id}/signals` | The lead's signals, best score first, each with age, freshness, status, citability and reason. Flag off → `{"enabled": false, "signals": []}`. Not the caller's lead → 404. |
| `POST /leads/{id}/signals/refresh` | Re-derive from stored data (no vendor call). Flag off → 409. |
| `GET /signals/report?days=90` | Success metric for the caller's account: email messages sent in the window, share citing a verified signal under 90 days old, reply rate split signal vs no-signal, sample sizes. Flag off → `enabled: false` with the same numbers (a report reads history; it never changes it). |

Every query filters by `Product.user_id == current_user.id` through
lead → strategy → product (leads) or message → sequence → strategy → product
(report), and `lead_signals.user_id`.

## UI

`SignalRadarPanel` on the lead page (next to Provenance): the top "why now"
line, then each signal with type, title, publisher, **source link** (opens in
a new tab, `rel="noopener noreferrer"`), age, status badge and "Not cited:
<reason>" for non-citable ones, plus a "Refresh from stored data" button. The
panel renders nothing when the flag is off. The report is API-only in v1.

## Tenancy

`lead_signals.user_id` is the strategy's owner at derivation time; every read
filters on it **and** on the lead's ownership chain. The GDPR account export
(`account_export.OWNERSHIP`) includes the table via `user_id`. Cross-account
reads are tested (404, and no rows leak through the report).

## Compliance review (CAN-SPAM / GDPR / CASL / PDPA)

- **No new send path.** The block is added inside the existing email render,
  which runs inside `send_message_impl` after suppression, consent, outreach
  gate and before the send-review queue; nothing about who receives what, when,
  changes.
- **Truthfulness (CAN-SPAM §5(a)(1)/(2), CASL s.6 misleading content).** A
  signal can only be cited if the claim engine verifies the sentence against
  the stored source; unverified claims are stripped or rewritten exactly as
  before. The flag cannot weaken the claim engine: with claim verification off,
  no signal is offered.
- **GDPR erasure (Art. 17).** `DELETE /leads/{id}` now deletes the lead's
  `lead_signals` rows (they hold post excerpts and headlines about the person);
  FK CASCADE covers hard deletes. Tested.
- **GDPR access/portability (Art. 15/20).** Included in the account export.
- **Data minimisation (GDPR Art. 5(1)(c), PDPA).** Only data already stored is
  re-shaped; excerpts are capped at 1,000 characters; nothing new is collected.
- **Prompt injection (F-AI-02).** Headline, summary, post text and publisher
  are delimited with `prompt_safety`.

Legal/safety guard added: the **citation tie** (a signal counts as cited only
through a verified claim-engine decision on its stored source) and the
**GDPR erasure** of signal rows. Neither is a send block.

## AI cost and budget impact

No new model call. The email prompt grows by one block (≈ 60–150 input tokens)
only when a citable signal exists: at $3/MTok input, ≈ $0.0005 per email at
most. The existing `usage_meter` scope around the send path covers it.

## Test plan

`tests/test_signal_radar.py` — derivation from each source, skip-undated,
dedupe/idempotence, decay maths and bands, citability rules (no URL, expired,
future date, claim engine off), prompt block delimiting, the citation tie
(verified + same source → cited; verified against another item → not cited;
stripped → not cited), market-intel evidence only when the flag is on,
`decisions_out` keeps existing behaviour, the real send path with the flag on
and off (flag off = prompt and `personalization_json` unchanged), GDPR delete,
API ownership (404 for another account; report excludes other accounts), report
maths. `tests/test_signal_radar_migration.py` — table matches the model,
revision chain, downgrade round trip. `frontend/src/tests/signals.test.ts` —
labels, ages, ordering, headline.

## Success metric (measured, never promised)

From `GET /signals/report`:
1. **Share of sent emails citing a verified signal under 90 days old.**
2. **Reply rate, signal vs no-signal**, with both sample sizes. Reported only;
   no lift is claimed, and below 30 sends in either group the report says the
   comparison is too small to read.

A reply counts for a message when the REPLIED outcome names that message, or
(when it names none) for the lead's latest email sent before the reply.

## Unverified

- **UNVERIFIED:** the Apollo organisation field names
  (`latest_funding_round_date`, `latest_funding_stage`,
  `organization_headcount_six_month_growth`) — inherited from
  `market_intel.apollo_signals`, which carries the same TODO; not checked
  against a live Apollo response.
- **UNVERIFIED:** the Google News RSS links resolve to the article (they are
  `news.google.com` redirect links); not checked live.
- **UNVERIFIED:** whether real Claude output actually uses the offered signal
  and phrases it in a way the claim engine verifies — tests use a scripted
  model. The share metric will show it after a pilot.
- **UNVERIFIED:** any reply-rate difference. Nothing here promises one.
- Help-center article: `docs/help-center/` does not exist on this branch's
  base (Phase 2 not merged here), so no help article was added; it is owed
  when the help center lands.
