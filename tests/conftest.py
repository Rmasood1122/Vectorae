"""Shared test fixtures for the root test modules.

This is the SQLite/in-process fixture library that the 22 root test
modules were written against. It was previously replaced wholesale by a
real-PostgreSQL conftest, which left every module importing NOW /
FakeLeadSource / make_raw / wa_outbound unable to even import. That
PostgreSQL harness is used only by tests/integration/, so it now lives in
tests/integration/conftest.py where it belongs - see the note there.

Rules enforced here:
- ZERO real Anthropic API usage: every module-level `get_client` binding is
  monkeypatched to a scriptable FakeClaude.
- ZERO real broker usage: `run_pipeline.delay` is stubbed and recorded.
- SQLite in-memory database built from the same models/metadata as prod.
"""

import os
import re
import socket
import sqlite3
import sys

os.environ["APP_ENV"] = "test"
# No proxies: the suite is offline (no_external_network below). A proxy would
# also hide the real destination from that guard -- the socket would only
# ever see the proxy's loopback address.
for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                   "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_proxy_var, None)
os.environ["ANTHROPIC_API_KEY"] = "test-key-never-used"
os.environ["DATABASE_URL"] = "sqlite://"
# Hard-assigned like DATABASE_URL, never setdefault: settings read .env for any
# key missing from os.environ, and .env points REDIS_URL / CELERY_* at the
# PRODUCTION Upstash instance. The limiter is faked below, but the response
# cache and circuit breaker (app/integrations/plumbing.py), get_redis() and
# Celery all build clients from these URLs. db 14 of the local compose Redis
# (docker-compose.yml, port 6380); if it is not running, those paths see a
# refused connection, never a remote host. tests/integration/conftest.py
# re-targets db 15 for its own run.
_TEST_REDIS_URL = "redis://localhost:6380/14"
os.environ["REDIS_URL"] = _TEST_REDIS_URL
os.environ["CELERY_BROKER_URL"] = _TEST_REDIS_URL
os.environ["CELERY_RESULT_BACKEND"] = _TEST_REDIS_URL
from cryptography.fernet import Fernet as _Fernet
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", _Fernet.generate_key().decode())
# M4: WhatsApp config — fake values; no test ever reaches the real Graph API.
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test-wa-token")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "5550001")
os.environ.setdefault("WHATSAPP_BUSINESS_ACCOUNT_ID", "waba-test")
os.environ.setdefault("WHATSAPP_APP_SECRET", "test-app-secret")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test-verify-token")

# ── Enterprise-app env requirements ───────────────────────────────────────
# app/core/crypto.py builds a MultiFernet from ENCRYPTION_KEY and raises
# EncryptionKeyError at import-time-of-use if it is unset or not a valid
# Fernet key, which is why 40 tests errored before this was added. Generate
# a real key per run rather than hardcoding a string - a hand-written
# "32 characters long!!" placeholder is *not* valid base64-url and fails
# Fernet() just as loudly as a missing one.
os.environ.setdefault("ENCRYPTION_KEY", _Fernet.generate_key().decode())
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("APOLLO_API_KEY", "apollo-test-key")
os.environ.setdefault("HUNTER_API_KEY", "hunter-test-key")
os.environ.setdefault("CALENDLY_WEBHOOK_SECRET", "calendly-secret-test")
os.environ.setdefault("FIREBASE_CREDENTIALS_JSON", "{}")
os.environ.setdefault("CELERY_ALWAYS_EAGER", "true")
os.environ.setdefault("CELERY_EAGER_PROPAGATES", "true")
# Mount the test-only debug router (app/api/debug.py). This is the ONLY place
# in the repo that turns it on; app_env stays non-production so main.py's
# defence-in-depth check also passes. See app/api/debug.py for the guard.
os.environ.setdefault("ENABLE_DEBUG_ROUTES", "true")
os.environ.setdefault("APP_ENV", "test")
# Feature 1: the in-process mail transport. Tests need to READ the verification
# link, not just assert that something was sent, and "memory" is the only
# transport that keeps the rendered message where a test can reach it. It is
# also the only one that cannot accidentally contact a real relay from CI.
os.environ.setdefault("EMAIL_PROVIDER", "memory")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("FRONTEND_URL", "http://frontend.test")
# Sections B/C/D (migration 0039). No test may reach a real geolocation, VPN
# detection or SMS provider: geolocation and VPN detection are disabled (tests
# that exercise them patch a fake provider in), blocking is explicitly off, and
# SMS uses the in-process transport so a test can read the code it was sent.
os.environ.setdefault("GEOLOCATION_PROVIDER", "disabled")
os.environ.setdefault("VPN_BLOCK_ENABLED", "false")
os.environ.setdefault("VPN_DETECTION_PROVIDER", "disabled")
os.environ.setdefault("SMS_PROVIDER", "memory")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base, get_db
from app.db import models as m  # noqa: F401  (registers tables)
from app.services.anthropic_client import get_client as _REAL_GET_CLIENT


def _assert_local_redis_only() -> None:
    """Fail the whole run, before any test, if a Redis URL resolves off-host.

    Checks the values the app actually uses, not the env vars: a settings
    object built before the overrides above, or a new key read from .env,
    would otherwise slip through silently.
    """
    from urllib.parse import urlparse

    from app.config import settings as legacy
    from app.core.config import settings as m8
    from app.workers.celery_app import celery_app

    resolved = {
        "app.config.settings.redis_url": legacy.redis_url,
        "app.config.settings.broker_url": legacy.broker_url,
        "app.config.settings.result_backend": legacy.result_backend,
        "app.core.config.settings.REDIS_URL": m8.REDIS_URL,
        "celery_app.conf.broker_url": celery_app.conf.broker_url,
        "celery_app.conf.result_backend": celery_app.conf.result_backend,
    }
    remote = {k: urlparse(v).hostname for k, v in resolved.items()
              if urlparse(v).hostname not in ("localhost", "127.0.0.1")}
    assert not remote, f"test run would reach a non-local Redis: {remote}"


_assert_local_redis_only()


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------


@event.listens_for(Engine, "connect")
def _sqlite_without_fsync(dbapi_connection, _record):
    """Tests only: SQLite files are scratch, so skip the fsync after each
    write. A migration test that builds ~130 tables in a file database spent
    ~1 s in fsync; ~0.2 s without. Data, schema and the journal are unchanged
    (synchronous only decides whether SQLite waits for the disk)."""
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        dbapi_connection.execute("PRAGMA synchronous=OFF")


_SCHEMA_TEMPLATE = None


