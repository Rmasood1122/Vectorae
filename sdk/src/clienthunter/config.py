"""Layered configuration for the Vectorae SDK / CLI.

Resolution order (first match wins):
    1. Environment variables   CLIENTHUNTER_API_URL, CLIENTHUNTER_API_KEY
    2. Config file             ~/.clienthunter/config.toml
    3. Built-in defaults

Token storage (written by ``clienthunter init`` / ``ch.auth.login``):
    ~/.clienthunter/config.toml  — created with mode 0o600 (owner read/write only)

Security rules baked in here:
    - Tokens are NEVER logged, printed, or included in exception messages.
    - The config file is written atomically via a temp file rename so a crash
      never leaves a partially-written file with a loose permission.
    - Reading a world-readable config file emits a warning; the SDK does NOT
      silently use it without alerting the user.
"""

from __future__ import annotations

import os
import stat
import tempfile
import tomllib
import warnings
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_API_URL = "https://api.clienthunter.ai"  # TODO: replace with production URL
_CONFIG_DIR = Path.home() / ".clienthunter"
_CONFIG_FILE = _CONFIG_DIR / "config.toml"

# ---------------------------------------------------------------------------
# Minimal TOML serialiser (no extra dep needed for the simple schema we write)
# ---------------------------------------------------------------------------


def _to_toml(data: dict[str, dict[str, str | None]]) -> str:
    """Serialise a two-level dict to TOML.  Only supports str | None values.

    Using a hand-rolled serialiser keeps the base install dep-free — we only
    ever write [core] and [auth] sections with simple scalar values.
    """
    lines: list[str] = []
    for section, values in data.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            if value is None:
                continue  # omit null fields; reading back treats absence as None
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


class Config:
    """Resolved SDK configuration.

    Attributes:
        api_url:        Base URL of the Vectorae backend.
        api_key:        Static API key (alternative to token-based auth).
        access_token:   Short-lived JWT returned by /auth/login or /auth/refresh.
        refresh_token:  Long-lived token used to obtain a new access_token.

    None of these attributes appear in ``__repr__`` or ``__str__`` to prevent
    accidental leakage in logs or error tracebacks.
    """

    __slots__ = ("api_url", "api_key", "access_token", "refresh_token")

    def __init__(
        self,
        api_url: str = _DEFAULT_API_URL,
        api_key: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        self.api_url: str = api_url.rstrip("/")
        self.api_key: str | None = api_key
        self.access_token: str | None = access_token
        self.refresh_token: str | None = refresh_token

    # Deliberately safe repr — never expose secrets
    def __repr__(self) -> str:
        has_token = self.access_token is not None
        has_key = self.api_key is not None
        return (
            f"Config(api_url={self.api_url!r}, "
            f"has_api_key={has_key}, has_access_token={has_token})"
        )


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def load() -> Config:
    """Load configuration using the layered resolution order.

    This is called once when ``ClientHunter()`` is instantiated without
    explicit arguments.  Call ``save()`` after mutating a ``Config`` object
    (e.g. after a successful login).
    """
    # --- 1. Start with defaults ---
    api_url = _DEFAULT_API_URL
    api_key: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None

    # --- 2. Layer in the config file (if present) ---
    if _CONFIG_FILE.exists():
        _warn_if_world_readable(_CONFIG_FILE)
        try:
            raw: dict[str, Any] = tomllib.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            warnings.warn(
                f"Could not parse {_CONFIG_FILE}: {exc}. Using defaults.",
                stacklevel=3,
            )
            raw = {}

        core = raw.get("core", {})
        auth_section = raw.get("auth", {})

        if "api_url" in core:
            api_url = str(core["api_url"])
        if "api_key" in core:
            api_key = str(core["api_key"])
        if "access_token" in auth_section:
            access_token = str(auth_section["access_token"])
        if "refresh_token" in auth_section:
            refresh_token = str(auth_section["refresh_token"])

    # --- 3. Environment variables always win ---
    env_url = os.environ.get("CLIENTHUNTER_API_URL")
    if env_url:
        api_url = env_url

    env_key = os.environ.get("CLIENTHUNTER_API_KEY")
    if env_key:
        api_key = env_key
        # API key auth supersedes stored tokens
        access_token = None
        refresh_token = None

    return Config(
        api_url=api_url,
        api_key=api_key,
        access_token=access_token,
        refresh_token=refresh_token,
    )


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def save(cfg: Config) -> None:
    """Persist *cfg* to ``~/.clienthunter/config.toml`` with mode 0o600.

    Uses an atomic write (temp file → rename) so a crash never corrupts the
    existing config.  Secrets are written as opaque strings; they are not
    validated or logged here.
    """
    _CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

    data: dict[str, dict[str, str | None]] = {
        "core": {
            "api_url": cfg.api_url,
        },
        "auth": {
            "access_token": cfg.access_token,
            "refresh_token": cfg.refresh_token,
        },
    }
    # Only persist api_key if it was explicitly set (not derived from env var,
    # which should stay in the environment, not written to disk)
    env_key = os.environ.get("CLIENTHUNTER_API_KEY")
    if cfg.api_key and cfg.api_key != env_key:
        data["core"]["api_key"] = cfg.api_key

    toml_text = _to_toml(data)

    # Atomic write: write to a temp file in the same directory then rename.
    fd, tmp_path_str = tempfile.mkstemp(dir=_CONFIG_DIR, suffix=".toml.tmp")
    tmp_path = Path(tmp_path_str)
    try:
        os.chmod(fd, stat.S_IRUSR | stat.S_IWUSR)  # 0o600 before writing
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(toml_text)
        tmp_path.rename(_CONFIG_FILE)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    # Ensure the final file also has 0o600 (rename preserves temp permissions
    # on most POSIX systems, but be explicit for correctness).
    _CONFIG_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _warn_if_world_readable(path: Path) -> None:
    """Emit a warning if the config file is readable by group or others."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        warnings.warn(
            f"{path} is world-readable (mode {oct(mode)}).  "
            "Run `chmod 600 ~/.clienthunter/config.toml` to secure your tokens.",
            stacklevel=4,
        )
