"""The privacy texts say what the code does (founder decision D1).

Each false claim removed on 2026-10-11 (docs/features/gdpr-erasure.md,
"Problem" item 4) is pinned here so it cannot come back, and each number the
texts state is read from the code that enforces it.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services import account_erasure
from app.services import auth as auth_svc

ROOT = Path(__file__).resolve().parents[1]
POLICY = (ROOT / "docs" / "PRIVACY_POLICY.md").read_text(encoding="utf-8")
PAGE = (ROOT / "frontend" / "src" / "app" / "privacy" / "page.tsx").read_text(encoding="utf-8")
PROFILE_COPY = (ROOT / "frontend" / "src" / "lib" / "profile.ts").read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


class TestPolicyMarkdown:
    def test_no_bcrypt_the_hash_is_pbkdf2_with_the_real_iteration_count(self):
        assert "bcrypt" not in POLICY.lower()
        assert "PBKDF2-HMAC-SHA256" in POLICY
        assert f"{auth_svc._PBKDF2_ITERATIONS:,}" in POLICY

    def test_the_export_is_on_profile_not_settings(self):
        assert "Settings → Export" not in POLICY
        assert "**Profile**" in POLICY and "Download your data" in POLICY

    def test_message_content_is_said_to_be_stored(self):
        assert "do not store the content" not in POLICY.lower()
        assert "stores the subject and body of every message" in _flat(POLICY)

    def test_no_invented_retention_jobs(self):
        flat = _flat(POLICY)
        assert "90 days" not in flat
        assert "within 30 days of account deletion" not in flat
        assert "30 days after their last message" in flat   # the one job that exists

    def test_account_deletion_is_described_as_built(self):
        flat = _flat(POLICY)
        assert "**Delete account**" in flat and "type DELETE" in flat
        assert f"{account_erasure.BILLING_RETENTION_YEARS} years" in flat

    def test_the_contact_is_not_an_unfilled_placeholder(self):
        assert "[your contact email]" not in POLICY.lower()
        assert "ahmadshahid@calendarharvest.com" in POLICY


class TestWebsitePage:
    def test_no_analytics_claim(self):
        flat = _flat(PAGE)
        assert "provides analytics" not in flat
        assert "We do not use Google Analytics" in flat

    def test_account_deletion_is_self_serve_and_says_what_is_kept(self):
        flat = _flat(PAGE)
        assert "To ask us to delete your account or any other data, contact us" not in flat
        assert "delete your whole account yourself" in flat
        assert f"for {account_erasure.BILLING_RETENTION_YEARS} years" in flat
        assert "do-not-contact list" in flat


def test_the_in_app_copy_states_the_same_retention():
    assert f"BILLING_RETENTION_YEARS = {account_erasure.BILLING_RETENTION_YEARS}" in PROFILE_COPY


# Review fix 5: every adapter that sends data out is named in the policy. A new
# module in app/integrations/ fails here until it is added to this map (and the
# policy) or to NOT_A_PROCESSOR with the reason.
PROCESSOR_NAMES = {
    "aegisaudit": ["AegisAudit"], "apollo": ["Apollo"], "calendly": ["Calendly"],
    "geolocation": ["ipapi.co", "ipinfo.io"], "gmail": ["Gmail API"],
    "google_meet": ["Calendar / Meet"], "google_postmaster": ["Postmaster Tools"],
    "hubspot": ["HubSpot"], "hunter": ["Hunter"],
    "ip_intelligence": ["IPQualityScore", "proxycheck.io"],
    "mailreach": ["Mailreach"], "newsapi": ["NewsAPI"],
    "openai_client": ["OpenAI"], "phone_channel": ["Vapi"], "postiq": ["PostIQ"],
    "recall": ["Recall.ai"], "salesforce": ["Salesforce"], "signalforge": ["SIGNALFORGE"],
    "slack": ["Slack"], "sms": ["Twilio"], "stripe_billing": ["Stripe"],
    "voice_providers": ["Vapi", "ElevenLabs"],
    "whatsapp": ["WhatsApp"], "zoom": ["Zoom"],
}
# Gate change 2026-11-02 (docs/program/plans/gate-linkedin-unipile-removal.md):
# Unipile (unipile_linkedin, linkedin_channel) and RapidAPI (linkedin_posts)
# were removed. linkedin_channel now only queues a task for the user and sends
# nothing out. The policy still names both removed providers until the
# founder updates it (legal text): over-disclosure, not a gap.
NOT_A_PROCESSOR = {"__init__": "package", "base": "interfaces", "outreach_base": "interfaces",
                   "linkedin_channel": "queues an L0 task for the user; sends nothing out",
                   "plumbing": "HTTP/cache/breaker helpers", "token_store": "local DB storage"}


def test_every_integration_module_is_a_named_processor_or_excluded():
    modules = {p.stem for p in (ROOT / "app" / "integrations").glob("*.py")}
    assert modules - set(PROCESSOR_NAMES) - set(NOT_A_PROCESSOR) == set()


def test_every_processor_is_named_in_the_policy():
    for module, names in PROCESSOR_NAMES.items():
        for name in names:
            assert name in POLICY, f"{module}: {name} missing from PRIVACY_POLICY.md §5"


def test_the_cache_retention_stated_is_the_configured_one():
    from app.config import settings  # noqa: PLC0415

    days = settings.cache_ttl_enrichment_seconds // 86400
    assert f"up to {days} days" in _flat(POLICY)
    assert f"up to {days} days" in _flat(PAGE)
    assert settings.cache_ttl_search_seconds == 86400   # "search results for 1 day"


def test_the_kept_consent_ledger_is_described_with_its_opt_ins():
    flat = _flat(POLICY)
    assert "opt-ins (with a short note on how consent was given)" in flat
    assert "Stripe keeps its own customer record" in flat
