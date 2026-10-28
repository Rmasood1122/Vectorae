# Vectorae

**End-to-end AI-powered client acquisition.** Takes a product or skill as input and carries it all the way from strategy → research → outreach → booked meeting. Runs 24/7 on a cloud backend — campaigns continue when your device is off.

---

## What It Does

1. **Intake** — Describe your product or skill. Optionally provide past client details for pattern-anchored strategies.
2. **Research** — The 72-step (or 144-step, for zero-history products) pipeline produces a full strategy document covering ICP, competitors, channels, messaging, objection handling, and execution plan.
3. **Verification** — Every strategy passes 10 independent verification checks (factual accuracy, ICP fit, compliance, KPI realism, etc.) before any outreach begins.
4. **Lead Sourcing** — Apollo.io sourced, Hunter.io email-verified, suppression-checked, enriched leads.
5. **Outreach** — Multi-channel sequences (Gmail, WhatsApp) with compliant templates, personalization, and cadence.
6. **Booking** — Calendly integration; webhook fires when a meeting is booked; lead is promoted to `meeting_booked`.
7. **Learning** — Every outcome is stored and fed back into the strategy playbook. A/B winners are auto-promoted. New strategies get playbook-injected context from high-scoring patterns.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Clients                                                      │
│  Web (Next.js)  ·  Mobile (Capacitor)  ·  CLI (pip install)  │
└─────────────────────────┬────────────────────────────────────┘
                          │  HTTPS / REST
┌─────────────────────────▼────────────────────────────────────┐
│  FastAPI — api:8000                                           │
│  Auth · Products · Strategies · Leads · Campaigns · Playbook │
└──────┬────────────────────────────────────┬──────────────────┘
       │ SQLAlchemy                          │ Redis
┌──────▼────────┐                  ┌────────▼────────────────┐
│  PostgreSQL   │                  │  Celery Beat + Workers  │
│  All state    │                  │  Pipeline · Outreach    │
└───────────────┘                  │  Learning · Monitoring  │
                                   └─────────────────────────┘
```

One backend, three clients. All business logic lives on the cloud — campaigns run whether your device is on or off.

---

## Quick Start (Local Development)

### Prerequisites
- Docker + Docker Compose v2
- Python 3.11+
- Node.js 18+

### Start the backend

```bash
# Clone and configure
git clone https://github.com/yourorg/clienthunter-enterprise
cd clienthunter-enterprise
cp .env.example .env   # fill in API keys

# Start services
docker compose up -d postgres redis

# Install Python deps
pip install -e ".[dev]"

# Run migrations
alembic upgrade head

# Start API + workers
uvicorn app.main:app --reload &
celery -A app.workers.celery_app worker -Q pipeline,outreach,learning,default -c 2 &
celery -A app.workers.celery_app beat &
```

### Start the frontend

```bash
cd frontend
npm install
npm run dev
# → http://localhost:3000
```

### Run tests

```bash
pytest tests/ -q --tb=short
```

---

## pip Package (CLI / SDK)

```bash
pip install clienthunter

# Initialize
clienthunter init --api-url https://api.yourdomain.com --email you@example.com

# Run a campaign
clienthunter run --product "AI-powered outreach automation" --flow with-clients

# Check status
clienthunter status

# Self-host
clienthunter serve --check-env   # validate env, then start
```

---

## Production Deployment

See [DEPLOY.md](DEPLOY.md) for the complete guide: SSL setup, Docker Compose production config, webhook configuration, monitoring endpoints, and update procedures.

---

## Integrations

| Integration | Purpose | Auth |
|---|---|---|
| Apollo.io | Lead sourcing + enrichment | API key |
| Hunter.io | Email verification | API key |
| Gmail | Outreach sending | OAuth 2.0 |
| WhatsApp Business | WhatsApp outreach | Meta App token |
| Calendly | Meeting booking | OAuth 2.0 |
| Anthropic (Claude) | Research pipeline, strategy, personalization | API key |
| Firebase FCM | Push notifications (mobile + web) | Service account |

All integrations live behind abstract interfaces — new channels can be added without touching core logic.

---

## Compliance

Compliance is non-negotiable and enforced at the code level:

- **Email**: CAN-SPAM / GDPR — mandatory unsubscribe header/footer, suppression list checked at send time, bounce rate auto-pause at 3%, daily send caps
- **WhatsApp**: Meta template-only for cold contact, opt-in required, 24-hour customer-service window enforced, STOP message handling
- **GDPR**: Full right-to-erasure via `DELETE /leads/{id}` — PII wiped, tombstone created, lead never re-sourced
- **Security**: Fernet-encrypted secrets at rest, JWT auth, signed webhook verification, cross-tenant isolation

See [docs/compliance.md](docs/compliance.md) for the full picture.

---

## Key Files

| File | Purpose |
|---|---|
| `DEPLOY.md` | Production deployment guide |
| `BACKUP.md` | Backup and restore procedures |
| `SECURITY.md` | Secrets inventory, rotation, compromise response |
| `LAUNCH_CHECKLIST.md` | Pre-launch verification checklist |
| `SYSTEM_HANDOFF.md` | Architecture decisions and extension guide |
| `CHANGES.md` | Full milestone changelog (M1–M8) |
| `docs/architecture.md` | Deep-dive architecture docs |
| `docs/compliance.md` | Compliance implementation details |
| `docs/api-reference.md` | Complete REST API reference |
| `docs/learning-loop.md` | Learning loop and A/B testing details |

---

## License

Private and proprietary. All rights reserved.
