# INTEGRATION_NOTES — Vectorae Combined Build

This document records every non-trivial decision made during the combined-build
session that merged milestones M1–M8 into a single runnable codebase.  Read
this before reporting a bug or starting a new milestone — some behaviours are
intentional repairs, not bugs.

---

## 1. Missing M8 Chunks 1+2 zip

The `m8-chunks-1-2.zip` delivery was not included in the ten uploads.  Migration
`0009_m8c3` declares `down_revision = "0008_m8c2"`, making those files required.
The following were reconstructed to spec:

| Reconstructed file | Spec source |
|---|---|
| `app/services/ab_testing.py` | Project knowledge + memory: z-test via `math.erfc`; four gates (sample size ≥ `PLAYBOOK_MIN_SAMPLE`, p < `AB_SIGNIFICANCE_THRESHOLD`, lift ≥ `AB_MIN_LIFT`, harm_rate ≤ `AB_HARM_CEILING`=0.05). |
| `app/services/similarity.py` | Project knowledge: pure-Python TF-IDF cosine, user-scoped, 500-strategy cap, 2000-char doc head. |
| `app/services/playbook_service.py` | Project knowledge: `PlaybookService.get_insights()` returning a prompt block; never raises; graceful empty. |
| `alembic/versions/0008_m8c2_learning_loop.py` | Derived from what `0009_m8c3` ALTER statements expect to find already in place. |

---

## 2. M7 model regression — restored M5 models.py

`leadpilot-m7-complete.zip` (actually a gzip tarball despite the `.zip` extension)
shipped an `app/db/models.py` that dropped every model added in M2–M5 and
defined its own `Base` pointing to a different `metadata`.  This would have caused
all M2–M5 tables to become invisible to the M7 application.

**Fix:** the M5 `models.py` was restored as the canonical file and all M7/M8
model additions (DeviceToken, IntegrationToken, SubjectLinePattern, WebhookTarget,
WebhookDelivery, PlaybookScore reshape, User admin columns, etc.) were appended
to it.  Alembic migrations continue to own all DDL.

---

## 3. M8 phantom layout — compat shims

The M8 delivery files import from a module layout that was never present in the
M1–M7 codebase.  Each phantom import was satisfied by a thin shim that
re-exports the real implementation:

| Phantom import | Real implementation | Shim file |
|---|---|---|
| `app.core.database` (Base, engine, SessionLocal, get_db) | `app.db.base` | `app/core/database.py` |
| `app.db.session` | `app.db.base` | `app/db/session.py` |
| `app.core.redis_client` (get_sync_redis, get_redis) | inline via `redis.from_url` | `app/core/redis_client.py` |
| `app.core.exceptions` (VectoraeError, ComplianceError …) | new; ComplianceError carries `rule`/`remediation`/`compliance_code` | `app/core/exceptions.py` |
| `app.api.deps` (get_current_user, require_admin) | `app.services.auth.get_current_user` | `app/api/deps.py` |
| `app.models.*` (per-model files) | monolithic `app/db/models.py` | `app/models/__init__.py` + per-model aliases |
| `app.services.suppression_service` | `app.workers.lead_tasks.is_suppressed` | `app/services/suppression_service.py` |
| `app.services.notification_service` | M7 `app.workers.fcm_tasks.send_to_device` | `app/services/notification_service.py` |

---

## 4. Router repositioning

`m8-final` replaced `app/api/strategies.py` and `app/api/webhooks.py` with
versions written against the phantom layout and with different route sets.
The originals were restored and the M8 additions were moved:

- `app/api/strategies_advanced.py` — M8-C4/C5 strategy endpoints
  (`/strategies/{id}/mv-results`, `/personalization-stats`, plans); prefix `/strategies`.
- `app/api/webhook_targets.py` — outbound webhook target CRUD only (POST/GET/DELETE
  `/webhooks/targets`); inbound Calendly and WhatsApp stay with the tested M3/M4
  routers to avoid duplicate route registration.

The M6 `GET /strategies` snippet (from `sdk/backend_additions/strategies_list.py`)
was appended directly to `app/api/strategies.py` per its inline paste instructions.

---

## 5. admin_service Campaign references

`app/services/admin_service.py` was delivered referencing a `Campaign` model with
a `user_id` column and `status IN ('active','paused_admin')`.  No such model exists
in the M1–M7 schema — campaign state lives on `Strategy.campaign_state` (M3) and
user scoping requires joining through `Product`.

**Fix:** four `Campaign` usages were replaced with helper functions
`_strategy_count()` and `_active_campaign_count()` / `_set_campaign_state()` that
join `strategies → products → users`.  The `Campaign` import was removed.

---

## 6. plans.py — enterprise tier

`app/core/plans.py` defined `free`, `starter`, and `pro` tiers.  The M5 frontend
and M8 admin pages use the `PlanTier.ENTERPRISE` enum value.  An `"enterprise"`
entry mirroring `pro` entitlements was added so enterprise users are never 402'd.

---

## 7. Migration repairs

Two migrations shipped that could never be applied on the real schema:

| Migration | Problem | Fix |
|---|---|---|
| `0009_device_tokens` | Used `Integer` id/user_id columns; real schema uses `Uuid`. Also included `ALTER TYPE outcomeevent` but the enum is VARCHAR-backed (`native_enum=False`). | File replaced (it had never been applied). |
| `0011_m8c5_production_launch` | `CREATE INDEX ix_webhook_targets_user_id` duplicated the index already created by `index=True` on the column above it. Caught live on PostgreSQL 16. | Explicit `create_index` call removed; comment added. |

