"""
Integration-test conftest.

Provides:
  - pg_engine / db_session / sync_redis / redis_client — real PostgreSQL and
    Redis, overriding the root conftest's in-process SQLite fixtures
  - api_client — AsyncClient against the real app with those overrides
  - admin_client / user_a_client / user_b_client  — authed AsyncClient instances
  - factory helpers: create_product, create_past_clients, create_strategy, seed_outcomes
  - mock_transports with all 7 external APIs pre-registered

The PostgreSQL/Redis harness below used to live in the ROOT conftest, where
it replaced the SQLite fixture library the 22 root test modules are written
against and broke all of them. Only tests/integration/ ever used it - every
one of pg_engine / sync_redis / redis_client / api_client / mock_transports
is referenced from this directory and nowhere else - so it lives here now.

These fixtures need a running PostgreSQL and Redis (docker compose up db
redis); the root suite deliberately needs neither.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Generator

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
import respx

from app.config import get_settings
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command

# The ROOT conftest hard-assigns DATABASE_URL="sqlite://" (not setdefault), and
# it is imported first - so a setdefault here silently loses and Alembic runs
# its migrations against SQLite, which fails on the first ALTER of a named
# constraint. These fixtures therefore read TEST_DATABASE_URL directly and
# never consult DATABASE_URL.
#
# The default deliberately names port 5433 and the leadpilot role, matching
# docker-compose.yml. Do NOT default to localhost:5432: pg_engine ends its
# session with DROP SCHEMA public CASCADE, and 5432 is a common host Postgres
# belonging to some entirely unrelated project.
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://leadpilot:leadpilot@localhost:5433/clienthunter_test",
)
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6380/15")
os.environ["REDIS_URL"] = TEST_REDIS_URL  # DB 15 reserved for tests


def _retarget_redis(url: str = TEST_REDIS_URL) -> None:
    """Point the ALREADY-BUILT settings objects and cached clients at the test
    Redis.

    Setting the env var alone is too late. The root conftest is imported first
    and pulls in app.config, so `settings.redis_url` was frozen at
    "redis://localhost:6379/0" before this module ran -- and 6379 is NOT the
    compose Redis (docker-compose.yml maps it to 6380 because 6379 was already
    taken on the dev host). The response cache and the circuit breaker in
    app/integrations/plumbing.py therefore read and wrote some unrelated
    service's Redis, which the redis_client fixture's flushdb could never
    clear: a cached Apollo search from an earlier test satisfied later ones
    without any HTTP call, so `search_calls` was empty and assertions about
    outbound requests failed depending on test order.
    """
    from app.config import settings as _legacy_settings
    _legacy_settings.redis_url = url
    try:
        from app.core.config import settings as _m8_settings
        _m8_settings.REDIS_URL = url
    except Exception:  # pragma: no cover - config module optional
        pass

    import app.core.redis_client as _rc
    import app.integrations.plumbing as _plumbing
    _plumbing._redis = None      # lazy singleton, rebuilt on next get_redis()
    _rc._async_client = None


_retarget_redis()

# ---------------------------------------------------------------------------
# Database fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pg_engine():
    """Create test DB, run Alembic migrations, yield engine, drop all after session.

    Migrations run in a SUBPROCESS. Setting sqlalchemy.url on an in-process
    AlembicConfig does nothing here: alembic/env.py overwrites it with
    settings.database_url, and `settings` was built at import time from the
    root conftest's DATABASE_URL="sqlite://" - so every migration was being
    applied to SQLite and dying on the first ALTER of a named constraint.
    A subprocess gets a clean import with the right DATABASE_URL.
    """
    db_url = TEST_DATABASE_URL
    engine = create_engine(db_url, echo=False)

    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(Path(__file__).resolve().parents[2]),
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "\n".join([
                f"alembic upgrade head failed against {db_url}",
                "--- stdout ---", proc.stdout,
                "--- stderr ---", proc.stderr,
            ])
        )

    # Point the APP's own session factory at this database too.
    #
    # api_client overrides the get_db dependency, which covers HTTP requests -
    # but nothing else. Celery tasks running eagerly in-process (run_pipeline,
    # lead_tasks), circuit_breaker and notification_service all open their own
    # session straight from SessionLocal, which is bound at import time to
    # settings.database_url - i.e. the root conftest's "sqlite://". Those tasks
    # were therefore querying an empty in-memory SQLite DB and blowing up
    # binding a UUID primary key ("'str' object has no attribute 'hex'").
    #
    # configure() mutates the existing sessionmaker in place, so modules that
    # already did `from app.db.base import SessionLocal` (lead_tasks does it at
    # module level) pick this up - rebinding the module attribute would not
    # reach them.
    from app.db import base as db_base

    previous_bind = db_base.SessionLocal.kw.get("bind")
    db_base.SessionLocal.configure(bind=engine)
    db_base.engine = engine

    yield engine

    db_base.SessionLocal.configure(bind=previous_bind)

    # Clean up: drop all tables after the test session
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    engine.dispose()


_TRUNCATE_ALL = text("""
DO $$
DECLARE stmt text;
BEGIN
    SELECT 'TRUNCATE TABLE '
           || string_agg(format('%I.%I', schemaname, tablename), ', ')
           || ' RESTART IDENTITY CASCADE'
      INTO stmt
      FROM pg_tables
     WHERE schemaname = 'public'
       AND tablename <> 'alembic_version';
    IF stmt IS NOT NULL THEN
        EXECUTE stmt;
    END IF;
