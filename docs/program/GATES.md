# Vectorae gates — what is enforced, where, and what is not

Phase 1 exit-gate record. Last updated 2026-10-10. Repo: private, GitHub Free
(no branch protection or rulesets: `DECISIONS.md` GH-PRO). Merge policy:
`DECISIONS.md` MERGE-B.

## 1. The layers

| # | Layer | Where it runs | Blocks | Can be bypassed by |
|---|---|---|---|---|
| 1 | `scripts/gate/quick_gate.sh` (~20 s) + `.githooks/pre-push` | the pusher's machine or cloud session | pushes to master/main; unstamped commits; leaked keys, syntax errors, 2 Alembic heads, failing related/sealed tests, TS errors | `git push --no-verify`; not installing the hook |
| 2 | Claude Code hooks (`.claude/settings.json`) | every Claude Code session on this repo | `gh pr merge`, merge API calls, push to master, `--no-verify`, unstamped push, sealed-file edits off `gate/` branches, ending a turn with unchecked code (once) | a human not using Claude Code |
| 3 | CI: `eval-gate` (`gate`) + `verify` (4 jobs) | GitHub Actions | nothing by itself — it reports | — |
| 4 | `scripts/gate/safe_merge.sh` | whoever merges | merges unless every check is green, PR not draft, up to date with master | the GitHub web UI Merge button |
| 5 | Deploy gating (Render "After CI Checks Pass", Vercel Deployment Checks) | Render / Vercel | deploying a commit whose checks failed | Vercel "Force Promote"; manual Render deploy of a red commit |
| 6 | `post-merge-alarm.yml` | GitHub Actions | nothing — opens a `master-broken` issue | — |

**What is NOT enforced (plainly):** a human can still merge from the GitHub
web UI, unreviewed and with red CI. That happened three times on 2026-10-10
(INC-43, INC-44/45; master went red). Only layer 5 stops such a merge from
reaching production, and layer 6 makes it visible. Rule for humans: merge only
with `scripts/gate/safe_merge.sh <PR>` (ask a Claude Code cloud session to run
it if your machine has no `gh`).

## 1b. Brand lint (added 2026-10-20, Vectorae program Phase 1)

`scripts/gate/brand_lint.py` fails on any previous product name (the
three it blocks are listed in the script; any case, with or without a
separator) in a git-tracked file or file name that `.brand-allowlist` does
not cover. Each
allowlist entry is `<path glob> | <regex> | <B: or C:> <reason>`; an entry
that allows nothing is itself an error. It runs in two places:

- the quick gate (`brand` step, every push);
- the offline suite (`tests/test_brand_lint.py::test_the_repository_has_no_old_name_outside_the_allowlist`),
  so the CI `gate` job fails when an old name comes back. The workflows are
  sealed, so it rides the existing suite rather than a new workflow step.

## 2. Required checks

`scripts/gate/required_checks.txt` is the list `safe_merge` requires on the PR
head. Today: `gate`. Independently of that list, `safe_merge` refuses if
**any** check on the head failed or is still running, so the `verify-*` jobs
already block merges whenever they run red.

To be added to the list once each has run green on a push to `master` (item
1.7 follow-up, its own PR): `verify-backend-postgres`, `verify-frontend`,
`verify-image`, `verify-security`. Until then a PR that somehow lacks them is
not refused for their absence.

## 3. Proof that every gate fires (seen-it-fire)

| Gate | Proof | Result |
|---|---|---|
| `gate` (offline suite, compliance, test count) | `evals/fixtures/verify_block_fixtures.py` + CI live-fire step (every `eval-gate` run) | BLOCK on each hard metric; re-measured in PR #49 |
| `verify-backend-postgres` | sabotage PR #53: a second Alembic head — [run](https://github.com/Rmasood1122/LeadPilot/actions/runs/38056888636/job/114227097710) | failed at "Single Alembic head" |
| `verify-frontend` | PR #53: a TypeScript type error — [run](https://github.com/Rmasood1122/LeadPilot/actions/runs/38056888636/job/114227097695) | failed at "Type-check" (after `npm ci` passed with the #50 lockfile) |
| `verify-image` | PR #53: Celery app imports a missing module — [run](https://github.com/Rmasood1122/LeadPilot/actions/runs/38056888636/job/114227097555) | failed at "Every application module imports inside the image" |
| `verify-security` | PR #53: DB URL with a password to `db.sabotage.example.com` — [run](https://github.com/Rmasood1122/LeadPilot/actions/runs/38056888636/job/114227097812) | failed at "Secrets in added lines (blocking)" |
| `safe_merge` | `tests/test_safe_merge.py` (22) + live dry-run on PR #53 | REFUSED: 2× `gate` failed, 4 verify jobs failed, Vercel pending |
| Claude hooks | `tests/test_claude_hooks.py`, `tests/test_gate_scripts.py` | 76 tests |

