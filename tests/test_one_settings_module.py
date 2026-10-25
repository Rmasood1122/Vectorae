"""F-OPS-03: one Settings class.

app/core/config.py held a second Settings class reading the same
environment, and the two drifted (different DATABASE_URL and Celery
defaults, two ENCRYPTION_KEY rules). Its fields moved, unchanged, into
app/config.py; the names it shared with that class are read-only aliases.

What must hold:
  * both import paths give the same class and the same settings object;
  * every name the old module defined still reads a value;
  * no environment variable is read by two fields, so two values for one
    variable cannot drift apart again.
"""

from __future__ import annotations

from collections import Counter

# The 67 names app/core/config.py defined on master before the merge.
FORMER_CORE_NAMES = [
    "APP_ENV", "SECRET_KEY", "ACCESS_TOKEN_EXPIRE_MINUTES", "REFRESH_TOKEN_EXPIRE_DAYS",
    "LOG_LEVEL", "ADMIN_EMAIL", "DATABASE_URL", "DB_POOL_SIZE", "DB_MAX_OVERFLOW",
    "REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "CELERY_ALWAYS_EAGER",
    "CELERY_EAGER_PROPAGATES", "CELERY_CONCURRENCY", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
    "ANTHROPIC_MAX_TOKENS", "APOLLO_API_KEY", "HUNTER_API_KEY", "GMAIL_CLIENT_ID",
    "GMAIL_CLIENT_SECRET", "GMAIL_REDIRECT_URI", "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_WABA_ID", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_VERIFY_TOKEN",
    "WHATSAPP_APP_SECRET", "CALENDLY_CLIENT_ID", "CALENDLY_CLIENT_SECRET",
    "CALENDLY_WEBHOOK_SECRET", "FIREBASE_PROJECT_ID", "FIREBASE_CREDENTIALS_JSON",
    "ENCRYPTION_KEY", "ENCRYPTION_KEY_PREVIOUS", "CIRCUIT_BREAKER_FAILURE_THRESHOLD",
    "CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "PIPELINE_STEP_TIMEOUT_MINUTES",
    "PLAYBOOK_MIN_SAMPLE", "AB_MIN_LIFT", "AB_SIGNIFICANCE_THRESHOLD", "AB_HARM_CEILING",
    "PLAYBOOK_AGGREGATION_UTC_HOUR", "PLAYBOOK_SCORE_HALF_LIFE_DAYS", "MV_MAX_VARIANTS",
    "RATE_LIMIT_STRATEGIES", "RATE_LIMIT_LEADS_SOURCE", "RATE_LIMIT_PLAYBOOK_RECOMPUTE",
    "RATE_LIMIT_WA_TEMPLATES", "RATE_LIMIT_PUBLIC_BOOKING", "RATE_LIMIT_CALENDAR_WRITE",
    "RATE_LIMIT_PUBLIC_VIDEO", "RATE_LIMIT_OPEN_PIXEL", "RATE_LIMIT_AI_ACTION",
    "RATE_LIMIT_AGENT_RUN", "RATE_LIMIT_CRM_WRITE", "RATE_LIMIT_GET", "RATE_LIMIT_OTP_SEND",
    "RATE_LIMIT_OTP_VERIFY", "RATE_LIMIT_PUBLIC_SHARE", "RATE_LIMIT_PUBLIC_STATUS",
    "RATE_LIMIT_AUTH", "RATE_LIMIT_OAUTH", "RATE_LIMIT_SUPPORT_CHAT", "FRONTEND_URL",
    "CORS_ORIGINS", "ANDROID_PACKAGE_NAME",
]


def _env_names(field_name, field) -> list[str]:
    alias = field.validation_alias or field.alias
    if alias is None:
        return [field_name.upper()]
    if isinstance(alias, str):
        return [alias.upper()]
    return [c.upper() for c in alias.choices if isinstance(c, str)]


def test_both_paths_are_one_class_and_one_object():
    import app.config as app_config
    import app.core.config as core_config

    assert core_config.Settings is app_config.Settings
    assert core_config.settings is app_config.settings
    assert core_config.get_settings is app_config.get_settings


def test_every_former_core_name_still_reads_a_value():
    from app.config import settings

    assert len(FORMER_CORE_NAMES) == 67
    missing = [n for n in FORMER_CORE_NAMES if not hasattr(settings, n)]
    assert missing == []


def test_an_alias_reads_the_same_value_as_its_field():
    from app.config import _CORE_ALIASES, settings

    for alias, field in _CORE_ALIASES.items():
        assert getattr(settings, alias) == getattr(settings, field), alias


def test_no_environment_variable_is_read_by_two_fields():
    from app.config import Settings

    counts = Counter(env for name, field in Settings.model_fields.items()
                     for env in _env_names(name, field))
    assert sorted(env for env, n in counts.items() if n > 1) == []
