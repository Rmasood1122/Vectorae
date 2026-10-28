# Architecture — Vectorae

## Design Philosophy

**One backend, three clients.** All business logic lives in the FastAPI + Celery cloud backend. The web app, mobile app, and CLI are thin clients that speak HTTP to the same API. This ensures campaigns run 24/7 regardless of device state.

**Compliance first.** Every channel integration enforces its compliance rules at the adapter layer — it's not possible to send a non-compliant message by calling the adapter directly.

**Pluggable adapters.** Every external service (lead source, outreach channel, email verifier) implements a typed abstract interface. Adding a new channel is implementing the interface and registering the adapter.

---

## Layer Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  CLIENTS (thin — no business logic)                          │
│  Next.js web  ·  Capacitor mobile  ·  clienthunter CLI      │
└───────────────────────────┬─────────────────────────────────┘
                            │  JWT-authenticated REST
┌───────────────────────────▼─────────────────────────────────┐
│  API LAYER (FastAPI)                                         │
│  app/api/                                                    │
│    auth.py          ← JWT issue/refresh/revoke               │
│    products.py      ← product + past-client CRUD            │
│    strategies.py    ← pipeline trigger, step/pass read      │
│    leads.py         ← sourcing, enrichment, GDPR delete     │
│    campaigns.py     ← sequence create/pause/resume          │
│    webhooks.py      ← WhatsApp + Calendly inbound           │
│    playbook.py      ← scores, similarity, promotion         │
│    health.py        ← /health, /health/learning-loop, /channels
│    admin.py         ← admin-only: users, task errors, CBs   │
└───────────────────────────┬─────────────────────────────────┘
                            │  SQLAlchemy ORM (sync)
┌───────────────────────────▼─────────────────────────────────┐
│  SERVICE LAYER                                               │
│  app/services/                                               │
│    pipeline_service.py   ← 72/144-step engine               │
│    strategy_service.py   ← verification, pattern extraction │
│    lead_service.py       ← sourcing, enrichment, suppression│
│    sequence_service.py   ← step scheduling, cadence         │
│    playbook.py           ← score CRUD, injection            │
│    similarity.py         ← TF-IDF similarity index          │
│    notification_service.py ← FCM dispatch                   │
│    admin_service.py      ← admin business logic             │
└─────────┬────────────────────────────────┬──────────────────┘
          │ ORM                            │ respx-mocked
┌─────────▼────────┐           ┌───────────▼─────────────────┐
│  PostgreSQL 16   │           │  INTEGRATION ADAPTERS        │
│  app/models/     │           │  app/integrations/           │
│  All persistent  │           │    apollo.py → LeadSource    │
│  state           │           │    hunter.py → EmailVerifier │
└──────────────────┘           │    gmail.py  → OutreachChannel
                               │    whatsapp.py → OutreachChannel
                               │    calendly.py → BookingProvider
                               │    token_store.py → Fernet-enc
                               └─────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  BACKGROUND WORKERS (Celery + Redis)                         │
│  app/workers/                                                │
│    celery_app.py          ← app config + task routing        │
│    pipeline_tasks.py      ← execute_pipeline_step, resume   │
│    outreach_tasks.py      ← send_sequence_step, flush       │
│    lead_tasks.py          ← source_leads, verify_emails     │
│    learning_tasks.py      ← aggregation, promotion          │
│    monitoring.py          ← failure signal, stale detection  │
│    beat_heartbeat.py      ← 60s Redis heartbeat key         │
└─────────────────────────────────────────────────────────────┘
```

---

## Research Pipeline Engine

The 72-step pipeline is organized as **8 phases × 9 steps each**:

| Phase | Steps | Name | Output |
|---|---|---|---|
| 1 | 1–9 | Product decomposition | value_prop, differentiators, pricing |
| 2 | 10–18 | ICP definition | firmographics, personas, triggers |
| 3 | 19–27 | Market sizing | TAM, segments, priority segment |
| 4 | 28–36 | Competitor analysis | competitors, positioning gap |
| 5 | 37–45 | Channel analysis | channel ranking, expected rates |
| 6 | 46–54 | Messaging & offer | subject variants, hook, offer |
| 7 | 55–63 | Objection mapping | objections, proof assets |
| 8 | 64–72 | Execution plan | sequence, cadence, KPIs |

Each step is:
1. Dispatched as a Celery task
2. Calls the Anthropic API with the accumulated context from prior steps
3. Persists the structured JSON output to `research_steps`
4. Marks `status = complete`

**Resumability**: If the pipeline crashes at step N, it resumes at N+1 by checking which steps already have `status = complete`. Completed steps are never re-run.

**Playbook injection**: Phase 6 and Phase 8 steps include playbook context (winning variants for matching ICP patterns) in their system prompt.

---

## Learning Loop

```
Outcomes table (every send/open/reply/booked/won)
         ↓