def _schema_template():
    """One in-memory database with every table created, built once per run.

    Building all ~130 tables took ~110 ms per test, about half the offline
    suite's wall time. Each test now gets its own fresh in-memory database
    copied from this template with SQLite's backup API (~1 ms): the same
    empty schema, nothing shared between tests. No engine-level connect
    listener exists in app/, so a copy and a fresh create_all are the same.
    """
    global _SCHEMA_TEMPLATE
    if _SCHEMA_TEMPLATE is None:
        template = sqlite3.connect(":memory:", check_same_thread=False)
        engine = create_engine("sqlite://", creator=lambda: template, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        _SCHEMA_TEMPLATE = template
    return _SCHEMA_TEMPLATE


@pytest.fixture()
def db_session():
    fresh = sqlite3.connect(":memory:", check_same_thread=False)
    _schema_template().backup(fresh)
    engine = create_engine("sqlite://", creator=lambda: fresh, poolclass=StaticPool)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# --------------------------------------------------------------------------
# FakeClaude — scriptable stand-in for every model call
# --------------------------------------------------------------------------


class FakeClaude:
    """Deterministic Claude stand-in.

    - `fail_after=N`: the (N+1)th `complete` call raises (crash simulation).
    - `judge_script[pass_no] = [("FAIL", "fix ..."), ("PASS", None)]`:
      scripted verdicts per verification pass; default verdict is PASS.
    - `pattern_result`: dict returned for pattern-extraction calls.
    """

    def __init__(self):
        self.completions = 0
        self.fail_after: int | None = None
        self.judge_script: dict[int, list[tuple[str, str | None]]] = {}
        self.judge_calls: list[int] = []
        self.fix_calls: list[int] = []
        self.reply_verdicts: dict[str, str] = {}
        # Feature 3: what complete_json returns for a support-chat call.
        # A dict is returned verbatim; an Exception instance is raised, so a
        # test can simulate the API being down without patching anything else.
        self.support_response: object | None = None
        self.support_prompts: list[str] = []
        # Engagement Hub. Same contract as support_response: a dict is
        # returned verbatim, an Exception instance is raised, so a test can
        # simulate the model being unavailable without patching anything else.
        self.meeting_summary_response: object | None = None
        self.meeting_prompts: list[str] = []
        self.followup_brief_response: object | None = None
        self.followup_prompts: list[str] = []
        # Feature Group 7. Same contract: dict returned, Exception raised.
        self.meeting_prep_response: object | None = None
        self.meeting_prep_prompts: list[str] = []
        self.followup_email_response: object | None = None
        self.followup_email_prompts: list[str] = []
        # Feature Group 1. Same contract: dict returned, Exception raised.
        self.consensus_response: object | None = None
        self.consensus_prompts: list[str] = []
        self.lead_score_response: object | None = None
        self.lead_score_prompts: list[str] = []
        # Default lead-score behaviour: baseline + this, so the clamp is
        # exercised by every scoring test that does not override it.
        self.lead_score_offset = 30
        # Feature Group 9: the automated-reply detector (reply_fraud). Same
        # contract: dict returned, Exception raised; default "human".
        self.automated_reply_response: object | None = None
        self.automated_reply_prompts: list[str] = []
        self.mutation_response: object | None = None
        self.mutation_prompts: list[str] = []
        # Feature Group 2.
        self.style_response: object | None = None
        self.video_script_response: object | None = None
        # Feature 2 (reply intelligence). Same contract as the others: a dict
        # is returned verbatim, an Exception instance is raised.
        self.reply_intelligence_response: object | None = None
        self.reply_intelligence_prompts: list[str] = []
        self.reply_intelligence_systems: list[str] = []
        # Part 1 Feature 1 (reply_intent). Same contract: a dict is returned
        # verbatim, a list is a script (one per call, last repeats), an
        # Exception is raised.
        self.reply_intent_response: object | None = None
        self.reply_intent_prompts: list[str] = []
        # Part 1 Feature 2 (fpta_scoring). Default: echo each prospect's
        # baselines back unchanged with a canned reason, so a test that does
        # not care about the model still exercises the real merge path.
        # Phase 3 item 2: the schema each complete_json call passed (None if none).
        self.json_schemas: list[dict | None] = []
        self.fpta_response: object | None = None
        self.fpta_prompts: list[str] = []
        # Part 1 Feature 7 (reengagement_memory). Same contract as the rest.
        self.not_now_response: object | None = None
        self.not_now_prompts: list[str] = []
        # Part 2 (roleplay). `roleplay_reply_response` is what the PROSPECT
        # says (a plain string, or an Exception to raise);
        # `roleplay_feedback_response` is the coach's JSON.
        self.roleplay_reply_response: object | None = None
        self.roleplay_prompts: list[str] = []
        self.roleplay_feedback_response: object | None = None
        self.roleplay_feedback_prompts: list[str] = []
        # Feature 3 (founder voice cloning). Same contract.
        self.voice_profile_response: object | None = None
        self.voice_profile_prompts: list[str] = []
        # Feature 4 (competitor displacement DMs). Same contract, and a list
        # is a SCRIPT (one response per call, the last one repeating) so the
        # Day 0 rule-repair path can be exercised.
        self.displacement_dm_response: object | None = None
        self.displacement_dm_prompts: list[str] = []
        # F04 Reply Copilot. Same contract: a dict is returned verbatim, a
        # list is a SCRIPT (one per call, last repeats), an Exception is raised.
        self.reply_copilot_response: object | None = None
        self.reply_copilot_prompts: list[str] = []
        self.reply_copilot_systems: list[str] = []
        # Feature A1 (claim engine). Same contract: dict returned, Exception
        # raised; default "no claims found" so the rules alone decide.
        self.claim_extraction_response: object | None = None
        self.claim_extraction_prompts: list[str] = []
        # Feature A7 (pre-send red team). Same contract; default no findings.
        self.red_team_response: object | None = None
        self.red_team_prompts: list[str] = []
        # Every (system, prompt) the personalization engine received, so a
        # test can assert on the voice suffix and the post/news/video blocks.
        self.personalization_calls: list[tuple[str, str]] = []
        self.default_reply_class = "interested"
        self.pattern_result = {
            "industry": "fire protection",
            "company_size": "11-50",
            "buyer_role": "Owner",
            "deal_size": "$3,000/mo",
            "acquisition_channel": "referral",
            "trigger_event": "failed inspection",
            "sales_cycle_length": "3 weeks",
        }

    # ---- text completions (pipeline steps + fixes) ----
    def complete(self, system: str, prompt: str, max_tokens: int | None = None, temperature=None,
                 schema=None, cache_prefix=None) -> str:
        prompt = (cache_prefix or "") + prompt  # what the model reads, in order
        if self.fail_after is not None and self.completions >= self.fail_after:
            raise RuntimeError("simulated crash: model call failed")
        self.completions += 1
        if "ROLEPLAYING A SALES PROSPECT" in system:  # Part 2
            self.roleplay_prompts.append(prompt)
            if isinstance(self.roleplay_reply_response, Exception):
                raise self.roleplay_reply_response
            if self.roleplay_reply_response is not None:
                if isinstance(self.roleplay_reply_response, list):
                    index = min(len(self.roleplay_prompts) - 1,
                                len(self.roleplay_reply_response) - 1)
                    return self.roleplay_reply_response[index]
                return self.roleplay_reply_response
            return "Honestly, we looked at something like this last year and parked it."
        if "strategy editor" in system:  # FIXER_SYSTEM
            match = re.search(r"criterion #(\d+)", prompt)
            if match:
                self.fix_calls.append(int(match.group(1)))
            return f"REVISED DOCUMENT v{self.completions}\n(fix applied)"
        return f"Mock research output #{self.completions}"

    # ---- JSON completions (patterns + verification judge) ----
    def complete_json(self, system: str, prompt: str, max_tokens: int | None = None, temperature=None,
                      schema=None, cache_prefix=None) -> dict:
        prompt = (cache_prefix or "") + prompt  # what the model reads, in order
        self.completions += 1
        self.json_schemas.append(schema)
        if "Reply Copilot drafter" in system:  # F04
            self.reply_copilot_prompts.append(prompt)
            self.reply_copilot_systems.append(system)
            response = self.reply_copilot_response
            if isinstance(response, list):
                index = min(len(self.reply_copilot_prompts) - 1, len(response) - 1)
                response = response[index]
            if isinstance(response, Exception):
                raise response
            if response is not None:
                return response
            return {"body": "Thanks Sara - happy to walk you through it. "
                            "Would a short call this week suit?",
                    "meeting_time_request": False}
        if "Victorae in-app support assistant" in system:  # Feature 3
            self.support_prompts.append(prompt)
            if isinstance(self.support_response, Exception):
                raise self.support_response
            if self.support_response is not None:
                return self.support_response
            # Default: a confident, on-topic, grounded answer.
            return {"on_topic": True, "confidence": 0.9,
                    "answer": "Victorae runs a 72-step research pipeline.",
                    "faq_ids": ["what-is-leadpilot"]}
        if "meeting analyst" in system:  # Engagement Hub, Feature 3
            self.meeting_prompts.append(prompt)
            if isinstance(self.meeting_summary_response, Exception):
                raise self.meeting_summary_response
            if self.meeting_summary_response is not None:
                return self.meeting_summary_response
            return {
                "summary": "They walked through their onboarding backlog.",
                "key_points": ["Two-week onboarding backlog",
                               "Budget approved for Q4"],
                "action_items": [
                    {"text": "Send the pricing sheet", "owner": "us",
                     "due": None},
                    {"text": "Introduce us to their ops lead",
                     "owner": "client", "due": None},
                ],
                "next_steps": ["Follow up Thursday"],
                "sentiment": "positive",
            }
        if "follow-up brief writer" in system:  # Engagement Hub, Feature 1
            self.followup_prompts.append(prompt)
            if isinstance(self.followup_brief_response, Exception):
                raise self.followup_brief_response
            if self.followup_brief_response is not None:
                return self.followup_brief_response
            return {"brief": "Re-open on their onboarding backlog and ask "
                             "which team owns it today."}
        if "meeting prep strategist" in system:  # Feature Group 7
            self.meeting_prep_prompts.append(prompt)
            if isinstance(self.meeting_prep_response, Exception):
                raise self.meeting_prep_response
            if self.meeting_prep_response is not None:
                return self.meeting_prep_response
            return {
                "company_overview": "A 30-person fire protection contractor.",
                "recent_activity": "Posted about inspection backlogs.",
                "why_they_booked": 'They replied to step 2: "we are drowning '
                                   'in inspections".',
                "pain_points": ["Inspection backlog (inferred from their reply)"],
                "likely_objections": [{"objection": "We use a spreadsheet",
                                       "response": "Ask what a missed "
                                                   "inspection costs."}],
                "talking_points": ["Lead with the backlog they named"],
                "discovery_questions": ["Who owns inspection scheduling today?"],
                "competitive_landscape": "Nothing in our records.",
                "next_steps": ["Offer a two-week pilot"],
                "deal_structure": "Monthly retainer per the strategy.",
                "opening_60_seconds": "Thanks for booking, Sara. You mentioned "
                                      "your team is drowning in inspections.",
            }
        if "post-meeting follow-up writer" in system:  # Feature Group 7
            self.followup_email_prompts.append(prompt)
            if isinstance(self.followup_email_response, Exception):
                raise self.followup_email_response
            if self.followup_email_response is not None:
                return self.followup_email_response
            return {"subject": "Great speaking today",
                    "body": "Hi Sara,\n\nThanks for the time today."}
        if "consensus judge" in system:  # Feature Group 1
            self.consensus_prompts.append(prompt)
            if isinstance(self.consensus_response, Exception):
                raise self.consensus_response
            if self.consensus_response is not None:
                return self.consensus_response
            return {"disagreements": [
                {"topic": "Primary channel", "a_position": "Email first",
                 "b_position": "LinkedIn first", "severity": "high"},
                {"topic": "Tone", "a_position": "Formal",
                 "b_position": "Casual", "severity": "low"},
            ], "agreement_summary": "Agree on the ICP."}
        if "sales coach reviewing a transcript" in system:  # Part 2
            self.roleplay_feedback_prompts.append(prompt)
            if isinstance(self.roleplay_feedback_response, Exception):
                raise self.roleplay_feedback_response
            if self.roleplay_feedback_response is not None:
                return self.roleplay_feedback_response
            return {
                "scores": {"overall": 72, "discovery": 80, "objections": 55,
                           "tone": 78, "close": 60},
                "went_well": ['You opened without pitching: "what made you take '
                              'the call?"'],
                "improve": ["You moved on after their price objection instead of "
                            "answering it."],
                "tone_notes": "Warm, slightly fast in the first minute.",
                "pacing_notes": "Three questions in a row at 02:10 — let one land.",
                "objections_missed": [{"objection": "We parked this last year",
                                       "why": "You changed the subject to features."}],
                "one_thing": "Answer the first objection before asking anything else.",
            }
        if "said about coming back" in system:  # Part 1 Feature 7
            self.not_now_prompts.append(prompt)
            if isinstance(self.not_now_response, Exception):
                raise self.not_now_response
            if self.not_now_response is not None:
                return self.not_now_response
            return {"reason_kind": "budget",
                    "reason_text": "no budget until the new fiscal year",
                    "return_on": None, "confidence": 0.8}
        if "F-P-T-A prospect analyst" in system:  # Part 1 Feature 2
            self.fpta_prompts.append(prompt)
            if isinstance(self.fpta_response, Exception):
                raise self.fpta_response
            if self.fpta_response is not None:
                return self.fpta_response
            import json as _json
            body = prompt.split("PROSPECTS:", 1)[1]
            prospects = _json.loads(body.rsplit("Return the JSON", 1)[0].strip())
            return {"prospects": [
                {"lead_id": p["lead_id"], **{
                    dim: {"score": p["baselines"][dim],
                          "reason": f"{dim} reason for {p.get('company') or 'them'}"}
                    for dim in ("fit", "problem", "timing", "access")}}
                for p in prospects
            ]}
        if "lead scoring analyst" in system:  # Feature Group 1
            self.lead_score_prompts.append(prompt)
            if isinstance(self.lead_score_response, Exception):
                raise self.lead_score_response
            if self.lead_score_response is not None:
                return self.lead_score_response
            pairs = re.findall(r'"lead_id": "([^"]+)".*?"baseline": (\d+)', prompt)
            return {"scores": [
                {"lead_id": lid, "score": int(base) + self.lead_score_offset,
                 "reason": "Owner at an ICP-fit company."}
                for lid, base in pairs
            ]}
        if "strategy mutation strategist" in system:  # Feature Group 1
            self.mutation_prompts.append(prompt)
            if isinstance(self.mutation_response, Exception):
                raise self.mutation_response
            if self.mutation_response is not None:
                return self.mutation_response
            return {
                "diagnosis": "Opens without replies: the offer is not landing.",
                "messaging_angle": {"current": "Save time on audits",
                                    "proposed": "Pass your next fire inspection first time",
                                    "rationale": "Loss aversion beats efficiency."},
                "channel": {"recommended": "linkedin",
                            "rationale": "Owners read LinkedIn more than email."},
                "icp_refinement": {"changes": ["Target companies with 11-50 staff"],
                                   "rationale": "Smaller firms feel the pain."},
                "revised_messaging": "Hook: failed inspections cost contracts.",
            }
        if "displacement DM writer" in system:  # Feature 4
            self.displacement_dm_prompts.append(prompt)
            if isinstance(self.displacement_dm_response, Exception):
                raise self.displacement_dm_response
            if self.displacement_dm_response is not None:
                if isinstance(self.displacement_dm_response, list):
                    index = min(len(self.displacement_dm_prompts) - 1,
                                len(self.displacement_dm_response) - 1)
                    return self.displacement_dm_response[index]
                return self.displacement_dm_response
            return {"dm": "You wrote that cold email stopped converting for "
                          "you. That usually means the list moved, not the "
                          "copy. What changed about who you were writing to?"}
        if "voice analyst" in system:  # Feature 3
            self.voice_profile_prompts.append(prompt)
            if isinstance(self.voice_profile_response, Exception):
                raise self.voice_profile_response
            if self.voice_profile_response is not None:
                return self.voice_profile_response
            return {"avg_sentence_length": "short", "punctuation_style": "minimal",
                    "opens_with": "question", "uses_numbers": True,
                    "emoji_usage": "none", "paragraph_length": "single-line",
                    "vocabulary_level": "conversational",
                    "signature_phrases": ["here is the thing", "no fluff"]}
        if "reply intelligence engine" in system:  # Feature 2
            self.reply_intelligence_prompts.append(prompt)
            self.reply_intelligence_systems.append(system)
            if isinstance(self.reply_intelligence_response, Exception):
                raise self.reply_intelligence_response
            if self.reply_intelligence_response is not None:
                # A list is a SCRIPT: one response per call, last one repeats.
                # That is how the banned-word repair path is exercised.
                if isinstance(self.reply_intelligence_response, list):
                    index = min(len(self.reply_intelligence_prompts) - 1,
                                len(self.reply_intelligence_response) - 1)
                    return self.reply_intelligence_response[index]
                return self.reply_intelligence_response
            return {"category": "BUYING_SIGNAL", "confidence": 0.88,
                    "next_action": "send_step_3_product_intro",
                    "draft_response": "Happy to show you how it works. "
                                      "Would Thursday morning suit?"}
        if "sales analyst" in system:  # pattern recognition
            return dict(self.pattern_result)
        if "writing style analyst" in system:  # Feature Group 2
            if isinstance(self.style_response, Exception):
                raise self.style_response
            if self.style_response is not None:
                return self.style_response
            return {"tone": "warm and direct", "formality": 2,
                    "vocabulary_level": "conversational", "sentence_length": "short",
                    "avg_words_per_sentence": 11, "humor": "light",
                    "greeting_style": "Hey <first name>", "signoff_style": "Cheers",
                    "signature_habits": ["asks one question at the end"],
                    "do": ["use contractions"], "dont": ["use jargon"],
                    "summary": "Short, friendly and to the point."}
        if "video script writer" in system:  # Feature Group 2
            if isinstance(self.video_script_response, Exception):
                raise self.video_script_response
            if self.video_script_response is not None:
                return self.video_script_response
            return {"title": "A quick idea for your team",
                    "script": "Hi Sara, I saw your post about inspections...",
                    "on_screen": "Their website"}
        if "cold call script writer" in system:  # Feature Group 6
            if isinstance(getattr(self, "call_script_response", None), Exception):
                raise self.call_script_response
            if getattr(self, "call_script_response", None) is not None:
                return self.call_script_response
            return {"first_message": "Hi Sara, this is an AI assistant calling on behalf "
                                     "of Rehan. You posted about inspections — is now a bad time?",
                    "objective": "Book a 15-minute call",
                    "talking_points": ["Inspection backlog"],
                    "objection_handling": [{"objection": "No time", "response": "Two minutes?"}],
                    "questions": ["Who owns scheduling?"],
                    "close": "Would Thursday work?",
                    "voicemail": "Hi Sara, AI assistant for Rehan here — call back on 555."}
        if "call transcript analyst" in system:  # Feature Group 6
            if isinstance(getattr(self, "call_analysis_response", None), Exception):
                raise self.call_analysis_response
            if getattr(self, "call_analysis_response", None) is not None:
                return self.call_analysis_response
            return {"outcome": "interested", "summary": "Agreed to a Thursday call.",
                    "objections": ["We already have a vendor"],
                    "interest_signals": ["'send me a calendar invite'"],
                    "next_step": "Send invite", "stop_request": False, "sentiment": "positive"}
        if "LinkedIn message writer" in system:  # Feature Group 5
            kind = "INMAIL" if "LINKEDIN INMAIL" in prompt else (
                "NOTE" if "CONNECTION NOTE" in prompt else "MESSAGE")
            return {"text": f"Hi — {kind.lower()} about your inspection backlog.",
                    "subject": "Quick question"}
        if "personalization engine" in system:  # M3 message rendering
            self.personalization_calls.append((system, prompt))
            return {"subject": f"Subject v{self.completions}",
                    "body": f"Hello, this is rendered body v{self.completions}."}
        if "template drafter" in system:  # M4 template generation
            return {"templates": [{
                "name": "generated_intro",
                "language": "en_US",
                "category": "marketing",
                "body": "Hi {{1}}, this is Sam from Victorae about {{2}}. "
                        "Reply STOP to opt out.",
                "variable_descriptions": {"1": "first name", "2": "pain point"},
            }]}
        if "variable filler" in system:  # M4 template variables
            import re as _re
            numbers = _re.findall(r"\{\{(\d+)\}\} ->", prompt)
            return {n: f"value{n}" for n in numbers}
        if "pre-send red-team auditor" in system:  # Feature A7
            self.red_team_prompts.append(prompt)
            if isinstance(self.red_team_response, Exception):
                raise self.red_team_response
            if self.red_team_response is not None:
                return self.red_team_response
            return {"findings": []}
        if "claim extractor" in system:  # Feature A1
            self.claim_extraction_prompts.append(prompt)
            if isinstance(self.claim_extraction_response, Exception):
                raise self.claim_extraction_response
            if self.claim_extraction_response is not None:
                return self.claim_extraction_response
            return {"claims": []}
        if "automated-reply detector" in system:  # Feature Group 9
            self.automated_reply_prompts.append(prompt)
            if isinstance(self.automated_reply_response, Exception):
                raise self.automated_reply_response
            if self.automated_reply_response is not None:
                return self.automated_reply_response
            return {"automated": False, "reason": "reads like a person"}
        if "reply-quality classifier" in system:  # Part 1 Feature 1
            self.reply_intent_prompts.append(prompt)
            if isinstance(self.reply_intent_response, Exception):
                raise self.reply_intent_response
            if self.reply_intent_response is not None:
                if isinstance(self.reply_intent_response, list):
                    index = min(len(self.reply_intent_prompts) - 1,
                                len(self.reply_intent_response) - 1)
                    return self.reply_intent_response[index]
                return self.reply_intent_response
            return {"label": "interested", "confidence": 0.9,
                    "reason": "Asked to book a call on Thursday."}
        if "reply classifier" in system:  # M3 inbound classification
            body_part = prompt.split("BODY:", 1)[-1]
            for needle, cls in self.reply_verdicts.items():
                if needle in body_part:
                    return {"classification": cls}
            return {"classification": self.default_reply_class}
        if "ICP extraction" in system:  # M2 criteria extraction
            return {
                "titles": ["Owner", "Operations Manager"],
                "industries": ["fire protection services"],
                "locations": ["Texas, US", "Georgia, US"],
                "company_size_ranges": ["11,50"],
                "keywords": ["fire inspection", "ITM", "NFPA"],
            }
        if "reviewer" in system:  # verification judge
            pass_no = int(re.search(r"CRITERION #(\d+)", prompt).group(1))
            self.judge_calls.append(pass_no)
            script = self.judge_script.get(pass_no)
            result, fix = script.pop(0) if script else ("PASS", None)
            out = {"result": result}
            if fix is not None:
                out["fix_description"] = fix
            return out
        return {}


@pytest.fixture()
def fake_claude(monkeypatch):
    fake = FakeClaude()
    # Patch every module-level binding of get_client.
    for target in (
        "app.services.anthropic_client.get_client",
        "app.services.pattern_recognition.get_client",
        "app.services.icp_extraction.get_client",
        "app.services.message_personalization.get_client",
        "app.services.meeting_ai.get_client",
        "app.services.reply_classification.get_client",
        "app.services.reply_intelligence.get_client",   # Feature 2
        "app.services.reply_intent.get_client",         # Part 1 Feature 1
        # Part 2 (roleplay) reaches it as
        # `from app.services.anthropic_client import get_client` INSIDE the
        # call, so the module attribute patched below is what takes effect --
        # there is no module-level binding here to patch.
        "app.services.displacement_monitor.get_client",  # Feature 4
        # Feature 3 (voice_profiler) and style_profile both reach it as
        # anthropic_client.get_client() at call time, so the module
        # attribute patched first in this list is what takes effect.
        "app.services.whatsapp_templates.get_client",
        # Feature 3 reaches get_client through the MODULE at call time
        # (anthropic_client.get_client()), so patching the module attribute
        # below is what actually takes effect. Listed anyway so the intent is
        # visible and a future refactor to a `from` import fails loudly here.
        "app.pipeline.engine.get_client",
        "app.verification.loop.get_client",
    ):
        monkeypatch.setattr(target, lambda: fake)
    # And every other module that binds get_client by import (found from its
    # source, the same rule no_real_anthropic uses), so a module missing from
    # the list above -- website_builder was -- gets the fake too, never a
    # refusal or a stale fake from another test.
    for module in list(sys.modules.values()):
        if _binds_anthropic_get_client(module):
            monkeypatch.setattr(module, "get_client", lambda: fake)
    return fake


# --------------------------------------------------------------------------
# API client (DB override + stubbed Celery enqueue)
# --------------------------------------------------------------------------


@pytest.fixture()
def enqueued():
    return []


@pytest.fixture(autouse=True)
def queued_jobs(monkeypatch):
    """Feature expansion: record jobs instead of publishing them.

    CELERY_ALWAYS_EAGER is declared in app/core/config.py but never applied to
    the Celery app, so a real `.apply_async()` here would try to reach a Redis
    broker. The two enqueue helpers are the only publish points the feature
    expansion adds; autouse so no test (including the pre-existing Calendly
    and calendar ones, which now request a prep brief) can reach a broker.
    """
    jobs = {"prep": [], "events": []}

    def _prep(brief_id):
        jobs["prep"].append(str(brief_id))
        return True

    def _event(user_id, event, **kwargs):
        jobs["events"].append((str(user_id), event, kwargs))
        return True

    monkeypatch.setattr("app.workers.meeting_prep_tasks.enqueue_generation", _prep)
    monkeypatch.setattr("app.workers.notification_tasks.enqueue_event", _event)

    jobs["intel"] = []

    def _intel(task, *args):
        jobs["intel"].append((task.name, args))
        return True

    monkeypatch.setattr("app.workers.intelligence_tasks.enqueue", _intel)

    jobs["calls"] = []

    def _analysis(call_id):
        jobs["calls"].append(str(call_id))
        return True

    monkeypatch.setattr("app.workers.call_tasks.enqueue_analysis", _analysis)

    jobs["analytics"] = []

    def _windows(strategy_id):
        jobs["analytics"].append(str(strategy_id))
        return True

    monkeypatch.setattr("app.workers.analytics_tasks.enqueue_send_windows", _windows)

    jobs["site_generate"] = []
    jobs["site_export"] = []

    def _site_generate(page_id, user_id=None):
        jobs["site_generate"].append(str(page_id))
        return True

    def _site_export(output_dir="/tmp/leadpilot_site_export"):
        jobs["site_export"].append(str(output_dir))
        return True

    monkeypatch.setattr("app.workers.site_tasks.enqueue_generation", _site_generate)
    monkeypatch.setattr("app.workers.site_tasks.enqueue_export", _site_export)

    jobs["voice"] = []

    def _voice(product_id, posts):
        jobs["voice"].append((str(product_id), list(posts)))
        return "task-voice-" + str(product_id)

    monkeypatch.setattr("app.workers.voice_tasks.enqueue", _voice)

    jobs["replies"] = []

    def _reply(reply_id):
        jobs["replies"].append(str(reply_id))
        return True

    monkeypatch.setattr("app.workers.reply_tasks.enqueue", _reply)

    jobs["crm"] = []
    jobs["webhooks"] = []

    def _record(bucket, *item):
        jobs[bucket].append(tuple(str(i) if not isinstance(i, bool) else i for i in item))
        return True

    monkeypatch.setattr("app.workers.crm_tasks.enqueue_push",
                        lambda kind, user_id, local_id: _record("crm", "push", kind, user_id,
                                                                local_id))
    monkeypatch.setattr("app.workers.crm_tasks.enqueue_sync",
                        lambda connection_id, full=False: _record("crm", "sync", connection_id,
                                                                  full))
    monkeypatch.setattr("app.workers.crm_tasks.enqueue_import",
                        lambda user_id, remote_id: _record("crm", "import", user_id, remote_id))
    monkeypatch.setattr("app.workers.webhook_tasks.enqueue_delivery",
                        lambda delivery_id, countdown=0: _record("webhooks", delivery_id,
                                                                 countdown))

    # Autopilot launch: `advance` enqueues and its sourcing chain. Recorded
    # only when the strategy really has Autopilot on (the real helper's rule).
    jobs["autopilot"] = []
    jobs["autopilot_chains"] = []

    def _advance(session, strategy_id):
        from app.services import autopilot  # noqa: PLC0415

        try:      # never raises, like the real helper
            row = autopilot.row_for(session, strategy_id)
        except Exception:  # noqa: BLE001
            return False
        if row is None or not row.enabled:
            return False
        jobs["autopilot"].append(str(strategy_id))
        return True

    monkeypatch.setattr("app.workers.autopilot_tasks.enqueue_advance", _advance)
    monkeypatch.setattr("app.workers.autopilot_tasks.start_lead_chain",
                        lambda batch_id, strategy_id: _record("autopilot_chains", batch_id,
                                                              strategy_id))
    jobs["reslot"] = []
    monkeypatch.setattr("app.workers.autopilot_tasks.enqueue_reslot",
                        lambda strategy_id: _record("reslot", strategy_id))
    return jobs


@pytest.fixture(autouse=True)
def no_real_anthropic(monkeypatch):
    """Refuse real Anthropic calls from code paths reached WITHOUT fake_claude.

    The feature expansion added model calls to paths that pre-existing tests
    exercise without requesting fake_claude -- lead scoring at the end of
    every sourcing batch is the obvious one. Unguarded, those tests would hit
    the real API with the placeholder key (and sit through its retry
    backoff). Every such caller degrades on an exception by design (heuristic
    score, FAILED brief), so refusing is safe. `fake_claude` patches the same
    attribute afterwards and wins whenever a test asks for it.
    """
    def _refuse():
        raise RuntimeError("test attempted a real Anthropic call -- use fake_claude")

    monkeypatch.setattr("app.services.anthropic_client.get_client", _refuse)
    # 13 modules do `from app.services.anthropic_client import get_client`, so
    # patching the module attribute alone missed any of them imported before
    # this fixture ran (at collection, say): they kept the REAL function, and
    # which ones did depended on test order. In a full run
    # test_terminal_failure.py sent a real request to api.anthropic.com
    # through icp_extraction this way. Patch every such binding too -- found
    # by what the module's SOURCE imports, not by what it holds now: a module
    # first imported while a test had fake_claude installed holds that test's
    # stale fake forever, and other modules define an unrelated get_client of
    # their own (Unipile, Mailreach) that must not be touched.
    for module in list(sys.modules.values()):
        if _binds_anthropic_get_client(module):
            monkeypatch.setattr(module, "get_client", _refuse)


_ANTHROPIC_IMPORT = re.compile(
    r"from\s+app\.services\.anthropic_client\s+import\s+"
    r"(\([^)]*\bget_client\b[^)]*\)|[^\n]*\bget_client\b)")
_BINDS_CACHE: dict[str, bool] = {}


def _binds_anthropic_get_client(module) -> bool:
    """True for an app module whose top level does
    `from app.services.anthropic_client import ... get_client ...`."""
    name = getattr(module, "__name__", "") or ""
    if not name.startswith("app.") or name == "app.services.anthropic_client":
        return False
    if name not in _BINDS_CACHE:
        path = getattr(module, "__file__", None)
        try:
            source = open(path, encoding="utf-8").read() if path else ""
        except OSError:
            source = ""
        # Top-level imports only: an import inside a function body reads the
        # module attribute at call time, which the setattr above covers.
        top_level = "\n".join(line for line in source.splitlines()
                              if not line.startswith((" ", "\t")) or line.startswith(")"))
        _BINDS_CACHE[name] = bool(_ANTHROPIC_IMPORT.search(top_level)) and hasattr(
            module, "get_client")
    return _BINDS_CACHE[name]


@pytest.fixture(autouse=True)
def no_market_intel_fetch(monkeypatch):
    """Refuse the market-intelligence fetchers (Google News RSS, Apollo org
    search) unless a test installs its own.

    run_pipeline calls market_intel.ensure_signals before any step, so every
    test that runs the pipeline task used to fetch news.google.com and call
    api.apollo.io for real. ensure_signals records fetch errors and never
    raises, so refusing is safe; tests of the feature itself monkeypatch these
    two attributes afterwards and win.
    """
    def _refuse(*_a, **_k):
        raise RuntimeError("test attempted a real market-intel fetch -- monkeypatch it")

    monkeypatch.setattr("app.services.market_intel.fetch_google_news", _refuse)
    monkeypatch.setattr("app.services.market_intel._apollo_org_search", _refuse)


# --------------------------------------------------------------------------
# The suite is OFFLINE. Enforced, not assumed.
# --------------------------------------------------------------------------
#
# CLAUDE.md: "The suite is offline by design ... Keep new tests deterministic
# and offline, or they don't belong in the hard gate." Nothing enforced it, and
# because the app catches network errors by design (market intel, scoring,
# briefs degrade instead of failing), a test that reached the internet still
# PASSED -- in CI, where the internet is reachable, it really fetched. Every
# connection to a non-loopback host is now refused AND fails the test that
# made it, even when the application swallowed the error.

_LOOPBACK_PREFIXES = ("127.", "::1", "0:0:0:0:0:0:0:1", "localhost")


class ExternalNetworkBlocked(OSError):
    pass


def _is_loopback(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    host = str(host).strip("[]").lower()
    return host == "" or host.startswith(_LOOPBACK_PREFIXES)


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch, request):
    attempts: list[str] = []
    real_getaddrinfo = socket.getaddrinfo
    real_create_connection = socket.create_connection
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _guard(host, port):
        if _is_loopback(host):
            return
        attempts.append(f"{host}:{port}")
        raise ExternalNetworkBlocked(
            f"test attempted a real network connection to {host}:{port}")

    def _getaddrinfo(host, port, *a, **k):
        _guard(host, port)
        return real_getaddrinfo(host, port, *a, **k)

    def _create_connection(address, *a, **k):
        _guard(address[0], address[1])
        return real_create_connection(address, *a, **k)

    def _addr(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple):
            _guard(address[0], address[1])

    def _connect(sock, address):
        _addr(sock, address)
        return real_connect(sock, address)

    def _connect_ex(sock, address):
        _addr(sock, address)
        return real_connect_ex(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", _create_connection)
    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    yield attempts
    if attempts:
        pytest.fail(
            "the offline suite reached for the network: "
            + ", ".join(sorted(set(attempts)))
            + ". Stub the client (see no_market_intel_fetch / fake_claude).",
            pytrace=False)


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
#
# Phase A added ownership/tenancy checks to every route, so an unauthenticated
# client no longer reaches any handler - it just collects 401s. Authenticating
# is not enough either: the ownership checks compare the JWT's user against the
# row's owner, so a client authenticated as a DIFFERENT user than the one the
# object fixtures build data under gets 404 instead. Both clients and every
# object builder therefore share ONE canonical user.


@pytest.fixture(autouse=True)
def send_review_off(monkeypatch):
    """Part 1 Feature 5's review queue is OFF for every suite that does not
    test it.

    WHY. The shared `verified_leads` fixture gives every lead the title
    "Owner", which is a VIP title, so with the gate on every existing engine
    test would return "held_for_review" instead of "sent" -- and those tests
    are about the send path, not about the review queue. Rather than rewriting
    120 assertions around a feature they are not testing, the gate is disabled
    here and turned back ON explicitly by tests/test_send_review.py (its
    `review_on` fixture), which is the only module that should exercise it.

    This is also the honest default for the product: whether the VIP trigger
    helps depends on the ICP, which is exactly why it is an admin setting.
    """
    from app.services import send_review

    monkeypatch.setattr(send_review, "enabled",
                        lambda db, code=None: False)


@pytest.fixture(autouse=True)
def daily_send_approval_off(monkeypatch):
    """The daily send approval gate is OFF for every suite that does not test
    it -- the same pattern, and the same reason, as `send_review_off` above.

    WHY. The gate defaults ON in production (founder decision 2026-10-11), so
    every pre-existing send-path test -- which is about suppression, caps,
    windows, guards, not about the daily approval -- would return
    "deferred_awaiting_send_approval" instead of "sent". It is switched back
    ON by tests/test_daily_send_approval.py (its `approval_on` fixture), which
    also asserts the production DEFAULT is True, so the default cannot drift
    while this fixture hides it from other suites.
    """
    from app.services import daily_send_approval

    monkeypatch.setattr(daily_send_approval, "enabled", lambda db: False)


@pytest.fixture(autouse=True)
def launch_lawful_basis_off(monkeypatch):
    """The manual-launch lawful-basis guard is OFF for every suite that does
    not test it -- the same pattern as `daily_send_approval_off` above.

    WHY. The guard is always on in production (app/services/launch_consent.py):
    POST /sequences/{id}/enroll needs `lawful_basis_confirmed` + the current
    text version. Dozens of suites -- several of them SEALED compliance suites
    that may not be edited -- launch with `json={}` to test other gates
    (preflight, plan, review, verified-only). It is switched back ON by
    tests/test_launch_lawful_basis.py, which also asserts the production
    default is on, so the default cannot drift while this hides it.
    """
    from app.services import launch_consent

    monkeypatch.setattr(launch_consent, "enforced", lambda: False)


@pytest.fixture(autouse=True)
def best_time_scheduling_off(monkeypatch):
    """Best-time scheduling is OFF for every suite that does not test it --
    the same pattern as `daily_send_approval_off` above.

    WHY. It defaults ON in production (docs/features/autopilot-launch.md), and
    a deferral out of hours lands on the best quarter-hour INSIDE the window
    rather than exactly on its start. Measured on 2026-10-12 (the reviewer's
    besttime_on_plugin forcing it ON over the 47 test files that touch
    send_message_impl / enroll_leads / the window helpers): 1,523 passed and 5
    failed -- every one pins the exact window start (09:00, 11:00, 14:00):
    test_compliance.py::TestSendWindow::test_outside_hours_defers_to_window_start
    (sealed), two in test_compliance_rules.py and two in test_lead_timezone.py.
    Nothing else depends on it being off. The sealed scenario's production
    behaviour (09:00-09:29 the same morning, inside the window) is pinned with
    best-time ON, across zones, DST and compliance-rule windows, by
    tests/test_best_time_window_guard.py. It is also switched back ON by
    tests/test_send_time.py and tests/test_autopilot_launch.py (their
    `best_time_on` fixtures), which assert the production DEFAULT is True and
    that off = the old schedule.
    """
    from app.services import send_time

    monkeypatch.setattr(send_time, "enabled", lambda db: False)


@pytest.fixture()
def test_user(db_session):
    """The canonical established user every object fixture builds data under.

    email_verified=True is set EXPLICITLY, not inherited. This fixture stands
    for an account that has been in the product for a while, which is exactly
    the population migration 0015 backfills to verified -- and every test that
    uses it is testing something other than signup verification. Leaving it
    False would make ~45 unrelated tests fail with 403 EMAIL_NOT_VERIFIED and
    say nothing useful about the feature.

    The unverified path has its own dedicated coverage in
    tests/test_email_verification.py, which asserts the 403 against this same
    get_current_user dependency.
    """
    user = m.User(email="test@leadpilot.dev", email_verified=True)
    db_session.add(user)
    db_session.commit()
    return user


def auth_headers(user) -> dict[str, str]:
    """Real signed access token - exercises get_current_user rather than
    stubbing it out, so the auth path itself stays under test."""
    from app.services.auth import issue_tokens
    return {"Authorization": f"Bearer {issue_tokens(user.id)['access_token']}"}


# --------------------------------------------------------------------------
# Feature 2 / Task 3 — the tutorial catalogue now lives in the DATABASE
# --------------------------------------------------------------------------


def seed_catalogue(db_session, *, published=True, with_video=False):
    """Insert the nine seed tutorials into tutorial_catalogue.

    Migration 0018 does this in a real database; the unit-test harness builds
    its schema with Base.metadata.create_all, which creates tables but runs no
    data migration. Without this the catalogue is empty and every tutorial test
    asserts against nothing -- which would PASS a lot of them vacuously.

    published defaults True so tests about progress, badges and search do not
    each have to set it up. The publish workflow itself has its own tests that
    pass published=False.
    """
    from app.db.models import TutorialCatalogue
    from app.services.tutorials import SEED_CATALOGUE

    rows = []
    for entry in SEED_CATALOGUE:
        row = TutorialCatalogue(
            slug=entry.slug,
            title=entry.title,
            description=entry.description,
            level=entry.level,
            youtube_id=("vid" + entry.slug[:8]) if with_video else None,
            duration_seconds=entry.duration_seconds,
            sort_order=entry.order,
            is_published=published,
        )
        db_session.add(row)
        rows.append(row)
    db_session.commit()
    return rows


@pytest.fixture()
def catalogue(db_session):
    """The nine seed tutorials, published, with no video ids (as seeded)."""
    return seed_catalogue(db_session)


# --------------------------------------------------------------------------
# Feature 1 — email verification helpers
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mailbox():
    """The in-process inbox, emptied around every test.

    autouse because SENT_MESSAGES is module-level state: without a reset a test
    that asserts "exactly one email was sent" passes alone and fails in a full
    run, which is the worst kind of flake to chase.
    """
    from app.services.email_sender import SENT_MESSAGES, reset_sent_messages

    reset_sent_messages()
    yield SENT_MESSAGES
    reset_sent_messages()


@pytest.fixture(autouse=True)
def sms_outbox():
    """The in-process SMS transport, emptied around every test (same reason as
    `mailbox`: module-level state leaks between tests otherwise)."""
    from app.integrations.sms import SENT_SMS, reset_sent_sms

    reset_sent_sms()
    yield SENT_SMS
    reset_sent_sms()


def verification_link(mailbox_messages) -> str:
    """Pull the verification URL out of the most recent email.

    Reads the rendered TEXT BODY rather than reaching into the database,
    because what actually has to work is the link a human receives. A test that
    fabricated its own URL from a token row would still pass if the email
    template shipped a broken link.
    """
    import re

    assert mailbox_messages, "no email was sent"
    body = mailbox_messages[-1]["text"]
    match = re.search(r"https?://\S+/auth/verify\?token=\S+", body)
    assert match, "no verification link in email body:\n" + body
    return match.group(0)


def complete_verification(client, mailbox_messages) -> int:
    """Click the emailed link. Returns the redirect status code.

    follow_redirects=False on purpose: the endpoint 302s to FRONTEND_URL, which
    is a different origin with no ASGI app behind it. Following it inside
    TestClient would leave the test asserting against a 404 from the wrong
    server.
    """
    from urllib.parse import urlparse

    link = verification_link(mailbox_messages)
    path_and_query = urlparse(link).path + "?" + urlparse(link).query
    resp = client.get(path_and_query, follow_redirects=False)
    return resp.status_code


@pytest.fixture()
def user_tokens(test_user):
    """{"access_token", "refresh_token", "user_id"} for the canonical user.

    tests/test_devices.py passes these headers explicitly per request rather
    than relying on the client's default header.
    """
    from app.services.auth import issue_tokens
    tokens = issue_tokens(test_user.id)
    return {**tokens, "user_id": test_user.id}


@pytest.fixture()
def anon_client(client):
    """`client` with the Authorization header stripped.

    For the handful of tests that assert an endpoint REJECTS unauthenticated
    requests - they need a client that genuinely sends no token, which the
    authenticated `client` no longer is.
    """
    client.headers.pop("Authorization", None)
    return client


@pytest.fixture()
def client(db_session, test_user, fake_claude, enqueued, monkeypatch):
    from app.main import app
    from app.workers import tasks as worker_tasks

    monkeypatch.setattr(
        worker_tasks.run_pipeline, "delay",
        lambda strategy_id: enqueued.append(strategy_id),
        raising=False,
    )
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(app) as c:
            c.headers.update(auth_headers(test_user))
            yield c
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Common object builders
# --------------------------------------------------------------------------


@pytest.fixture()
def product_with_strategy(db_session, test_user):
    def _make(flow_type: m.FlowType, with_past_client: bool = True):
        product = m.Product(
            user=test_user, name="SEO System X",
            description="Local SEO audits for fire protection companies",
            type=m.ProductType.SKILL,
        )
        db_session.add(product)
        db_session.flush()
        if with_past_client:
            db_session.add(m.PastClient(
                product_id=product.id,
                details="Texas fire ITM company, 30 staff",
                acquisition_story="Referral after a failed audit",
                extracted_patterns_json={"industry": "fire protection",
                                         "acquisition_channel": "referral"},
            ))
        strategy = m.Strategy(product_id=product.id, flow_type=flow_type)
        db_session.add(strategy)
        db_session.commit()
        return product, strategy
    return _make


# --------------------------------------------------------------------------
# M2 fixtures — fake adapters, fake redis, verified strategy, lead batch
# --------------------------------------------------------------------------

import fakeredis

from app.integrations.base import (
    EmailVerificationStatus,
    EmailVerifier,
    EnrichedLead,
    LeadSource,
    RawLead,
    VerificationResult,
)


@pytest.fixture()
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def isolated_rate_limiter(monkeypatch):
    """Give every root test its own empty rate-limit store.

    Two problems this solves, both surfaced the moment RATE_LIMIT_AUTH was
    wired to /auth/signup and /auth/login on 2026-08-20:

    1. SHARED STATE. TestClient reports a client host of "testclient" for every
       request in the process, so all tests share one `rate:ip:testclient:*`
       bucket. Dozens of root tests sign up a user as setup; past the 10th, the
       rest got 429 instead of 201. That is a test-ordering landmine, not a
       product bug — verified as exactly this failure in
       test_m5_backend.py::TestTheme::test_background_upload_serves_url.

    2. A HIDDEN EXTERNAL DEPENDENCY. get_sync_redis() connects to the REAL
       Redis, so the root suite — documented at the top of this file as the
       in-process SQLite library, and deliberately needing neither PostgreSQL
       nor Redis (see tests/integration/conftest.py) — had quietly acquired a
       live Redis requirement via the limiter.

    Autouse so it cannot be forgotten. Tests that want to assert on limiter
    behaviour just patch get_sync_redis again with their own instance; the
    later patch wins.
    """
    store = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client.get_sync_redis", lambda: store)
    return store


class FakeLeadSource(LeadSource):
    """Deterministic in-memory LeadSource. `enrich_fail_after=N` makes the
    (N+1)th enrich call raise, to simulate a mid-batch crash."""

    provider = "fakesource"

    def __init__(self, feed: list[RawLead] | None = None):
        self.feed = feed if feed is not None else []
        self.search_calls = 0
        self.enrich_calls = 0
        self.enrich_fail_after: int | None = None

    def search(self, icp_criteria: dict, max_leads: int) -> list[RawLead]:
        self.search_calls += 1
        return self.feed[:max_leads]

    def enrich(self, lead: RawLead) -> EnrichedLead:
        if self.enrich_fail_after is not None and self.enrich_calls >= self.enrich_fail_after:
            raise RuntimeError("simulated enrichment crash")
        self.enrich_calls += 1
        domain = lead.company_domain or f"{(lead.company or 'acme').lower().replace(' ', '')}.com"
        enriched = EnrichedLead.from_raw(lead, enrichment={"provider": "fakesource"})
        enriched.company_domain = domain
        return enriched

    def health_check(self) -> bool:
        return True


class FakeVerifier(EmailVerifier):
    """find_email derives an address from name+domain; verify is scripted
    per email via `verdicts`, defaulting to deliverable."""

    provider = "fakeverifier"

    def __init__(self):
        self.verdicts: dict[str, EmailVerificationStatus] = {}
        self.findable: dict[tuple[str, str], str | None] = {}
        self.find_calls = 0
        self.verify_calls = 0

    def find_email(self, full_name: str, domain: str) -> str | None:
        self.find_calls += 1
        if (full_name, domain) in self.findable:
            return self.findable[(full_name, domain)]
        if not full_name or not domain:
            return None
        slug = full_name.lower().replace(" ", ".")
        return f"{slug}@{domain}"

    def verify(self, email: str) -> VerificationResult:
        self.verify_calls += 1
        status = self.verdicts.get(email, EmailVerificationStatus.DELIVERABLE)
        return VerificationResult(email=email, status=status, score=90, raw={"result": status.value})

    def health_check(self) -> bool:
        return True


@pytest.fixture()
def fake_source():
    return FakeLeadSource()


@pytest.fixture()
def fake_verifier():
    return FakeVerifier()


def make_raw(i: int, email: str | None = None, phone: str | None = None,
             company: str = None, domain: str | None = None) -> RawLead:
    return RawLead(
        source="fakesource",
        external_id=f"p{i}",
        full_name=f"Person {i}",
        first_name="Person",
        last_name=str(i),
        title="Owner",
        company=company or f"Acme {i}",
        company_domain=domain,
        email=email,
        phone=phone,
        raw={"id": f"p{i}"},
    )


@pytest.fixture()
def verified_strategy(db_session, product_with_strategy):
    _, strategy = product_with_strategy(m.FlowType.WITH_CLIENTS)
    strategy.status = m.StrategyStatus.VERIFIED
    strategy.strategy_document = "# Strategy\nverified"
    db_session.commit()
    return strategy


@pytest.fixture()
def lead_batch(db_session, verified_strategy):
    batch = m.LeadBatch(
        strategy_id=verified_strategy.id,
        requested_leads=50,
        icp_criteria_json={"titles": ["Owner"]},
        source_provider="fakesource",
        verifier_provider="fakeverifier",
    )
    db_session.add(batch)
    db_session.commit()
    return batch


@pytest.fixture()
def chain_calls():
    return []


@pytest.fixture()
def leads_client(db_session, test_user, fake_claude, chain_calls, monkeypatch):
    """API client with the Celery lead chain stubbed and recorded."""
    from app.main import app
    from app.workers import lead_tasks

    monkeypatch.setattr(lead_tasks, "start_lead_chain",
                        lambda batch_id: chain_calls.append(batch_id))
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(app) as c:
            c.headers.update(auth_headers(test_user))
            yield c
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# M3 fixtures — fake outreach channel, sequences, connected gmail account
# --------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone

from app.integrations.outreach_base import (
    InboundMessage,
    OutboundMessage,
    OutreachChannel,
    SendResult,
)

# A weekday inside the 9-17 UTC send window, used everywhere for determinism.
NOW = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)  # Tuesday 10:00 UTC


class FakeChannel(OutreachChannel):
    """Records every send; results are scriptable via result_queue."""

    channel = "email"
    provider = "fakechannel"

    def __init__(self):
        self.sent: list[OutboundMessage] = []
        self.result_queue: list[SendResult] = []
        self.inbox: list[InboundMessage] = []

    def send(self, message: OutboundMessage) -> SendResult:
        self.sent.append(message)
        if self.result_queue:
            return self.result_queue.pop(0)
        n = len(self.sent)
        return SendResult(ok=True, provider_message_id=f"pm{n}", thread_ref=f"th{n}")

    def fetch_replies(self, since=None):
        return list(self.inbox)

    def status(self, provider_message_id):
        return {}

    def health_check(self):
        return True


@pytest.fixture()
def fake_channel():
    return FakeChannel()


@pytest.fixture()
def gmail_account(db_session, verified_strategy):
    from app.services import crypto
    product = db_session.get(m.Product, verified_strategy.product_id)
    account = m.GmailAccount(
        user_id=product.user_id,
        email_address="sender@leadpilot.dev",
        token_ciphertext=crypto.encrypt_json({"access_token": "at", "refresh_token": "rt"}),
        token_expires_at=NOW + timedelta(hours=1),
        scopes="send read",
    )
    # created_at drives the warm-up ramp; backdate far enough that the
    # ramp reaches the full daily cap unless a test overrides it.
    db_session.add(account)
    db_session.commit()
    account.created_at = NOW - timedelta(days=365)
    db_session.commit()
    return account


@pytest.fixture()
def verified_leads(db_session, verified_strategy):
    leads = []
    for i in range(3):
        lead = m.Lead(
            strategy_id=verified_strategy.id, source="apollo", external_id=f"L{i}",
            full_name=f"Lead {i}", title="Owner", company=f"Co {i}",
            email=f"lead{i}@co{i}.com", status=m.LeadStatus.VERIFIED,
            enrichment_json={"company_domain": f"co{i}.com"},
        )
        db_session.add(lead)
        leads.append(lead)
    db_session.commit()
    return leads


@pytest.fixture()
def email_sequence(db_session, verified_strategy):
    seq = m.Sequence(strategy_id=verified_strategy.id, channel=m.ChannelType.EMAIL,
                     name="Intro sequence", status=m.SequenceStatus.ACTIVE,
                     booking_url="https://calendly.com/founder/intro")
    db_session.add(seq)
    db_session.flush()
    for step_no, delay in [(1, 0), (2, 3), (3, 4)]:
        db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=step_no,
                                      template=f"Step {step_no} brief", delay_days=delay))
    db_session.commit()
    return seq