**The proof found a real bug.** On the first sabotage run
([run](https://github.com/Rmasood1122/LeadPilot/actions/runs/38056519971/job/114226051097))
`verify-security` passed: the secret scanner excluded the compose host `db`
with `db\b`, which also matched `db.sabotage.example.com`. Fixed in PR #51
(commit "secret scanner flagged nothing for hosts like db.example.com") with
regression tests; the second run above failed as it should.

PR #53 was closed unmerged. Its branch `gate/sabotage-verify` could not be
deleted from this session (the proxy refuses ref deletion) — **founders: delete
it in GitHub → Branches.**

## 4. Founder actions — deploy gating (layer 5)

These are dashboard settings; nothing in the repo can set them.

### Render (both services: `leadpilot-api`, `leadpilot-worker`)
Auto-deploy is currently **off** on both (Phase 0). Turn it on in the gated mode:
1. dashboard.render.com → open `leadpilot-api` → **Settings**.
2. **Build & Deploy** → **Auto-Deploy** → choose **After CI Checks Pass**. Save.
3. Repeat for `leadpilot-worker`.
Render then deploys a `master` commit only when all its GitHub checks pass
(source: render.com/changelog/skip-auto-deploying-if-ci-checks-fail).
UNVERIFIED: whether this option is offered on Render's free instance type; if
it is missing, keep auto-deploy off and deploy manually only commits whose
checks are all green on GitHub.

**Before the first deploy after #46:** migration 0071 will run on Neon. Run the
read-only query in `docs/program/plans/phase1-01b-pd1-webhook-schema.md` first.

### Vercel (project `lead-pilot`, team `ahmad-b2-b` — Ahmad's account)
1. vercel.com → project → **Settings → Environments → Production**: confirm
   automatic aliasing for production is on (Deployment Checks prerequisite).
2. **Settings → Build and Deployment → Deployment Checks → Add Checks →
   GitHub** → select `verify-frontend` (and `gate`). Save.
Vercel then builds every production deployment but assigns it to the custom
domain only after those checks pass (source: vercel.com/docs/deployment-checks).
UNVERIFIED: whether Deployment Checks is available on the team's current
Vercel plan. "Force Promote" bypasses it — don't.

## 5. Actions minutes (GitHub Free: 2,000 / month)

Measured so far (2026-10-10):

| Job | Billed when it really runs | When skipped by scope |
|---|---|---|
| `gate` (eval-gate) | ~9 min (8 m 52 s on run 38038727255) | always runs |
| `verify-backend-postgres` | not yet measured end to end in CI (local: 2 m 50 s for the suite + install) | < 1 min |
| `verify-frontend` | not yet measured end to end (sabotage run stopped at type-check, 27 s) | < 1 min |
| `verify-image` | not yet measured end to end | < 1 min |
| `verify-security` | ~1 min (40 s) | — |

Since #47: one `gate` run per PR commit (was two). Drafts run `gate` only.
Projection for a typical PR (2 pushes, backend-only): 2 × (9 + ~6 + 1 + 1 + ~4)
≈ 42 min → ~45 PRs/month. Frontend-only PRs are cheaper. The real per-job
numbers replace the estimates after the first full `verify` run on `master`.

## 6. Install once per clone (humans)

```bash
scripts/gate/install_hooks.sh    # pre-push runs the quick gate
scripts/gate/quick_gate.sh       # before every push
scripts/gate/full_gate.sh        # before marking a PR ready
scripts/gate/safe_merge.sh <PR>  # the only way to merge
```
