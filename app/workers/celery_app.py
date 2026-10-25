"""Celery application shared by the worker and beat containers.

Start worker:  celery -A app.workers.celery_app worker --loglevel=INFO
Start beat:    celery -A app.workers.celery_app beat   --loglevel=INFO

Task modules are registered via `include` as they are built:
  Chunk 4: app.workers.tasks (pipeline step execution)
  Chunk 5: verification tasks
  M8:      nightly learning-loop aggregation (Celery Beat schedule below)
"""

from celery import Celery

from app.config import settings

celery_app = Celery(
    "leadpilot",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=[
        "app.workers.tasks",
        "app.workers.lead_tasks",
        "app.workers.outreach_tasks",
        # M8 additions
        "app.workers.learning_tasks",   # nightly aggregation + A/B promotion
        "app.workers.webhook_tasks",    # C5 reliable outbound webhooks
        "app.workers.beat_heartbeat",   # C3 beat liveness key
        "app.workers.support_tasks",    # Feature 3 chat retention purge
        # Engagement Hub: post-booking work, meeting summaries, stale-meeting
        # sweep. The follow-up tasks live in outreach_tasks (already included
        # above) because they send outreach messages and belong on the same
        # queue as every other send.
        "app.workers.calendar_tasks",
        # Private LinkedIn network: weekly re-scoring (Growth plan and up).
        "app.workers.network_tasks",
        # Feature expansion: the notification hub's queue, and the meeting
        # prep brief + pre-meeting reminders that feed it.
        "app.workers.notification_tasks",
        "app.workers.meeting_prep_tasks",
        # Feature Group 3: send-time windows + weekly reply sentiment.
        "app.workers.analytics_tasks",
        # Feature Group 4: HubSpot / Salesforce two-way sync.
        "app.workers.crm_tasks",
        # Feature Group 9: daily sending-domain health + blacklist sweep.
        "app.workers.deliverability_tasks",
        # Feature Group 1: idle-campaign mutation sweep, lead rescoring,
        # market-signal refresh.
        "app.workers.intelligence_tasks",
        # Feature Group 6: AI call transcript analysis.
        "app.workers.call_tasks",
        # Tool integrations: AegisAudit draft audit, PostIQ drafts,
        # SIGNALFORGE lead research.
        "app.workers.tool_integration_tasks",
        # Feature 1: six-hourly pipeline health score.
        "app.workers.health_tasks",
        # Feature 2: reply intelligence (category + next action + draft).
        "app.workers.reply_tasks",
        # Feature 3: founder voice cloning from LinkedIn posts.
        "app.workers.voice_tasks",
        # Feature 4: twelve-hourly competitor displacement sweep.
        "app.workers.displacement_tasks",
        # Feature 5: nightly client ROI snapshots.
        "app.workers.roi_tasks",
        # Website builder: page generation + static export (on demand only).
        "app.workers.site_tasks",
        # Section E: pay-per-meeting charging sweep (+ the metering listener).
        "app.workers.billing_tasks",
        # Feature A2: seal the hash-chained activity audit trail (+ its listener).
        "app.workers.audit_tasks",
        # Feature A4: hourly cross-channel stagnation step.
        "app.workers.channel_tasks",
        # Feature A5: hourly conversion-probability rescore.
        "app.workers.conversion_tasks",
        # Feature 6: nightly anonymised benchmark snapshot.
        "app.workers.benchmark_tasks",
        # F06: Quickstart (website + offer -> review-only drafts).
        "app.workers.quickstart_tasks",
        # F07: Spend Guard 50/80/100% alert sweep.
        "app.workers.spend_guard_tasks",
        # F09: 30-Day Pilot Mode -- hourly scorecard / verdict sweep.
        "app.workers.pilot_tasks",
        # F10: Champion Tracker sweeps (held re-verification, champion refresh).
        "app.workers.champion_tasks",
        # Plan holds: release messages a downgrade held once the plan covers
        # them again (app/services/plan_holds.py).
        "app.workers.plan_hold_tasks",
        # Autopilot launch: advance a strategy's automatic launch + the
        # 15-minute resume sweep (app/services/autopilot.py).
        "app.workers.autopilot_tasks",
    ],
)

