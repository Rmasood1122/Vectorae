"""
Nightly learning loop Celery tasks — M8 Chunks 1+2+4 combined.

run_strategy_aggregation():
  1. compute_scores_with_decay()  → playbook_scores (replaces simple mean)
  2. update_send_time_scores()    → Redis send-time cache (NEW: C4)
  3. extract_patterns_from_outcomes() + persist_patterns()  (NEW: C4)
  4. detect_stale_pipeline_steps()
  5. Score all strategy messages for personalization correlation  (NEW: C4)

auto_promote_winners():
  - Now calls compare_multi_variants() when step has >2 variants (NEW: C4)
  - Falls back to compare_variants() for exactly 2
  - Double-promotion guard unchanged
"""
from __future__ import annotations

import datetime
import time

from app.workers.celery_app import celery_app
from app.core.logging import get_logger, bind_celery_task_context, TaskTimer

logger = get_logger("workers.learning_tasks")


@celery_app.task(
    name="app.workers.learning_tasks.run_strategy_aggregation",
    bind=True,
    max_retries=2,
    acks_late=True,
)
def run_strategy_aggregation(self) -> dict:
    """
    Nightly learning loop aggregation.
    Scheduled by Celery Beat at PLAYBOOK_AGGREGATION_UTC_HOUR (default 02:00 UTC).
    """
    bind_celery_task_context(task_id=self.request.id, task_name="run_strategy_aggregation")
    started_at = time.monotonic()

    from app.core.database import SessionLocal
    from app.core.redis_client import get_sync_redis
    from app.core.config import settings
    from app.services.score_decay import compute_scores_with_decay, determine_trend
    from app.services.send_time_optimizer import update_send_time_scores
    from app.services.subject_intelligence import extract_patterns_from_outcomes, persist_patterns
    from app.services.personalization_scorer import score_strategy_messages
    from app.workers.monitoring import detect_stale_pipeline_steps

    db = SessionLocal()
    redis = get_sync_redis()
    patterns_updated = 0
    errors: list[str] = []

    try:
        with TaskTimer(logger, "aggregation"):
            # 1. Compute decayed playbook scores
            logger.info("aggregation.decay_scores_started")
            decayed = compute_scores_with_decay(db, settings.PLAYBOOK_SCORE_HALF_LIFE_DAYS)

            for composite_key, score_data in decayed.items():
                try:
                    _upsert_playbook_score(db, score_data)
                    patterns_updated += 1
                except Exception as e:
                    db.rollback()  # REPAIR: don't poison the session for later steps
                    errors.append(f"score upsert {composite_key}: {e}")

            # 2. Send-time optimizer update
            logger.info("aggregation.send_time_update_started")
            try:
                keys_written = update_send_time_scores(db)
                logger.info("aggregation.send_time_done", keys_written=keys_written)
            except Exception as e:
                db.rollback()  # REPAIR
                errors.append(f"send_time_update: {e}")
                logger.warning("aggregation.send_time_failed", error=str(e))

            # 3. Subject line pattern extraction
            logger.info("aggregation.subject_patterns_started")
            try:
                patterns = extract_patterns_from_outcomes(db)
                persist_patterns(db, patterns)
                logger.info("aggregation.subject_patterns_done", count=len(patterns))
            except Exception as e:
                db.rollback()  # REPAIR
                errors.append(f"subject_patterns: {e}")
                logger.warning("aggregation.subject_patterns_failed", error=str(e))

            # 4. Stale pipeline step detection
            try:
                stalled = detect_stale_pipeline_steps()
                if stalled:
                    logger.warning("aggregation.stale_steps_found", count=len(stalled))
            except Exception as e:
                errors.append(f"stale_detection: {e}")

            # 5. Personalization correlation per strategy (sample up to 50 recent strategies)
            logger.info("aggregation.personalization_correlation_started")
            try:
                from sqlalchemy import text
                strategy_ids = db.execute(text("""
                    SELECT id FROM strategies
                    -- REPAIR: id is the PK (DISTINCT was invalid with this
                    -- ORDER BY); real StrategyStatus values, not 'complete'.
                    WHERE status IN ('verified', 'executing')
                    ORDER BY updated_at DESC LIMIT 50
                """)).scalars().all()
                for sid in strategy_ids:
                    try:
                        score_strategy_messages(str(sid), db)
                    except Exception:
                        pass
            except Exception as e:
                db.rollback()  # REPAIR
                errors.append(f"personalization_corr: {e}")

        duration_ms = int((time.monotonic() - started_at) * 1000)

        # Update Redis health keys
        now = time.time()
        redis.set("learning_loop:last_run", str(now))
        redis.set("learning_loop:last_duration_ms", str(duration_ms))
        redis.set("learning_loop:patterns_updated", str(patterns_updated))
        if errors:
            redis.set("learning_loop:last_error", "; ".join(errors[:3]))
        else:
            redis.delete("learning_loop:last_error")

        logger.info(
            "aggregation.complete",
            duration_ms=duration_ms,
            patterns_updated=patterns_updated,
            errors=len(errors),
        )
        return {"patterns_updated": patterns_updated, "duration_ms": duration_ms, "errors": errors}

    except Exception as e:
        logger.error("aggregation.fatal_error", error=str(e))
        redis.set("learning_loop:last_error", str(e))
        raise
    finally:
        db.close()