@pytest.fixture()
def enrolled(db_session, fake_claude, email_sequence, verified_leads, gmail_account):
    """Sequence with all verified leads enrolled at NOW (step-1 scheduled)."""
    from app.services.sequence_engine import enroll_leads
    count = enroll_leads(db_session, email_sequence, now=NOW)
    assert count == 3
    return email_sequence


# --------------------------------------------------------------------------
# M4 fixtures — WhatsApp templates, opt-ins, multi-channel sequences,
# fake Graph API HTTP, webhook signing. ZERO real Meta/Anthropic calls.
# --------------------------------------------------------------------------

import hashlib
import hmac
import json as _json


@pytest.fixture()
def approved_template(db_session):
    """An APPROVED template with two variables (created as draft, then
    approved the way Meta would — via apply_meta_status)."""
    from app.services import whatsapp_templates as tmpl_svc
    t = tmpl_svc.create_draft(
        db_session,
        name="intro_v1", language="en_US",
        category=m.WhatsAppTemplateCategory.MARKETING,
        body="Hi {{1}}, Sam from Victorae about {{2}}. Reply STOP to opt out.",
        variable_descriptions={"1": "first name", "2": "pain point"},
    )
    t.status = m.WhatsAppTemplateStatus.SUBMITTED
    db_session.commit()
    tmpl_svc.apply_meta_status(db_session, t, "APPROVED", meta_template_id="meta-1")
    return t