# Queue routing.
#
# WITHOUT this block every task was published to Celery's implicit default
# queue, "celery". docker-compose.prod.yml starts its three workers bound to
# -Q pipeline / -Q outreach / -Q learning,default, and monitoring.get_celery_stats()
# reports depth for those same four names — so NOTHING consumed "celery" and
# nothing published to the four queues. In production every worker would idle
# on empty queues while all real work (strategy generation, outreach dispatch,
# reply polling, the learning loop and the beat heartbeat) piled up unconsumed.
# It only worked in dev because the dev worker is started without -Q, which
# makes it consume "celery" by chance.
#
# Every registered task must appear below, and every queue named below must be
# consumed by some worker in docker-compose*.yml. Adding a task without adding
# a route silently strands it in "celery" again.
celery_app.conf.task_routes = {
    # Strategy construction: the 72/144-step pipeline, verification, sourcing.
    "leadpilot.run_pipeline": {"queue": "pipeline"},
    "leadpilot.run_verification": {"queue": "pipeline"},
    "leadpilot.leads.*": {"queue": "pipeline"},
    # Outreach: dispatch, sending, reply polling.
    "leadpilot.outreach.*": {"queue": "outreach"},
    # Nightly learning loop.
    "app.workers.learning_tasks.*": {"queue": "learning"},
    # Everything else: heartbeat, outbound webhooks, ping.
    "app.workers.beat_heartbeat.*": {"queue": "default"},
    "app.workers.webhook_tasks.*": {"queue": "default"},
    "app.workers.support_tasks.*": {"queue": "default"},
    # Engagement Hub. "default" rather than "outreach": these tasks send
    # transactional email and write CRM rows, they do not dispatch outreach,
    # and the outreach worker's concurrency is sized for send throughput
    # against a daily cap. Note the follow-up tasks are NOT here -- they match
    # leadpilot.outreach.* above, which is correct: they send real outbound
    # messages under the same caps and windows as any other send.
    "app.workers.calendar_tasks.*": {"queue": "default"},
    "leadpilot.ping": {"queue": "default"},
    # Weekly LinkedIn-network re-scoring: batched, offline work shaped like the
    # learning loop, so it runs with it.
    "leadpilot.network.*": {"queue": "learning"},
    # Feature expansion: the `notifications` queue. Push/Slack/webhook fan-out
    # and the meeting prep brief are user-facing and time-sensitive -- a
    # one-hour reminder that queues behind a 144-step pipeline or the nightly
    # learning loop arrives after the call has started. Its own queue gets its
    # own worker (docker-compose.prod.yml: celery-worker-notifications).
    "app.workers.notification_tasks.*": {"queue": "notifications"},
    "app.workers.meeting_prep_tasks.*": {"queue": "notifications"},
    # Feature Group 1. The mutation sweep is the learning loop reacting to
    # outcomes, so it runs with it; rescoring and signal refresh are batched
    # model/API work shaped like sourcing, so they run with it. The exact name
    # is matched before the glob.
    "app.workers.intelligence_tasks.check_idle_campaigns": {"queue": "learning"},
    "app.workers.intelligence_tasks.*": {"queue": "pipeline"},
    # Feature Group 6: the phone channel's reply routing -- same queue as
    # every other reply path.
    "app.workers.call_tasks.*": {"queue": "outreach"},
    # Feature Group 3: the learning loop reacting to opens and replies.
    "app.workers.analytics_tasks.*": {"queue": "learning"},
    # Feature Group 4: CRM sync is one user's integration I/O at a time --
    # neither outreach volume nor the learning loop -- like webhook delivery.
    "app.workers.crm_tasks.*": {"queue": "default"},
    # Feature Group 9: outbound DNS / API lookups, one user at a time.
    "app.workers.deliverability_tasks.*": {"queue": "default"},
    # Tool integrations: one outbound call to an internal/partner service at
    # a time -- integration I/O like CRM sync and webhook delivery.
    "tool_integrations.*": {"queue": "default"},
    # Feature 1: the pipeline health sweep is aggregation over outcomes,
    # enrollments and leads -- the same shape of work as the learning loop,
    # and never time-critical to the minute.
    "app.workers.health_tasks.*": {"queue": "learning"},
    # Feature 2: classifying an inbound reply is the reply path, same as
    # leadpilot.outreach.poll_replies and call_tasks above.
    "app.workers.reply_tasks.*": {"queue": "outreach"},
    # Feature 3: one user-triggered model call with a human watching for the
    # result. NOT `pipeline` -- that queue runs 72/144-step strategy builds at
    # concurrency 2, and a voice analysis behind one would take minutes.
    "app.workers.voice_tasks.*": {"queue": "default"},
    # Feature 4: batched external-API-plus-model work over every lead's posts
    # -- the same shape as intelligence_tasks' rescoring, and it must not sit
    # in front of a time-sensitive send or a meeting reminder.
    "app.workers.displacement_tasks.*": {"queue": "pipeline"},
    # Feature 5: nightly aggregation, with the other nightly aggregations.
    "app.workers.roi_tasks.*": {"queue": "learning"},
    # Website builder. A page is the largest single generation in this system
    # (up to 8k output tokens of HTML), so it belongs with the other long
    # model calls and must not sit in front of a time-sensitive send. Note
    # the task names are "workers.site_tasks.*", not "app.workers.site_tasks.*"
    # -- they are registered under an explicit short name.
    "workers.site_tasks.*": {"queue": "pipeline"},
    # Section E: one Stripe call per due meeting -- integration I/O, like CRM
    # sync and webhook delivery.
    "app.workers.billing_tasks.*": {"queue": "default"},
    # F07: the spend-alert sweep -- a few sums per capped account and at most
    # one transactional email; not outreach, not the learning loop.
    "app.workers.spend_guard_tasks.*": {"queue": "default"},
    # Feature A2: short per-account database work.
    "app.workers.audit_tasks.*": {"queue": "default"},
    # Feature A4: may re-channel a scheduled send -- runs beside the sends.
    "app.workers.channel_tasks.*": {"queue": "outreach"},
    # Feature A5: scoring over outcomes, like the other learning sweeps.
    "app.workers.conversion_tasks.*": {"queue": "learning"},
    # Feature 6: read-only aggregation over outcomes, with the nightly jobs.
    "app.workers.benchmark_tasks.*": {"queue": "learning"},
    # F06 Quickstart: a user is waiting for the first draft, so NOT `pipeline`
    # (concurrency 2, occupied by the very strategy run the same request
    # starts). Its lead chain is published to `default` per signature -- see
    # app/workers/quickstart_tasks.py.
    "leadpilot.quickstart.*": {"queue": "default"},
    # F09 Pilot Mode: short per-pilot database work + at most one
    # transactional email per pilot -- account mail, like invitations.
    "app.workers.pilot_tasks.*": {"queue": "default"},
    # F10 Champion Tracker: one Apollo lookup per lead, and a held message is
    # waiting on the first sweep -- NOT behind 144-step builds on `pipeline`.
    "leadpilot.champion.*": {"queue": "default"},
    # Plan-hold release: short database work; a held campaign waits on it.
    "leadpilot.plan_holds.*": {"queue": "default"},
    # Autopilot launch: short database work (its sourcing chain is published
    # to `default` explicitly, see app/workers/autopilot_tasks.py).
    "leadpilot.autopilot.*": {"queue": "default"},
}