A new migration `0012_build_repairs` was created for defects found by running the
live nightly aggregation task:

- `strategies.icp_industry` + `icp_company_size_bucket` — the M8-C4 send-time
  optimizer references these columns; they were never created.
- `outcomes.lead_id` → nullable — the `ab_promoted` system-level event has a
  strategy but no lead; the original `NOT NULL` made every such insert fail.
- Partial unique index `uq_outcomes_ab_promoted WHERE event='ab_promoted'` — makes
  `auto_promote_winners`'s `ON CONFLICT DO NOTHING` a real DB-level guarantee.

---

## 8. Nightly learning loop task repairs (live testing)

Five defects surfaced when running `run_strategy_aggregation` against real data:

| Defect | Fix |
|---|---|
| `monitoring.py` filtered `ResearchStep.status == 'in_progress'` — no such column | Rewritten to detect stalled strategy _runs_ (researching/verifying with no recent completed step), detection-only, never mutates status |
| `score_decay.py` used `datetime.utcnow()` (naive) vs tz-aware `outcomes.ts` | Changed to `datetime.now(timezone.utc)`; naive rows normalised before subtraction |
| `learning_tasks.py` upsert omitted `id` from `playbook_scores` INSERT | Added `gen_random_uuid()` to both the score upsert and the `ab_promoted` marker |
| `SELECT DISTINCT id FROM strategies ORDER BY updated_at` — invalid DISTINCT+ORDER BY | Removed DISTINCT; fixed phantom status `'complete'` → real values `('verified','executing')` |
| One failed sub-step could poison the shared DB session for subsequent steps | Explicit `db.rollback()` added to every per-step except block in `learning_tasks`, `send_time_optimizer`, `subject_intelligence`, and `personalization_scorer` |

---

## 9. Frontend integration

### (admin) route group — now compiling
- Created `src/lib/stores/authStore.ts` shim: wraps M5 session helpers and
  `GET /auth/me`; no zustand dependency.  `GET /auth/me` now returns `is_admin`.
- Created missing ui primitives: `table.tsx`, `checkbox.tsx`, `textarea.tsx`
  (re-exports from input.tsx).
- Extended `Badge` to accept both `tone` (M5 API) and `variant` (M8/shadcn API).
- Added shadcn-style `Dialog`, `DialogContent`, `DialogHeader`, `DialogTitle`,
  `DialogFooter` exports to `dialog.tsx` (wrapping the existing M5 `<dialog>` element).
- Created `ThemeProvider` and `QueryProvider` shims under `components/providers/`.
- Added `apiClient` compat export to `client.ts` (maps get/post/put/patch/delete
  onto the M5 `api<T>()` function).
- Installed `sonner` (toast library used by M8-C3 admin pages).
- Fixed `capacitor.config.ts`: removed `androidScheme` from the `android` block
  (invalid in Capacitor 6; it belongs only under `server`).
- Added `integration-pending` and `android` to `tsconfig.json` exclude.
- **Result:** 0 TypeScript errors outside `integration-pending/` and test files.

### (dashboard) route group — shelved to `frontend/integration-pending/`
The four M8-final dashboard pages import 11 components that were never delivered
in any milestone zip:

```
MeetingRateChart  ReplyRateChart  OutcomesFunnel  QuickStats
ThemeSection  AccountSection  IntegrationsSection  KanbanPipeline
CampaignSummaryRow  CampaignSequenceView  LeadStatusBoard
```

Additionally `(dashboard)/page.tsx` would resolve to `/`, conflicting with the M5
root `page.tsx` (which redirects to `/pipeline` or `/login`).

The files are in `frontend/integration-pending/(dashboard)/` with a README.  The
six M8 components that _do_ compile (`SendTimeCard`, `SubjectLineCard`,
`MultiVariateResults`, `PersonalizationHealth`, `OnboardingChecklist`, `PlanCard`)
remain under `src/components/`.

---

## 10. Celery beat schedule

The M5 `celery_app.py` only had `dispatch-due-messages` and `poll-gmail-replies`.
M8 additions wired:

```
learning-loop-aggregation   → run_strategy_aggregation  (crontab hour=PLAYBOOK_AGGREGATION_UTC_HOUR, minute=5)
ab-auto-promotion           → auto_promote_winners       (same hour, minute=35)
beat-heartbeat              → refresh_celery_heartbeat   (every 60s)
```

The M8-C5 "webhook retry sweep" beat entry was removed — `deliver_webhook`
self-schedules retries via `apply_async(countdown=…)`.

---

## 11. .env keys added vs M5 baseline

The following settings are new in M8 and must be added to `.env`:

```env
PLAYBOOK_MIN_SAMPLE=30
AB_MIN_LIFT=0.10
AB_SIGNIFICANCE_THRESHOLD=0.05
AB_HARM_CEILING=0.05
PLAYBOOK_AGGREGATION_UTC_HOUR=2
PLAYBOOK_SCORE_HALF_LIFE_DAYS=90
PIPELINE_STEP_TIMEOUT_MINUTES=10
ENCRYPTION_KEY=<fernet-key>          # generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
CORS_ORIGINS=https://your-frontend.example.com
```

---

## 12. Codebase stats (combined build)

| Metric | Value |
|---|---|
| Alembic migrations | 14 (0001–0012) |
| PostgreSQL tables | 26 |
| FastAPI routes | 97 |
| Python source files | ≥ 69 |
| TypeScript TS errors (src/) | 0 |
| Missing app.* imports | 0 |