Nightly aggregation (Celery Beat, 00:05 UTC)
         ↓
PlaybookScore upsert per (pattern_key, variant)
         ↓
auto_promote_winners (two-proportion z-test, min lift 10%, p < 0.05)
         ↓
default_variant updated on strategy
         ↓
New strategy for same ICP → Phase 6 + 8 get playbook context injected
```

**Pattern key** is derived from the ICP: `{industry}_{company_size_bucket}_{channel}`. Example: `saas_smb_email`.

**TF-IDF similarity index** finds strategies with similar product descriptions at strategy creation time, surfacing relevant playbook context even when the exact pattern key hasn't been seen before.

---

## Data Models (key tables)

| Table | Purpose |
|---|---|
| `users` | Auth + admin/suspension status |
| `products` | User's product/skill + past clients |
| `strategies` | Pipeline runs — references product, holds step outputs, verification passes |
| `research_steps` | 72/144 rows per strategy — one per pipeline step |
| `leads` | Sourced, enriched, scored lead records |
| `sequences` | Multi-step outreach sequences per lead+strategy |
| `campaigns` | Aggregate status tracker for a strategy's outreach |
| `outcomes` | Event log (sent, opened, replied, booked, won, etc.) |
| `playbook_scores` | Aggregated variant performance per ICP pattern |
| `suppression_list` | Global do-not-contact list |
| `integration_tokens` | Encrypted OAuth tokens + API keys per user |
| `device_tokens` | FCM push notification tokens per user |
| `task_errors` | Celery task failure log for admin review |

---

## Security Model

- **Authentication**: HS256 JWTs, 60-min access + 30-day refresh
- **Authorization**: Every resource query filters by `user_id = current_user.id`
- **Secrets at rest**: Fernet-encrypted (`ENCRYPTION_KEY`) in `integration_tokens`
- **Webhook verification**: HMAC-SHA256 on WhatsApp + Calendly payloads
- **Tenant isolation**: DB-layer filter on every query — no shared IDs between tenants
- **Circuit breakers**: OPEN after 5 consecutive failures per external provider

---

## Extension Points

### Adding a new outreach channel

1. Implement `app.integrations.interfaces.OutreachChannel`:
   ```python
   class MyChannel(OutreachChannel):
       async def send(self, lead, message, template=None): ...
       async def check_reply(self, lead): ...
       def validate_compliance(self, message): ...
   ```
2. Register in `app/integrations/__init__.py`
3. Add to `sequence_service.py` channel dispatch
4. Add an adapter env var to `config.py` and `.env.example`

### Adding a new lead source

1. Implement `app.integrations.interfaces.LeadSource`
2. Register in `lead_service.py`

### Adding a new verification pass

The verification loop in `strategy_service.py` iterates a list of pass functions. Add a new function to the list; it receives the full strategy document and returns `VerificationPassResult`.

### Adding a new pipeline phase

Extend `PIPELINE_PHASES` in `pipeline_service.py`. The 72-step count is enforced by tests — update the test assertion if you add phases.