@pytest.fixture()
def wa_lead(db_session, verified_strategy):
    """A verified lead with phone + email and a REAL recorded opt-in."""
    from app.services import whatsapp_optin as optin_svc
    lead = m.Lead(
        strategy_id=verified_strategy.id, source="apollo", external_id="WA1",
        full_name="Sara Khan", title="CTO", company="Acme",
        email="sara@acme.com", phone="+923001234567",
        status=m.LeadStatus.VERIFIED,
        enrichment_json={"company_domain": "acme.com"},
    )
    db_session.add(lead)
    db_session.commit()
    optin_svc.record_opt_in(db_session, lead, source=m.OptInSource.API,
                            evidence="test fixture consent")
    return lead


@pytest.fixture()
def wa_lead_no_optin(db_session, verified_strategy):
    lead = m.Lead(
        strategy_id=verified_strategy.id, source="apollo", external_id="WA2",
        full_name="Ali Raza", title="COO", company="Beta",
        email="ali@beta.com", phone="+923009998877",
        status=m.LeadStatus.VERIFIED,
        enrichment_json={"company_domain": "beta.com"},
    )
    db_session.add(lead)
    db_session.commit()
    return lead


def entitle_owner(db_session, strategy, plan) -> None:
    """Put the account that owns `strategy` on `plan`.

    Since send-time plan entitlement (app/services/plan_holds.py), a message on
    a channel the owner's plan lacks is held instead of sent -- exactly what a
    downgrade must do. Tests of a channel's send path (WhatsApp, LinkedIn,
    phone) put the owner on a plan that includes it, the same way they would
    have launched it; tests ABOUT plans use the default Free plan.
    """
    product = db_session.get(m.Product, strategy.product_id)
    owner = db_session.get(m.User, product.user_id)
    owner.plan = plan
    db_session.commit()


