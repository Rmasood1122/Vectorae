# Vectorae agents

Five advisory agents with native cores, plus a master supervisor that runs and
watches them. Code: `app/agents/` (Python orchestration), `native/rust/`
(Rust, PyO3), `native/cpp/` (C++17, pybind11). API: `app/api/agents.py`.

| Agent | Core | What it returns |
|---|---|---|
| `lead_priority` | Rust `scoring.rs` | Leads ranked by who to work next, with an action: `respond_now`, `follow_up_now`, `start_sequence`, `nurture`, `rest`, `do_not_contact` |
| `followup_timing` | Rust `timing.rs` | The hours of the week (UTC) this account's prospects reply, and the next follow-up slot after a minimum gap |
| `deliverability_guard` | C++ `deliverability.cpp` | Per mailbox, last 30 days: bounce / opt-out / reply rates, risk level, recommended daily cap |
| `reply_triage` | C++ `triage.cpp` | Each recent reply's intent; replies it reads as an opt-out that the stored classification did not file as one, under `needs_review` |
| `claim_check` | C++ `claims.cpp` | Pending email drafts with risky wording: guarantees, superlatives, unsourced numbers, false urgency, spam triggers, `Re:`/`Fwd:` on a first touch |
| **master** (`supervisor`) | Rust `supervisor.rs` | Circuit breaker and health for each agent |

## What the agents may and may not do

Every agent is **advisory**. It reads one account's data and returns findings.
It never sends, schedules, pauses, suppresses, re-classifies or edits
anything. The send-time gates (suppression, guardrails, claim verification,
daily send approval, mailbox health) remain the only things that decide
whether a message goes out. `tests/test_agents_api.py` asserts that reply
triage re-files nothing and that the claim check leaves messages as they were.

Because they decide nothing, these are not legal/safety guards in the sense of
CLAUDE.md, and their tests are not in the sealed set.

**Tenant isolation.** Leads, messages and replies are read through
`app/agents/base.py::owned_lead_ids` (lead -> strategy -> product -> user);
mailboxes by `gmail_accounts.user_id`. Passing another account's
`strategy_id` returns no leads.

## The master supervisor

`app/agents/supervisor.py::run_agent` wraps every run:

1. Evaluates the agent's last 50 runs (all accounts) with the Rust
   `evaluate_agent`. After **3 consecutive failures** the circuit is **open**
   for **300 s** (the run is refused with 503); after that it is **half-open**
   and one trial run decides. A success closes it.
2. Times the run and writes an `agent_runs` row: agent, ok, latency, backend
   (`native` or `python`), item count, and on failure a 300-character error
   class. No request content.
3. Logs `agent_circuit_opened` at ERROR when a failure opens the circuit.

Health per agent is `idle`, `healthy`, `degraded` (half-open, smoothed error
rate over 30%, or p95 latency over 5 s) or `down` (open). The master's status
is the worst of the five. A malformed parameter (422) is not recorded and
cannot trip the breaker.

Health is system-wide and carries only counts, rates and latencies, so every
signed-in account sees the same agent health.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/agents` | Master status, then each agent's language, backend and health |
| POST | `/agents/{name}/run` | Body `{"params": {...}}`. 404 unknown agent, 422 bad parameter, 503 circuit open, 502 agent failed. Rate limit `RATE_LIMIT_AGENT_RUN` (120 per user per hour) |
| POST | `/agents/claim_check/check` | Body `{"subject", "body", "first_touch"}`. Checks one text, touches no data |

Parameters: `lead_priority` takes `strategy_id` and `limit` (1-200, default
50); `followup_timing` takes `min_gap_hours` (0-336, default 48) and
`business_only` (default true); `reply_triage` and `claim_check` take `limit`
(1-500, default 200).

## Native cores and the Python reference

`app/agents/native.py` imports `vectorae_native_rs` and `vectorae_native_cpp`.
If either is missing it uses `app/agents/reference.py`, the same arithmetic
in Python. That fallback exists because the sealed eval workflow installs
`requirements.txt` only. Production never uses it: the Docker image builds both
wheels in a `native-builder` stage and sets `VECTORAE_REQUIRE_NATIVE=1`, which
makes a missing module an import error at boot.

The reference is also the oracle. `tests/test_agents_native_parity.py` runs
both on thousands of generated inputs and requires equal answers and identical
phrase tables. To keep them equal, text handling is ASCII-only in both, and
rounding is `floor(x*10 + 0.5)/10` everywhere, never Python's banker's
`round()`.

**Changing an algorithm or a phrase list means changing it in the native code
and in `reference.py` together**, or the parity tests fail.

## Build and test

```bash
cd native/rust && cargo test --locked                  # Rust core
cd native/cpp && cmake -S . -B build && cmake --build build && ctest --test-dir build
pip install ./native/rust ./native/cpp                 # the Python modules
VECTORAE_REQUIRE_NATIVE=1 python -m pytest tests/test_agents_*.py
```

CI: the `verify-native-agents` job in `.github/workflows/verify.yml` runs all of
the above. `verify-image` builds the production image and imports both modules
inside it.

## Data

Migration `0092_agent_runs` creates `agent_runs` (user_id ON DELETE CASCADE).
It is in `account_export.OWNERSHIP` and `account_erasure.DELETED`.
