"""linkedin-guard (LEGAL/SAFETY GUARD): Vectorae never automates or scrapes LinkedIn.

Founder decision 2026-11-02 (evals/vectorae, Phase 5B, option a). LinkedIn's
User Agreement and its "Prohibited software and extensions" page ban bots,
scrapers, extensions, cookie/session reuse (li_at), headless browsers and
private (Voyager) endpoints. Vectorae reaches LinkedIn only through (a) an
official API the user authorized, behind a recorded consent, or (b) a task
the user performs themselves. This file fails the build when shipped code:

  1. names a prohibited mechanism (li_at, JSESSIONID, Voyager, sales-api,
     chrome.runtime, a headless-browser driver) anywhere in app/, sdk/src/,
     frontend/src/ (tests excluded) or the deploy requirements;
  2. names a third-party LinkedIn access provider (Unipile, RapidAPI,
     PhantomBuster, Proxycurl, Expandi, HeyReach, Waalaxy, Linked API)
     outside the reviewed allowlist below (legacy column names, the
     offboarding record key, comments recording the removal);
  3. calls api.linkedin.com without app.services.linkedin_consent.require in
     the same module (L1/L2, when built, must be consent-gated);
  4. requests a LinkedIn OAuth scope that has no consent text/version in
     app.services.linkedin_consent.TEXTS.
Offline and deterministic: a scan of tracked source text.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SCANNED_PREFIXES = ("app/", "sdk/src/", "frontend/src/", "scripts/", "native/")
SKIP_PARTS = ("/tests/", "frontend/src/tests/", "frontend/src/content/help-center/")
DEPLOY_DEPENDENCY_FILES = ("requirements.txt", "requirements.lock", "sdk/pyproject.toml")

PROHIBITED = re.compile(
    r"\bli_at\b|JSESSIONID|voyager|linkedin\.com/(?:voyager|sales-api)|sales-api"
    r"|chrome\.runtime|undetected[_-]chromedriver|\bselenium\b|\bpuppeteer\b|\bpyppeteer\b"
    r"|playwright\.(?:sync_api|async_api)",
    re.IGNORECASE,
)
PROVIDERS = re.compile(
    r"unipile|rapidapi|phantombuster|proxycurl|expandi|heyreach|waalaxy|linked[\s_-]?api\b",
    re.IGNORECASE,
)

# path -> regex the provider mention must sit inside (reviewed; each with why).
PROVIDER_ALLOWLIST: dict[str, str] = {
    # Legacy schema: column/constraint names of rows created before removal
    # (dropping them is a founder-run migration) and their comments.
    "app/db/models.py": r".*",
    # The one-time offboarding script that ends the old sessions.
    "scripts/linkedin_unipile_offboard.py": r".*",
    # Erasure record key noting whether an account had legacy rows.
    "app/services/account_erasure.py": r"unipile|Unipile held|linkedin_unipile_offboard",
    # Comments recording the removal.
    "app/api/linkedin.py": r"no Unipile hosted auth and no Unipile webhook",
    "app/services/personalization_context.py": r"Unipile / RapidAPI readers were removed|linkedin_unipile_offboard",
    # Legacy enum values in the network-message type (old rows only).
    "frontend/src/lib/api/network.ts": r'"unipile" / "bulk" only on legacy rows|"copy" \| "unipile" \| "bulk"',
    # The Privacy Policy still names the removed processors until the founder
    # updates the legal text (a stop gate); over-disclosure, not a mechanism.
    "frontend/src/app/privacy/page.tsx": r"Unipile|RapidAPI",
}

LINKEDIN_SCOPES = re.compile(r"\b(?:r_liteprofile|r_emailaddress|w_member_social|r_basicprofile"
                             r"|r_organization_social|w_organization_social|rw_organization_admin"
                             r"|r_1st_connections_size|r_network)\b")


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    return [p for p in out.stdout.decode().split("\0") if p]


def _scanned() -> list[str]:
    return [p for p in _tracked()
            if p.startswith(SCANNED_PREFIXES) and not any(s in p for s in SKIP_PARTS)
            and not Path(p).name.startswith("test_")
            and p.endswith((".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".toml", ".rs", ".cpp", ".h"))
            and "package-lock" not in p]


def _lines(path: str):
    try:
        text = (ROOT / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        return []
    return list(enumerate(text.splitlines(), 1))


def test_no_prohibited_automation_mechanism():
    hits = [f"{p}:{n}: {line.strip()[:100]}"
            for p in _scanned() for n, line in _lines(p) if PROHIBITED.search(line)]
    assert hits == []


def test_no_browser_automation_dependency_ships():
    bad = re.compile(r"^\s*(?:selenium|pyppeteer|undetected-chromedriver|playwright)\b", re.I | re.M)
    for name in DEPLOY_DEPENDENCY_FILES:
        path = ROOT / name
        if path.exists():
            assert not bad.search(path.read_text()), f"{name} ships a browser-automation driver"


def test_no_third_party_linkedin_provider_outside_the_allowlist():
    hits = []
    for p in _scanned():
        allowed = PROVIDER_ALLOWLIST.get(p)
        for n, line in _lines(p):
            for m in PROVIDERS.finditer(line):
                if allowed and any(a.start() <= m.start() and a.end() >= m.end()
                                   for a in re.finditer(allowed, line)):
                    continue
                hits.append(f"{p}:{n}: {line.strip()[:100]}")
    assert hits == []


def test_allowlist_has_no_stale_entries():
    for path in PROVIDER_ALLOWLIST:
        assert any(PROVIDERS.search(line) for _, line in _lines(path)), f"stale: {path}"


def test_linkedin_api_calls_are_consent_gated():
    offenders = []
    for p in _scanned():
        text = (ROOT / p).read_text(encoding="utf-8", errors="ignore")
        if "api.linkedin.com" in text and "linkedin_consent.require" not in text:
            offenders.append(p)
    assert offenders == []


def test_every_linkedin_scope_has_a_consent_text():
    from app.services import linkedin_consent

    known = set(linkedin_consent.TEXTS) | {"openid", "profile", "email"}
    used = {m.group(0) for p in _scanned() if p != "app/services/linkedin_consent.py"
            for _, line in _lines(p) for m in LINKEDIN_SCOPES.finditer(line)}
    assert used <= known, f"LinkedIn scopes without a consent text: {sorted(used - known)}"


@pytest.mark.parametrize("module", ["app.integrations.unipile_linkedin",
                                    "app.integrations.linkedin_posts",
                                    "app.services.linkedin_limits"])
def test_the_removed_modules_stay_removed(module):
    import importlib.util

    assert importlib.util.find_spec(module) is None


def test_the_guard_itself_fires():
    """Seen-it-fire: each detector catches a planted line."""
    assert PROHIBITED.search('cookies = {"li_at": token}')
    assert PROHIBITED.search("https://www.linkedin.com/voyager/api/me")
    assert PROHIBITED.search("from playwright.sync_api import sync_playwright")
    assert PROVIDERS.search("client = UnipileClient(key, dsn)")
    assert PROVIDERS.search("X-RapidAPI-Key")
    assert LINKEDIN_SCOPES.search('scope="w_member_social"')
