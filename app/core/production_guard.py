"""Refuse to boot a production process that is misconfigured in a way that
fails *quietly* rather than loudly.

Every check here exists because the failure it prevents does NOT look like a
crash — it looks like the app working, until it doesn't:

* **SECRET_KEY unset** — `app/services/auth.py::_secret()` falls back to a
  RANDOM PER-PROCESS secret. One process is fine. Production runs uvicorn with
  `--workers 2` behind three Celery containers, so every process signs with a
  different key: a token minted by worker 1 is rejected by worker 2 and users
  see intermittent, unreproducible 401s. Nothing logs an error.
* **ENCRYPTION_KEY unset** — raises, but only at the FIRST OAuth token
  encrypt/decrypt, which can be days after deploy and far from the cause.
* **CORS_ORIGINS unset** — `app/main.py` defaults it to
  `http://localhost:3000`, so a deployed API silently allows only localhost and
  the real frontend is CORS-blocked in the browser with nothing wrong
  server-side.
* **PUBLIC_BASE_URL unset** — unsubscribe links, WhatsApp opt-in links and
  media URLs are built from it and default to `http://localhost:8000`. Real
  email would ship with an unsubscribe link no recipient can use (CAN-SPAM).
* **SENDER_IDENTITY unset** — the CAN-SPAM footer address defaults to the
  literal placeholder "123 Main St, City, Country".

All problems are collected and reported together — fixing one env var,
redeploying, and discovering the next one is a slow way to learn about three.

Deliberately scoped to production: `app_env` in {production, prod, live}.
Development and test are unaffected, which is why the whole suite still runs
without any of these set.
"""

from __future__ import annotations

import os

_PRODUCTION_ENVS = {"production", "prod", "live"}

# Values that are obviously not real secrets. Substring match, case-insensitive.
_PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "dev-secret",
    "test-secret",
    "placeholder",
    "your-secret",
    "insecure",
    "xxxxx",
)

_MIN_SECRET_LENGTH = 32