celery_app.conf.update(
    # A task with no matching route lands here rather than in "celery", so a
    # newly added, unrouted task is still picked up by the learning/default
    # worker instead of vanishing.
    task_default_queue="default",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Pipeline steps persist their own state to research_steps before ack,
    # but late-ack + reject-on-worker-lost gives an extra safety net so a
    # killed worker never silently drops a step.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=settings.pipeline_step_timeout_seconds + 60,
    task_soft_time_limit=settings.pipeline_step_timeout_seconds,
    # With acks_late, Redis redelivers a task that has been unacked longer than
    # visibility_timeout (default 3600s) -- while it is STILL RUNNING. The
    # pipeline tasks may now run up to pipeline_task_timeout_seconds, so keep
    # the redelivery window past their hard limit or a long run is duplicated.
    broker_transport_options={
        "visibility_timeout": settings.pipeline_task_timeout_seconds + 600,
    },
)

# Celery Beat schedule — the 24/7 heartbeat of outreach (M3) + learning (M8).
from celery.schedules import crontab  # noqa: E402


def _aggregation_hour() -> int:
    """PLAYBOOK_AGGREGATION_UTC_HOUR (default 02:00 UTC / 07:00 PKT)."""
    try:
        from app.core.config import settings as m8_settings
        return int(getattr(m8_settings, "PLAYBOOK_AGGREGATION_UTC_HOUR", 2))
    except Exception:
        return 2


# The database sweeps below that run more than once a day are grouped into
# bursts: every settings.beat_sweep_minutes (30 by default), at the same
# minutes of the hour, each job at its own offset of 0-4 minutes into the
# burst; hourly jobs join the burst at the top of the hour. Between bursts the
# worker leaves the database alone, so a database that suspends when idle
# (Neon's free plan, after 5 minutes) can sleep. Before this, a sweep fired
# almost every minute somewhere in the hour and the database never slept.
# Jobs that run every few hours or daily keep their own times.
def _sweep(offset: int) -> crontab:
    """Every burst, `offset` minutes in (0-4)."""
    minutes = range(offset, 60, settings.beat_sweep_minutes)
    return crontab(minute=",".join(str(m) for m in minutes))


