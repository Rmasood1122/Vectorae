# Vectorae — System Handoff

*Last updated: M9 (native CRM + real-time). All 9 milestones complete.*

---

## What is this system?

An end-to-end, self-learning B2B client acquisition platform. You describe a product or skill, and the system:
1. Researches your market (72-step pipeline), builds a verified strategy (10 verification passes).
2. Sources leads (Apollo + Hunter), enriches them, sequences outreach (Gmail + WhatsApp).
3. Books meetings (Calendly webhooks), tracks outcomes, and improves its own strategies over time.
4. Runs 24/7 in the cloud — continues even when your device is off.

---

## Status — Everything built

### M1 — Backend core
- FastAPI + PostgreSQL + Celery + Redis
- 72-step research pipeline (8 phases × 9 steps, resumable)
- 10-pass verification loop
- Product/skill intake, past-client pattern extraction

### M2 — Lead sourcing
- Apollo.io adapter (people/company search + enrichment)
- Hunter.io adapter (email find + verify)
- Suppression list, bounce handling

### M3 — Gmail sequences + Calendly
- Gmail OAuth 2.0 per user, warm-up schedule, daily send cap
- Sequence + step engine, reply detection
- Calendly webhook → "meeting_booked" outcome

### M4 — WhatsApp
- WhatsApp Business Cloud API (Meta)
- Template message enforcement (cold contact only)
- 24-hour customer service window for free-form replies

### M5 — Web UI + Theme Engine
- Next.js 14, TypeScript, Tailwind CSS, shadcn/ui
- 5 preset themes + full custom mode (CSS variables, live preview)
- Kanban pipeline, campaigns, analytics, settings
- Vitest unit tests, Playwright E2E

### M6 — pip SDK + CLI
- `pip install clienthunter`
- Typer/Rich CLI: `clienthunter init|run|status|serve`
- 60 passing tests

### M7 — Android mobile app
- Capacitor 6 wrapper (static Next.js export)
- FCM push notifications
- Deep links, native Preferences storage
- GitHub Actions release pipeline (android-release.yml)