@pytest.fixture()
def multichannel_sequence(db_session, verified_strategy, approved_template):
    """email step 1 -> WhatsApp template step 2 -> email step 3.

    The owner is on Starter, the cheapest plan with WhatsApp: a sequence with a
    WhatsApp step can only be launched on such a plan (app/api/sequences.py
    _enforce_plan), and the send path re-checks it (app/services/plan_holds.py).
    """
    entitle_owner(db_session, verified_strategy, m.PlanTier.STARTER)
    seq = m.Sequence(strategy_id=verified_strategy.id, channel=m.ChannelType.EMAIL,
                     name="Multi", status=m.SequenceStatus.ACTIVE)
    db_session.add(seq)
    db_session.flush()
    db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=1,
                                  template="email intro", delay_days=0))
    db_session.add(m.SequenceStep(
        sequence_id=seq.id, step_no=2, template="wa follow", delay_days=0,
        channel=m.ChannelType.WHATSAPP,
        whatsapp_kind=m.WhatsAppStepKind.TEMPLATE,
        whatsapp_template_id=approved_template.id,
        variable_mapping_json={"1": "lead.first_name", "2": "brief: their pain"},
    ))
    db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=3,
                                  template="email close", delay_days=0))
    db_session.commit()
    return seq


class FakeGraphHTTP:
    """Scriptable stand-in for httpx.Client hitting the Meta Graph API."""

    class _Resp:
        def __init__(self, status_code: int, data: dict):
            self.status_code = status_code
            self.headers: dict = {}
            self._data = data
            self.text = _json.dumps(data)

        def json(self):
            return self._data

    def __init__(self):
        self.requests: list[tuple[str, str, dict | None]] = []
        self.queue: list[tuple[int, dict]] = []

    def request(self, method, endpoint, params=None, json=None, headers=None):
        self.requests.append((method, endpoint, json))
        if self.queue:
            status, data = self.queue.pop(0)
            return self._Resp(status, data)
        return self._Resp(200, {"messages": [{"id": f"wamid.{len(self.requests)}"}]})


