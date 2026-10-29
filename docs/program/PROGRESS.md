# Program progress

Resume from this file, `status.json`, `DECISIONS.md` and `git log`.

- 2026-10-28 — **F-QA-02, HubSpot, Recall.ai, Vapi and ElevenLabs checked against their public docs** (founder choices in session: "Add it, ask to reconnect", "Require auth", "Google", "Move to v3"). No account or key used. The vendor doc hosts do not resolve from the session (network allowlist), so the check used web-search excerpts of the official pages; the HubSpot OAuth endpoints were re-checked directly in search results.
  - HubSpot: sign-in moves from the deprecated v1 OAuth endpoints to the dated 2026-03 ones (`/oauth/2026-03/token`, same form fields; token metadata via `POST /oauth/2026-03/token/introspect` with the token in the form body, never the URL path). Third-party integrators report v1 stops on 2027-02-16 (not confirmed in HubSpot's own changelog). The account id falls back to `hub_id` from the token response. `crm.objects.companies.read` is now requested: the Guardrails "Sync from CRM" companies search had no scope for it; an older grant now gets a fixed "reconnect HubSpot" message (`reconnect: true`) instead of HubSpot's raw text. Webhook signature v3 decodes HubSpot's listed %-escapes in the URI. Contact search and the v4 default association match the docs (no change).
  - Vapi: voicemail detection `twilio` (legacy, more false positives) -> `google`; `monitorPlan.controlAuthenticationEnabled` on, so a call's control URL needs our key (the code already sent it). Not exercised on a live account.
  - ElevenLabs: an outbound call answered `success: false` or without `conversation_id` is no longer recorded as queued (its post-call webhook, matched by conversation_id, would have been dropped with any opt-out spoken on it); with a `callSid` it fails permanently so the person is never rung twice. Post-call webhooks older than 30 minutes are refused, and a non-UTF-8 body is refused instead of a 500.
  - Recall: the bot asks for `prioritize_accuracy` transcription (only the end-of-call transcript is used); `Authorization: Token` confirmed.
  - Pre-fix: 13 of the 17 tests in `tests/test_hubspot_recall_voice_contract.py` fail on master. `tests/test_crm_ecosystem.py`: the fake `token_info` takes the new client-credential arguments (assertion unchanged).
  - Help articles 16, 20 and 29 updated in both copies and fact-checked (one sentence narrowed, one corrected to name Recall's open markers).
  - Founder action: every existing HubSpot connection must be disconnected and connected again to grant the companies permission.
  - Reported, not fixed: ElevenLabs silently ignores the per-call prompt / first message / voice overrides unless each is allowed in the agent's Security > Overrides settings, so a call could run the dashboard prompt without our AI and recording disclosure (nothing detects this; `call_recording_enabled` is never set); Recall ad-hoc bots can get 507 when the pool is empty (not handled); Recall regions are not limited to the four documented ones (the regex already prevents a foreign host); Vapi's top-level `metadata` on POST /call and inline `server.headers` webhook auth are not confirmed in the docs reached (Vapi now documents `server.credentialId`); the meeting bot is named "Vectorae Notetaker" while the product name is Victorae; HubSpot search could use 200 per page and back off on its shared 5-per-second limit.
- 2026-10-28 — **Calendly reschedules keep the meeting** (founder choice in session: "Calendly reschedule fix"; reported under the previous entry).
  - Calendly reports a reschedule as `invitee.canceled` for the old event with `rescheduled: true` plus `invitee.created` for the new one, in either order (developer.calendly.com, "Webhook Payloads for Rescheduled Events", read 2026-10-28, no account used). The handler ignored the flag: the lead went back to Replied and a "replied" outcome was written, and when the new booking arrived first the lead stayed Replied.
  - Now a cancel with `rescheduled` exactly `true` stops the old event's prep reminders, leaves the lead's status alone and writes no outcome (`action: "rescheduled"`). A plain cancel, `false`, or a non-boolean value still reverts as before.
  - Pre-fix: 2 of the 6 tests in `tests/test_calendly_reschedule.py` fail (both reschedule orders); the other 4 pin kept behaviour.
  - Help article 14 updated in both copies and fact-checked (12 supported; two sentences narrowed). Its stale line saying the Calendly code was "marked as not yet checked" (the markers were removed in the previous entry) now says it was checked against the docs, not the live service.
  - Not covered: a lead with two separate Calendly bookings who cancels one goes back to Replied (unchanged; there is no per-booking state on the lead).
- 2026-10-28 — **F-QA-02, Hunter, Calendly and Zoom checked against their public docs** (founder choices in session: "Risky -> flagged" for webmail, "Suppress the person" on Hunter 451, "Require a host email" for Zoom). No account used.
  - Hunter: `result` is deprecated; the adapter reads `status` first (webmail = risky, flagged in the waterfall). 202/222 are not final answers: VerificationPending, and BaseHttpAdapter now caches only a 200. 451 (the person objected at Hunter) raises DataSubjectOptedOut; every find/verify loop drops the lead and suppresses the person (`consent.suppress_everywhere`). Key moved to the X-API-KEY header. Pre-fix: webmail+"deliverable" verified; a status-only "valid" was UNKNOWN; a 451 was retried.
  - Zoom: no default host ("me" is documented for user-level apps only); an unset host_user refuses before any call. Duration capped at 1440. Calendly: event_types paged (default page was 20); the signature is computed over bytes (a non-UTF-8 body was a 500). Pre-fix: 7 of 9 tests fail.
  - Help articles 6 and 29 updated (both copies).
  - Reported, not fixed: Calendly reschedules (`invitee.canceled` processed after the new `invitee.created` sets the lead back to REPLIED; the `rescheduled` field is not read); a tagged Calendly link can be booked by anyone holding it (signature proves Calendly, not the recipient); Zoom `start_url` storage not reviewed; remaining TODO markers in HubSpot, Recall, voice providers, frontend native hooks and a few services.
- 2026-10-27 — **F-QA-02, Apollo, Gmail and LinkedIn (Unipile) checked against their public docs** (founder choices in session: "Exact, then keywords", "Never auto-retry a send", "Retry later + our own caps"). No account or key used; three research agents read the docs, and the load-bearing claims were re-read directly.
  - Apollo: the search endpoint we called (`mixed_people/search`) is gone from Apollo's docs; now `mixed_people/api_search` (0 credits, no emails; the pipeline already enriches every lead) with []-suffixed query parameters, `total_entries` paging, per_page <= 100. Industries go to `organization_industries[]`, and if that finds nobody the search reruns with them as keywords. Health needs `healthy` and `is_logged_in`. Phone read from `person.contact` too. Search is still metered one unit per requested lead (errs toward the cap). Pre-fix: 11 of 17 Apollo tests fail.
  - Gmail: a send that timed out or got a 5xx was POSTed up to 3 times and then rescheduled; now `BaseHttpAdapter.call(idempotent=False)` raises `OutcomeUnknown` and the message fails once with "check the Sent folder". Quota 403s retry later; other 403s and 400s stay permanent. The inbox poll follows `nextPageToken`. Pre-fix: on master a 500 produced 3 POSTs.
  - LinkedIn: Unipile limit errors (e.g. `errors/cannot_resend_yet`) and 429 retry later instead of failing the lead; disconnected / restricted / credential errors mark the account and take it out of rotation (`pick_account` now needs status "ok"); weekly invite cap `linkedin_weekly_connection_limit` (180, at most 150 without Premium); webhook recognises our own messages and parses `AccountStatus` events (all were dropped before). Pre-fix: 23 of 24 tests fail.
  - Help articles 6, 9 and 11 updated in both copies.
  - Reported, not fixed: Gmail threadId without In-Reply-To/References (follow-ups and meeting drafts; outbound rows do not store their Message-ID); Apollo `email_status: extrapolated` emails are accepted (Bounce Shield still verifies before send); free LinkedIn accounts allow 200-character notes, the code cuts at 300; Unipile webhook replies are processed synchronously (30 s window); the hosted-auth `notify_url` callback is unauthenticated (handler not reviewed); remaining TODO markers in Hunter, Calendly, Zoom, Recall, HubSpot, voice providers and frontend hooks.
- 2026-10-27 — **F-QA-02, WhatsApp: 27 "TODO: verify" markers checked against Meta's public docs** (founder choices in session: "International only", "v25.0").
  - Checked without any account or key, on developers.facebook.com, 2026-10-27. 17 matched; 8 partly; 2 wrong. No WhatsApp marker remains; 94 remain in other integrations.
  - Fixed: default Graph API version v20.0 (expired 2026-09-24; Meta runs such calls on the next oldest usable version) -> v25.0. Sends carry '+<digits>'; a number not stored with '+' or '00' is refused (ComplianceError `whatsapp_phone_not_international`), because Meta prepends the business's own country code to a number without '+'. Template bodies with {{n}} send Meta's required examples (each placeholder's description). PAUSED / DISABLED / ARCHIVED / DELETED / PENDING_DELETION templates stop being sendable; REINSTATED / UNARCHIVED restore them. Status lookup by template id, and paging by name. "played" status mapped. Text over 4,096 characters fails locally.
  - Pre-fix: 24 of the 30 tests in `tests/test_whatsapp_cloud_contract.py` fail on master; the other 6 pin kept behaviour.
  - Help article 10 updated in both copies, fact-checked (11 supported, 1 corrected before commit).
  - Reported, not fixed: a refused number is recorded as NEEDS_TEMPLATE and counted under "need template" on the campaign card; the template-webhook dedupe key `tmpl:{id}:{event}` drops a repeated event (a second PAUSED after a reinstatement), so Sync is needed; every `failed` delivery is treated as a hard bounce, though Meta codes such as 131050 (user stopped marketing messages) are closer to an opt-out; inbound `product` interactive replies come through as empty text.
- 2026-10-27 — **Send-time slots per account** (founder choice in session: "Send-time per account").
  - Before, on master: the nightly `update_send_time_scores` cached one slot list per (channel, ICP industry, ICP size) for the whole install, and `GET /playbook/send-times` served it to every account with playbook access. One account's send hours and reply rates were shown to the others.
  - Now: the job groups by the strategy's owner (`products.user_id`) and writes `send_time:slots:{owner_id}:{channel}:{industry}:{size}`. The API reads only the caller's key; a caller with no data of their own gets the default hours. Old pooled keys are never read again and expire within 25 h (TTL).
  - Pre-fix: the 3 new tests in `tests/test_send_time_slots_per_account.py` fail; the API test shows the other account's Thursday 15:00 slot first.
  - Two existing tests now pass an owner (`tests/test_revenue_analytics.py`, `tests/integration/test_chunk4_advanced_learning.py`); their assertions are unchanged. The integration file passed on local PostgreSQL 16 (21 passed).
  - Not covered: the scheduler's own send-time learning (`app/services/send_time.py`) was already per owner and is unchanged. `SendTimeCard` is imported by no page.
- 2026-10-27 — **Offline fixes: admin Playbook Scores page, subject-line examples** (founder choice in session for the second: "Drop the example text").
  - Admin Playbook Scores page: `GET /admin/playbook/scores` answers `{scores: [...]}`, but the page used the reply as a bare array, so it crashed on `scores.map` whenever a row existed. It also read `last_updated`, which the API never sends. It now unwraps `scores`, shows `last_aggregated_at`, and has an Account column; rows from before 0093 read "pooled (before 0093)". Pre-fix: both page tests fail with `scores.map is not a function`.
  - Subject-line patterns: `subject_line_patterns` is one aggregate for the install, read by every user with playbook access. Each row kept `example`, a real sent subject line from whichever account sent it; "anonymize" replaced only `{braces}`/`[brackets]`. No example is now stored or returned: the nightly write sets it NULL (which clears old rows), the API and the prompt helper name none, and the card and help article 34 say the rates are pooled. Checked on PostgreSQL 16.15 with the migrated schema: an old row's example became NULL and new rows got ids.
  - Also in that module: both error handlers called `db.rollback()` (undefined name, swallowed), so a failed query left the caller's session in a failed transaction. They now roll back `db_session`. The ORM model's timestamp is `last_updated`, as migration 0010 creates it (it said `updated_at`; nothing read it), and the upsert supplies the row id, which the SQLite test schema has no default for.
  - Pre-fix: 4 of the 6 new subject-pattern tests fail on master (the leak is shown by the extraction test: `example='Quick question for Zyntrex, Priyanka?'`; two fail earlier on the column name); the 2 rollback tests fail without that fix; the card test fails on the old card.
  - Not covered: the rates themselves stay pooled across accounts (founder choice). Send-time scores (Redis, keyed by channel, industry and size) are also pooled; they hold hours and rates, no text. `SubjectLineCard` is imported by no page. The new tenancy tests are not sealed yet (a `gate/` branch item).
  - Process: the first full run BLOCKED (2 of 8,182) on a stale `scripts/help_center/index_manifest.json` after the article edit; regenerated with `--write-manifest` (hashes only, no embedding call). A local `node_modules` with vitest 1.6.1 (lockfile: 5.0.5) made the frontend type-check fail until `npm ci`.
- 2026-10-25 — **Offline fixes after F-SEC-05** (founder-approved in session):
  - Findings register: statuses as of 2026-10-25, each re-read on master `ec2952e` (#140).
  - F-AI-05: an applied mutation's model-written angle and messaging lose any value with a link or instruction before they go in front of the research as "takes precedence".
  - F-DB-04, step 1 of 3: migration 0095 makes `playbook_scores.score` nullable, and the trend reads `reply_rate`. Steps 2 (stop writing `score`) and 3 (drop it) each wait for the previous deploy on API and worker.
  - docker-compose: PostgreSQL runs from `pgvector/pgvector:0.8.1-pg16`, as in CI. DEPLOY.md has the one-time dump-and-restore move for existing volumes; it was not rehearsed here (no Docker daemon in this environment).
  - F-OPS-03: `app/config.py` is the one Settings class, and `app/core/config.py` re-exports it. All 48 moved fields keep their type and default, and the 19 shared names read the same env var. Moved fields now also accept a lower-case env name.
  - Reported, not fixed: `DEPLOY_RENDER_FREE.md` calls `FRONTEND_URL` "inert, read nowhere", but share links, OAuth consent, billing redirects and the personalization link check read it.
- 2026-10-25 — **F-SEC-05, WhatsApp templates half: admins manage templates** (founder choice in session: "Admins manage"). Gate PR #138 first: the sealed opt-out API test now creates its template as an admin, with the same assertions.
  - Before, on master: any signed-in user could create, edit, submit to Meta, sync and generate the install's templates. Generated drafts, written from one account's private research, were readable by every user.
  - Now: those five routes require an admin; generate still checks that the strategy is the caller's. A non-admin lists and reads APPROVED templates only. The Campaigns template panel is its own component, and its controls appear for admins only. `is_admin` is shared from `app/api/deps.py`; attribution and website builder used their own copies. Help article 10 updated.
  - Pre-fix: on the old code, the 2 non-admin tests fail; the 2 admin tests pin kept behaviour.
  - Not covered: a non-admin can still name a draft template in a sequence step by typing its name (the server accepts any template that is not rejected); it sends only once approved. Nothing imports `frontend/src/components/campaign/TemplateManager.tsx`, so it looks unused; reported, not removed. Making the install owner an admin is `ADMIN_EMAIL` (startup) or `app/cli/create_admin.py`.
- 2026-10-25 — **F-SEC-05, website builder half: pages with no workspace are admin-only** (founder choice in session: "Admin-only").
  - Before, on master: any user on a plan with the builder could see, regenerate, publish and unpublish our marketing pages. Workspaceless users shared one bucket and saw each other's pages. Each generation put every workspace's published titles into the prompt. The public sitemap and the export listed customers' pages under leadpilot.io.
  - Now: a customer reaches only their workspace's pages, and only an admin reaches the global ones. A non-admin with no workspace gets 409 `workspace_required`. `POST /site/export` is admin-only, and the export and sitemap hold global pages only. The link menu holds only the same site's pages. Publishing a customer page no longer queues our export. The page limit counts only the bucket being written to.
  - Pre-fix: on the old code, 11 tests fail (the 10 new `TestTenancy` tests and the changed `test_publish_page`).
  - Not covered: rows created before this by non-admins with no workspace stay in the global bucket. They become admin-only, and there is no creator column to hand them back. Slug uniqueness stays global, so a 409 on create still reveals that a slug exists somewhere. Where customers' published pages are served is not defined in this repo, except for download. The WhatsApp templates half of F-SEC-05 is still OPEN.
- 2026-10-22 — **Victorae Phase 3, I2: deterministic output checks** (12 fix commits, local, on top of I1).
  - Known defects 41 → 23. Baseline 1,087 → 1,105 cases at pass@1 1.0. No slice below 1.0.
  - Fixed: the `$2.5M` split (T05); "I'm Priya, a real person" (T14); empty research steps saved (T01); the roleplay tell (T21); the nested ICP read flat by the scorers (I3: T27, T28); links in LinkedIn copy (T13), reply drafts (T09) and WhatsApp slots (T04); over-long email bodies (T03); WhatsApp drafts with links, guarantees or invented figures (T33); instructions and links in style and voice profiles (T26).
  - Process miss: one commit edited a sealed test file. The next commit restored it and moved the cases; no sealed file differs from master.
  - Remaining 23: invented content that needs a source comparison (19) and the verification loop (4).
  - Report: `docs/program/ai/phase3-i2-output-checks.md`.
- 2026-10-21 — **Victorae Phase 3, I1: consent defects fixed in code** (5 commits, local, on top of item 4 and I4).
  - Known defects 53 → 41. Baseline 1,075 → 1,087 cases at pass@1 1.0. No safety slice regressed.
  - Opt-out replies get no model call and no draft (T09). A later rewrite cannot silently drop what the legal pass approved (T31, presence floor only). Spoken stops on outbound calls are recorded without the model (T15). Foreign links are stripped from email bodies (T03). Promotional WhatsApp "utility" drafts are saved as marketing (T33).
  - Founder decisions: `not_interested` drafts, no-lead callee suppression, the promotional word list, third-party link stripping.
  - Not measured live (no eval key). Report: `docs/program/ai/phase3-i1-consent.md`.
- 2026-10-21 — **Victorae Phase 3 item 4 (Batch API): decision record, not built.**
  - The item's premise fails: pipeline steps and verification passes are chained, each reads the previous output, so they cannot be batched within a strategy.
  - Never batch: send-path rendering and claim check, reply routing and consent, call analysis (stop requests), meeting prep.
  - Ranked candidates: displacement DMs, idle mutation, lead scoring with Loom, F-P-T-A at enrollment, website bulk.
  - Building needs a migration, submit and poll tasks, and metering at the 50% price. Its value depends on the founders' `api_usage` volume query.
  - Proposed threshold: build if the candidates spend more than about $50 a month.
  - Record: `docs/program/ai/phase3-item4-batch-api.md`.
- 2026-10-21 — **Victorae Phase 3 item 3 (prompt caching)**, local commits on top of item 2.

  **Measured first:** no touchpoint's stable prefix in the golden replays reaches sonnet-4-6's 1,024-token minimum. The production volume is in the verification loop (10 passes × document + research) and the research pipeline (72/144 steps).

  **Done**
  - Gateway `cache_prefix` with a 5-minute breakpoint.
  - Metering at the real cache price (input + 1.25× writes + 0.1× reads). Without it, the spend caps would under-count cached calls.
  - Judge, fixer and pipeline prompts reordered stable-first.
  - The live runner reports `cache_hit_rate`.

  **Proven offline:** byte-identical prefixes across the 10 judge passes and across the 9 steps of each phase (each check fails on the old layout); verification 2,048 passed; pipeline 5,104 passed; PostgreSQL integration 155 passed.

  **Not measured:** hit rate and cost (no eval key); the ~78% judge-input saving is an ESTIMATE.

  **Process miss, caught:** item 2's first full run BLOCKed (pass rate 0.99898). A test fake in `tests/help_center/` did not accept `schema=`. The per-group checks had searched only `tests/*.py`. Fixed (`c2a64e6`); searches now include subdirectories.

  Report: `docs/program/ai/phase3-item3-prompt-caching.md`.
- 2026-10-22 — **Help AI v2 Phase 2 (indexing)** on `claude/awesome-lovelace-okii9j`, after PR #112 (Phase 1) merged. Plan and evidence: `docs/program/plans/help-ai-v2-02-indexing.md`.

  **Done**
  - `app/services/embeddings_client.py`: the only Voyage gateway. Contextualized embeddings and rerank, a hash-vector mock, retries and timeouts, metered through `usage_meter` as `voyage` / `voyage_rerank`.
  - Alembic `0094_help_chunks`: halfvec(1024) + HNSW, and a generated tsvector + GIN. Without pgvector the table is created text-only; SQLite gets only the mapped columns.
  - `app/services/help_index.py`.
  - `scripts/help_center/reindex.py`: idempotent, prints a diff, with `--check`.
  - A non-fatal reindex in `scripts/render_api_entrypoint.sh`.
  - The committed `index_manifest.json`, checked in CI.
  - CI's PostgreSQL moved to `pgvector/pgvector:0.8.1-pg16`.
  - DECISIONS HELPAI-RERANK (`rerank-3-lite`) and HELPAI-INDEX.

  **Exit gate 2 (local PostgreSQL 16.15 + pgvector 0.8.1, mock vectors)**
  - Second reindex: 0 writes, 0 re-embeds.
  - Vector search: 10/10 fixed golden queries top-1. HNSW is used, and its order equals an exact scan.

  **Not verified:** real Voyage calls and quality (no key in this environment); Neon's pgvector version (founder query pending).

  **Reported, not fixed:** `docker-compose.yml` and `docker-compose.prod.yml` still use `postgres:16-alpine` (no pgvector). Switching an existing alpine data volume to the Debian-based pgvector image risks collation changes, so that is its own change. (Done 2026-10-25, with the dump-and-restore steps in DEPLOY.md.)
- 2026-10-21 — **Victorae Phase 3 item 2 (output contracts)**, local commits on top of PR #122 (item 1, open). Phase 2 (#121) was merged 18:11Z from the web UI with no review recorded, not via `safe_merge.sh` (MERGE-B bypass, reported).

  **Done**
  - `complete_json(schema=)` sends `output_config.format` only to structured-output models.
  - All 38 call sites name a schema (39 schemas).
  - Coverage is enforced.

  **Proven offline**
  - API-valid schemas: 39/39.
  - Every good golden answer fits its schema; a wrong schema was shown to turn T07 red.
  - Evals unchanged: 1,173 passed, 53 xfailed. Sealed tests: 801 passed.

  **No change on claude-sonnet-4-6** (not a structured-output model). The benefit comes with item 6, which needs the eval key and I4.

  Known limits: WhatsApp variable keys 1-20; closed JSON-LD keys on an unread field. Report: `docs/program/ai/phase3-item2-output-contracts.md`.
- 2026-10-21 — **Victorae Phase 3 item 1 (prompt injection)** on `claude/bold-heisenberg-58xvzj`, local commits on top of PR #121 (Phase 2, open, all 7 required checks green). Not pushed until #121 merges.

  **Done**
  - `complete_json` returns an object or raises ValueError (output contract).
  - Untrusted text is wrapped in 17 modules, including the live call's system prompt.
  - ICP criteria are validated.
  - The re-engagement brief's second-order injection is closed.
  - Analysed model text (our sent message, the draft under claim check) is delimited.

  **Before and after (offline):**
  - known defects 102 → 53;
  - baseline 1,026 → 1,075 cases at pass@1 1.0;
  - all 43 pinned injection defects and all 6 pinned output-shape defects fixed;
  - no safety slice regressed.

  Live before/after: blocked (no eval key). Report: `docs/program/ai/phase3-item1-injection.md`.

  **Found by audit, not by a golden set:** the re-engagement brief path (HIGH) and four analysed-text sites; each now has a failing-first test.

  **Reported, not fixed**
  - Dependabot #117 (on master) broke a fresh `npm ci` (vitest 5 vs @types/node 20), so Vercel builds from master likely fail. A founder decision.
  - Research text is wrapped inconsistently; this is for item 5.
- 2026-10-21 — **Victorae Phase 2 (AI eval foundation)** on `claude/bold-heisenberg-58xvzj` after PR #118 (Phase 1) merged at 11:42Z. #118 was merged from the web UI as a merge commit, not via `safe_merge.sh`, and before the fresh-session /code-review and /security-review: that bypasses MERGE-B again (reported, not fixed). Master was merged into the branch, not rebased (no force-push).

  **Done**
  - `evals/ai/`: the framework (specs, replay client, code graders, gate rules) and 34 golden sets, one per inventory touchpoint except T16 (live Vapi) and T36 (manual scripts): 1,128 cases. A coverage test fails if a touchpoint has no set.
  - Offline gate in CI via `tests/test_ai_evals.py`, plus `tests/test_ai_live_eval.py`: 1,095 passed, 102 strict xfail.
  - Offline baseline `evals/ai/baseline.json`: 1,026 cases at pass@1 1.0.
  - Opt-in live runner `evals/ai/live_eval.py`:
    - refuses in CI, without `EVAL_ANTHROPIC_API_KEY`, over $250 per run, or over $1,100 on the ledger;
    - applies `evals/ai/sandbox.py`, because `.env` points Redis at production;
    - refuses if any case replays differently than in the gate;
    - meters every call before sending.
  - Worst-case k=3 dry runs: sonnet-4-6 $157, sonnet-5-5 $104, haiku-5-5 $5, opus-5-5 $209.
  - Broken-prompt proof: `docs/program/ai/evidence/broken-prompt-T06.md` (4 safety cases fail, all 33 pass after the revert).

  **Found:** 102 known defects, written into the sets with file:line (summary in `docs/program/ai/eval-baseline.md` §3). Largest groups:
  - unwrapped untrusted text in prompts (I2);
  - consent: unsubscribers get sales drafts (T09); outbound "stop calling" is model-only (T15); a "utility" cold pitch has no opt-out (T33); rewrites are not re-judged by the legal pass (T30);
  - invented claims sent: "$2.5M" mangled (T05); Copilot auto-sends some invented claims (T11);
  - crashes in code documented never to raise (T28, T29, T34);
  - I3 ICP nesting.

  **Not done / blocked**
  - Live baseline: no eval key from the founders.
  - Judge calibration (`evals/ai/calibration.json` is empty, so the judge gates nothing).
  - Noise bands: need two live baseline runs.
  - The new eval test files are not on the sealed list; that needs a `gate/` PR.

  **Next:** the Phase 2 PR, then Phase 3 (I1, I2, I4 first; fixing a defect removes its xfail marker).
- 2026-10-20 — **Victorae Phase 1 (rename + brand_lint)** on `claude/bold-heisenberg-58xvzj`, commits `7761324`..`ee5188e` (12), NOT pushed yet: waiting on the founders' choice (merge PR #115 first, then a new PR; or add to #115). Done: Class A renamed per area (backend, docs + help center, web, Android/SDK/native, root/config); brand formula is "Victorae" (no "by Calendarharvest", VIC-NAME); About page says formerly LeadPilot; defects fixed with before/after tests: BR-D5 DM guard misses Victorae (4/7 names passed before), BR-D1 website builder wrote customers' pages as us (5 new tests failed before), BR-D2 WhatsApp sample "Sam from Vectorae"; `scripts/gate/brand_lint.py` + `.brand-allowlist` (every entry B:/C: with reason; stale entries are errors) wired as quick-gate step `brand` and as repo-wide test `tests/test_brand_lint.py` (CI gate job runs it; workflows are sealed). Evidence: `run_evals.py` 6,644 tests 0 failed, `promote.py` PROMOTE (soft runtime WARN 1,227 s vs 900 s, pre-existing); BLOCK fixtures fire; PostgreSQL 16 local: migration round trip exit 0, integration 155 passed; frontend tsc 0, lint clean, vitest 1,237 passed; status page 77 passed; SDK 74 passed; static export built, check_static_seo PASS (64 URLs), built output 3,347 old-name hits, all under allowlisted B/C reasons (87 from C sources: Terms/Privacy, consent screen, voice-twin text, logo alt, formerly line); API: OpenAPI title "Victorae API", 155 parameterless GETs called, the only old names come from C sources (billing catalogue, compliance/consent disclaimers, voice consent, LinkedIn disclosure); screenshots of 13 pages: titles all Victorae; the logo PNG and homepage video still show LeadPilot (C, founder-actions M1/M2). Sabotage: an injected "LeadPilot" line and a renamed file both fail brand_lint and the repo test. Not done / limits: no Android build (no Android SDK here; repo lacks Gradle root files); POST endpoints, emails and push not exercised live (unit tests only, FakeClaude/SQLite); tutorial titles already seeded in a database keep the old title until reseeded; support-chat rows cite old heading slugs (30-day retention). Mistakes caught and fixed: a docstring rename in the hashed eval adapter made promote BLOCK as stale (restored, allowlisted); billing files were touched by the bulk rename and reverted (out of scope). Reported, not fixed: website builder canonical origin is https://leadpilot.io for every page; its internal-link menu lists published pages of ALL workspaces (cross-tenant titles in a prompt); a user without a workspace creates global pages. Founder actions: `docs/program/brand/founder-actions.md`. Next: Phase 1 PR + merge via safe_merge.sh, then Phase 2.
- 2026-10-20 — Victorae program: founder answers recorded (VIC-NAME: Victorae is a new name, no "by Calendarharvest"; VIC-TM: not trademarked; VIC-KEEP, VIC-BACKLOG, VIC-SPEND as recommended; VIC-ORDER: F-DB-01 first). **F-DB-01 fixed on `claude/bold-heisenberg-58xvzj`** (commits `ffce72b`, `0dab560`; not yet merged): migration `0093_playbook_scores_owner` (owner column, per-account unique key; pooled rows kept with user_id NULL, never read), per-account aggregation and readers, export/erasure ownership, help articles 24/29/34 (fact-checked: 16 supported, 1 corrected). Evidence: offline `run_evals.py` 6,574 tests 0 failed, `promote.py` PROMOTE (soft runtime WARN 1,288 s vs 900 s, pre-existing: baseline 1,020 s), BLOCK fixtures all fire; PostgreSQL 16 local: migration round trip exit 0, integration suite 155 passed, sealed export/erasure tests via pg_root_suite 187 passed. Pre-fix: with the column present and old readers, 8 new isolation tests fail. Process miss: the first push of `0dab560` happened before the quick gate's frontend step passed (exit code masked by a pipe; hooks were not installed in this clone); the gate was then completed on the same tree (7/7 PASS) and hooks installed. Follow-ups (reported, not fixed): seal the new isolation tests on a `gate/` branch; admin Playbook Scores page likely cannot render (expects an array, gets `{scores: [...]}`); whether `subject_line_patterns` and send-time scores also pool across accounts is unchecked; `oldest_outcome_ts` is tz-aware in the model but not in migration 0010. Next: open the PR for F-DB-01 (founder go-ahead), then Phase 1.
- 2026-10-20 — Victorae program (claude/bold-heisenberg-58xvzj): prompt saved verbatim to `STAR_VICTORAE.md`. **Program start SHA: `7872f9c6d4cda7796d43ed3fe486799a197ed74d`** (master, merge of PR #114). Phase 0 recon done (read-only; no code changed): `plans/victorae-00-recon.md`, `brand/rename-inventory.md`, `ai/ai-inventory.md`, `ai/ai-backlog.md`. Findings: the code is already branded **Vectorae** (PR #113; 2,896 occurrences) and "Victorae" appears 0 times, so the name must be confirmed (Q-a); no P0 open in code, but F-DB-01 and RF-5 are OPEN HIGH (sign-up open), and F-DB-01 puts pooled cross-account rates into pipeline prompts under a false "this account" label; 36 AI touchpoints on `claude-sonnet-4-6`, none with an eval; the gateway's `temperature=0` on judge calls blocks every Claude 4.7+ candidate (HTTP 400). Live-eval estimate ≈ $950–1,050 for the program (code token sizes, no metering access). **Waiting on founder answers Q-a to Q-f; Phase 1 has not started.**
- 2026-10-18 — Help AI v2 (claude/awesome-lovelace-okii9j): Phase 0 recon (`plans/help-ai-v2-00-recon.md`; production widget was on the mock fallback, Anthropic key 401, since replaced by the founder) and founder decisions HELPAI-*; Phase 1 corpus: 44 articles (14 new, 4 merged; the corpus does not earn 50), 857 first-pass sentences fact-checked to 0 unsupported / 0 contradicted (`help-center-factcheck.md`), new drift test `tests/help_center/test_code_facts.py` (380 code-sourced numbers checked against code, every number-with-a-unit registered, prices only from code), golden set 151 answerable / 31 must-refuse, lexical recall@6 0.967, mock-mode coverage floor 0.55 → 0.60 (`help-center-eval.md`), 20 new out-of-scope defects (#29–#48 in `help-center-discoveries.md`). Next: Phase 2 (Voyage embeddings, pgvector `help_chunks`, reindex).
- 2026-10-17 — Claude / ChatGPT connector (claude/awesome-lovelace-okii9j): recon + founder decisions CONN-* (#105, merged), read-only MCP server at /mcp (#106, merged), OAuth 2.1 authorization server (#107), three write tools, Skill + Claude Code plugin; off until `MCP_ENABLED=true` on production. Verified offline (6,110 tests, PROMOTE), on PostgreSQL 16 (integration 151 passed, migration 0091 round trip) and against a running server with MCP Inspector; NOT yet verified from claude.ai / ChatGPT (needs the public host) — `docs/program/plans/connector-05-verification.md`. Follow-ups F1–F6 there (F1: seal the connector guard tests on a gate branch).
- 2026-10-16 — GEO publish round (claude/relaxed-lamport-ab7g3j, not merged): 12 GEO pages, /help/ (30 articles), /llms.txt, explicit robots.txt, both founders (Ahmad Shahid, Rehan Masood); founder answers in `docs/program/ai-visibility/NOTES.md`; published before trademark review by founder choice — `docs/program/ai-visibility/geo/state.md`.
- 2026-10-15 — GEO/AEO system (docs only, claude/relaxed-lamport-ab7g3j): entity card, niche statement, 50-query library, competitor analysis, page drafts (definition, 10 use cases, 5 comparisons, 30 FAQs, sourced statistics), JSON-LD/robots/llms.txt/architecture, 5 platform playbooks, 50-platform citation plan, Reddit and syndication plans, tracker; nothing published (trademark gate, Q-04); 15 founder questions — `docs/program/ai-visibility/geo/state.md`.
- 2026-10-13 — AI visibility Part A (seo/ai-visibility): canonical www host, static plan names, product-facts source, JSON-LD, copy contradictions removed, IndexNow script, git lastmod, check_static_seo.mjs as postbuild; Part B waits for trademark review — `docs/program/ai-visibility/REPORT.md`.

## 2026-10-10 — Phase 1, items 1.6 and 1.7, cloud session

**State: all Phase 1 PRs open: #47, #48, #49, #50, #51, #52 and this one (GATES.md). Merge order: #48 → #47 → #49 → #50 → #51 → #52 → this.**

- 1.6 (#52): `verify.yml` (postgres, frontend, image, security) + `post-merge-alarm.yml`. Validated locally on PostgreSQL 16 + Redis 7 (integration 132 passed; migration round-trip exit 0).
- Sabotage proof (#53, closed): all four verify jobs failed at the intended step. The first run exposed a scanner bug (`db\b` excluded `db.example.com`); fixed in #51 with regression tests; second run red as required.
- PD-2 (#50): `frontend/package-lock.json` out of sync; `npm ci` failed on master. Fixed; Vercel preview build green; vitest 721 passed.
- 1.7: `docs/program/GATES.md` — layers, what is not enforced, proofs with run URLs, founder steps for Render/Vercel deploy gating, minutes.

Next (after merges): add `verify-*` to `required_checks.txt` once green on master; founders set Render/Vercel deploy gating; then Phase 1 exit. Open P0/HIGH items (spend cap, GDPR account export/deletion, plan enforcement, sign-up shut-off P0-D-ENF) are next, one PR each.

## 2026-10-10 — Phase 1, items 1.2, 1.3, 1.5, cloud session

**State: PRs #47 (1.2) and #48 (1.4) green; #49 (1.3) open; item 1.5 PR stacked on #48.**

- 1.2 (#47, sealed): `eval-gate.yml` runs on `pull_request` + `push` to master only; superseded PR runs cancelled. Verified on #47: one `gate` run per commit, no `push` run on the branch.
- 1.3 (#49, sealed): `offline_test_count` floor 1,900 → 3,150; baseline rebuilt from 2 runs (3,319 tests, 436.7 s); fixtures re-measured with real sabotage runs. PROMOTE; all hard gates fire; live-fire BLOCK.
- 1.5: `scripts/gate/quick_gate.sh` (secrets, syntax, critical lint, single Alembic head, registry lint, related + sealed tests, frontend if changed; stamps the tree on a clean full pass), `scan_secrets.py`, `full_gate.sh`, `.githooks/pre-push` + `install_hooks.sh`, Claude hooks `require_gate.py` (no `--no-verify`, no unstamped push), `protect_sealed.py` (gate files only on `gate/`/`claude/gate-` branches), `stop_check.py` (once per stop, never loops). Tests: `tests/test_gate_scripts.py`.


## 2026-10-10 — Phase 1, item 1.4 (safe_merge + merge hook), cloud session: PR open

**State: merge gate built and tested; PR open. PRs #44, #45, #46 are merged (INC-44/45 recorded in `DECISIONS.md`).**

- `scripts/gate/safe_merge.sh` → `safe_merge.py`: refuses unless the PR is open, targets master, is not a draft, has no conflict, is not behind master, every check in `scripts/gate/required_checks.txt` (today: `gate`) succeeded on the head SHA, no other check is running/failed, and no commit status is pending/failed. Squash-merges with the head SHA pinned. `--dry-run` checks only. Live dry-run against PR #46 refused it correctly (closed; behind master).
- `.claude/hooks/guard_merge.py` + `.claude/settings.json`: PreToolUse hook blocks `gh pr merge`, REST calls to `pulls/<n>/merge`, `git push` to master/main (incl. `HEAD:master`, `+master`, bare push while on master) and MCP merge tools. `safe_merge.sh` is allowed.
- Tests: `tests/test_safe_merge.py` (22) and `tests/test_claude_hooks.py` (26), offline.
- Still not enforced: the GitHub web UI Merge button. Deploy gating (item 1.7) is the backstop.

Next: item 1.2 (sealed: eval-gate triggers), 1.3 (sealed: baseline refresh), 1.5 (quick gate + pre-push + more hooks).

## 2026-10-10 — Phase 1, item 1b (PD-1 webhook schema), cloud session: PR open

**State: PostgreSQL integration suite 132 passed / 0 failed with migration 0071. Stacked on PR #44; merge #44 first.**

- Migration `0071_webhook_schema_sync`: `webhook_targets.event_types` text[] → json; `webhook_deliveries.payload_json` text → json (invalid JSON kept as `legacy_text`); `last_status_code` (copied from `last_response_code`) and `delivered_at` added. Nothing dropped. Downgrade tested.
- Correction: `subject_line_patterns` is NOT part of PD-1. Live raw SQL matches the migrated table; the ORM model is stale. Reported only.
- Deploy: the next API deploy runs 0071 on Neon. Founders: run the read-only query in `docs/program/plans/phase1-01b-pd1-webhook-schema.md` first.
- Detail: `docs/program/plans/phase1-01b-pd1-webhook-schema.md`.

PR: https://github.com/Rmasood1122/LeadPilot/pull/46 (draft; merge after #44).

Next: item 2 (sealed PR: eval-gate.yml triggers).

## 2026-10-10 — Phase 1, item 1 (PostgreSQL integration repair), cloud session: PR open, suite NOT green

**State: 54 failed / 70 passed → 125 passed / 4 errors. The 4 errors are product defect PD-1, reported, not fixed. Waiting on founder review of the PR and on the A/B merge-policy answer (Amendment 0).**

- Root causes, all 54, by group: `docs/program/plans/phase1-01-integration-repair.md`. G1 (50): root conftest Anthropic guard; the integration mock never intercepted anthropic SDK 1.x (`httpx2`), so lifting the guard sent a real request to api.anthropic.com. G2 (7 of G1): Calendly mock signed with a 2023 timestamp, refused by F-OPS-02 replay check. G3 (4 of G1): `/suppression` is admin-only (F-SEC-04); tests called it as a user and ignored the 403. G4 (4): `webhook_delivery` API rebuilt in `41cea92`; tests ported to the current API.
- PD-1: `webhook_targets.event_types` is `text[]` (migration 0011) but `JSON` in the model; creating any outbound webhook target fails on PostgreSQL. Needs its own PR with a migration.
- Offline gate unchanged: 4 commands exit 0, 3,319 tests, PROMOTE (1 soft runtime warning).
- Amendment 0: founders chose merge policy **B** (2026-10-10). Recorded as INC-43 and MERGE-B in `DECISIONS.md`; 3 lines added to `CLAUDE.md`.

PR: https://github.com/Rmasood1122/LeadPilot/pull/44 (draft).

Next: founders review the PR; decide whether PD-1 is fixed before `verify-backend-postgres` becomes required. Then item 2 (eval-gate.yml triggers, sealed-file PR).

## 2026-10-10 — Phase 0 (baseline), cloud session: completed

**State: Phase 0 done. The exit-gate stop condition is met (OPEN P0/HIGH items), so Phases 2–4 wait. Phase 1 may start.**

Done in this session:

- Dependencies installed on CPython 3.12.3 in `/home/user/.venvs/leadpilot312` (exit 0, 58 s).
- **Offline gate (step 3) run to completion**, four commands exactly as `CLAUDE.md` lists them, nothing else running: all exit 0; 3,319 tests executed (3,326 collected, 7 skipped), 0 failed; `run_evals.py` 530.5 s wall; `promote.py` = PROMOTE with one soft runtime warning; all 3 hard gates proven to fire. Peak memory 1.3 GB — `BASELINE.md` §4.
- Founder decisions recorded in `DECISIONS.md`: D3 (repo private, 2026-10-10), P0-D (self-hosted, single-tenant, 2026-10-04), D2 deferred to Phase 3.
- F-DB-01 and RF-5 re-rated HIGH → MEDIUM under P0-D, with reasoning — `BASELINE.md` §3.5. GDPR account export/deletion, P0-5 per-account spend cap and plan enforcement are **not** downgraded.
- New finding **P0-D-ENF (HIGH)**: public sign-up cannot be disabled, so single-tenancy is not enforced by the code — `BASELINE.md` §3.5.
- Phase 1 inputs — `BASELINE.md` §11: GitHub Pro is needed for the ruleset on a private repo. `eval-gate` bills 9 min per run (measured on CI run 38038727255); runs per PR = 3 + 2N; about 31 PRs/month on Free and 47 on Pro at N = 2.
- Draft PR: https://github.com/Rmasood1122/LeadPilot/pull/43.
- Correction: `BASELINE.md` §9 row 9 was wrong (`CLAUDE.md` no longer names a migration chain since `609af9a`).
- Non-English text check on `BASELINE.md`, `PROGRESS.md`, `status.json`: none found; nothing to rewrite.

Earlier (2026-10-10, laptop session): git state, findings reconciliation, PostgreSQL suite (54 failed / 70 passed, harness stale), production read-only checks and D2 diagnosis, Help AI diagnostic SQL, prompt-vs-reality discrepancies. The laptop's offline-gate run was killed for low memory; superseded by the run above.

Follow-up decisions, same day (`DECISIONS.md`): GitHub Pro will not be bought, so the Layer 4 ruleset is dropped and the founders will give a replacement design next session. D5 is a written policy only; GitHub cannot enforce it. `CLAUDE.md` lines 11 and 19 updated (single-tenant per P0-D; 3,319 tests, ~9 minutes).

Open questions for the founders:

1. D1 (website copy vs. code) and D6 (status page key and DNS) are unanswered. *(Superseded: D1 was decided 2026-10-10 and D6 resolved 2026-10-11 by the /status page on the main site -- `DECISIONS.md`.)*
2. Is sign-up open on the current Render production? (UNVERIFIED: no production requests were made in this session.)

Next:

- Phase 1, using the founders' replacement for Layer 4. First task: repair `tests/integration/` so the PostgreSQL suite is green without weakening any test. Only then can `verify-backend-postgres` be a required check.
- Phases 2–4 stay blocked until the OPEN P0/HIGH items in `BASELINE.md` §1 are fixed or accepted in writing.
