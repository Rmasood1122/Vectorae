"""Recent LinkedIn posts for one lead — Unipile first, RapidAPI as fallback.

Used by Feature Group 2 (the first line of an email references something the
lead actually wrote) and read again by the meeting prep brief.

Unipile needs a LinkedIn account to act THROUGH: a user's connected account
(Feature Group 5's linkedin_accounts) when there is one, else the deployment's
optional `reader_account_id` (Admin > Integrations > Unipile). The RapidAPI
scraper needs no account, only its key and host.

Both providers' response shapes are parsed tolerantly into
[{"text", "posted_at", "url"}] -- the three things the prompt and the brief
use -- and anything else in the payload is dropped. Endpoints and fields not
verified against current provider docs are marked TODO, per this codebase's
honesty rule (see apollo.py).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

MAX_POSTS = 3
_SLUG = re.compile(r"linkedin\.com/in/([^/?#]+)", re.I)


class LinkedInPostsUnavailable(Exception):
    pass


def public_identifier(linkedin_url: str | None) -> str | None:
    """'https://www.linkedin.com/in/sara-khan-12ab/' -> 'sara-khan-12ab'."""
    match = _SLUG.search(linkedin_url or "")
    return match.group(1).strip() if match else None


def _http() -> httpx.Client:
    """Factory tests monkeypatch."""
    return httpx.Client(timeout=20.0)


def _iso(value) -> str | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()


def normalise(items: list) -> list[dict]:
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or item.get("commentary") or item.get("content")
                or item.get("post_text") or "")
        if isinstance(text, dict):
            text = text.get("text") or ""
        text = str(text).strip()
        if not text:
            continue
        out.append({
            "text": text[:2000],
            "posted_at": _iso(item.get("parsed_datetime") or item.get("date")
                              or item.get("posted") or item.get("posted_at")
                              or item.get("time")),
            "url": item.get("share_url") or item.get("post_url") or item.get("url"),
        })
    out.sort(key=lambda p: p["posted_at"] or "", reverse=True)
    return out[:MAX_POSTS]


# ---------------------------------------------------------------------------
# Unipile
# ---------------------------------------------------------------------------


def fetch_unipile(api_key: str, dsn: str, account_id: str, linkedin_url: str) -> list[dict]:
    """# TODO: verify against current Unipile docs (users/{id}, users/{id}/posts)"""
    slug = public_identifier(linkedin_url)
    if not slug:
        raise LinkedInPostsUnavailable("not a linkedin.com/in/ profile URL")
    base = f"https://{dsn.strip().removeprefix('https://').rstrip('/')}/api/v1"
    headers = {"X-API-KEY": api_key, "accept": "application/json"}
    with _http() as client:
        profile = client.get(f"{base}/users/{slug}", params={"account_id": account_id},
                             headers=headers)
        if profile.status_code >= 400:
            raise LinkedInPostsUnavailable(f"unipile profile HTTP {profile.status_code}")
        provider_id = (profile.json() or {}).get("provider_id") or slug
        posts = client.get(f"{base}/users/{provider_id}/posts",
                           params={"account_id": account_id, "limit": MAX_POSTS},
                           headers=headers)
        if posts.status_code >= 400:
            raise LinkedInPostsUnavailable(f"unipile posts HTTP {posts.status_code}")
        return normalise((posts.json() or {}).get("items") or [])


# ---------------------------------------------------------------------------
# RapidAPI fallback
# ---------------------------------------------------------------------------


def fetch_rapidapi(api_key: str, host: str, linkedin_url: str) -> list[dict]:
    """# TODO: verify against the chosen RapidAPI LinkedIn scraper's docs
    (path and response shape differ between listings; this matches the
    common `get-profile-posts?linkedin_url=` form)."""
    with _http() as client:
        resp = client.get(f"https://{host}/get-profile-posts",
                          params={"linkedin_url": linkedin_url, "type": "posts"},
                          headers={"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": host})
    if resp.status_code >= 400:
        raise LinkedInPostsUnavailable(f"rapidapi HTTP {resp.status_code}")
    data = resp.json() or {}
    items = data.get("data") if isinstance(data, dict) else data
    return normalise(items if isinstance(items, list) else [])


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _reader_account(db, user_id) -> str | None:
    from app.services import credentials  # noqa: PLC0415

    try:
        from sqlalchemy import select  # noqa: PLC0415

        from app.db.models import LinkedInAccount  # noqa: PLC0415 -- Feature Group 5

        row = db.execute(
            select(LinkedInAccount.unipile_account_id)
            .where(LinkedInAccount.user_id == user_id, LinkedInAccount.is_active.is_(True))
        ).scalars().first()
        if row:
            return row
    except Exception:  # noqa: BLE001 -- the table may not exist yet
        pass
    return credentials.get_secret(db, "unipile", "reader_account_id", user_id=user_id)


def fetch_recent_posts(db, linkedin_url: str, user_id) -> tuple[list[dict], str]:
    """(posts, source). Raises LinkedInPostsUnavailable when no provider is
    configured or every configured provider failed."""
    from app.integrations.unipile_linkedin import unofficial_providers_enabled  # noqa: PLC0415
    from app.services import credentials  # noqa: PLC0415

    # GAP-LI-01: both readers are third-party access to other members'
    # LinkedIn content (a held session, a scraper). Off unless the founder
    # turns the kill switch on; no request is made.
    if not unofficial_providers_enabled():
        raise LinkedInPostsUnavailable(
            "LinkedIn data providers are disabled on this deployment "
            "(LINKEDIN_UNOFFICIAL_PROVIDERS_ENABLED=false)")
    errors = []
    api_key = credentials.get_secret(db, "unipile", "api_key", user_id=user_id)
    dsn = credentials.get_secret(db, "unipile", "dsn", user_id=user_id)
    account = _reader_account(db, user_id) if api_key and dsn else None
    if api_key and dsn and account:
        try:
            return fetch_unipile(api_key, dsn, account, linkedin_url), "unipile"
        except Exception as exc:  # noqa: BLE001 -- fall through to RapidAPI
            errors.append(f"unipile: {exc}")
    rapid_key = credentials.get_secret(db, "rapidapi_linkedin", "api_key", user_id=user_id)
    rapid_host = credentials.get_secret(db, "rapidapi_linkedin", "host", user_id=user_id)
    if rapid_key and rapid_host:
        try:
            return fetch_rapidapi(rapid_key, rapid_host, linkedin_url), "rapidapi"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"rapidapi: {exc}")
    raise LinkedInPostsUnavailable("; ".join(errors) or "no LinkedIn data provider configured")
