# Vectorae Python SDK

**Vectorae** is an AI-powered client acquisition system: give it a product or skill, and it researches your market, sources verified leads, runs multi-channel outreach (email + WhatsApp), and books meetings — 24/7, in the cloud.

This package gives you:
- A **typed Python SDK** (`clienthunter.ClientHunter`) for scripting and integration
- A **CLI** (`clienthunter run / status / init / serve`) for interactive use
- A **self-hosting command** (`clienthunter serve`) to run the backend on your own machine

---

## ⚠️ Architecture note — read this first

**Installing this package alone does NOT run campaigns.**

The SDK is a thin typed client over the Vectorae HTTP API.  All the intelligence lives in the cloud backend:

```
 Your script / CLI          Cloud backend (or self-hosted via `clienthunter serve`)
┌──────────────────┐       ┌───────────────────────────────────────────────────────┐
│  clienthunter    │  HTTP │  FastAPI + Celery + Redis + PostgreSQL                │
│  SDK / CLI       │──────▶│  72-step research pipeline  •  10× verification loop │
└──────────────────┘       │  Apollo + Hunter + Gmail + WhatsApp + Calendly        │
                           └───────────────────────────────────────────────────────┘
```

- **Cloud-hosted**: sign up at [clienthunter.ai](https://clienthunter.ai) — campaigns run 24/7 even when your laptop is off.
- **Self-hosted**: run `clienthunter serve` to start the full backend stack on your machine via Docker.  Campaigns run only while that machine is on; for 24/7 operation, deploy the compose stack to a cloud host (see [deploy docs](https://docs.clienthunter.ai/deploy)).

---

## Install

```bash
pip install clienthunter
```

Python 3.11+ required.

---

## Configure

### Option A — interactive (recommended for humans)

```bash
clienthunter init
```

This prompts for your API URL, logs you in, and writes `~/.clienthunter/config.toml` with `0600` permissions.

### Option B — environment variables (CI / scripts)

```bash
export CLIENTHUNTER_API_URL=https://api.clienthunter.ai
export CLIENTHUNTER_API_KEY=sk-...          # or use email+password login
```

### Option C — constructor arguments (testing / multi-tenant)

```python
from clienthunter import ClientHunter

ch = ClientHunter(api_url="https://api.clienthunter.ai", api_key="sk-...")
```

---

## Quickstart

```python
from clienthunter import ClientHunter

ch = ClientHunter()
ch.auth.login("you@example.com", "your-password")   # stores token in config

# Step 1 — describe your product
product = ch.products.create(
    name="Project Pulse",
    description="Real-time project tracking for remote engineering teams",
    type="product",
    user_email="you@example.com",
)

# Step 2 — add past clients (Flow 1) or skip to let the system do GTM research (Flow 2)
ch.products.add_past_clients(
    product.id,
    clients=[
        {
            "details": "Acme Corp, 80-person SaaS startup, Head of Engineering",
            "acquisition_story": "Found them via a LinkedIn post about remote work tools",
        }
    ],
)

# Step 3 — create strategy (kicks off the 72-step research pipeline in the cloud)
strategy = ch.strategies.create(product_id=product.id)
print(strategy.id, strategy.status)   # e.g. "abc-123", "pending"

# Step 4 — poll until verified
import time
while True:
    s = ch.strategies.progress(strategy.id)
    print(s.status, [(p.pipeline, p.done, p.total) for p in s.progress])
    if s.status in ("verified", "failed", "needs_human_review"):
        break
    time.sleep(10)

# Step 5 — check campaign stats (after execution begins)
overview = ch.campaigns.stats(strategy.id)
print(f"Meetings booked: {overview.meetings_booked}")
print(f"Reply rate: {overview.reply_rate:.1%}")
```

---

## CLI reference (preview)

```bash
clienthunter init          # interactive setup
clienthunter run           # intake wizard → strategy → live progress
clienthunter status        # table of all strategies + campaign state
clienthunter serve         # self-host the backend via Docker
```

Full CLI docs: [docs/cli.md](docs/cli.md) (added in Chunk 2).

---

## Error handling

All SDK methods raise subclasses of `clienthunter.ClientHunterError`:

```python
from clienthunter import ClientHunter, AuthError, ComplianceError, NotFoundError

ch = ClientHunter()
try:
    ch.strategies.create(product_id="nonexistent-id")
except NotFoundError:
    print("product not found")
except ComplianceError as e:
    print(f"blocked by compliance rule [{e.rule}]")
    if e.remediation:
        print(f"fix: {e.remediation}")
except AuthError:
    print("run `clienthunter init` to re-authenticate")
```

---

## Development

```bash
git clone https://github.com/clienthunter/clienthunter
cd clienthunter/sdk
pip install -e ".[dev]"
pytest
```

---

## Platform matrix

| Platform | Milestone | Status |
|---|---|---|
| **Web app** (Next.js) | M5 | ✅ Complete |
| **pip / CLI** (`pip install clienthunter`) | M6 | ✅ Complete (this package) |
| **Mobile app** (Capacitor / Play Store) | M7 | 🔜 Next |

All three clients talk to the same cloud backend API.  No business logic is duplicated in any client.

---

## License

MIT © ClientHunter
