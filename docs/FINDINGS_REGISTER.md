# Vectorae — Findings Register

Supporting detail for `docs/FULL_PRODUCT_AUDIT.md`. Audit date 2026-10-01.
Severity (impact) and confidence (evidence strength) are rated **independently**.
Confidence: CONFIRMED (path read/executed end-to-end) · LIKELY · POTENTIAL ·
UNVERIFIED.

Branches audited: `master` @ `1ad2cab`, `feature/8-new-features` @ `5705e81`.

## Status as of 2026-10-25 (checked against `master` @ `ec2952e`)

This table supersedes the per-finding text below and the 2026-10-10/11 snapshot in
`docs/program/BASELINE.md` §3.2. Each row was re-read in code on 2026-10-25. Where the
evidence is a test file, the file was confirmed present.

| ID | Status | Evidence on master |
|---|---|---|
| F-SEC-01 | FIXED | `app/services/auth.py` suspension check at the chokepoint (`get_current_user`) |
| F-SEC-02 | FIXED, residual | `app/api/webhooks_whatsapp.py` `_lead_for_inbound`; `tests/test_whatsapp_inbound_tenant_match.py`. Residual: two tenants who both messaged a number → the most recent sender wins (RF-5) |
| F-SEC-03 | FIXED | `app/api/onboarding.py` (a foreign `user_id` needs admin); `tests/test_onboarding_complete_step_authz.py` |
| F-SEC-04 | FIXED | `app/api/ui_support.py` suppression handlers `Depends(require_admin)`; `tests/test_suppression_endpoints_admin_only.py` |
| F-SEC-05 | FIXED | website builder #137, WhatsApp templates #139 (see the status lines under F-SEC-05) |
| F-SEC-06 | FIXED | `DEBUG_ROUTE_ENVS` allowlist, `app/api/debug.py`; `tests/test_debug_router_environment.py` |
| F-AI-01 | FIXED | per-strategy caps and per-account spend guard: `app/services/spend_guard.py` (`check_model_call`), called from `app/services/usage_meter.py` |
| F-AI-02 | FIXED | `app/services/prompt_safety.py` delimits untrusted text across the services; `tests/test_prompt_injection_containment.py` |
| F-AI-03 | FIXED | `JUDGE_TEMPERATURE` in `app/services/anthropic_client.py`; models that refuse a temperature are handled (`TEMPERATURE_FREE_MODELS`, #130) |
| F-AI-04 | **OPEN** | default `claude-sonnet-4-6` in `app/config.py` and `app/core/config.py`. The health probe (`app/services/health_checks.py`) checks the key and reachability; by its own comment it does not see a wrong model name. Needs the live key |
| F-AI-05 | **OPEN** (LOW) | `app/services/message_personalization.py` `_applied_mutation` puts an applied mutation's `messaging_angle.proposed` and `revised_messaging` in front of the research, marked "takes precedence", without `prompt_safety`. That text is model-written (`app/services/strategy_mutation.py`). Only a person applying the version stands between the model and the precedence block |
| F-AI-06 | FIXED | `app/verification/loop.py` `_support_block` wraps research outputs, patterns and the GTM plan in `prompt_safety.block` |
| F-OPS-01 | FIXED in manifest | `pyproject.toml`; `tests/test_firebase_dependency_declared.py`. The image itself was not rebuilt in this check |
| F-OPS-02 | FIXED | `app/integrations/calendly.py` timestamp tolerance; `tests/test_calendly_webhook_freshness.py` |
| F-OPS-03 | **OPEN** | both `app/config.py` and `app/core/config.py` exist; 97 modules import the first, 12 the second |
| F-OPS-04 | FIXED | `app/services/notifications.py` accepts both names; `tests/test_firebase_credentials_env.py` |
| F-OPS-05 | FIXED | `app/core/config.py` warning text |
| F-OPS-06 | FIXED | `requirements.lock`, `frontend/package-lock.json` |
| F-OPS-07 | FIXED (removal); scan REPORT-ONLY | vestigial deps removed. `.github/workflows/verify.yml` runs `pip-audit` and `npm audit --omit=dev --audit-level=high` as report-only steps: findings are summarised, never block |
| F-REL-01 | FIXED | `app/workers/send_tasks.py` deleted |
| F-REL-02 | FIXED | `app/workers/monitoring.py`; `tests/test_monitoring_task_names.py` |
| F-DB-01 | FIXED | migration `0093_playbook_scores_owner`, per-account `UniqueConstraint("user_id", "pattern_key", "variant", ...)` in `app/db/models.py`; `tests/test_playbook_owner_migration.py`, `tests/integration/test_migration_0093_playbook_owner_pg.py` |
| F-DB-02 | FIXED | see P0-6 in `docs/program/BASELINE.md` |
| F-DB-03 | FIXED | `alembic/versions/0065_messages_dispatch_index.py` |
| F-DB-04 | **OPEN** (LOW) | `app/workers/learning_tasks.py` binds `:reply_rate` into both `score` and `reply_rate` |
| F-QA-01 | FIXED | `pyproject.toml` `norecursedirs` excludes `tests/integration` |
| F-QA-02 | **OPEN** | 121 `TODO: verify` markers in `app/`, `frontend/src` and `sdk`; no live vendor contract confirmed |

Also fixed since the audit, per `docs/program/BASELINE.md` §3.2: GDPR account export (#57) and
deletion (`app/api/account_data.py`, `app/services/account_erasure.py`,
`tests/test_account_erasure.py`, sealed); plan enforcement of `max_leads_per_strategy`
(`app/api/leads.py`), `max_sequence_steps` and channels (`app/services/plan_holds.py`,
`app/services/autopilot.py`, `check_send_channel` on the send paths).

---

## F-SEC-01 — Suspended users retain full API access (master)
- **Category:** Authorization / account lifecycle · **Severity:** CRITICAL · **Confidence:** CONFIRMED
- **Branch/commit:** `master` @ `1ad2cab` (fix present only on `feature/8-new-features` @ `6e11abe`)
- **File/line:** `app/services/auth.py:130–178` (`get_current_user`); only `is_suspended` reference at `:205` (workspace-owner path)
- **Evidence:** both token and `lpk_` API-key paths converge at the `email_verified` check (`:176`) then return via `_workspace_principal`, which returns the actor early when there is no `X-Workspace-Id` header — so the signed-in principal's `is_suspended` is never checked. `feature/8` adds `if user.is_suspended: raise 403` at `:207`. `git branch -r --contains 6e11abe` → feature/8 only.
- **Verification method:** read `get_current_user` end-to-end on both branches; branch-contains check.
- **Expected:** a suspended account is refused at every protected route. **Actual (master):** full access retained; can also mint fresh tokens via `/auth/login`,`/auth/refresh`.
- **Impact:** the primary abuse/compliance kill-switch is cosmetic for non-admins on the default branch.
- **Remediation:** port `auth.py` suspension guard + `tests/test_account_suspension.py` to the trunk.
- **Re-verify:** suspended user → 403 on a protected route and via API key; test red→green.

## F-SEC-02 — Cross-tenant WhatsApp inbound reply routing (both)
- **Category:** Multi-tenancy / compliance · **Severity:** HIGH · **Confidence:** CONFIRMED
- **File/line:** `app/api/webhooks_whatsapp.py` `_handle_inbound` (master ~316–325; feature/8 ~318)
- **Evidence:** `select(Lead).where(Lead.phone.isnot(None))` scans all tenants; first normalized-phone match wins; `phone_number_id` stored as `account_ref` but never used to scope.
- **Verification:** read handler on both branches.
- **Impact:** private reply attaches to the wrong tenant's lead; opens their 24h window; flips lead to REPLIED; a STOP suppresses the wrong account's sequences (data disclosure + compliance mis-routing). Third instance of the class `notifications.py:300` warns about; email variant fixed, phone variant not.
- **Remediation:** resolve tenant from `phone_number_id` → account; scope the lead lookup to that tenant.
- **Re-verify:** two tenants, same phone → reply routes to the correct tenant only (add a test).

## F-SEC-03 — Onboarding IDOR / cross-user write (both)
- **Category:** Multi-tenancy / IDOR · **Severity:** HIGH · **Confidence:** CONFIRMED
- **File/line:** `app/api/onboarding.py:110–133` (`complete_step`), `:126` `target_user_id = body.user_id or str(current_user.id)`
- **Evidence:** `CompleteStepRequest.user_id` is read from the request body and used unchecked; no equality check vs `current_user.id`, no admin gate; `mark_complete` raw-UPDATEs that user's `onboarding_state`.
- **Verification:** read handler on both branches.
- **Impact:** any authenticated user can mark/overwrite another user's onboarding state. Docstring claims otherwise; code does not enforce it.
- **Remediation:** remove `body.user_id`, or gate it behind `require_admin` / an internal service credential.
- **Re-verify:** non-admin passing another `user_id` → 403; test.

## F-SEC-04 — Global suppression list: cross-tenant PII read + permanent cross-tenant write (both)
- **Category:** Multi-tenancy / PII / compliance · **Severity:** HIGH (SaaS) / LOW (self-host) · **Confidence:** CONFIRMED
- **File/line:** `app/api/ui_support.py` `list_suppression` / `add_suppression` (~`:268–301`)
- **Evidence:** `GET /suppression` returns every tenant's suppressed emails/phones to any authenticated user; `POST` adds a deployment-wide permanent suppression ("no delete endpoint by design").
- **Verification:** read both handlers.
- **Impact:** global *enforcement* is correct compliance; unscoped *listing* leaks others' opt-out PII and unscoped *write* lets any tenant permanently block others' outreach. Severity depends on deployment model.
- **Remediation (if SaaS):** scope listing per tenant; gate/authorize cross-tenant adds; keep global enforcement at send time.
- **Re-verify:** tenant A cannot read tenant B's suppression entries.

## F-SEC-05 — website_builder NULL-pool + whatsapp_templates deployment-global (both)
- **Category:** Multi-tenancy · **Severity:** MEDIUM (SaaS-dependent) · **Confidence:** LIKELY (per LP-T-002; not re-read line-by-line this pass)
- **File/line:** `app/api/website_builder.py:182–211`; `app/api/whatsapp_templates.py:96–186`
- **Evidence:** workspace-less users share a `workspace_id IS NULL` page pool (read/regenerate/publish each other's and the marketing site's pages); WhatsApp templates are deployment-global and editable by any tenant, while `POST /generate` writes drafts derived from private strategy research into the shared table.
- **Impact:** content leakage/tampering on a multi-tenant SaaS; defensible single-tenant.
- **Remediation:** dedicated marketing-workspace id; tenant-scope or role-gate template management.
- **Status (2026-10-25), website builder half:** fixed on branch `claude/bold-heisenberg-58xvzj`. The founder chose admin-only global pages over a dedicated marketing workspace. Only an admin reaches a `workspace_id IS NULL` page. A non-admin with no workspace cannot create pages (409 `workspace_required`). The export (now admin-only), the public sitemap and the generation prompt's link menu never mix workspaces. Tests: `TestTenancy` in `tests/test_website_builder.py`. **The WhatsApp templates half is still OPEN.**
- **Status (2026-10-25), WhatsApp templates half:** fixed on branch `claude/bold-heisenberg-58xvzj` (founder choice: "Admins manage"). Create, edit, submit, sync and generate require an admin. A non-admin lists and reads approved templates only. The Campaigns page shows template controls to admins only. Tests: `TestWhoManagesTemplates` in `tests/test_whatsapp_template_lifecycle.py`, `frontend/src/tests/templatesPanel.test.tsx`. With the website builder half merged (#137), **F-SEC-05 is closed once this merges.**

## F-SEC-06 — debug router guard allows `staging` env (both)
- **Category:** Config / exposure · **Severity:** LOW · **Confidence:** CONFIRMED
- **File/line:** `app/api/debug.py`; guard in `production_guard.py` + `main.py` (triple-gated); env check blocks only `{production,prod,live}`
- **Evidence:** a real-data environment named `staging` with `ENABLE_DEBUG_ROUTES=true` would expose debug routes (incl. send-cap mutation, unsubscribe-token minting).
- **Remediation:** deny debug routes outside an explicit allowlist of non-prod-data envs; never on real data.

---

## F-AI-01 — Unbounded AI consumption / no cost ceiling (both)
- **Category:** AI (OWASP LLM10) · **Severity:** HIGH · **Confidence:** CONFIRMED
- **File/line:** `app/api/strategies.py:70` (rate-limit only), `:98` + `:193` (`run_pipeline.delay`); no `check_plan_limit(max_strategies)`; grep `spend_cap|budget_cap|max_spend|token_budget` across `app/` → none
- **Evidence:** only `RATE_LIMIT_STRATEGIES` bounds strategy creation; `max_strategies` enforced nowhere; `usage_meter` records but never enforces cost. One strategy = 72–144 Claude calls + ≤10×≤3 verification rewrites (≤18,432 tokens each) + optional consensus.
- **Impact:** a single account (or a resume loop) can bill unbounded Anthropic spend.
- **Remediation:** enforce `check_plan_limit` on create + resume; add a per-account monthly token/cost cap checked before enqueue.

## F-AI-02 — Indirect prompt injection via scraped content (both)
- **Category:** AI (OWASP LLM01) · **Severity:** HIGH · **Confidence:** CONFIRMED
- **File/line:** `app/services/personalization_context.py:138–160` (LinkedIn `post["text"][:700]`, news); `app/services/reply_classification.py`, `reply_fraud.py` (inbound bodies)
- **Evidence:** attacker-controllable external text concatenated into generation/classification prompts with no delimiting, escaping, or injection instruction; classifier output drives suppression/stop logic.
- **Impact:** a prospect can steer generated outreach or flip a reply classification. Partially contained by claim-verification + reply human-in-the-loop; classification manipulation uncovered.
- **Remediation:** delimit/label external text; treat model classification as untrusted; optional inbound injection screen.

## F-AI-03 — Non-deterministic compliance/verification judges (both)
- **Category:** AI (OWASP LLM09) · **Severity:** MEDIUM · **Confidence:** CONFIRMED
- **File/line:** `app/services/anthropic_client.py` request builder (no `temperature`); only `temperature` in `app/` is `openai_client.py:46`
- **Evidence:** the legal-compliance pass (a documented hard ship-blocker) runs at API default (~1.0); PASS/FAIL can vary run-to-run.
- **Remediation:** `temperature=0` on all judge/classifier/extractor calls.

## F-AI-04 — Default model ID validity (both)
- **Category:** AI / config · **Severity:** MEDIUM · **Confidence:** UNVERIFIED
- **File/line:** `app/config.py:45`, `app/core/config.py:77`, `app/services/voice_providers.py:72` → `claude-sonnet-4-6`
- **Evidence:** env-overridable default; a wrong ID returns 404 → classified PERMANENT → every strategy fails terminally with no fallback. Auditor could not confirm the ID against the live Models API (no access; post-dates knowledge cutoff).
- **Remediation:** add a startup model-ID health-check that fails loudly; confirm the ID against the live API.

---

## F-OPS-01 — firebase-admin missing from the manifest the Docker image installs (both)
- **Category:** Dependencies / deploy · **Severity:** HIGH · **Confidence:** CONFIRMED
- **File/line:** `Dockerfile:41–46` (`COPY pyproject.toml` + `pip install .`; then `pip install --no-deps .`); `pyproject.toml` (no `firebase-admin`); `requirements.txt` (has it); `app/services/notifications.py:73` lazy import
- **Evidence:** the image installs deps from `pyproject.toml`, which omits `firebase-admin`; so the import raises `ImportError` in-image and FCM push silently fails. Same defect class the pyproject comment documents for `structlog` having broken the image before.
- **Remediation:** add `firebase-admin` (and any other requirements-only runtime deps) to `pyproject.toml`; build and smoke-test an FCM path inside the container.

## F-OPS-02 — Calendly webhook has no replay/timestamp protection (both)
- **Category:** Webhook security · **Severity:** HIGH · **Confidence:** CONFIRMED
- **File/line:** `app/integrations/calendly.py:66–82`
- **Evidence:** the `t=<ts>,v1=<hmac>` scheme parses the timestamp but never checks its freshness (Stripe/HubSpot/Salesforce enforce a 300s tolerance). Partly mitigated by `(provider,event_id)` idempotency, but a capture replayed before the original is processed is not covered.
- **Remediation:** reject when `abs(now - t) > tolerance`.

## F-OPS-03 — Two settings modules drift (both)
- **Category:** Config · **Severity:** MEDIUM · **Confidence:** CONFIRMED
- **File/line:** `app/config.py` (lowercase, `case_sensitive=False`) vs `app/core/config.py` (UPPERCASE, `case_sensitive=True`)
- **Evidence:** JWT key read only from `app/config.py`; `ENCRYPTION_KEY` declared in both but read directly via `os.getenv` in `crypto.py`; rate-limit knobs only in `app/core/config.py` with docstrings warning which module wins — the team has already been bitten (a documented dead-env-var bug).
- **Remediation:** consolidate to one settings module; single source per secret.

## F-OPS-04 — Firebase env var name mismatch (both)
- **Category:** Config · **Severity:** MEDIUM · **Confidence:** CONFIRMED
- **File/line:** `app/core/config.py:119–120` declares `FIREBASE_CREDENTIALS_JSON`; `app/services/notifications.py:81` reads `FIREBASE_CREDENTIALS_PATH`
- **Evidence:** operator setting the documented var gets a RuntimeError at first push; not covered by `production_guard`.
- **Remediation:** align on one name and a single format (path vs JSON).

## F-OPS-05 — Misleading ENCRYPTION_KEY-unset warning (both)
- **Category:** Config / clarity · **Severity:** LOW · **Confidence:** CONFIRMED
- **File/line:** warning at `app/core/config.py:446–455`; actual behavior `app/core/crypto.py:47–52` (`_get_fernet` raises `EncryptionKeyError`), `token_store.py:107`
- **Evidence:** the warning implies plaintext-at-rest; the real behavior is **fail-closed** (token storage throws). No plaintext path exists.
- **Remediation:** correct the warning copy to "token storage will fail".

## F-OPS-06 — No lockfile; manifest drift; unused deps (both)
- **Category:** Dependencies · **Severity:** MEDIUM · **Confidence:** CONFIRMED
- **Evidence:** `requirements.txt` (`anthropic>=0.28`) vs `pyproject.toml` (`anthropic>=0.40`, uncapped); `pandas`, `google-auth*`, `python-jose` appear unused (`requirements` only); no `requirements.lock`/`package-lock.json`.
- **Remediation:** add lockfiles; reconcile manifests; drop unused deps.

## F-OPS-07 — Vestigial / CVE-prone dependencies (both)
- **Category:** Dependencies · **Severity:** POTENTIAL · **Confidence:** POTENTIAL
- **Evidence:** `python-jose` (vestigial; historically CVE-prone) and `pillow` (recurring image CVEs, used by the ROI-card fallback) are installed. **No specific CVE asserted.**
- **Remediation:** run `pip-audit`/`safety` in CI; remove `python-jose`.

---

## F-REL-01 — Duplicate, non-atomic, orphaned send path (both)
- **Category:** Reliability / dead code · **Severity:** MEDIUM · **Confidence:** CONFIRMED
- **File/line:** `app/workers/send_tasks.py:36` (`send_sequence_step`), `:57` (read `sent_at IS NULL`), `:103` (unconditional `UPDATE … sent_at = NOW()`); enqueuer `schedule_sequence_step:123` has no live caller (only a docstring + commented-out `scripts/benchmark.py:128`)
- **Evidence:** a second registered Celery task (routed to the `outreach` queue) implements sending with a check-then-act pattern, unlike the canonical atomic claim in `outreach_tasks.send_message_impl` (feature/8) / `meeting_prep_tasks._claim`. It is effectively dead (no enqueuer caller) but live-registered.
- **Impact:** confusion + a latent duplicate-send vector if ever wired up; the P0-4 fix did not touch it.
- **Remediation:** delete it, or wire it and apply the atomic claim.

## F-REL-02 — Dead task-name reference in monitoring (both)
- **Category:** Reliability / dead code · **Severity:** LOW · **Confidence:** CONFIRMED
- **File/line:** `app/workers/monitoring.py:32` lists `app.workers.outreach_tasks.send_sequence_step`, which is not defined in `outreach_tasks.py`
- **Impact:** the critical-task FCM-on-failure alert for the real send path never matches; silent monitoring gap.
- **Remediation:** reference the real send task name.

---

## F-DB-01 — playbook_scores has no tenant column (both)
- **Category:** Data / tenancy · **Severity:** HIGH (SaaS) / LOW (self-host) · **Confidence:** CONFIRMED
- **File/line:** `alembic/versions/0001`,`0008_m8c2`,`0010_m8c4`; `app/db/models.py:1497` (`PlaybookScore`, unique `(pattern_key,variant)`, no `user_id`); writer `app/workers/learning_tasks.py:145`
- **Evidence:** learned scores are global; read paths join to tenant via `strategies→products.user_id`, but stored rows and the nightly upsert are not tenant-scoped.
- **Impact:** one tenant's outcomes can influence another's playbook if `pattern_key` is not tenant-unique; small statistical disclosure.
- **Remediation (if SaaS):** add `user_id`; scope the upsert. Else document the global-by-design intent.
- **Status (2026-10-20):** fixed on branch `claude/bold-heisenberg-58xvzj`: migration `0093_playbook_scores_owner`, per-account aggregation and readers (`docs/program/plans/f-db-01-playbook-owner.md`). Closed when merged to master.

## F-DB-02 — admin playbook-scores filters a nonexistent column, swallowed (master)
- **Category:** Data / correctness · **Severity:** MEDIUM · **Confidence:** CONFIRMED
- **File/line:** `app/api/admin.py:291` (`ps.user_id`), `:310–312` (bare `except → {"scores": []}`)
- **Evidence:** `playbook_scores` has no `user_id`; the filtered query raises UndefinedColumn, swallowed to an empty 200. Fixed on feature/8 (SQLAlchemy Core rewrite, swallow removed); still present on master.
- **Remediation:** port the feature/8 fix; stop masking DB errors.

## F-DB-03 — Missing index on messages hot columns (both)
- **Category:** Performance · **Severity:** MEDIUM · **Confidence:** CONFIRMED
- **File/line:** `alembic/versions/0001` (messages indexes only `lead_id`, `sequence_id`)
- **Evidence:** no index on `messages.status` / `scheduled_at` — the scheduler's hot "due & not sent" scan columns. Other tables index these well.
- **Remediation:** add `ix_messages_status` and/or composite `(status, scheduled_at)`.

## F-DB-04 — Redundant playbook_scores.score (both)
- **Category:** Data · **Severity:** LOW · **Confidence:** CONFIRMED
- **File/line:** `app/workers/learning_tasks.py:145` binds `:reply_rate` into both `score` and `reply_rate`
- **Evidence:** `score == reply_rate` on every write; `score` carries no independent signal (confirms the P0-6 report's verdict).
- **Remediation:** drop `score` after checking legacy M1 rows (its own migration).

---

## F-QA-01 — Order-dependent offline test failures (both)
- **Category:** Testing · **Severity:** MEDIUM · **Confidence:** CONFIRMED
- **Evidence:** raw `pytest tests/` (integration co-collected) → 9 WhatsApp failures; each passes in isolation/per-file; the official gate (`--ignore=tests/integration`) is 100% green. State/import pollution from the integration dir.
- **Remediation:** make the offline suite order-independent; or set the default `pytest` path to exclude integration in config.

## F-QA-02 — Integrations are mock-only (both)
- **Category:** Testing / integrations · **Severity:** MEDIUM · **Confidence:** CONFIRMED
- **Evidence:** 88 "TODO: verify against current … docs" across Apollo/Hunter/Gmail/WhatsApp/Capacitor; tests use `respx`/fakes; no live vendor request/response ever confirmed.
- **Remediation:** credentialed staging run per integration; convert TODOs into recorded contract tests.

---

## F-AI-05 — Learning-loop / playbook context-poisoning surface (both)
- **Category:** AI (OWASP LLM03/LLM01) · **Severity:** LOW · **Confidence:** CONFIRMED
- **File/line:** `app/services/playbook_service.py:55–83` (aggregate stats injected into Phase 6/8 prompts); `app/services/message_personalization.py:65–91` ("APPLIED STRATEGY MUTATION … takes precedence" block injected into outreach generation)
- **Evidence:** stored data is fed back into prompts (the classic learning-loop poisoning vector). Risk is low today because the playbook block is numeric aggregates and mutations are user/admin-applied, not model-written.
- **Impact:** latent — if a future mutation's free-text fields ever become model- or externally-sourced, the "takes precedence" block becomes a direct override channel into every outbound email.
- **Remediation:** keep mutation text human/admin-sourced; never let model/external text populate a "takes precedence" instruction.

## F-AI-06 — Cross-pipeline synthesis fed to the judge without provenance isolation (both)
- **Category:** AI (OWASP LLM01) · **Severity:** LOW · **Confidence:** POTENTIAL
- **File/line:** `app/pipeline/engine.py:109–121` (strategy syntheses feed GTM steps); `app/verification/loop.py:58–102` (`_support_block` concatenates phase outputs into the judge prompt)
- **Evidence:** upstream research can already contain injected external content (see F-AI-02); a successful injection can propagate transitively into the judge's evidence. Contained by the claim-verification backstop at send time.
- **Impact:** low; noted for completeness as part of the F-AI-02 blast-radius picture.
- **Remediation:** label/segregate externally-derived evidence in the judge prompt; rely on deterministic claim verification as the backstop (already present).
