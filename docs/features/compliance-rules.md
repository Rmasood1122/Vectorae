# Feature 8 — Configurable compliance rules

## What it does
Stage 6 used to take its compliance parameters from deployment-wide
environment settings. They can now be overridden in **Admin › Compliance Rules**
per **workspace**, per **recipient region** (us, ca, eu, uk, sg, au, nz, other,
unknown — from `app/services/compliance_region.py`) and per **channel**:

| Field | Baseline (no rule) | Rules may |
|---|---|---|
| `send_start_hour` / `send_end_hour` | `SEND_WINDOW_START_HOUR` / `END_HOUR` (9–17) | move it within **07:00–20:00** local time |
| `skip_weekends` | `SEND_WINDOW_SKIP_WEEKENDS` (true) | set either way |
| `daily_cap` (email, WhatsApp) | `GMAIL_DAILY_CAP`, `WHATSAPP_DAILY_CAP` | **lower only** |
| `consent_required` | false | require recorded consent |
| `bounce_pause_threshold` | `BOUNCE_RATE_PAUSE_THRESHOLD` (3%) | **lower only** |

All of these are checked by `send_message_impl` at the moment a message sends.
The bounce threshold is checked when a bounce is recorded. There is still no
bypass flag.

## Resolution
- The **most specific valid row that sets a field** wins. Specificity: workspace
  over global, then a named region over `*`, then a named channel over `*`.
- Fields a row leaves empty inherit from the next layer, and finally from the
  baseline.
- Caps and the bounce threshold are clamped to the baseline after resolution,
  whatever a row says.

## Fail-closed guarantees (pinned in `tests/test_compliance_rules.py`)
| Situation | Result |
|---|---|
| Empty table | Exactly the baseline, i.e. the pre-Feature-8 behaviour |
| Table unreadable (database error) | Baseline, logged, `failed_closed` |
| A matching row is invalid (start ≥ end, hours outside 07–20, negative cap, cap above the deployment cap, threshold outside (0, baseline]) | The **intersection** of the baseline and every valid matching row: latest start, earliest end, weekends skipped if any says so, lowest cap and threshold, consent required if any says so. It never widens |
| Rules leave no permitted hours | `deferred_no_send_window`: nothing transmits; checked again the next day |
| Cap reached | `deferred_cap`: deferred, never dropped |
| Consent required, none recorded | `skipped_consent_required`: the step is skipped and the sequence continues |

The API refuses invalid values with 422, using the same validator. An invalid
row can therefore only arrive by raw SQL, and the admin page flags it.

## Consent evidence
- **WhatsApp:** the recorded opt-in.
- **Phone:** the recorded call consent (`phone_calls.consent_ok`).
- **Email and LinkedIn:** Vectorae records no consent, so a consent rule on
  those channels **stops those sends**. That is the restrictive reading, and it
  is intentional.

## API (deployment admins)
| Endpoint | Purpose |
|---|---|
| `GET /admin/compliance-rules?workspace_id=` | Baseline, hard bounds, global rules and that workspace's rules (with any `problems`) |
| `PUT /admin/compliance-rules` | Upsert one (scope, region, channel) |
| `DELETE /admin/compliance-rules/{id}` | Delete |
| `GET /admin/compliance-rules/effective?workspace_id=&region=&channel=` | What a send there resolves to now |
| `GET /admin/compliance-rules/workspaces` | Workspace selector |

## Limits
- **Scheduling:** it still uses the baseline window. The send-time check moves
  anything a rule forbids. The smart-send-time heatmap shading is baseline too.
- **LinkedIn and phone caps:** they keep their own per-account limits in System
  Settings; rule `daily_cap` applies to email and WhatsApp.
- **Dashboards:** the campaign overview and the CRM campaigns dashboard display
  the baseline bounce threshold; the auto-pause uses the workspace rule.
- **Region:** it is inferred from the lead's enrichment country, else timezone.
  With neither, the lead is `unknown`.
- **Not legal advice.** This enforces the operator's policy; it does not decide
  what the law requires.

## Files
`app/services/compliance_rules.py`, `app/api/compliance_rules.py`,
`app/services/sequence_engine.py` (window/cap/threshold parameters),
`app/workers/outreach_tasks.py` (send-time resolution), migration
`0051_compliance_rules.py`, `frontend/src/app/(admin)/admin/compliance-rules/page.tsx`.
Tests: `tests/test_compliance_rules.py`, `tests/test_compliance_rules_migration.py`.