def _upsert_playbook_score(db, score_data: dict) -> None:
    """Upsert one account's playbook score row with decay data.

    F-DB-01: keyed on (user_id, pattern_key, variant), so each account has its
    own row per pattern. A row without a user_id is never written.
    """
    from sqlalchemy import text

    db.execute(text("""
        INSERT INTO playbook_scores
            -- REPAIR: id is a UUID PK with no DB default; PG16 supplies it.
            (id, user_id, pattern_key, variant, score, reply_rate, booking_rate,
             sample_size, effective_sample_size,
             decay_half_life_days, oldest_outcome_ts,
             is_reliable, trend, last_aggregated_at)
        VALUES
            (gen_random_uuid(), CAST(:user_id AS uuid), :pattern_key, :variant, :reply_rate, :reply_rate,
             :booking_rate,
             :raw_n, :effective_n,
             :half_life, :oldest_ts,
             :reliable, 'new', NOW())
        ON CONFLICT (user_id, pattern_key, variant) DO UPDATE SET
            score = EXCLUDED.score,
            reply_rate = EXCLUDED.reply_rate,
            booking_rate = EXCLUDED.booking_rate,
            sample_size = EXCLUDED.sample_size,
            effective_sample_size = EXCLUDED.effective_sample_size,
            decay_half_life_days = EXCLUDED.decay_half_life_days,
            oldest_outcome_ts = EXCLUDED.oldest_outcome_ts,
            is_reliable = EXCLUDED.is_reliable,
            -- F-DB-04: score is a copy of reply_rate and is being removed
            -- (migration 0095), so the trend compares reply_rate itself.
            trend = CASE
                WHEN playbook_scores.reply_rate IS NULL THEN 'new'
                WHEN EXCLUDED.reply_rate > playbook_scores.reply_rate + 0.005 THEN 'rising'
                WHEN EXCLUDED.reply_rate < playbook_scores.reply_rate - 0.005 THEN 'falling'
                ELSE 'stable'
            END,
            last_aggregated_at = NOW()
    """), {
        "user_id": str(score_data["user_id"]),
        "pattern_key": score_data["pattern_key"],
        "variant": score_data["variant"],
        "reply_rate": score_data["reply_rate"],
        "booking_rate": score_data["booking_rate"],
        "raw_n": score_data["raw_n"],
        "effective_n": score_data["effective_n"],
        "half_life": score_data["half_life_days"],
        "oldest_ts": score_data.get("oldest_outcome_ts"),
        "reliable": score_data["is_reliable"],
    })
    db.commit()


