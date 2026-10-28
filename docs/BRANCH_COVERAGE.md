# Vectorae — Branch Coverage Matrix

Supporting document for `docs/FULL_PRODUCT_AUDIT.md`. Audit date 2026-10-01.
Full-history clone; default branch `master`.

## Method / evidence

For every remote branch the auditor computed: tip SHA, last-commit date/subject,
total commit count, ahead/behind vs `master` (`git rev-list --left-right
--count origin/master...origin/<b>`), the list of commits unique to the branch
(`git log origin/master..origin/<b>`), the merge-base with `master`, and the
file-level diff where it mattered (`git diff --name-status`). This is the evidence
behind the claim of complete branch coverage.

## Matrix

| Branch | Tip SHA | Last commit (date · subject) | Total commits | Behind / Ahead vs master | Unique commits | Unique content | Audited |
|---|---|---|---|---|---|---|---|
| **master** | `1ad2cab` | 2026-09-30 · *report(LP-T-002): tenancy/authz audit* | 46 | 0 / 0 | — | Release gate (`evals/`, `.github/workflows/eval-gate.yml`); `reports/LP-T-002`, `LP-T-004` | **Primary** |
| **feature/8-new-features** | `5705e81` | 2026-09-27 · *Fix raw-SQL UUID bind in /playbook/scores* | 81 | 4 / **39** | 39 | All P0 fixes; 12 features; migrations 0048–0064; `docs/audit-fixes/*`, `docs/features/*`. **No `evals/`, no CI.** | **Primary** |
| **vercel/install-and-configure-vercel-s-xnm1g4** | `15dc632` | 2026-09-16 · *Install and configure Vercel Speed Insights* | 59 | 4 / 17 | 1 unique vs feature/8 | Snapshot of feature/8 line up to `038782c` **+** one unique commit: Vercel Speed Insights (frontend telemetry) | Unique item noted; code ⊂ feature/8 |
| **claude/confident-franklin-lv1our** | `9c316f9` | 2026-09-26 · *Add 7-day paid-client LinkedIn sprint plan* | 43 | 4 / 1 | 1 | `docs/ONE_WEEK_CLIENT_SPRINT.md` — business plan, **no product code** | Noted; no code |
| **feature/engagement-hub** | `92f12a2` | 2026-09-07 · *Fixed* | 29 | 17 / **0** | **0** | Ancestor of master — fully subsumed | Confirmed subsumed |
| **feature/tool-integrations** | `98c666b` | 2026-09-13 · *Add sitemap.xml and robots.txt for SEO* | 35 | 11 / **0** | **0** | Ancestor of master — fully subsumed | Confirmed subsumed |

merge-base(`master`, `feature/8-new-features`) = `847e103` (2026-09-13).
merge-base(`vercel`, `feature/8-new-features`) = `038782c` (the vercel branch is
the feature/8 line truncated at that commit, plus the Vercel commit).

## feature/8-new-features: scale of divergence

`git diff --stat origin/master origin/feature/8-new-features`:
**306 files changed, 34,068 insertions(+), 2,407 deletions(-)** — 181 files added,
103 modified, 22 deleted. New backend surface includes
`app/api/{attribution,benchmarks,client_workspaces,compliance_rules,consent,inbox,practice,recording,reengagement,send_reviews}.py`,
matching services, `app/integrations/recall.py`, `app/workers/benchmark_tasks.py`,
and migrations `0048_reengagement` … `0064_meeting_practice`.

## Key structural facts (evidence-backed)

1. **The P0 remediation work exists only on `feature/8-new-features`.** Verified:
   `docs/audit-fixes/P0-*.md` present only there; `billing.py:82` prod guard,
   `auth.py:207` suspension check, `outreach_tasks` atomic claim, and the
   `admin.py` playbook-scores rewrite are present on feature/8 and absent on
   master (`git show` on each path, both branches; `git branch -r --contains
   6e11abe` → feature/8 only).
2. **The release-gate infrastructure exists only on `master`.** `evals/` has 17
   files on master, **0** on feature/8; `.github/workflows/eval-gate.yml` is on
   master, absent on feature/8.
3. **The LP-T-002 tenancy findings apply to both branches.** The vulnerable code
   (`webhooks_whatsapp.py` phone match, `onboarding.py` `body.user_id`,
   `ui_support.py` global suppression) is present on both.

## Runtime verification per branch

| Branch | Offline collection | Offline run | Gate (`evals/run_evals.py`) |
|---|---|---|---|
| master | 2,114 under `tests/` | gate: **1,985 / 100% / exit 0**; raw `pytest tests/`: 9 order-dependent WhatsApp failures + 124 integration errors (both explained in audit §10–11) | **PASS** |
| feature/8-new-features | **2,834** offline (`--ignore=tests/integration`) | **2,827 passed, 7 skipped, 0 failed, exit 0** (477s) | **N/A — no `evals/` on this branch** |

**feature/8 offline run:** executed via `pytest tests/ --ignore=tests/integration`
in an isolated worktree → **2,827 passed, 7 skipped, 0 failed (exit 0)**. The
WhatsApp order-dependent failures seen in master's *raw* `pytest tests/` run did
**not** appear here because the integration dir was excluded (matching the gate's
approach), which corroborates F-QA-01 (the flakiness is triggered by co-collecting
`tests/integration/`, not by the offline suite itself). The branch has **no
release-gate adapter**, so this was a plain pytest run — the fail-closed scoring,
sealed-file and test-count floor protections that `master`'s `evals/run_evals.py`
provides do **not** exist on feature/8.