### M8 — Learning loop
**Chunk 1:** Outcomes aggregation, playbook scores, nightly Celery Beat job.
**Chunk 2:** A/B testing (binary, Welch's t-test), auto-promotion with 4-gate guard.
**Chunk 3:** Strategy similarity (TF-IDF cosine), playbook injection at Phases 6+8, pipeline monitoring + admin dashboard.
**Chunk 4 (advanced learning):**
- Send-time optimizer (Redis-cached slot recommendations per channel+ICP)
- Score decay engine (exponential decay, configurable half-life, trend labels)
- Multi-variate testing (Kruskal-Wallis + Bonferroni, up to 5 variants)
- Subject line intelligence (8 pattern classifiers, nightly extraction + injection)
- Personalization quality scorer (field-presence check at send time, Pearson correlation nightly)
**Chunk 5 (production launch):**
- Redis sliding-window rate limiting (per-user, per-endpoint, fails open)
- Plan enforcement (free/starter/pro, HTTP 402, PlanGate dependency)
- Reliable outbound webhooks (HMAC-signed, 5-retry exponential backoff, delivery log)
- User onboarding flow (7-step JSONB state, auto-detected by services)
- PyPI release workflow (4-job: build → TestPyPI → smoke test → PyPI)
- Performance benchmark script (dry-run + JSON output)

### M9 - Native CRM + real-time push
Migration `0019_m9_crm`. Eight new tables, no existing table altered.

**Data model:** `crm_notes`, `crm_activities` (UI-facing audit trail, kept
separate from `outcomes` so the learning loop's denominators are untouched),
`crm_tags` + `crm_lead_tags`, `crm_saved_views`, `crm_custom_fields` +
`crm_custom_field_values` (EAV, so custom columns sort and filter server-side
identically on SQLite and PostgreSQL), and `crm_lead_meta` (owner,
`stage_entered_at` - the stage-history measurement nothing before M9 recorded).

**Real-time (SSE):** `GET /crm/stream` streams `lead.updated`,
`lead.status_changed`, `note.created` and `outcome.created`, scoped to one
Redis channel per user. Events are published by SQLAlchemy session listeners
(`app/services/crm_events.py`) rather than by a call at each write site, so the
~10 places that already write `Lead.status` or `Outcome` - including the Celery
workers and the inbound webhooks - are covered without being edited, and a
future write site is covered automatically. `EventSource` cannot send an
Authorization header, so the stream authenticates with a single-use 60-second
ticket from `POST /crm/stream/ticket` instead of a JWT in the URL.
UNGATED BY PLAN: a subscription costs one Redis connection, and the polling
fallback would give a free-tier user the same data anyway.

**View 1 - executive dashboard** (`/crm/dashboard/*`, 4 pages): pipeline funnel
with cumulative stage conversion, lead velocity + source mix + verification
quality + stuck-lead detection, per-sequence/per-channel/per-variant outreach
performance against the 3% bounce auto-pause threshold, and a keyset-paginated
account activity feed. Every figure is a real SQL aggregation.

**View 2 - data grid** (`/crm/table`): built from scratch - no spreadsheet
library, and zero new frontend dependencies. Virtualized rows (smooth at
5,000+), resizable and reorderable columns, inline editing with optimistic
update and rollback, multi-column sort, per-column filters, multi-select with
bulk actions, full keyboard navigation, and saved views persisted SERVER-side
so they follow the user across devices. CSV leaves as a one-way download
(formula-injection neutralised); there is no import.

**Tests:** `tests/test_crm_{api,dashboard,stream,migration}.py` (+151 backend),
`src/tests/crm-{grid-state,sse-client}.test.ts` (+59 frontend),
`e2e/crm.spec.ts` (+8 per browser project).

---

## What is NOT built (honest list)

These are out of scope and would be next-phase work:

- **LinkedIn outreach channel.** LinkedIn's official API doesn't allow automated outreach at this level. A compliant implementation would require a LinkedIn partnership or manual workflow.
- **Stripe billing / payment.** Plan upgrade is currently handled via email (`upgrade@clienthunter.io`). There is no payment processing built in.
- **Self-hosted email deliverability.** The system uses Gmail OAuth per user. A dedicated SMTP warm-up infrastructure (Mailgun/Postmark with domain authentication) is not built but is the right production path for high volume.
- **iOS/App Store app.** The Capacitor wrapper targets Android (Google Play). A separate Xcode build + provisioning profile is needed for iOS.
- **Multi-tenant team accounts.** All objects are user-scoped (user_id). Team-level sharing, role-based access within an org, and white-labeling are not built.
- **HubSpot / Salesforce CRM sync.** M9 built Vectorae's OWN CRM (dashboard + data grid, `/crm`); this entry is about syncing to a THIRD-PARTY one. The outbound webhook system (M8-C5) lets you push events to any CRM via webhook, but there are no native HubSpot/Salesforce adapters.

---

## Three-client architecture

```
[Web App (Next.js)]   [Android (Capacitor)]   [CLI/SDK (pip)]
         \                    |                    /
          +--------- REST API (FastAPI) ----------+
                              |
              +-----------+---+-----------+
              |           |               |
         PostgreSQL    Celery workers    Redis
          (all data)  (research, send,  (queue, cache,
                       learn, monitor)   rate limits)
                              |
                External APIs: Anthropic, Apollo,
                Hunter, Gmail, WhatsApp, Calendly
```

All automation runs on the cloud backend. Clients are read-windows, not the engine.

---

## Costs at scale (rough estimates)

| Scale | Anthropic (claude-sonnet-4-6) | Apollo/Hunter | Infra | Total/mo |
|---|---|---|---|---|
| 100 strategies/mo | ~$15 | ~$50 | $20 (Railway starter) | ~$85 |
| 1,000 strategies/mo | ~$150 | ~$200 | $80 (Railway pro) | ~$430 |
| 10,000 strategies/mo | ~$1,500 | ~$1,200 | $400 (Docker on AWS) | ~$3,100 |

*At 10k strategies/month, move to Anthropic Batch API for 50% cost reduction.*

---

## "Your system gets smarter" — what the timeline actually looks like

| Campaigns sent | What improves |
|---|---|
| 0–50 | Nothing yet — no reliable data. Uses best-practice defaults. |
| 50–150 | First playbook scores form. A/B winners start emerging. |
| 150–300 | Send-time optimizer gets reliable slots. Subject patterns become statistically significant. |
| 300–500 | Personalization correlation becomes meaningful. Multi-variate tests can complete. |
| 500+ | System is consistently outperforming the defaults. Score decay keeps it current. |

---

## Running the system

### Start everything (local Docker)
```bash
docker-compose up -d
```
API: http://localhost:8000 | Docs: http://localhost:8000/api/v1/docs

### First run checklist
See `LAUNCH_CHECKLIST.md` for the complete pre-launch verification list.

### Release a new SDK version
```bash
python scripts/bump_version.py patch     # or minor / major
git push origin release/sdk-v<version>  # CI does the rest
```

### Run the benchmark
```bash
python scripts/benchmark.py --dry-run   # validate harness
python scripts/benchmark.py             # full benchmark vs live stack
```

### Apply migrations
```bash
docker-compose exec api alembic upgrade head
```

---

## Key environment variables (summary)

See `.env.example` for the complete list with descriptions.

Critical secrets:
- `SECRET_KEY` — JWT signing
- `ENCRYPTION_KEY` — Fernet key for integration tokens
- `ANTHROPIC_API_KEY`
- `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET`
- `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_APP_SECRET`
- `APOLLO_API_KEY` / `HUNTER_API_KEY`
- `CALENDLY_WEBHOOK_SECRET`

Learning loop tuning:
- `PLAYBOOK_SCORE_HALF_LIFE_DAYS` (default 90) — how fast old data fades
- `MV_MAX_VARIANTS` (default 5) — max variants in multi-variate test
- `AB_MIN_LIFT` (default 0.10) — minimum relative lift to declare a winner
- `PLAYBOOK_AGGREGATION_UTC_HOUR` (default 2) — when nightly job runs (UTC)

Rate limits (per user per hour):
- `RATE_LIMIT_STRATEGIES` (default 2)
- `RATE_LIMIT_LEADS_SOURCE` (default 5)
- `RATE_LIMIT_GET` (default 300)
- `RATE_LIMIT_AUTH` (default 10, per 15-min window)
- `RATE_LIMIT_CRM_WRITE` (default 600) — M9. The loosest limit here on purpose:
  it is the only one whose operation costs nothing external, and it governs
  inline grid editing, where a tighter cap mostly catches a real user
  mid-keystroke.

Native CRM (M9):
- `CRM_STREAM_MAX_SECONDS` (default 3600) — how long one SSE connection is held
  before the client is asked to reconnect. **Set to 0 to turn real-time off
  entirely**: `GET /crm/stream` then answers 503 and every client falls back to
  its 60-second poll. The kill switch to pull if the stream misbehaves.