def promote_winners(db, *, min_sample: int, now=None) -> int:
    """The promotion sweep itself (auto_promote_winners minus the session and
    the admin flag). Returns how many strategies were promoted.

    DIALECT-NEUTRAL since gaps-sweep-2 #18: it was raw PostgreSQL SQL
    (array_agg, gen_random_uuid(), NOW(), ON CONFLICT, dashed-UUID strings
    bound into raw SQL), so no offline test could run it. Same rules:
      * candidates: a strategy with >= 2 variants among its sent / replied /
        booked outcomes and >= `min_sample` sends in all;
      * a strategy that already has an `ab_promoted` outcome is never
        promoted again (the marker is permanent);
      * > 2 variants: compare_multi_variants; exactly 2: compare_variants;
      * a winner sets strategies.default_variant and writes the ab_promoted
        marker in the same commit. The marker insert runs in a SAVEPOINT: on
        PostgreSQL the partial unique index from 0012 rejects a concurrent
        second marker (the old ON CONFLICT DO NOTHING), and that rejection
        is swallowed exactly as before.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    from sqlalchemy import case, func, select  # noqa: PLC0415
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    from app.db.models import Outcome, OutcomeEvent, Strategy  # noqa: PLC0415
    from app.services.ab_testing import compare_variants  # noqa: PLC0415
    from app.services.multi_variate import compare_multi_variants  # noqa: PLC0415

    now = now or datetime.now(timezone.utc)
    rows = db.execute(
        select(Outcome.strategy_id, Outcome.variant,
               func.sum(case((Outcome.event == OutcomeEvent.SENT, 1), else_=0)).label("sends"))
        .where(Outcome.variant.isnot(None), Outcome.strategy_id.isnot(None),
               Outcome.event.in_([OutcomeEvent.SENT, OutcomeEvent.REPLIED,
                                  OutcomeEvent.BOOKED]))
        .group_by(Outcome.strategy_id, Outcome.variant)).all()
    candidates: dict = {}
    for strategy_id, variant, sends in rows:
        slot = candidates.setdefault(strategy_id, {"variants": [], "sends": 0})
        slot["variants"].append(variant)
        slot["sends"] += int(sends or 0)

    promoted = 0
    for strategy_id, slot in sorted(candidates.items(), key=lambda kv: str(kv[0])):
        variants = sorted(slot["variants"])
        if len(variants) < 2 or slot["sends"] < min_sample:
            continue
        already = db.execute(select(func.count(Outcome.id)).where(
            Outcome.strategy_id == strategy_id,
            Outcome.event == OutcomeEvent.AB_PROMOTED)).scalar_one()
        if already:
            continue   # skip -- already promoted
        try:
            if len(variants) > 2:
                winner = compare_multi_variants(strategy_id=str(strategy_id), variants=variants,
                                                metric="meeting_rate", db_session=db).winner
            else:
                result = compare_variants(str(strategy_id), variants[0], variants[1],
                                          db_session=db)
                winner = getattr(result, "winner", None)
            if not winner:
                continue
            strategy = db.get(Strategy, strategy_id)
            if strategy is None:
                continue
            strategy.default_variant = winner
            try:
                with db.begin_nested():
                    db.add(Outcome(strategy_id=strategy_id, event=OutcomeEvent.AB_PROMOTED,
                                   variant=winner, ts=now))
            except IntegrityError:
                pass   # a concurrent sweep wrote the marker first
            db.commit()
            promoted += 1
            logger.info("promotion.winner_set", strategy_id=str(strategy_id), winner=winner,
                        variant_count=len(variants))
        except Exception as e:
            db.rollback()
            logger.warning("promotion.strategy_failed", strategy_id=str(strategy_id),
                           error=str(e))
    return promoted


@celery_app.task(
    name="app.workers.learning_tasks.auto_promote_winners",
    bind=True,
    max_retries=1,
    acks_late=True,
)
def auto_promote_winners(self) -> dict:
    """
    A/B / MV auto-promotion nightly sweep.
    Now uses compare_multi_variants() when >2 variants detected.
    """
    bind_celery_task_context(task_id=self.request.id, task_name="auto_promote_winners")

    from app.core.database import SessionLocal
    from app.core.config import settings

    db = SessionLocal()
    promoted = 0

    try:
        # Review M5: off unless an admin turns it on (system_settings).
        from app.services import system_settings  # noqa: PLC0415

        if not system_settings.get(db, "ab_auto_promotion_enabled"):
            logger.info("promotion.disabled")
            return {"promoted": 0, "skipped": "ab_auto_promotion_enabled is off"}

        promoted = promote_winners(db, min_sample=settings.PLAYBOOK_MIN_SAMPLE)
        return {"promoted": promoted}
    finally:
        db.close()
