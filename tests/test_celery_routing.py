"""Architecture tripwire: the queue a task is PUBLISHED to must be a queue a
worker actually CONSUMES.

This caught a total production outage. celery_app.py had no `task_routes`, so
every task went to Celery's implicit "celery" queue, while
docker-compose.prod.yml starts its workers bound to -Q pipeline / -Q outreach /
-Q learning,default. Nothing consumed "celery" and nothing was published to the
four declared queues, so in production the entire async backend — strategy
generation, outreach dispatch, reply polling, the learning loop, the beat
heartbeat — would have silently done nothing. It passed unnoticed in dev only
because the dev worker is started without -Q and therefore consumes "celery".

Do not delete these tests. If you add a task or rename a queue, fix the routing
or the compose file, not the assertion.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

# Importing the task modules is what registers them on the app.
import app.workers.beat_heartbeat  # noqa: F401
import app.workers.calendar_tasks  # noqa: F401  (Engagement Hub)
import app.workers.lead_tasks  # noqa: F401
import app.workers.learning_tasks  # noqa: F401
import app.workers.outreach_tasks  # noqa: F401
import app.workers.tasks  # noqa: F401
import app.workers.support_tasks  # noqa: F401  (Feature 3 retention purge)
import app.workers.webhook_tasks  # noqa: F401
from app.workers.celery_app import celery_app

# Every module the worker loads, not only the ones listed above. The feature
# expansion's task modules (notification, meeting prep, intelligence, calls,
# analytics, CRM, deliverability) were missing from that list and only
# registered when some OTHER test file happened to import them first -- so
# these assertions depended on test order and failed when run alone.
# Importing celery_app's own include list makes that impossible to repeat.
import importlib  # noqa: E402

for _module in celery_app.conf.include:
    importlib.import_module(_module)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_COMPOSE_FILES = ["docker-compose.yml", "docker-compose.prod.yml"]


def _app_task_names() -> list[str]:
    """Every task this project registers (Celery's own internals excluded)."""
    return sorted(n for n in celery_app.tasks if not n.startswith("celery."))


def _queue_of(task_name: str) -> str:
    return celery_app.amqp.router.route({}, task_name)["queue"].name


def _consumed_queues(compose_file: str) -> set[str]:
    """Queues named by `-Q a,b,c` across every worker service in a compose file."""
    data = yaml.safe_load((_REPO_ROOT / compose_file).read_text(encoding="utf-8"))
    queues: set[str] = set()
    for service in (data.get("services") or {}).values():
        command = service.get("command")
        if not command:
            continue
        if isinstance(command, list):
            command = " ".join(str(c) for c in command)
        if " worker" not in command:
            continue
        match = re.search(r"-Q\s+([A-Za-z0-9_,.-]+)", command)
        assert match, (
            f"{compose_file}: worker started without -Q, so it consumes only the "
            f"implicit 'celery' queue and will process nothing: {command.strip()}"
        )
        queues.update(q for q in match.group(1).split(",") if q)
    return queues


def test_every_task_has_an_explicit_route():
    """No task may fall through to the default queue by accident."""
    unrouted = [
        name for name in _app_task_names()
        if celery_app.amqp.router.route({}, name).get("queue") is None
    ]
    assert not unrouted, f"tasks with no resolvable queue: {unrouted}"


@pytest.mark.parametrize("compose_file", _COMPOSE_FILES)
def test_every_task_lands_in_a_consumed_queue(compose_file):
    """The bug this file exists for: published-to but never consumed-from."""
    consumed = _consumed_queues(compose_file)
    assert consumed, f"{compose_file}: no worker consumes any queue"

    stranded = {}
    for name in _app_task_names():
        queue = _queue_of(name)
        if queue not in consumed:
            stranded[name] = queue
    assert not stranded, (
        f"{compose_file}: these tasks are published to queues no worker consumes "
        f"(consumed={sorted(consumed)}): {stranded}"
    )


@pytest.mark.parametrize("compose_file", _COMPOSE_FILES)
def test_no_worker_listens_to_a_queue_nothing_publishes_to(compose_file):
    """The mirror image: a worker burning a container on an always-empty queue
    is the symptom that tells you the routing drifted."""
    consumed = _consumed_queues(compose_file)
    published = {_queue_of(name) for name in _app_task_names()}
    # `default` is deliberately allowed to be consumed-but-empty: it is the
    # catch-all for any future task added without an explicit route.
    idle = consumed - published - {"default"}
    assert not idle, (
        f"{compose_file}: workers consume {sorted(idle)} but no task is routed "
        f"there (published={sorted(published)})"
    )


def test_beat_scheduled_tasks_are_registered_and_routed():
    """A beat entry naming a task that does not exist fails silently at runtime:
    beat publishes it, no worker can execute it."""
    registered = set(celery_app.tasks)
    for entry_name, entry in (celery_app.conf.beat_schedule or {}).items():
        task_name = entry["task"]
        assert task_name in registered, (
            f"beat entry {entry_name!r} schedules unregistered task {task_name!r}"
        )
        assert _queue_of(task_name), f"beat task {task_name!r} has no queue"


def test_default_queue_is_not_celerys_implicit_one():
    """`celery` is the name nothing in this project consumes."""
    assert celery_app.conf.task_default_queue == "default", (
        "task_default_queue fell back to Celery's implicit 'celery' queue, which "
        "no worker in any compose file consumes"
    )


# ---------------------------------------------------------------------------
# Railway per-service configs
# ---------------------------------------------------------------------------
# The queue list is the one deployment detail no test could reach while it was
# typed into the Railway dashboard: a worker started without -Q consumes only
# the implicit "celery" queue and sits idle, healthy, processing nothing.
# Moving each service's startCommand into railway/*.json puts it back under
# review — and under these tests. Railway resolves config-as-code over dashboard
# values, so the file wins even if someone edits the dashboard field.

_RAILWAY_DIR = _REPO_ROOT / "railway"


def _railway_configs() -> dict[str, dict]:
    return {p.name: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(_RAILWAY_DIR.glob("*.json"))}


def test_railway_config_dir_exists():
    assert _RAILWAY_DIR.is_dir(), "railway/ per-service configs are missing"
    assert _railway_configs(), "no railway/*.json files found"


def test_no_root_railway_json():
    """A root config is inherited by any service without an explicit path — a
    worker picking up the API's uvicorn startCommand would serve HTTP and
    consume no queues at all."""
    assert not (_REPO_ROOT / "railway.json").exists(), (
        "root railway.json would be inherited by worker services; give every "
        "service an explicit railway/<service>.json instead"
    )


def test_every_railway_worker_declares_queues():
    for name, cfg in _railway_configs().items():
        start = cfg["deploy"]["startCommand"]
        if " worker" not in start:
            continue
        match = re.search(r"-Q\s+([A-Za-z0-9_,.-]+)", start)
        assert match, (
            f"railway/{name}: celery worker started without -Q — it would "
            f"consume only the implicit 'celery' queue and process nothing: "
            f"{start}"
        )


def test_railway_queues_match_task_routes():
    """Union of every Railway worker's -Q must cover every routed queue."""
    consumed: set[str] = set()
    for cfg in _railway_configs().values():
        start = cfg["deploy"]["startCommand"]
        if " worker" not in start:
            continue
        match = re.search(r"-Q\s+([A-Za-z0-9_,.-]+)", start)
        if match:
            consumed.update(q for q in match.group(1).split(",") if q)
    published = {_queue_of(n) for n in _app_task_names()}
    missing = published - consumed
    assert not missing, (
        f"railway/*.json consume {sorted(consumed)} but tasks are routed to "
        f"{sorted(missing)} — those tasks would never run in production"
    )


def test_railway_beat_takes_no_queue_flag():
    """Beat publishes; it consumes nothing. A -Q there signals a copy-paste."""
    for name, cfg in _railway_configs().items():
        start = cfg["deploy"]["startCommand"]
        if " beat" in start:
            assert "-Q" not in start, f"railway/{name}: beat should not take -Q"


def test_only_api_runs_migrations():
    """Migrations belong in exactly one pre-deploy command."""
    migrators = [n for n, c in _railway_configs().items()
                 if any("app.db.migrate" in cmd
                        for cmd in c["deploy"].get("preDeployCommand", []))]
    assert migrators == ["api.json"], (
        f"expected only api.json to run migrations, got {migrators}"
    )


# ---------------------------------------------------------------------------
# The database gets quiet windows between worker bursts.
# ---------------------------------------------------------------------------

def test_frequent_sweeps_run_only_inside_the_bursts():
    """Every database sweep that runs more than every few hours sits in the
    first five minutes of a BEAT_SWEEP_MINUTES burst, so the database hears
    nothing from beat for the rest of each interval and a database that
    suspends after 5 idle minutes (Neon's free plan) can sleep. Before, a
    sweep fired in almost every minute of the hour and the database never
    slept; the free plan's compute quota ran out.

    beat-heartbeat is exempt: it writes Redis only.
    """
    from celery.schedules import crontab

    from app.config import settings

    sweep = settings.beat_sweep_minutes
    outside = []
    for name, entry in celery_app.conf.beat_schedule.items():
        if name == "beat-heartbeat":
            continue
        sched = entry["schedule"]
        assert isinstance(sched, crontab), f"{name}: interval schedules drift off the bursts"
        if len(sched.hour) < 6 or len(sched.day_of_week) < 7:
            continue  # every 4+ hours, daily or weekly: a few wakes a day
        outside += [f"{name} at :{m:02d}" for m in sorted(sched.minute) if m % sweep > 4]
    assert outside == []


def test_the_bursts_leave_most_of_each_interval_quiet():
    from app.config import settings

    busy = set()
    for name, entry in celery_app.conf.beat_schedule.items():
        sched = entry["schedule"]
        if name == "beat-heartbeat" or len(sched.hour) < 6:
            continue
        busy |= set(sched.minute)
    assert busy == {b + k for b in range(0, 60, settings.beat_sweep_minutes) for k in range(5)}


def test_heartbeat_touches_only_redis():
    import inspect

    import app.workers.beat_heartbeat as hb

    source = inspect.getsource(hb.refresh_celery_heartbeat)
    assert "get_sync_redis" in source
    assert "SessionLocal" not in source and "get_db" not in source


@pytest.mark.parametrize("minutes", [4, 7, 25, 61])
def test_a_sweep_interval_that_does_not_fit_the_hour_is_refused(minutes):
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(beat_sweep_minutes=minutes)


@pytest.mark.parametrize("minutes", [5, 10, 15, 20, 30, 60])
def test_a_sweep_interval_that_fits_the_hour_is_accepted(minutes):
    from app.config import Settings

    assert Settings(beat_sweep_minutes=minutes).beat_sweep_minutes == minutes


def test_replies_are_polled_well_before_due_messages_are_dispatched():
    """A "stop" reply must be on record before the next step goes out. The
    poll walks every mailbox and classifies each reply with a model call, and
    the outreach worker runs tasks concurrently, so dispatch waits until the
    end of the burst (4 minutes) rather than one minute."""
    sched = celery_app.conf.beat_schedule
    polls = sorted(sched["poll-gmail-replies"]["schedule"].minute)
    dispatches = sorted(sched["dispatch-due-messages"]["schedule"].minute)
    assert len(polls) == len(dispatches)
    assert all(d - p >= 4 for p, d in zip(polls, dispatches))
