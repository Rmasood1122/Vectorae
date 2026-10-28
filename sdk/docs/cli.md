# Vectorae CLI Reference

The `clienthunter` CLI is installed by `pip install clienthunter`.  It is a
window into the cloud backend — all intelligence (research pipeline, outreach,
compliance) runs server-side.

```
clienthunter --help
```

---

## `clienthunter init`

Interactive first-time setup.  Prompts for API URL and credentials, verifies
connectivity with a health check, and writes `~/.clienthunter/config.toml`
(mode `0600`).

```
clienthunter init
```

**Example session:**

```
─── Vectorae — Setup ───────────────────────────────
Connect to the hosted cloud backend at clienthunter.ai? [Y/n]: Y

Backend API URL [https://api.clienthunter.ai]:
Authenticate with an API key? [y/N]: N
Email address: you@example.com
Password:

  Connecting to backend…

✓ Connected and authenticated.
✓ Config saved to ~/.clienthunter/config.toml (mode 0600).

Next steps:
  clienthunter run     — start the intake wizard
  clienthunter status  — view all strategies
```

---

## `clienthunter run`

Intake wizard: describe a product or skill, optionally add past clients, then
launch the research pipeline and watch it progress in real time.

```
clienthunter run [OPTIONS]
```

**Options:**

| Flag | Description |
|---|---|
| `--product-id UUID` | Use an existing product; skip creation |
| `--file FILE` | YAML or JSON file with product + past clients (non-interactive) |
| `--no-follow` | Fire-and-forget: exit after creating the strategy |
| `--flow TEXT` | Force `with_clients` or `no_clients` |

**Interactive example:**

```
─── Vectorae — New Campaign ────────────────────────
Product / skill name: Project Pulse
Description: Real-time project tracking for remote engineering teams
Type (product, skill) [product]:
Your email address: you@example.com

✓ Product saved: f3a8c1d2-…

Do you have past clients for this product/skill? [y/N]: y

Client #1
  Who were they?: Acme Corp, 80-person SaaS, Head of Engineering
  How did you acquire them?: Found via a LinkedIn post about remote work tools
  Add another client? [y/N]: N

✓ 1 past client(s) saved.
✓ Strategy created: 9b2e7f4a-… (72-step pipeline launching in the cloud)

Following pipeline (Ctrl-C to detach — pipeline keeps running)

╭─ Vectorae — Pipeline ────────────────────────────────────────╮
│ Strategy 9b2e7f4a-…  Flow: with_clients  Status: researching     │
│                                                                   │
│ STRATEGY Pipeline (9/72 steps)                                    │
│  1  Product Decomposition  ████████████████████  9/9  ✓          │
│  2  ICP Definition         ████░░░░░░░░░░░░░░░░  4/9  ↻          │
│  3  Market Sizing          ░░░░░░░░░░░░░░░░░░░░  0/9  …          │
│  …                                                                │
╰───────────────────────────────────────────────────────────────────╯
```

**`--file` YAML format:**

```yaml
name: My SaaS
description: Project management for remote engineering teams
type: product
user_email: you@example.com
past_clients:
  - details: Acme Corp, 80-person SaaS, Head of Engineering
    acquisition_story: Found via a LinkedIn post about remote work tools
  - details: Beta Ltd, 30-person agency, CTO
    acquisition_story: Referral from a mutual contact
```

---

## `clienthunter status`

Show pipeline progress, verification passes, and campaign stats.

```
clienthunter status [OPTIONS]
```

**Options:**

| Flag | Description |
|---|---|
| `--strategy UUID` | Detail view for one strategy |
| `--watch` / `-w` | Auto-refresh every ~8 seconds (Ctrl-C to stop) |
| `--json` | Machine-readable JSON output |

**Example — all strategies table:**

```
╭──────────────┬──────────────┬──────────────────┬──────────┬──────────╮
│ ID (prefix)  │ Flow         │ Status           │ Pipeline │ Verified │
├──────────────┼──────────────┼──────────────────┼──────────┼──────────┤
│ 9b2e7f4a…   │ with_clients │ executing        │  72/72   │   10/10  │
│ c1d4a8f2…   │ no_clients   │ researching      │  27/144  │    0/0   │
╰──────────────┴──────────────┴──────────────────┴──────────┴──────────╯
```

> **Note:** The all-strategies table requires `GET /strategies` to be added to
> the backend (see `backend_additions/strategies_list.py`).  Until deployed, use
> `--strategy UUID` for per-strategy detail.

**`--json` example:**

```bash
clienthunter status --strategy 9b2e7f4a --json | jq .campaign.meetings_booked
```

---

## `clienthunter serve`

Self-host the full backend stack on your machine via Docker Compose.

```
clienthunter serve [--detach]
clienthunter serve stop
clienthunter serve logs [SERVICE] [--tail N]
```

> ⚠️ **Self-hosted mode:** campaigns run **only while this machine is on**.
> For 24/7 operation, deploy the compose stack to a cloud server.
> See [docs.clienthunter.ai/deploy](https://docs.clienthunter.ai/deploy).

**Requirements:** Docker Desktop (Mac/Windows) or Docker Engine (Linux) must
be installed and running.  `clienthunter serve` checks this on startup and
exits clearly if Docker is missing — it never tries to install server
dependencies inside Python.

**Subcommands:**

```bash
clienthunter serve              # start (blocking; Ctrl-C to stop)
clienthunter serve --detach     # start in background; waits for health
clienthunter serve stop         # stop all containers
clienthunter serve logs         # tail logs from all services
clienthunter serve logs backend # tail logs from one service
clienthunter serve logs -n 200  # last 200 lines
```

---

## `clienthunter leads`

```
clienthunter leads list   --strategy UUID [--status STATUS] [--json]
clienthunter leads export --strategy UUID [--output FILE.csv]
```

**Status filter values:** `sourced`, `enriched`, `email_found`, `verified`,
`flagged`, `dropped`, `contacted`, `replied`, `meeting_booked`.

**Example — export verified leads to CSV:**

```bash
clienthunter leads export --strategy 9b2e7f4a --status verified --output leads.csv
```

---

## `clienthunter campaign`

```
clienthunter campaign pause  --strategy UUID [--yes]
clienthunter campaign resume --strategy UUID [--yes]
```

`pause` stops outreach immediately.
`resume` clears a manual **or** bounce-triggered pause — note that the backend
monitor will re-pause if the bounce rate remains above 3%.  Fix your lead
quality before resuming after a bounce pause.

---

## `clienthunter version`

```
clienthunter version [--json]
```

```
$ clienthunter version
clienthunter 0.1.0

$ clienthunter version --json
{"version": "0.1.0"}
```

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Error (API error, auth failure, compliance block, bad input) |
| Other | Propagated from `docker compose` (for `serve` subcommands) |

All errors are printed to **stderr**; structured output (`--json`) goes to
**stdout** so piping works cleanly.