def _hourly(offset: int = 4) -> crontab:
    """Once an hour, in the burst at the top of the hour."""
    return crontab(minute=offset)


celery_app.conf.beat_schedule = {
    # Voice twin: retry ElevenLabs deletes of revoked voice clones and expire
    # clones unused for 3 years (docs/features/voice-twin.md). Hourly.
    "voice-clone-housekeeping": {
        "task": "app.workers.call_tasks.voice_clone_housekeeping",
        "schedule": _hourly(),
    },
    # Private LinkedIn network: re-score stored connections every Monday 04:10 UTC.
    "linkedin-network-weekly-refresh": {
        "task": "leadpilot.network.weekly_refresh",
        "schedule": crontab(day_of_week="monday", hour=4, minute=10),
    },
    # Email network: re-filter + re-score stored contacts, Monday 04:25 UTC.
    "email-network-weekly-refresh": {
        "task": "leadpilot.network.email_weekly_refresh",
        "schedule": crontab(day_of_week="monday", hour=4, minute=25),
    },
    # Replies are polled first in each burst and due messages dispatched at
    # its end, so a reply that arrived since the last burst ("stop" included)
    # is on record before the next step is sent. The poll gets four minutes:
    # it walks every mailbox and classifies each reply with a model call
    # (with retries), and the outreach worker runs tasks concurrently, so a
    # one-minute gap let dispatch overtake a slow poll.
    "dispatch-due-messages": {
        "task": "leadpilot.outreach.dispatch",
        "schedule": _sweep(4),
    },
    "poll-gmail-replies": {
        "task": "leadpilot.outreach.poll_replies",
        "schedule": _sweep(0),
    },
    # M8-C1: nightly learning-loop aggregation
    "learning-loop-aggregation": {
        "task": "app.workers.learning_tasks.run_strategy_aggregation",
        "schedule": crontab(hour=_aggregation_hour(), minute=5),
    },
    # M8-C2: A/B / multi-variate auto-promotion sweep (30 min after aggregation)
    "ab-auto-promotion": {
        "task": "app.workers.learning_tasks.auto_promote_winners",
        "schedule": crontab(hour=_aggregation_hour(), minute=35),
    },
    # M8-C3: beat liveness heartbeat (60s, TTL 300s — health endpoint reads it)
    "beat-heartbeat": {
        "task": "app.workers.beat_heartbeat.refresh_celery_heartbeat",
        "schedule": 60.0,
    },
    # Feature 3: delete chat history past SUPPORT_CHAT_RETENTION_DAYS.
    # 03:20 UTC — after the learning-loop jobs at :05 and :35 so a slow
    # aggregation never delays the purge, and vice versa.
    "support-chat-retention-purge": {
        "task": "app.workers.support_tasks.purge_old_chats",
        "schedule": crontab(hour=3, minute=20),
    },
    # Engagement Hub, Feature 1: the automated follow-up sweep, every burst.
    # It only ENQUEUES; each send goes through the same compliance path as any
    # other message.
    "check-followup-due": {
        "task": "leadpilot.outreach.check_followup_due",
        "schedule": _sweep(2),
    },
    # Feature 5: opt-in post-sequence re-engagement. Hourly: its delays are
    # measured in weeks. Enqueues at most each campaign's remaining
    # re-engagement allowance; every send is re-gated in send_message_impl.
    "check-reengagement-due": {
        "task": "leadpilot.outreach.check_reengagement_due",
        "schedule": _hourly(),
    },
    # Engagement Hub, Feature 3: close meetings nobody pressed End on. Hourly
    # rather than on a schedule tied to meeting times, because the thing being
    # cleaned up is precisely the case where nobody was watching. A meeting
    # left in_progress keeps blocking calendar slots and keeps its lead's
    # enrollment paused, so this is not cosmetic.
    "close-stale-meetings": {
        "task": "app.workers.calendar_tasks.close_stale_meetings",
        "schedule": _hourly(),
    },
    # Feature 3: mark recording-provider transcripts that never arrived as
    # timed out. The notes-only summary already exists; this only ends the wait.
    "expire-transcript-waits": {
        "task": "app.workers.calendar_tasks.expire_transcript_waits",
        "schedule": _sweep(3),
    },
    # Feature Group 7: 24h and 1h pre-meeting reminders, every burst; each
    # reminder is claimed with a conditional UPDATE, so overlapping sweeps
    # cannot double-send (see meeting_prep_tasks).
    "send-meeting-reminders": {
        "task": "app.workers.meeting_prep_tasks.send_meeting_reminders",
        "schedule": _sweep(1),
    },
    # Feature Group 1: mutate campaigns idle for N days with zero replies.
    # Daily, after the learning-loop aggregation (:05) and promotion (:35) so
    # it reasons from that night's numbers.
    "check-idle-campaigns": {
        "task": "app.workers.intelligence_tasks.check_idle_campaigns",
        "schedule": crontab(hour=_aggregation_hour(), minute=50),
    },
    # Feature 6: anonymised benchmarks, after the aggregation (:05) and
    # promotion (:35) so they read the same night's outcomes.
    "compute-benchmarks": {
        "task": "app.workers.benchmark_tasks.compute_benchmarks",
        "schedule": crontab(hour=_aggregation_hour(), minute=40),
    },
    # Feature Group 3: recompute every campaign's smart-send windows after
    # the aggregation, and roll up last week's reply sentiment (Mondays) --
    # the objection-spike alert runs off that roll-up.
    "refresh-send-windows": {
        "task": "app.workers.analytics_tasks.refresh_send_windows",
        "schedule": crontab(hour=_aggregation_hour(), minute=15),
    },
    "aggregate-reply-sentiment": {
        "task": "app.workers.analytics_tasks.aggregate_reply_sentiment",
        "schedule": crontab(day_of_week=1, hour=_aggregation_hour(), minute=25),
    },
    # Feature Group 4: push what changed to each connected CRM and pull its
    # linked records back. Catches every status change no event announces.
    "crm-sync": {
        "task": "app.workers.crm_tasks.sync_all",
        "schedule": _sweep(4),
    },
    # F05 Guardrails Studio: customers / open deals / churned from the CRM
    # (and Victorae's own deals) into each account's exclusion lists. Daily:
    # a customer list changes slowly, and the send-time check reads whatever
    # is there. A no-op while guardrails_enabled is off.
    "guardrails-crm-sync": {
        "task": "app.workers.crm_tasks.guardrails_crm_sync_all",
        "schedule": crontab(hour=6, minute=35),
    },
    # Feature Group 9: daily blacklist + health check of every sending
    # domain. A new listing pauses the user's campaigns immediately.
    # Part 1 Feature 7: dated return visits from "not now" replies. Daily at
    # 05:50 -- the unit of this feature is a DAY ("come back in March"), and a
    # plan coming due today should be in the user's list before they start
    # work. :50 keeps it clear of the 06:10 domain sweep on the same queue.
    "reengagement-memory-due": {
        "task": "app.workers.reply_tasks.run_reengagement_memory",
        "schedule": crontab(hour=5, minute=50),
    },
    # Part 1 Feature 8: which touch earned each booking. Every burst, because
    # the question is asked the moment the meeting lands, not the next
    # morning; **Recompute** runs it on demand.
    "attribution-sweep": {
        "task": "app.workers.analytics_tasks.run_attribution_sweep",
        "schedule": _sweep(3),
    },
    "deliverability-checks": {
        "task": "app.workers.deliverability_tasks.run_daily_checks",
        "schedule": crontab(hour=6, minute=10),
    },
    # Part 1 Feature 4: per-MAILBOX health. Every four hours, because the two
    # signals it exists to catch -- a complaint spike and a volume spike -- do
    # their damage inside a day, which a daily sweep would only notice the
    # morning after. At :04, inside the burst at the top of the hour.
    "refresh-mailbox-health": {
        "task": "app.workers.deliverability_tasks.refresh_mailbox_health",
        "schedule": crontab(hour="*/4", minute=4),
    },
    # F03: the Google Postmaster spam-rate import. Daily -- Postmaster data
    # is daily and lags a day or two. 07:25, clear of the domain sweep (06:10)
    # and the :05 mailbox sweep it re-scores through. A no-op until both F03
    # flags are on.
    "import-postmaster-spam-rates": {
        "task": "app.workers.deliverability_tasks.import_postmaster_spam_rates",
        "schedule": crontab(hour=7, minute=25),
    },
    # Feature 1: recompute every campaign's pipeline health score. Every six
    # hours, at :40 past the hour, so it never starts in the same minute as
    # the learning-loop jobs (:05/:15/:25/:35/:50) that share this queue.
    "refresh-pipeline-health": {
        "task": "app.workers.health_tasks.refresh_all_pipeline_health",
        "schedule": crontab(hour="*/6", minute=40),
    },
    # Feature 4: competitor displacement sweep. Every twelve hours at :20 --
    # offset from the health sweep (:40) and the learning-loop hour so the
    # three never contend for the same worker minute.
    "scan-displacement-signals": {
        "task": "app.workers.displacement_tasks.scan_all_strategies",
        "schedule": crontab(hour="*/12", minute=20),
    },
    # Feature 5: the client ROI snapshot, daily at 01:00 UTC. Late enough
    # that yesterday has closed everywhere the product sells (US -> NZ), and
    # finished before the 02:00 learning-loop aggregation wants the same
    # worker.
    "refresh-roi-snapshots": {
        "task": "app.workers.roi_tasks.refresh_all_roi_snapshots",
        "schedule": crontab(hour=1, minute=0),
    },
    # Section E: charge pay-per-meeting usage whose 48h cancellation grace has
    # passed. Every burst, so a charge lands within one interval of it.
    "charge-due-meetings": {
        "task": "app.workers.billing_tasks.charge_due_meetings",
        "schedule": _sweep(4),
    },
    # Annual billing: auto-renewal notices (acknowledgment, trial ending,
    # 30 days before an annual renewal). Daily at 15:10 UTC -- morning in the
    # US; the notice window is 15-30 days wide, so a missed day catches up.
    "send-renewal-notices": {
        "task": "app.workers.billing_tasks.send_renewal_notices",
        "schedule": crontab(hour=15, minute=10),
    },
    # Feature A2: chain newly recorded audit events, every burst.
    "seal-audit-trails": {
        "task": "app.workers.audit_tasks.seal_audit_trails",
        "schedule": _sweep(3),
    },
    # Feature A4: suggest (or switch to) the next channel for quiet leads.
    "detect-channel-stagnation": {
        "task": "app.workers.channel_tasks.detect_channel_stagnation",
        "schedule": _hourly(),
    },
    # Feature A5: cool / archive leads that went cold between sends.
    "rescore-conversion-probability": {
        "task": "app.workers.conversion_tasks.rescore_conversion_probability",
        "schedule": _hourly(),
    },
    # F07: Spend Guard alerts (no-op while spend_guard_enabled is off).
    "sweep-spend-alerts": {
        "task": "app.workers.spend_guard_tasks.sweep_spend_alerts",
        "schedule": _sweep(4),
    },
    # F09: end pilots whose 30 days are over (verdict), send the weekly
    # scorecard once per pilot per week, send the verdict notice. Hourly, a
    # minute after the attribution sweep (+3) whose entries the verdict reads.
    # A no-op while pilot_mode_enabled is off.
    "pilot-mode-sweep": {
        "task": "app.workers.pilot_tasks.run_pilot_sweep",
        "schedule": _hourly(),
    },
    # F10 Champion Tracker. Held messages are re-verified every burst; the
    # champion refresh runs daily at 04:40, before the 05:50 re-engagement
    # memory sweep, so a job change found overnight is in the morning list.
    # Both are no-ops with the flag off except releasing any leftover holds.
    "champion-reverify-held": {
        "task": "leadpilot.champion.reverify_held",
        "schedule": _sweep(2),
    },
    "champion-refresh": {
        "task": "leadpilot.champion.refresh",
        "schedule": crontab(hour=4, minute=40),
    },
    # Plan holds (app/services/plan_holds.py): every burst. Releases holds the
    # owner's plan covers again.
    "plan-hold-release": {
        "task": "leadpilot.plan_holds.release",
        "schedule": _sweep(2),
    },
    # Autopilot launch (app/services/autopilot.py): every burst. Resumes a
    # waiting channel once its prerequisite appears; enrolls newly verified
    # leads of strategies with Autopilot on. Nothing here sends.
    "autopilot-sweep": {
        "task": "leadpilot.autopilot.sweep",
        "schedule": _sweep(2),
    },
    # Best time to send: the accounts' learned rates into Redis every 6 hours
    # (app/services/send_time.py refresh_learning) -- the send path only reads.
    "send-time-learning-refresh": {
        "task": "leadpilot.autopilot.refresh_learning",
        "schedule": crontab(minute=29, hour="*/6"),
    },
    # NOTE: outbound webhook retries (M8-C5) self-schedule via apply_async
    # countdown inside deliver_webhook — no beat sweep needed.
}
