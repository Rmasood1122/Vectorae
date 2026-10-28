"""Mailreach — email account health (Feature Group 9). Optional.

Vectorae's own DNS checks (SPF, DKIM, DMARC, blacklists) run for every
sending domain whether or not Mailreach is configured. A Mailreach key adds
what DNS cannot see: the account's inbox-placement / spam score from warm-up
traffic. The key may be a system credential or the user's own ("both" scope).

TODO: verify against current Mailreach API docs -- endpoint paths and field
names below follow the public v1 API as documented at the time of writing,
and every field is read tolerantly so a renamed field degrades to "no
Mailreach data" rather than an error.
"""

from __future__ import annotations

import httpx

API_BASE = "https://api.mailreach.co/api/v1"


class MailreachError(Exception):
    pass


def _http() -> httpx.Client:
    """Factory tests monkeypatch."""
    return httpx.Client(timeout=20.0)


class MailreachClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def account_health(self, email: str) -> dict | None:
        """{"score": 0-100 | None, "spam_rate": 0-1 | None, "raw": {...}} for
        the warm-up account with this address, or None if Mailreach has none."""
        with _http() as client:
            resp = client.get(f"{API_BASE}/email_accounts", params={"email": email},
                              headers={"X-Api-Key": self.api_key})
        if resp.status_code >= 400:
            raise MailreachError(f"HTTP {resp.status_code}")
        data = resp.json()
        items = data if isinstance(data, list) else data.get("email_accounts") or data.get("data") or []
        account = next((a for a in items if isinstance(a, dict)
                        and str(a.get("email", "")).lower() == email.lower()), None)
        if account is None:
            return None
        score = account.get("reputation") or account.get("score") or account.get("health_score")
        spam = account.get("spam_rate") or account.get("spam_percentage")
        try:
            score = int(round(float(score))) if score is not None else None
        except (TypeError, ValueError):
            score = None
        try:
            spam = float(spam) / (100 if spam and float(spam) > 1 else 1) if spam is not None else None
        except (TypeError, ValueError):
            spam = None
        return {"score": score, "spam_rate": spam, "raw": account}


def get_client(db, user_id=None) -> MailreachClient | None:
    from app.services import credentials  # noqa: PLC0415

    key = credentials.get_secret(db, "mailreach", "api_key", user_id=user_id)
    return MailreachClient(key) if key else None
