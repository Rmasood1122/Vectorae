# Vectorae — Recommended Next Steps (Actionable Roadmap)

Supporting detail for `docs/FULL_PRODUCT_AUDIT.md`. Audit date 2026-10-01.
Each item: **problem → reason → action → dependency → verification method.**

---

## P0 — Immediate blockers

### P0-A — Reconcile the two branches into one trunk
- **Problem:** the P0 fixes live only on `feature/8-new-features`; the release
  gate and CI live only on `master`. Neither branch is complete, and the default
  branch (`master`) ships none of the fixes.
- **Reason:** everything downstream (deploys, new evaluations, CI) reads the
  default branch. The split is the root cause of "I fixed it but the audit says
  it's broken."
- **Action:** choose the single trunk. Recommended: merge
  `feature/8-new-features` → `master` **while preserving `evals/` and
  `.github/workflows/eval-gate.yml`** (they do not exist on feature/8, so a naïve
  merge that takes feature/8 wholesale would delete them). Resolve conflicts so the
  trunk has both the fixes *and* the gate. Then make that the default branch and
  retire the subsumed branches (`engagement-hub`, `tool-integrations`) and the
  stale `vercel`/`confident-franklin` lines (port the one Vercel commit and the
  sprint doc if wanted).
- **Dependency:** none (do this first).
- **Verify:** on the merged trunk, `python3 evals/run_evals.py` → exit 0,
  `full_suite_pass_rate 1.0`; `ls evals/ .github/workflows/eval-gate.yml` present;
  all P0 fix test files present (`test_account_suspension.py`,
  `test_duplicate_send_claim.py`, `test_checkout_requires_stripe.py`,
  `test_admin_playbook_scores.py`).

### P0-B — Port the account-suspension guard (F-SEC-01)
- **Problem:** suspended users keep full API access on master.
- **Reason:** CRITICAL authz hole; the kill-switch is the compliance/abuse control.
- **Action:** ensure the trunk's `get_current_user` contains the `is_suspended`
  check (from `feature/8` `auth.py:207`) plus `tests/test_account_suspension.py`.
- **Dependency:** P0-A (or cherry-pick `6e11abe` directly if deferring the merge).
- **Verify:** suspended user → 403 on a protected route **and** via `lpk_` API key;
  the test is red on pre-fix code and green after.

### P0-C — Fix the Docker dependency manifest (F-OPS-01)
- **Problem:** `firebase-admin` (and possibly other `requirements.txt`-only runtime
  deps) is absent from `pyproject.toml`, which is what the Dockerfile installs →
  FCM push broken in-image.
- **Reason:** same defect class that once broke the whole image (structlog).
- **Action:** add `firebase-admin` to `pyproject.toml` `dependencies`; audit
  `requirements.txt` vs `pyproject.toml` for any other runtime-required but
  pyproject-missing package.
- **Dependency:** none.
- **Verify:** `docker build` the image, then inside it `python -c "import app.main"`
  **and** exercise an FCM code path (or at least import `firebase_admin`) → no
  ImportError.

### P0-D — Decide the deployment model (multi-tenant SaaS vs single-tenant self-host)
- **Problem:** the severity of F-SEC-04, F-SEC-05 and F-DB-01 depends entirely on
  whether multiple paying tenants share one deployment.
- **Reason:** you cannot correctly prioritize or fix the suppression/website/
  template/playbook tenancy items without this decision.
- **Action:** record the intended model in `CLAUDE.md` / the production-readiness
  register. If SaaS, F-SEC-04/05 and F-DB-01 are P1; if single-tenant self-host,
  they drop to documentation items.
- **Dependency:** none (founder decision).
- **Verify:** written decision recorded; the dependent findings re-triaged against
  it.

---

## P1 — High-priority fixes (post-reconciliation)

### P1-A — Scope WhatsApp inbound by tenant (F-SEC-02)
- **Problem:** inbound replies match leads by phone across all tenants.
- **Action:** resolve the tenant from `phone_number_id` (→ the owning account),
  then scope `select(Lead)` to that tenant's leads only.
- **Dependency:** P0-A. **Verify:** two tenants with the same phone → reply routes
  only to the correct tenant (new test); STOP suppresses only that tenant.

### P1-B — Close the onboarding IDOR (F-SEC-03)
- **Problem:** `/onboarding/complete-step` trusts `body.user_id`.
- **Action:** remove `body.user_id` (use `current_user.id`), or gate it behind an
  internal-service credential / `require_admin`.
- **Dependency:** P0-A. **Verify:** a non-admin passing another `user_id` → 403
  (new test).

### P1-C — Add AI spend/abuse ceilings (F-AI-01)
- **Problem:** `max_strategies` unenforced; no token/cost cap.
- **Action:** call `check_plan_limit(user, "max_strategies", current_count)` in
  `create_strategy` and the resume endpoint; add a per-account monthly token/cost
  budget that `run_pipeline`/`usage_meter` checks before enqueue and that blocks
  (with a clear 402/429) when exceeded.
