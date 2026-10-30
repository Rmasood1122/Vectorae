# Vectorae

Vectorae is an AI outbound and go-to-market system. You describe a product or
skill; it researches the market, writes and verifies a strategy, sources and
verifies leads, runs compliant multi-channel outreach, and books meetings. It
learns from every outcome.

It is a revenue product with a **zero-tolerance legal-compliance posture**
(CAN-SPAM, GDPR, CASL, PDPA): sending rules are enforced in code, at the moment
of sending, and proven by a sealed test set that blocks any release that
weakens them.

---

## How it works

| Stage | What happens |
|---|---|
| Intake | You describe the product or skill, optionally with past clients. |
| Research | A 72-step pipeline (144 steps for a product with no past clients) produces the strategy: ideal customer, competitors, channels, messaging, objections, plan. |
| Verification | The strategy is checked before any outreach starts. |
| Leads | Leads are sourced, verified and checked against the suppression list. |
| Outreach | Sequences run over email, WhatsApp, LinkedIn and AI phone calls, each with its own consent and sending rules. |
| Meetings | Bookings, meeting links and call notes come back into the lead's record. |
| Learning | Outcomes feed the strategy playbook; winning variants are promoted. |

The customer-facing explanation of every feature, limit and rule is the help
center in [`docs/help-center/`](docs/help-center/).

---

## Architecture

```
  Web app (Next.js)   Android app (Capacitor)   Python SDK / CLI   MCP connector
          \                    |                       |               /
           +-------------------+-----------+-----------+--------------+
                                           | HTTPS
                                 FastAPI  (app/main.py, :8000)
                                 /                         \
                     PostgreSQL (SQLAlchemy,         Redis + Celery workers and beat
                     Alembic migrations)             (pipeline, outreach, learning,
                                                      notifications, default)
                                           |
                     Anthropic (research pipeline)  +  outside services (below)
```

| Path | Contents |
|---|---|
| `app/` | Backend: `api/` routes, `services/` business logic, `integrations/` outside services, `pipeline/` research engine, `workers/` Celery tasks, `mcp/` remote MCP server, `db/` models |
| `alembic/` | Database migrations (the head is whatever `alembic heads` reports) |
| `frontend/` | Next.js web app; `frontend/android/` is the Capacitor Android app |
| `sdk/` | Python SDK and CLI (package and command `clienthunter`, see [`sdk/README.md`](sdk/README.md)) |
| `plugins/leadpilot/` | Claude plugin for the MCP connector |
| `evals/` | Release gate: test registry, sealed compliance set, baseline, gate tools |
| `tests/` | Backend test suite (offline: SQLite in memory, a fake Claude, no network) |
| `docs/` | Help center, architecture, compliance, API reference, program records |
| `scripts/` | Gate scripts, help-center index, operations tools |

---

## Run it locally

Requirements: Docker with Compose v2, Python 3.11+, Node.js 18+.

```bash
git clone https://github.com/Rmasood1122/Vectorae.git
cd Vectorae
cp .env.example .env            # fake values only; fill in your own keys
docker compose up -d            # db, redis, backend (:8000), worker, beat, frontend (:3000)
```

The backend container runs `alembic upgrade head` before it starts.

To run the backend outside Docker:

```bash
pip install -r requirements.txt
docker compose up -d db redis
alembic upgrade head
uvicorn app.main:app --reload
celery -A app.workers.celery_app worker -Q pipeline,outreach,learning,default,notifications
celery -A app.workers.celery_app beat
```

Frontend: `cd frontend && npm install && npm run dev` (http://localhost:3000).

---

## Tests and the release gate

Nothing merges without the gate. Install the test dependencies once:

```bash
pip install -r requirements.txt fakeredis pyyaml pytest pytest-asyncio pytest-mock
scripts/gate/install_hooks.sh                  # once per clone
```

| Command | Purpose |
|---|---|
| `scripts/gate/quick_gate.sh` | Before every push: secrets, syntax, lint, migrations, brand, affected tests, frontend. Stamps the commit. |
| `python3 evals/run_evals.py` | The full offline suite (all backend tests), scored. |
| `python3 evals/tools/promote.py` | Compares the run with the baseline: PROMOTE or BLOCK. |
| `python3 evals/fixtures/verify_block_fixtures.py` | Proves every hard gate still blocks. |
| `scripts/gate/full_gate.sh` | What CI runs; before marking a PR ready. |
| `cd frontend && npm test` | Frontend tests (Vitest). |

Merges to `master` go only through `scripts/gate/safe_merge.sh <PR>`. The sealed
files (registry, compliance test set, baseline, workflows, gate scripts and the
sealed tests) change only in their own reviewed gate PR. See
[`CLAUDE.md`](CLAUDE.md) and [`docs/program/GATES.md`](docs/program/GATES.md).

---

## Deployment

The model is self-hosted, single-tenant (decision P0-D in
[`docs/program/DECISIONS.md`](docs/program/DECISIONS.md)).

| Guide | Path |
|---|---|
| [`DEPLOY.md`](DEPLOY.md) | A VPS with Docker Compose, SSL, webhooks and monitoring |
| [`DEPLOY_RENDER_FREE.md`](DEPLOY_RENDER_FREE.md) | Free tier: Render (`render.yaml`), Neon, Upstash, Vercel |
| [`DEPLOY_RAILWAY.md`](DEPLOY_RAILWAY.md) | Railway (paid tier) |
| [`LAUNCH_CHECKLIST.md`](LAUNCH_CHECKLIST.md) | Checks before going live |
| [`BACKUP.md`](BACKUP.md) / [`SECURITY.md`](SECURITY.md) | Backups; secrets inventory and rotation |

---

## Outside services

Each is optional and configured by an administrator; a feature whose service is
not configured stays off.

| Area | Services |
|---|---|
| AI | Anthropic Claude (research, writing, checks) |
| Leads and data | Apollo, Hunter |
| Email | Gmail (OAuth), Google Postmaster, Mailreach |
| Messaging | WhatsApp Business (Meta Cloud API), LinkedIn via Unipile, SMS |
| Calls | Vapi and ElevenLabs (AI calls), Recall.ai (meeting notes) |
| Meetings | Calendly, Google Meet, Zoom |
| CRM and alerts | HubSpot, Salesforce, Slack, Firebase push |
| Billing | Stripe |

What has and has not been checked against each live service is listed in
[`docs/help-center/29-known-limitations.md`](docs/help-center/29-known-limitations.md).

---

## Compliance

- Every email carries an unsubscribe link and header; the suppression list is checked again at the moment of sending.
- Cold WhatsApp needs an approved template and a recorded opt-in.
- AI calls need recorded consent (by default), always say they are an AI and that the call is recorded, and are refused when that disclosure cannot be guaranteed.
- A prospect or an account can be erased; a prospect who opted out or asked to be erased is never contacted again.
- Secrets are encrypted at rest; webhooks are verified by signature.

Details: [`docs/compliance.md`](docs/compliance.md) and
[`docs/help-center/20-compliance.md`](docs/help-center/20-compliance.md).

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Architecture in depth |
| [`docs/api-reference.md`](docs/api-reference.md) | REST API reference |
| [`docs/learning-loop.md`](docs/learning-loop.md) | Learning loop and A/B testing |
| [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md) | What is ready and what still needs action |
| [`docs/program/PROGRESS.md`](docs/program/PROGRESS.md) | Change log of the production-readiness program |
| [`CHANGES.md`](CHANGES.md) | Milestone change log |

---

## License

Private and proprietary. All rights reserved.
