# CLAUDE.md — Vectorae

Rules for any Claude Code session working in this repository. The governing
project records (gap register, requirements, evidence index) live in the
"LeadPilot Production Readiness" Claude Project, not in this repo.

## What this product is
AI outbound/GTM system (FastAPI + SQLAlchemy/PostgreSQL + Celery/Redis +
Anthropic pipeline; Next.js frontend; Android app; Python SDK). Revenue
product with a **zero-tolerance legal-compliance posture** (CAN-SPAM / GDPR /
CASL / PDPA). Self-hosted, single-tenant per P0-D (`docs/program/DECISIONS.md`) — cross-tenant bugs have shipped before from
ad-hoc lookups (`app/services/notifications.py:300`); never query tenant-owned
rows without an ownership filter.

## Run the tests and the gate (before claiming anything works)
```bash
pip install -r requirements.txt fakeredis pyyaml pytest pytest-asyncio pytest-mock
python3 evals/tools/registry_lint.py evals/registry.yaml   # schema lint
python3 evals/run_evals.py                                  # full offline suite (3,319 tests, ~9 minutes, junit-scored)
python3 evals/tools/promote.py                              # gate: candidate vs baseline (exit 0 = PROMOTE)
python3 evals/fixtures/verify_block_fixtures.py             # seen-it-fire: every hard gate proven to block
```
The suite is offline by design: SQLite in-memory, FakeClaude, zero network.
Keep new tests deterministic and offline, or they don't belong in the hard gate.
Merge policy B (`docs/program/DECISIONS.md` MERGE-B): merge to `master` only via
`scripts/gate/safe_merge.sh <PR> [--dry-run]` (all required checks in
`scripts/gate/required_checks.txt` green, PR not a draft, up to date, squash);
co-founder review follows the merge. Never merge from the web UI or with `gh pr merge`.
Before every push: `scripts/gate/quick_gate.sh` (stamps the commit; the pre-push hook
and Claude hooks require the stamp). Once per clone: `scripts/gate/install_hooks.sh`.
Before marking a PR ready: `scripts/gate/full_gate.sh` (runs what CI runs).

## Sealed files — do not touch inside a feature task
`evals/registry.yaml`, `evals/compliance_testset.yaml`, `evals/baseline.json`,
`.github/workflows/eval-gate.yml`, and the five sealed compliance test files
listed in `evals/compliance_testset.yaml`. A threshold, seal, or baseline
change is its own reviewed PR with a written justification — never a side
effect of making something pass. Never weaken or delete a test to go green.
New legal/safety guard test files are added to the sealed list **in the same
commit** that introduces them.

## Definition of done (all ten, every task)
Behavior defined · implementation addresses it · tests exist/updated · tests
**actually executed** · results reviewed · regression considered · security
considered · integration/deploy considered · evidence recorded · no silent
unresolved dependency. Generated code is not a fix; "looks right" is not
evidence.

## Evidence package every task returns
1. Changed files (path + purpose). 2. Commands executed verbatim with exit
codes. 3. Test output (counts, failures, runtime). 4. `promote.py` result.
5. Pre-fix failing state of new tests (or why it can't be shown).
6. Remaining failures / limitations. 7. Out-of-scope discoveries — **reported,
not fixed**. 8. Commit SHA + CI run URL.
Missing items ⇒ the work is NOT VERIFIED.

## Scope
Solve the specified requirement only. Unrelated problems get reported for
triage, not fixed in passing. Inspect the repository before modifying —
never assume paths, schemas, or functions.

## Known stale spots (do not trust these at face value)
Corrected under GAP-106: `LAUNCH_CHECKLIST.md` §2 used to claim an 11-migration chain
with a head near 0011. It no longer hard-codes a count or head; the chain's head is
whatever `alembic heads` reports (tests/test_docs_migration_claims.py fails if a
doc names a revision that does not exist).

Still open, and NOT a stale-domain error: `calendarharvest.com` is the live
production domain and the configured from-address (render.yaml, app/config.py,
scripts/activate_with_keys.py), so GAP-007's premise that it is wrong does not
hold. What is unverified is whether it is verified in Resend with SPF/DKIM
(docs/PRODUCTION_READINESS.md still lists that as NEEDS ACTION). That is a
founder-side check, tracked under GAP-007.