END $$;
""")


@pytest.fixture(autouse=True)
def clean_database(pg_engine):
    """Empty every table before each test.

    The previous approach -- open a session, begin_nested(), roll the savepoint
    back at teardown -- isolated nothing. The application does not use the
    test's session: HTTP requests use the get_db override, and eager Celery
    tasks, the circuit breaker and notification_service open their own sessions
    from SessionLocal. Everything they COMMIT is durable, outside any savepoint
    the test holds, and the tests themselves commit too (seed_outcomes does).

    So state accumulated across the whole session and tests only passed in a
    particular order: an earlier GDPR-delete or suppression test put
    alice@/carol@ on the global suppression list, after which sourcing
    correctly refused to insert them and later tests failed with KeyError on
    the very leads they had just sourced; re-adding the same suppression entry
    raised UniqueViolation on ix_suppression_list_email. Truncating gives real
    isolation and makes the suite order-independent.

    Truncation happens BEFORE db_session opens its transaction (db_session
    depends on this fixture), otherwise TRUNCATE would block on that lock.
    """
    with pg_engine.begin() as conn:
        conn.execute(_TRUNCATE_ALL)
    yield


@pytest.fixture()
def db_session(pg_engine, clean_database):
    """Per-test DB session against the freshly-truncated database."""
    Session = sessionmaker(bind=pg_engine)
    session = Session()

    yield session

    session.rollback()
    session.close()


# ---------------------------------------------------------------------------
# Redis fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sync_redis():
    """Synchronous Redis client for test setup/assertions."""
    import redis as sync_redis_lib
    r = sync_redis_lib.from_url(TEST_REDIS_URL, decode_responses=True)
    r.flushdb()
    yield r
    r.flushdb()
    r.close()


@pytest_asyncio.fixture()
async def redis_client():
    """Async Redis client reset between tests."""
    r = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


# ---------------------------------------------------------------------------
# Celery eager mode — tasks run synchronously inline so we can assert outcomes
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def configure_celery_eager():
    """Force Celery ALWAYS_EAGER so task chains run synchronously in tests."""
    from app.workers.celery_app import celery_app
    celery_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
    )
    yield


# ---------------------------------------------------------------------------
# HTTPX / respx transport mock — intercepts all external HTTP at transport layer
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_transports():
    """
    Activate respx router as the default HTTPX transport.
    Individual test modules add routes via the returned router.
    """
    with respx.mock(assert_all_called=False) as router:
        yield router


# ---------------------------------------------------------------------------
# FastAPI test client
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def api_client(db_session, redis_client, mock_transports) -> AsyncGenerator[AsyncClient, None]:
    """
    Async test client for the FastAPI app.
    DB session and Redis are overridden to use test instances.

    This fixture had never run. It used to import `create_app` from app.main
    and `get_db` from app.core.database - neither exists. app/main.py builds a
    module-level `app`, and get_db lives in app/db/base.py. It also passed
    `app=` to AsyncClient, which httpx removed in 0.28 in favour of an
    explicit ASGITransport.

    get_db is a SYNC generator dependency, so the override must be sync too;
    get_redis is a plain function returning an already-async client.
    """
    from app.main import app
    from app.db.base import get_db
    from app.core.redis_client import get_redis

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_redis] = lambda: redis_client
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()



from tests.integration.mocks import (
    anthropic_mock,
    apollo_mock,
    calendly_mock,
    firebase_mock,
    gmail_mock,
    hunter_mock,
    whatsapp_mock,
)


# ---------------------------------------------------------------------------
# All-mocks transport fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _align_webhook_secret():
    """Point the app at the secret tests/integration/mocks sign with.

    whatsapp_mock.sign_payload() defaults to "wh-app-secret-test", but the
    ROOT conftest sets WHATSAPP_APP_SECRET="test-app-secret" (its own
    wa_signed_post helper uses that one) and, being imported first, wins the
    setdefault. Every signed integration webhook was therefore rejected 401.
    Settings is already constructed by now, so set the attribute rather than
    the env var.
    """
    settings = get_settings()
    previous = settings.whatsapp_app_secret
    settings.whatsapp_app_secret = "wh-app-secret-test"
    yield
    settings.whatsapp_app_secret = previous


@pytest.fixture(autouse=True)
def capture_push_notifications(monkeypatch):
    """Intercept FCM for every integration test.

    firebase_admin sends over `requests`, which respx cannot see, so this
    patches the SDK boundary instead - see tests/integration/mocks/
    firebase_mock.py for why that is the right seam here.
    """
    from tests.integration.mocks import firebase_mock

    monkeypatch.setenv("FIREBASE_CREDENTIALS_PATH", "/fake/service-account.json")
    return firebase_mock.install(monkeypatch)


def _respx_backed_anthropic_client(router):
    """A real ClaudeClient whose SDK transport is the test's respx router.

    respx patches httpx/httpcore. The anthropic SDK (1.x) sends through its
    own `httpx2` package, which respx never sees: with only the respx route
    registered, a ClaudeClient built normally reached the real
    api.anthropic.com (observed 2026-10-10: HTTP 401 "invalid x-api-key" with
    a live request_id). So the SDK is handed an explicit MockTransport that
    forwards each request to router.handler. An unrouted request raises
    inside respx (assert_all_mocked) and never reaches a socket.
    """
    import anthropic
    import httpx
    from anthropic import _base_client

    from app.services.anthropic_client import ClaudeClient

    sdk_http = getattr(_base_client, "httpx2", None) or httpx

    def _forward(sdk_request):
        sdk_request.read()
        response = router.handler(httpx.Request(
            sdk_request.method, str(sdk_request.url),
            headers=list(sdk_request.headers.raw), content=sdk_request.content,
        ))
        response.read()
        return sdk_http.Response(
            response.status_code,
            headers=list(response.headers.raw), content=response.content,
        )

    client = ClaudeClient()
    client._client = anthropic.Anthropic(
        api_key=get_settings().anthropic_api_key,
        http_client=sdk_http.Client(transport=sdk_http.MockTransport(_forward)),
    )
    return client


@pytest.fixture(autouse=True)
def no_real_anthropic(request, monkeypatch):
    """Integration override of the root conftest's `no_real_anthropic`.

    The root guard replaces anthropic_client.get_client with a function that
    always raises, because root tests use FakeClaude. This directory mocks
    Anthropic one layer lower, at HTTP (tests/integration/mocks/
    anthropic_mock.py on the respx router), so that the real ClaudeClient --
    retries, truncation check, usage meter -- runs. Under the root guard all
    50 of those tests failed before reaching the code under test.

    The guard is kept, narrowed to what it protects against: a request leaving
    the machine. get_client returns a ClaudeClient wired to this test's respx
    router only when that router has an api.anthropic.com route; any other
    test keeps the refusal. The module singleton is never built or touched.
    """
    router = (request.getfixturevalue("mock_transports")
              if "mock_transports" in request.fixturenames else None)
    built: list = []

    def _guarded():
        routed = router is not None and any(
            "api.anthropic.com" in repr(route.pattern) for route in router.routes
        )
        if not routed:
            raise RuntimeError(
                "test attempted a real Anthropic call -- register "
                "anthropic_mock on mock_transports (all_mocks) first"
            )
        if not built:
            built.append(_respx_backed_anthropic_client(router))
        return built[0]

    for module in _anthropic_get_client_bindings():
        monkeypatch.setattr(module, "get_client", _guarded)


def _is_gateway_binding(fn) -> bool:
    """True for the gateway's get_client or a test stand-in for it.

    A module that binds get_client by name can hold the gateway itself or
    whatever an earlier test patched in: root conftest's _refuse or
    fake_claude, _guarded above, or a root test module's own lambda
    (test_support_chat, test_strategy_budget, ...). All of those must be
    replaced, or a stale fake answers instead of the respx mock.
    """
    origin = getattr(fn, "__module__", None) or ""
    return origin == "app.services.anthropic_client" or origin.startswith("tests.")


def _anthropic_get_client_bindings() -> list:
    """Every loaded app module holding the Anthropic gateway's get_client.

    Twelve modules bind it by name at import (engine, pattern_recognition,
    icp_extraction, ...). A module first imported while a test's patch is in
    place keeps THAT test's function for the rest of the session, so patching
    only anthropic_client.get_client left later tests calling a stale guard --
    the suite passed or failed depending on order. Scanning sys.modules
    catches every binding without a hand-kept list. Same-named functions that
    are not the gateway (mailreach, unipile_linkedin, client_workspaces) have
    another __module__ and are left alone.
    """
    # Import the gateway first: a module that is not loaded yet cannot be
    # patched, and anything imported later binds the patched attribute.
    import app.services.anthropic_client  # noqa: F401

    return [
        module for name, module in list(sys.modules.items())
        if (name == "app" or name.startswith("app.")) and module is not None
        and _is_gateway_binding(getattr(module, "get_client", None))
    ]


@pytest.fixture()
def all_mocks(mock_transports):
    """Register every external API mock on the shared respx router."""
    anthropic_mock.register(mock_transports)
    apollo_mock.register(mock_transports)
    hunter_mock.register(mock_transports)
    gmail_mock.register(mock_transports)
    whatsapp_mock.register(mock_transports)
    calendly_mock.register(mock_transports)
    firebase_mock.register(mock_transports)   # clears the capture only

    # Reset call captures
    apollo_mock.reset_capture()
    gmail_mock.reset_capture()
    whatsapp_mock.reset_capture()
    firebase_mock.reset_capture()

    yield mock_transports


# ---------------------------------------------------------------------------
# User fixtures
# ---------------------------------------------------------------------------

def _mark_verified(db_session, email: str) -> None:
    """Flip email_verified on a freshly signed-up integration user.

    Feature 1 made /auth/signup produce an UNVERIFIED account, and
    get_current_user answers 403 EMAIL_NOT_VERIFIED for one -- so without this
    every authenticated call in this directory would 403 while testing
    something unrelated to verification.

    Flipping the column directly is the same shortcut admin_tokens already
    takes for is_admin, and for the same reason: the fixture's job is to hand
    back a usable account, not to re-test the signup flow. The verification
    flow itself is covered end to end in tests/test_email_verification.py.
    """
    from app.db.models import User

    user = db_session.query(User).filter(User.email == email).one()
    user.email_verified = True
    db_session.commit()


@pytest_asyncio.fixture()
async def admin_tokens(api_client: AsyncClient, db_session) -> dict[str, str]:
    """Register, PROMOTE and authenticate a fresh admin user.

    /auth/signup takes `Credentials` -- email and password, nothing else -- so
    an `is_admin: True` key in the signup body is silently dropped. That is
    correct (no self-promotion over HTTP); admins are made out of band by
    `python -m app.cli.create_admin`. This fixture therefore flips the column
    directly, which is what that CLI does. Signing up with the flag and
    expecting an admin back, as this fixture and several tests used to, yields
    an ordinary user and a 403 from every /admin and /playbook/aggregate call.
    """
    from app.db.models import User

    email = f"admin_{uuid.uuid4().hex[:6]}@test.io"
    await api_client.post("/auth/signup", json={
        "email": email,
        "password": "Admin1234!",
    })

    user = db_session.query(User).filter(User.email == email).one()
    user.is_admin = True
    user.email_verified = True   # Feature 1 — see _mark_verified
    db_session.commit()

    resp = await api_client.post("/auth/login", json={
        "email": email,
        "password": "Admin1234!",
    })
    # Return the whole login body, exactly like user_a_tokens / user_b_tokens.
    # This used to hand back only the two tokens, so helpers that resolve the
    # caller from tokens["user"]["id"] (set_plan, register_device_token) raised
    # KeyError on an admin.
    return resp.json()


@pytest_asyncio.fixture()
async def user_a_tokens(api_client: AsyncClient, db_session) -> dict[str, str]:
    email = f"usera_{uuid.uuid4().hex[:6]}@test.io"
    await api_client.post("/auth/signup", json={"email": email, "password": "UserA1234!"})
    _mark_verified(db_session, email)
    resp = await api_client.post("/auth/login", json={"email": email, "password": "UserA1234!"})
    return resp.json()


@pytest_asyncio.fixture()
async def user_b_tokens(api_client: AsyncClient, db_session) -> dict[str, str]:
    email = f"userb_{uuid.uuid4().hex[:6]}@test.io"
    await api_client.post("/auth/signup", json={"email": email, "password": "UserB1234!"})
    _mark_verified(db_session, email)
    resp = await api_client.post("/auth/login", json={"email": email, "password": "UserB1234!"})
    return resp.json()


def _auth_headers(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

async def create_product(
    client: AsyncClient,
    headers: dict[str, str],
    name: str = "Vectorae",
    description: str = "AI-powered client acquisition",
    product_type: str = "product",
) -> dict[str, Any]:
    resp = await client.post("/products", headers=headers, json={
        "name": name,
        "description": description,
        "type": product_type,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_past_clients(
    client: AsyncClient,
    headers: dict[str, str],
    product_id: str,
) -> list[dict[str, Any]]:
    """Create the product's past clients.

    POST /products/{id}/past-clients takes ONE request carrying every client
    under a `clients` key (PastClientsCreate) and returns the created list.
    This helper used to post each client separately as a flat body, which the
    API rejects with 422 "Field required: clients" - and because it asserted
    201, it took the cross-tenant isolation tests down in setup before they
    reached a single isolation assertion.

    PastClientIn accepts only `details` and `acquisition_story`; the industry
    / deal_size / sales_cycle fields this helper used to send were never
    persisted by the endpoint, so they are dropped rather than silently
    ignored. Pattern extraction runs off the free text.
    """
    clients_data = [
        {
            "details": "Acme Corp, 50 employees, SaaS startup",
            "acquisition_story": (
                "Met the VP Sales on LinkedIn after their Series A "
                "announcement. Sent a cold email with a case study. "
                "Closed in 2 weeks at $12,000."
            ),
        },
        {
            "details": "BetaCo, agency, 12 employees",
            "acquisition_story": (
                "Referral from a mutual connection during a team expansion. "
                "WhatsApp intro to the founder, closed in 3 weeks at $6,000."
            ),
        },
        {
            "details": "GammaServices, professional services, 80 employees",
            "acquisition_story": (
                "Cold outreach via email around their new product launch. "
                "The subject line mentioned their industry specifically. "
                "VP Sales signed after a 30-day cycle at $24,000."
            ),
        },
    ]
    resp = await client.post(
        f"/products/{product_id}/past-clients",
        headers=headers,
        json={"clients": clients_data},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_strategy(
    client: AsyncClient,
    headers: dict[str, str],
    product_id: str,
    flow_type: str = "with_clients",
) -> dict[str, Any]:
    resp = await client.post(
        f"/products/{product_id}/strategies",
        headers=headers,
        json={"flow_type": flow_type},
    )
    # 202, not 201: app/api/strategies.py returns Accepted because creating a
    # strategy only enqueues the generation pipeline.
    assert resp.status_code == 202, resp.text
    return resp.json()


async def seed_outcomes(
    db_session,
    strategy_id: str,
    variant: str,
    sends: int,
    replies: int,
    bookings: int,
) -> None:
    """Directly insert outcome rows to simulate a completed campaign.

    One real Lead row per send, because reply/booking attribution in
    score_decay.compute_scores_with_decay() is per lead: a send counts as
    replied when THAT lead also has a replied outcome. The previous
    f"lead_{...}" strings were not UUIDs and could never have satisfied the
    leads.id foreign key. `sends` leads are created; the first `replies` of
    them reply and the first `bookings` of them book, so the expected
    reply_rate is exactly replies/sends.
    """
    import datetime

    from app.db.models import Lead, LeadStatus
    from app.models.outcome import Outcome, OutcomeEvent

    leads: list[Lead] = []
    for i in range(sends):
        lead = Lead(
            strategy_id=strategy_id,
            source="seed",
            email=f"seed_{variant}_{i}_{uuid.uuid4().hex[:8]}@example.test",
            status=LeadStatus.CONTACTED,
        )
        db_session.add(lead)
        leads.append(lead)
    db_session.flush()

    for i in range(sends):
        db_session.add(Outcome(
            lead_id=leads[i].id,
            strategy_id=strategy_id,
            variant=variant,
            event=OutcomeEvent.SENT,
            ts=datetime.datetime.utcnow(),
        ))

    for i in range(replies):
        db_session.add(Outcome(
            lead_id=leads[i].id,
            strategy_id=strategy_id,
            variant=variant,
            event=OutcomeEvent.REPLIED,
            ts=datetime.datetime.utcnow(),
        ))

    for i in range(bookings):
        db_session.add(Outcome(
            lead_id=leads[i].id,
            strategy_id=strategy_id,
            variant=variant,
            event=OutcomeEvent.BOOKED,
            ts=datetime.datetime.utcnow(),
        ))

    db_session.commit()


# ---------------------------------------------------------------------------
# Direct-DB assertions
#
# These read state that has no public HTTP surface and no client that needs
# one: research_steps rows are the engine's internal resumability record (the
# UI renders progress from GET /strategies/{id}), and outcomes are the
# immutable learning-loop event log. The suite used to call
# GET /strategies/{id}/steps, /leads/{id}/outcomes and
# /strategies/{id}/outcomes -- none of which have ever existed in this app.
# Asserting against the tables directly tests the same invariant without
# inventing public API surface to satisfy a test.
# ---------------------------------------------------------------------------


def research_steps(db_session, strategy_id, pipeline: str = "strategy") -> list:
    """Persisted steps for one pipeline of a strategy, ordered by step_no.

    NOTE: ResearchStep has no `status` column -- a row is written only when a
    step COMPLETES, and row presence IS the completion record (that is the
    resumability contract behind UNIQUE(strategy_id, pipeline, step_no)).
    """
    from app.db.models import PipelineKind, ResearchStep

    kind = PipelineKind(pipeline) if not isinstance(pipeline, PipelineKind) else pipeline
    db_session.expire_all()
    return list(
        db_session.query(ResearchStep)
        .filter(ResearchStep.strategy_id == str(strategy_id),
                ResearchStep.pipeline == kind)
        .order_by(ResearchStep.step_no)
        .all()
    )


def lead_outcome_events(db_session, lead_id) -> list[str]:
    """Outcome event values recorded against one lead, oldest first."""
    from app.db.models import Outcome

    db_session.expire_all()
    rows = (
        db_session.query(Outcome)
        .filter(Outcome.lead_id == str(lead_id))
        .order_by(Outcome.ts)
        .all()
    )
    return [o.event.value for o in rows]


def strategy_outcomes(db_session, strategy_id, event: str | None = None) -> list:
    """Outcome rows carrying this strategy_id, optionally filtered by event."""
    from app.db.models import Outcome, OutcomeEvent

    db_session.expire_all()
    q = db_session.query(Outcome).filter(Outcome.strategy_id == str(strategy_id))
    if event is not None:
        q = q.filter(Outcome.event == OutcomeEvent(event))
    return list(q.order_by(Outcome.ts).all())


def set_plan(db_session, tokens: dict, plan: str = "pro") -> None:
    """Put a test user on a paid plan.

    Everything under /playbook is gated by check_feature(..., "playbook_access"),
    which is False on `free` -- the plan every signup starts on -- so those
    endpoints answer 402 Payment Required. Tests that exercise the learning
    loop have to buy the feature first; asserting 200 as a free user was
    asserting the gate does not work.
    """
    from app.db.models import PlanTier, User

    user_id = tokens["user"]["id"]
    user = db_session.get(User, user_id)
    user.plan = PlanTier(plan)
    db_session.commit()


def register_device_token(db_session, tokens: dict, label: str = "dev") -> str:
    """Give a user one valid FCM device token and return it.

    Notification tests assert on WHICH token a push reached, so every test
    registers a token per user (owner and non-owner) and matches on it.
    """
    import uuid as _uuid

    from app.db.models import DeviceToken

    token = f"fcm_{label}_{_uuid.uuid4().hex[:12]}"
    db_session.add(DeviceToken(
        user_id=tokens["user"]["id"],
        token=token,
        platform="android",
        is_valid=True,
    ))
    db_session.commit()
    return token


def strategy_row(db_session, strategy_id):
    """The Strategy ORM row -- for columns StrategyStatusOut does not expose
    (default_variant, campaign_pause_reason, the raw documents)."""
    from app.db.models import Strategy

    db_session.expire_all()
    return db_session.get(Strategy, str(strategy_id))


def enrollment_statuses(db_session, lead_id) -> list[str]:
    """Status of every sequence enrollment for a lead."""
    from app.db.models import SequenceEnrollment

    db_session.expire_all()
    rows = (
        db_session.query(SequenceEnrollment)
        .filter(SequenceEnrollment.lead_id == str(lead_id))
        .all()
    )
    return [e.status.value for e in rows]


# ---------------------------------------------------------------------------
# Expose helpers as fixtures for test use
# ---------------------------------------------------------------------------

@pytest.fixture()
def factories():
    return {
        "create_product": create_product,
        "create_past_clients": create_past_clients,
        "create_strategy": create_strategy,
        "seed_outcomes": seed_outcomes,
        "auth_headers": _auth_headers,
    }