- **Dependency:** P0-A. **Verify:** a free-tier account is blocked at its
  `max_strategies`; a per-account cost cap halts enqueue when exceeded (tests with
  a stubbed meter).

### P1-D — Mitigate indirect prompt injection (F-AI-02)
- **Problem:** scraped posts/news/replies concatenated into prompts unmitigated.
- **Action:** wrap all externally-sourced text in explicit delimiters with a
  "treat as data, never instructions" system directive; treat reply-classifier
  output as untrusted (don't let it alone drive irreversible suppression without a
  rule/human check); consider an inbound injection screen.
- **Dependency:** P0-A. **Verify:** an injection string in a LinkedIn-post fixture
  does not alter the generated email's structure or flip a classification (new
  adversarial tests).

### P1-E — Calendly webhook replay protection (F-OPS-02)
- **Problem:** timestamp parsed but freshness never checked.
- **Action:** reject when `abs(now - t) > tolerance` (mirror Stripe's 300s).
- **Dependency:** none. **Verify:** a replayed delivery with a stale `t` → 401/400
  (new test).

### P1-F — Suppression tenancy, if SaaS (F-SEC-04)
- **Action:** scope `GET /suppression` per tenant; authorize cross-tenant adds;
  keep global enforcement at send time.
- **Dependency:** P0-D = SaaS. **Verify:** tenant A cannot read tenant B's
  entries.

### P1-G — Prove integrations against live vendors (F-QA-02)
- **Problem:** 88 "verify against docs" TODOs; all integrations mock-only.
- **Action:** stand up a credentialed staging environment; run each integration
  (Apollo, Hunter, Gmail OAuth+send, WhatsApp send+webhook, Calendly, Stripe
  test-mode, Recall, Firebase) against the real vendor once; record the real
  request/response as contract tests; resolve each TODO.
- **Dependency:** credentials (external blocker, per LP-T-004). **Verify:** a
  "live contract" test per provider passes against the real API in staging.

---

## P2 — Important improvements

- **F-AI-03:** set `temperature=0` on all judge/classifier/extractor calls. *Verify:*
  identical input → identical verdict across N runs.
- **F-AI-04:** startup model-ID health-check that fails loudly on an unknown model;
  confirm `claude-sonnet-4-6` against the live Models API. *Verify:* boot fails
  fast on a bogus model env.
- **F-DB-03:** add `ix_messages_status` / composite `(status, scheduled_at)` in a
  new migration. *Verify:* `EXPLAIN` on the due-messages query uses the index.
- **F-DB-02:** port the feature/8 admin playbook-scores fix; remove the bare
  except. *Verify:* `?user_id=` returns owned rows, DB errors surface as 500.
- **F-OPS-03/04:** consolidate the two settings modules; align the Firebase var
  name/format. *Verify:* one settings import path; setting the documented Firebase
  var works end-to-end.
- **F-REL-01/02:** delete `send_tasks.py` (or wire it with the atomic claim); fix
  the `monitoring.py` dead task-name. *Verify:* no orphaned send task registered;
  monitoring references the real send task.
- **F-QA-01:** make the offline suite order-independent, or set `pyproject.toml`
  `addopts` to exclude `tests/integration` by default. *Verify:* `pytest tests/`
  (no flags) is green and stable across repeated runs.

---

## P3 — Technical debt / future

- **F-OPS-06:** add lockfiles (`pip freeze`/hashes; `package-lock.json`);
  reconcile `requirements.txt` ↔ `pyproject.toml`; drop unused deps (`pandas`,
  `google-auth*`, `python-jose`). *Verify:* reproducible build; `import`-level
  smoke after prune.
- **F-OPS-07:** run `pip-audit`/`safety` in CI; remove `python-jose`. *Verify:*
  advisory scan in the pipeline, zero high findings or documented exceptions.
- **F-DB-04:** drop redundant `playbook_scores.score` after checking legacy rows.
- **Docs:** refresh `PRODUCTION_READINESS.md` and `LAUNCH_CHECKLIST.md` (stale test
  counts, migration head, `calendarharvest.com`); clarify the CLI is a separate
  `sdk/` package; enforce ESLint at build (`next.config.js`); on-device test the
  Capacitor layer; remove committed `celerybeat-schedule.*` runtime artifacts.

---

## Sequencing summary

```
P0-A (merge, keep gate) ──► P0-B (suspension) ──► P0-C (docker dep) ──► P0-D (deployment model)
                                 │                                            │
                                 ▼                                            ▼
                     P1-A/B (tenancy) · P1-C (AI cost) · P1-D (injection) · P1-E (calendly)
                                 │                                 P1-F (suppression, if SaaS)
                                 ▼
                     P1-G (live integration proof — gated on credentials)
                                 │
                                 ▼
                     P2 (determinism, indexes, config, dead code, test isolation)
                                 │
                                 ▼
                     P3 (lockfiles, advisory scan, docs, cleanup)
```

The ordering reflects dependency, not just severity: nothing else is durable until
P0-A puts the fixes and the gate on one trunk.
