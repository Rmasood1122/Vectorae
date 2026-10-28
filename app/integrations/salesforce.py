"""Salesforce — OAuth web-server flow and the REST API (Feature Group 4).

The connected app (client id / secret) is a system credential under
"salesforce_app"; each user's grant (refresh token, access token, instance
URL) is a user credential under "salesforce". Salesforce access tokens carry
no expiry -- they die with the session policy -- so the client refreshes on a
401 and retries once.

Vectorae leads sync to Salesforce LEADS (not Contacts): a cold prospect is
exactly what the Lead object is for, and conversion to Contact/Opportunity
remains the Salesforce user's decision. Won/lost deals sync to Opportunities.
"""

from __future__ import annotations

from typing import Callable
from urllib.parse import urlencode

import httpx

LOGIN_URL = "https://login.salesforce.com"
API_VERSION = "v60.0"
PROVIDER = "salesforce"
APP_PROVIDER = "salesforce_app"

LEAD_FIELDS = ["Id", "Email", "Status", "LastModifiedDate", "IsConverted"]
OPPORTUNITY_FIELDS = ["Id", "Name", "StageName", "Amount", "CloseDate", "IsWon", "IsClosed",
                      "LastModifiedDate"]


class SalesforceError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status
        self.permanent = status is not None and 400 <= status < 500 and status not in (401, 429)


def _http() -> httpx.Client:
    """Factory tests monkeypatch."""
    return httpx.Client(timeout=20.0)


def build_auth_url(client_id: str, redirect_uri: str, state: str,
                   login_url: str = LOGIN_URL) -> str:
    return f"{login_url}/services/oauth2/authorize?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "api refresh_token",
        "state": state,
    })


def _token_request(data: dict, login_url: str = LOGIN_URL) -> dict:
    with _http() as client:
        resp = client.post(f"{login_url}/services/oauth2/token", data=data)
    if resp.status_code >= 400:
        raise SalesforceError(f"token endpoint: HTTP {resp.status_code} {resp.text[:200]}",
                              resp.status_code)
    return resp.json()


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str,
                  login_url: str = LOGIN_URL) -> dict:
    """{access_token, refresh_token, instance_url, id (identity URL)}."""
    return _token_request({"grant_type": "authorization_code", "client_id": client_id,
                           "client_secret": client_secret, "redirect_uri": redirect_uri,
                           "code": code}, login_url)


def refresh(client_id: str, client_secret: str, refresh_token: str,
            login_url: str = LOGIN_URL) -> dict:
    return _token_request({"grant_type": "refresh_token", "client_id": client_id,
                           "client_secret": client_secret,
                           "refresh_token": refresh_token}, login_url)


def org_id_from_identity(identity_url: str | None) -> str | None:
    """https://login.salesforce.com/id/<orgId>/<userId> -> orgId."""
    if not identity_url or "/id/" not in identity_url:
        return None
    parts = identity_url.split("/id/", 1)[1].split("/")
    return parts[0] or None


def soql_quote(value: str) -> str:
    """A SOQL string literal. Backslash and quote are the two characters that
    can break out of one; nothing user-controlled reaches SOQL unquoted."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


class SalesforceClient:
    def __init__(self, instance_url: str, access_token: str,
                 on_unauthorized: Callable[[], str] | None = None):
        self.instance_url = instance_url.rstrip("/")
        self.access_token = access_token
        self.on_unauthorized = on_unauthorized

    def _req(self, method: str, path: str, *, json: dict | None = None,
             params: dict | None = None, _retried: bool = False) -> dict:
        url = f"{self.instance_url}/services/data/{API_VERSION}{path}"
        with _http() as client:
            resp = client.request(method, url, json=json, params=params,
                                  headers={"Authorization": f"Bearer {self.access_token}"})
        if resp.status_code == 401 and self.on_unauthorized and not _retried:
            self.access_token = self.on_unauthorized()
            return self._req(method, path, json=json, params=params, _retried=True)
        if resp.status_code >= 400:
            raise SalesforceError(f"{method} {path}: HTTP {resp.status_code} {resp.text[:300]}",
                                  resp.status_code)
        return resp.json() if resp.content else {}

    def query(self, soql: str) -> list[dict]:
        data = self._req("GET", "/query", params={"q": soql})
        return list(data.get("records") or [])

    def query_all(self, soql: str, *, max_pages: int = 50) -> list[dict]:
        """Every record of a query, following `nextRecordsUrl` (2,000 per page by
        default), at most `max_pages` pages. F05 Guardrails Studio.
        # TODO: verify against current Salesforce REST docs (query locator paging)"""
        data = self._req("GET", "/query", params={"q": soql})
        records = list(data.get("records") or [])
        prefix = f"/services/data/{API_VERSION}"
        for _ in range(max_pages - 1):
            next_url = data.get("nextRecordsUrl")
            if data.get("done", True) or not next_url:
                break
            data = self._req("GET", next_url.split(prefix, 1)[-1])
            records.extend(data.get("records") or [])
        return records

    def find_lead_by_email(self, email: str) -> str | None:
        rows = self.query(f"SELECT Id FROM Lead WHERE Email = {soql_quote(email)} "
                          "ORDER BY CreatedDate DESC LIMIT 1")
        return rows[0]["Id"] if rows else None

    def create(self, sobject: str, fields: dict) -> str:
        return str(self._req("POST", f"/sobjects/{sobject}", json=fields)["id"])

    def update(self, sobject: str, record_id: str, fields: dict) -> None:
        self._req("PATCH", f"/sobjects/{sobject}/{record_id}", json=fields)

    def get_many(self, sobject: str, ids: list[str], fields: list[str]) -> list[dict]:
        out: list[dict] = []
        for start in range(0, len(ids), 200):
            chunk = ids[start:start + 200]
            in_list = ", ".join(soql_quote(i) for i in chunk)
            out.extend(self.query(f"SELECT {', '.join(fields)} FROM {sobject} "
                                  f"WHERE Id IN ({in_list})"))
        return out