class ProductionConfigError(RuntimeError):
    """Raised at startup when production configuration is unsafe."""


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _check_secret_key(problems: list[str]) -> None:
    value = os.getenv("SECRET_KEY", "").strip()
    if not value:
        problems.append(
            "SECRET_KEY is not set. Each process would generate its own random "
            "JWT signing key, so tokens issued by one worker are rejected by "
            "the next (intermittent 401s). Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
        return
    if _is_placeholder(value):
        problems.append(
            "SECRET_KEY looks like a placeholder value, not a generated secret."
        )
    elif len(value) < _MIN_SECRET_LENGTH:
        problems.append(
            f"SECRET_KEY is only {len(value)} characters; use at least "
            f"{_MIN_SECRET_LENGTH}."
        )


def _check_encryption_key(problems: list[str]) -> None:
    value = os.getenv("ENCRYPTION_KEY", "").strip()
    if not value:
        problems.append(
            "ENCRYPTION_KEY is not set. Stored OAuth tokens cannot be "
            "encrypted or decrypted, and this surfaces only on the first "
            "Gmail/Calendly/WhatsApp token access. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
        return
    # Validate it is a usable Fernet key now, not on first use.
    try:
        from cryptography.fernet import Fernet

        Fernet(value.encode())
    except Exception as exc:  # noqa: BLE001 - any failure means unusable key
        problems.append(f"ENCRYPTION_KEY is not a valid Fernet key: {exc}")


def _check_cors_origins(problems: list[str]) -> None:
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if not raw:
        problems.append(
            "CORS_ORIGINS is not set. app/main.py would fall back to "
            "'http://localhost:3000', so the deployed frontend is CORS-blocked "
            "in the browser while the API itself looks healthy. Set it to the "
            "real frontend origin(s), comma-separated."
        )
        return
    # app/main.py parses this by splitting on commas. A JSON array — which is
    # what .env.production.example used to document — splits into fragments
    # like '["https://app.example.com"' that can never match a browser Origin
    # header. The API still returns 200 to curl and every healthcheck passes;
    # only the browser sees the failure, as a CORS error with no server-side
    # trace. Verified live: the JSON form yields no access-control-allow-origin
    # header at all, the comma-separated form yields the correct one.
    if any(ch in raw for ch in "[]\"'"):
        problems.append(
            "CORS_ORIGINS must be a plain comma-separated list, not a JSON "
            "array. app/main.py splits this value on commas, so brackets and "
            "quotes become part of the origin string and never match a real "
            "browser Origin — the API looks healthy while every cross-origin "
            "request is blocked. Use: https://app.example.com,https://www.app.example.com"
        )
        return

    origins = [o.strip() for o in raw.split(",") if o.strip()]
    local = [o for o in origins if "localhost" in o or "127.0.0.1" in o]
    if local:
        problems.append(
            f"CORS_ORIGINS contains local development origin(s) {local} in a "
            "production environment. Remove them."
        )
    insecure = [
        o for o in origins
        if o.startswith("http://") and "localhost" not in o and "127.0.0.1" not in o
    ]
    if insecure:
        problems.append(
            f"CORS_ORIGINS uses plaintext http:// for {insecure}; production "
            "origins should be https://."
        )


def _check_public_base_url(problems: list[str]) -> None:
    """PUBLIC_BASE_URL is what unsubscribe links are built from.

    `app/config.py` defaults it to http://localhost:8000 and it was absent from
    .env.production.example entirely, so a production deploy would have sent
    real marketing email whose unsubscribe link
    (app/services/sequence_engine.py) pointed at localhost. That is a CAN-SPAM
    violation — no recipient can act on it — and dead unsubscribe links are a
    fast route to a spam-folder reputation. The same value builds WhatsApp
    opt-in links and media URLs.
    """
    value = os.getenv("PUBLIC_BASE_URL", "").strip()
    if not value:
        problems.append(
            "PUBLIC_BASE_URL is not set. It defaults to http://localhost:8000, "
            "which is what unsubscribe links, WhatsApp opt-in links and media "
            "URLs are built from — every one of them would be unreachable for "
            "recipients. Set it to the public API origin."
        )
        return
    if "localhost" in value or "127.0.0.1" in value:
        problems.append(
            f"PUBLIC_BASE_URL is {value!r}. Unsubscribe links built from it "
            "would be unreachable for recipients (CAN-SPAM)."
        )
    elif value.startswith("http://"):
        problems.append(
            f"PUBLIC_BASE_URL is {value!r}; use https:// for links that appear "
            "in outbound email."
        )


def _check_redis_url(problems: list[str]) -> None:
    """REDIS_URL must not silently fall back to localhost.

    This one is sneaky because NOTHING ELSE BREAKS. Celery reads
    CELERY_BROKER_URL / CELERY_RESULT_BACKEND, so an unset REDIS_URL still
    leaves the broker, beat and every worker perfectly healthy. Only the things
    that go through app/core/redis_client.py suffer: the beat heartbeat, the
    response cache, the circuit breakers and the auth rate limiter.

    And it cannot fail loudly on its own, because settings.redis_url has a
    NON-EMPTY default of redis://localhost:6379/0 — so `if url:` in
    _redis_url() is satisfied and the os.getenv fallback never runs. The
    process just connects to a Redis that does not exist inside the container.

    Observed live on 2026-08-20: the Render worker's beat scheduled the
    heartbeat every 60s, every write failed against localhost, and the API's
    /health reported "no heartbeat key found" for hours with nothing pointing
    at the cause.

    Note this guard runs on the API only (app/main.py). The worker does not
    import it — app/worker_health.py carries the equivalent check instead.
    """
    value = os.getenv("REDIS_URL", "").strip()
    if not value:
        problems.append(
            "REDIS_URL is not set. It defaults to redis://localhost:6379/0, and "
            "because that default is non-empty the app connects to it silently "
            "rather than failing. Celery keeps working (it reads "
            "CELERY_BROKER_URL), so the only symptoms are a missing beat "
            "heartbeat, a dead response cache and an inert rate limiter."
        )
        return
    if "localhost" in value or "127.0.0.1" in value or "[::1]" in value:
        problems.append(
            "REDIS_URL points at a loopback address, which is unreachable from "
            "a deployed container. The beat heartbeat, response cache, circuit "
            "breakers and auth rate limiter all depend on it."
        )


_PLACEHOLDER_SENDER_MARKERS = ("123 main st", "city, country", "your address")


def _check_sender_identity(problems: list[str]) -> None:
    """CAN-SPAM requires a real physical postal address in the footer.

    The default is the literal placeholder
    "Vectorae User, 123 Main St, City, Country", and it was absent from every
    env template — so it would have shipped verbatim in production email.
    """
    value = os.getenv("SENDER_IDENTITY", "").strip()
    if not value:
        problems.append(
            "SENDER_IDENTITY is not set. It defaults to the placeholder "
            "'Vectorae User, 123 Main St, City, Country', which would appear "
            "as the CAN-SPAM physical address in every outbound email. Set it "
            "to a real postal address."
        )
        return
    lowered = value.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_SENDER_MARKERS):
        problems.append(
            f"SENDER_IDENTITY still looks like the placeholder address "
            f"({value!r}). CAN-SPAM requires a real physical postal address."
        )


# Transports that accept a send and deliver nothing. Fine locally; catastrophic
# in production, where they make signup keep returning 201 while not one user
# ever receives a verification link.
_UNDELIVERABLE_EMAIL_PROVIDERS = {"console", "memory"}
_VALID_EMAIL_PROVIDERS = {"resend", "smtp", "console", "memory"}


def _check_email_delivery(problems: list[str]) -> None:
    """Signup verification must be able to actually send mail.

    Without this check the failure is invisible from outside: /health is green,
    POST /auth/signup returns 201, the account row is created -- and the user
    sits in front of an empty inbox unable to enter the product, with the only
    evidence a log line nobody is watching. Every other check in this file
    exists for the same reason, which is why this one belongs here.
    """
    provider = os.getenv("EMAIL_PROVIDER", "").strip().lower()

    if not provider:
        problems.append(
            "EMAIL_PROVIDER is not set. It defaults to 'console', which LOGS "
            "verification emails instead of sending them -- signup would "
            "succeed and no user would ever receive a link. Set it to "
            "'resend' (or 'smtp')."
        )
        return

    if provider not in _VALID_EMAIL_PROVIDERS:
        problems.append(
            f"EMAIL_PROVIDER={provider!r} is not a known transport. Valid "
            f"values: {', '.join(sorted(_VALID_EMAIL_PROVIDERS))}."
        )
        return

    if provider in _UNDELIVERABLE_EMAIL_PROVIDERS:
        problems.append(
            f"EMAIL_PROVIDER={provider!r} does not deliver mail -- it only "
            f"records the message in-process. Signup verification emails would "
            f"never reach a user. Set EMAIL_PROVIDER=resend in production."
        )
        return

    if provider == "resend" and not os.getenv("RESEND_API_KEY", "").strip():
        problems.append(
            "EMAIL_PROVIDER=resend but RESEND_API_KEY is empty. Every "
            "verification email would fail to send."
        )
    if provider == "smtp" and not os.getenv("SMTP_HOST", "").strip():
        problems.append(
            "EMAIL_PROVIDER=smtp but SMTP_HOST is empty. Every verification "
            "email would fail to send."
        )

    sender = os.getenv("EMAIL_FROM", "").strip()
    if not sender:
        problems.append(
            "EMAIL_FROM is not set. Verification emails have no From address."
        )
    elif "@" not in sender:
        problems.append(
            f"EMAIL_FROM={sender!r} is not an email address."
        )


def is_production(app_env: str | None = None) -> bool:
    env = (app_env if app_env is not None else os.getenv("APP_ENV", "")).strip().lower()
    return env in _PRODUCTION_ENVS


def validate_production_config(app_env: str | None = None) -> None:
    """Raise ProductionConfigError listing every misconfiguration.

    No-op outside production. Call this as early in startup as possible — the
    whole point is to fail before the process starts serving traffic.
    """
    if not is_production(app_env):
        return

    problems: list[str] = []
    _check_secret_key(problems)
    _check_encryption_key(problems)
    _check_cors_origins(problems)
    _check_public_base_url(problems)
    _check_redis_url(problems)
    _check_sender_identity(problems)
    _check_email_delivery(problems)

    if problems:
        numbered = "\n".join(f"  {i}. {p}" for i, p in enumerate(problems, 1))
        raise ProductionConfigError(
            f"Refusing to start: {len(problems)} unsafe production "
            f"configuration problem(s) found.\n{numbered}\n"
            "Set these in the deployment environment and redeploy."
        )