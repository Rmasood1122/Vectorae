# Vectorae Python SDK Reference

```python
pip install clienthunter
from clienthunter import ClientHunter
```

---

## `ClientHunter(...)` — client constructor

```python
ch = ClientHunter(
    api_url="https://api.clienthunter.ai",   # or CLIENTHUNTER_API_URL env var
    api_key="sk-...",                         # or CLIENTHUNTER_API_KEY env var
    access_token="...",                       # JWT; stored in config file after login
    refresh_token="...",
    timeout=30.0,       # request timeout in seconds
    max_retries=3,      # retries on 429 / 5xx with exponential back-off
)
```

Config resolution order: constructor args → env vars → `~/.clienthunter/config.toml` → defaults.

---

## `ch.auth`

### `ch.auth.login(email, password) → TokenPair`
Authenticate and store tokens.  Tokens are written to `~/.clienthunter/config.toml` (`0600`).

### `ch.auth.signup(email, password) → TokenPair`
Create an account and receive tokens.

### `ch.auth.refresh(refresh_token=None) → TokenPair`
Exchange a refresh token for a new pair.  Called automatically on 401 responses.

### `ch.auth.me() → UserOut`
Return the currently authenticated user (`id`, `email`, `plan`).

---

## `ch.products`

### `ch.products.create(name, description, type, user_email) → Product`
Create a product or skill entry (intake Step 1).
- `type`: `"product"` or `"skill"`

### `ch.products.get(product_id) → Product`

### `ch.products.add_past_clients(product_id, clients) → list[PastClient]`
Submit past clients for Flow 1 (intake Step 2, "yes" path).
- `clients`: list of `{"details": "...", "acquisition_story": "..."}`
- Min 1, max 50 entries
- Claude extracts patterns (industry, channel, trigger event, deal size) into `extracted_patterns_json`

---

## `ch.strategies`

### `ch.strategies.create(product_id, flow_type=None) → Strategy`
Create a strategy and enqueue the research pipeline (HTTP 202).
- `flow_type`: `"with_clients"` (72 steps) or `"no_clients"` (144 steps).  Auto-inferred if omitted.
- Returns `Strategy` with `status="pending"`.  Pipeline runs in the cloud immediately.

### `ch.strategies.get(strategy_id) → StrategyStatus`
Full status including per-phase step counts and verification pass results.

### `ch.strategies.progress(strategy_id) → StrategyStatus`
Alias for `get()`.  Preferred name in progress-polling loops.

### `ch.strategies.list(product_id=None) → list[StrategyStatus]`
All strategies, optionally filtered by product.
> **Requires** `GET /strategies` backend endpoint — see `backend_additions/strategies_list.py`.

**Polling pattern:**
```python
import time
while True:
    s = ch.strategies.progress(strategy_id)
    if s.status in ("verified", "failed", "needs_human_review"):
        break
    print(s.status, [(p.pipeline, p.done, p.total) for p in s.progress])
    time.sleep(10)
```

---

## `ch.leads`

### `ch.leads.list(strategy_id, status=None, limit=100, offset=0) → LeadList`
Paginated lead list.
- `status`: filter by lead status string (e.g. `"verified"`, `"contacted"`, `"meeting_booked"`)
- Returns `LeadList` with `total`, `limit`, `offset`, `items: list[Lead]`

### `ch.leads.get(strategy_id, lead_id) → LeadDetail`
Full lead with enrichment JSON (Apollo payload).

### `ch.leads.source(strategy_id, max_leads=None, icp_criteria=None) → LeadBatch`
Kick off a new Apollo sourcing batch.  Returns immediately; leads appear asynchronously.

---

## `ch.campaigns`

### `ch.campaigns.overview(strategy_id) → CampaignOverview`
Real-time campaign snapshot: leads by status, sends, reply rate, bounce rate, meetings booked.

### `ch.campaigns.stats(strategy_id) → CampaignOverview`
Alias for `overview()`.

### `ch.campaigns.analytics(strategy_id, granularity="day") → Analytics`
Time-series outcomes.  `granularity`: `"day"`, `"week"`, `"month"`.

### `ch.campaigns.pause(strategy_id) → CampaignStateUpdate`
Stop outreach immediately.

### `ch.campaigns.resume(strategy_id) → CampaignStateUpdate`
Resume.  After a bounce-rate pause, fix lead quality first.

---

## `ch.whatsapp`

### `ch.whatsapp.list_templates() → list[WhatsAppTemplate]`
### `ch.whatsapp.get_template(template_id) → WhatsAppTemplate`
### `ch.whatsapp.create_template(name, language, body, category, variable_descriptions) → WhatsAppTemplate`
Creates a DRAFT.  Must be submitted for Meta review before it can be sent cold.
### `ch.whatsapp.submit_template(template_id) → WhatsAppTemplate`
Submit for Meta review.  Only `APPROVED` templates reach cold contacts.

---

## `ch.integrations`

### `ch.integrations.gmail_status(user_email) → GmailStatus`
### `ch.integrations.gmail_auth_url(user_email) → str`
Returns the OAuth URL to open in a browser.

---

## `ch.ping() → bool`
Health check — returns `True` if the backend is reachable.

---

## Exception hierarchy

```
ClientHunterError
├── AuthError             # 401 / 403; run clienthunter init to re-authenticate
├── NotFoundError         # 404
├── ValidationError       # 422 (field errors); .detail has the raw error structure
├── ComplianceError       # 422 (compliance block)
│       .rule             # "gdpr" | "can_spam" | "whatsapp_cold_freeform" | ...
│       .detail           # full explanation
│       .remediation      # plain-language fix (populated for known rules)
├── RateLimitError        # 429 (after all retries); .retry_after in seconds
└── APIError              # other HTTP errors or network failures
        .status_code      # HTTP status (None for network errors)
        .detail           # error body
```

```python
from clienthunter import ComplianceError, AuthError

try:
    ch.strategies.create(product_id=some_id)
except ComplianceError as e:
    print(f"Rule: {e.rule}")
    print(f"Fix: {e.remediation}")
except AuthError:
    print("Token expired — run `clienthunter init` to re-authenticate")
```

---

## Models reference

All models are frozen Pydantic v2 dataclasses.

| Model | Returned by |
|---|---|
| `Product` | `ch.products.create`, `.get` |
| `PastClient` | `ch.products.add_past_clients` |
| `Strategy` | `ch.strategies.create` |
| `StrategyStatus` | `ch.strategies.get`, `.progress`, `.list` |
| `PhaseProgress` | inside `StrategyStatus.progress[*].phases` |
| `Lead` | inside `LeadList.items` |
| `LeadDetail` | `ch.leads.get` |
| `LeadList` | `ch.leads.list` |
| `LeadBatch` | `ch.leads.source` |
| `CampaignOverview` | `ch.campaigns.overview`, `.stats` |
| `Analytics` | `ch.campaigns.analytics` |
| `WhatsAppTemplate` | `ch.whatsapp.*` |
| `TokenPair` | `ch.auth.login`, `.signup`, `.refresh` |
| `UserOut` | `ch.auth.me` |
| `GmailStatus` | `ch.integrations.gmail_status` |
| `CampaignStateUpdate` | `ch.campaigns.pause`, `.resume` |