@pytest.fixture()
def fake_graph_http():
    return FakeGraphHTTP()


@pytest.fixture()
def wa_channel(db_session, fake_redis, fake_graph_http):
    from app.integrations.whatsapp import WhatsAppChannel
    return WhatsAppChannel(session=db_session, redis=fake_redis,
                           http=fake_graph_http)


def wa_signed_post(client, payload: dict, secret: str = "test-app-secret"):
    """POST a Meta-style signed delivery to /webhooks/whatsapp."""
    raw = _json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/webhooks/whatsapp", content=raw,
                       headers={"X-Hub-Signature-256": sig,
                                "Content-Type": "application/json"})


def wa_outbound(lead, *, kind="template", template="intro_v1",
                language="en_US", body="hello", message_id=None):
    from app.integrations.outreach_base import OutboundMessage
    import uuid as _uuid
    metadata = {"kind": kind}
    if kind == "template":
        metadata.update(template_name=template, language=language)
    return OutboundMessage(
        message_id=str(message_id or _uuid.uuid4()),
        lead_id=lead.id, to_address=lead.phone or "", body=body,
        metadata=metadata,
    )


@pytest.fixture(autouse=True)
def fresh_health_db_check(monkeypatch):
    """No test sees another test's cached database check.

    app/services/health_checks.check_database reuses a success for
    HEALTH_DB_CHECK_TTL_SECONDS (in-process). Without this reset, a test that
    makes the database fail would read the "ok" an earlier test cached.
    """
    from app.services import health_checks

    monkeypatch.setattr(health_checks, "_db_ok_at", None)
