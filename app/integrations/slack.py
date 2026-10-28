"""Slack — OAuth v2 install + chat.postMessage (Feature Group 4, used from FG7 on).

ONE CHANNEL PER USER
The product promise is "a Slack message to the user's chosen channel". So a
user connects Slack once (OAuth v2, bot scopes only), picks a channel, and
every notification event the event bus fans out lands there. The bot token and
the channel id are per-user TokenStore credentials under provider "slack";
the Slack APP's client id/secret are system credentials under "slack_app".

NEVER RAISES INTO A BUSINESS OPERATION
`post_for_user` returns False on every failure -- not connected, token
revoked, channel archived, Slack down. A meeting prep brief that fails to reach
Slack is still a meeting prep brief, and the booking that produced it must
never roll back because a chat message did not send.

Slack answers most failures with HTTP 200 and {"ok": false, "error": "..."},
so the status code alone says nothing; every call here checks `ok`.
"""

from __future__ import annotations

import logging
import uuid
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.integrations.token_store import TokenStore

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api"
SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
# Bot scopes only. chat:write posts; chat:write.public posts to a public
# channel without the bot being invited first (otherwise the first message
# fails "not_in_channel"); channels:read / groups:read list the channels the
# picker offers. No user scopes: the app never acts as the person. A PRIVATE
# channel still needs the bot invited -- the settings page says so.
BOT_SCOPES = ("chat:write", "chat:write.public", "channels:read", "groups:read")

PROVIDER = "slack"          # per-user grant
APP_PROVIDER = "slack_app"  # deployment-wide app credentials


class SlackError(Exception):
    pass


def _http() -> httpx.Client:
    """Factory tests monkeypatch."""
    return httpx.Client(timeout=15.0)


def _api(method: str, token: str, *, json_body: dict | None = None,
         params: dict | None = None) -> dict:
    with _http() as client:
        if json_body is not None:
            resp = client.post(f"{SLACK_API}/{method}", json=json_body,
                               headers={"Authorization": f"Bearer {token}"})
        else:
            resp = client.get(f"{SLACK_API}/{method}", params=params or {},
                              headers={"Authorization": f"Bearer {token}"})
    try:
        data = resp.json()
    except ValueError as exc:
        raise SlackError(f"{method}: HTTP {resp.status_code}, non-JSON body") from exc
    if not data.get("ok"):
        raise SlackError(f"{method}: {data.get('error') or resp.status_code}")
    return data


# ---------------------------------------------------------------------------
# OAuth v2
# ---------------------------------------------------------------------------


def build_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    return f"{SLACK_AUTHORIZE_URL}?" + urlencode({
        "client_id": client_id,
        "scope": ",".join(BOT_SCOPES),
        "redirect_uri": redirect_uri,
        "state": state,
    })


def exchange_code(client_id: str, client_secret: str, code: str,
                  redirect_uri: str) -> dict:
    """oauth.v2.access. Form-encoded, authenticated by the app credentials."""
    with _http() as client:
        resp = client.post(f"{SLACK_API}/oauth.v2.access", data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        })
    data = resp.json()
    if not data.get("ok"):
        raise SlackError(f"oauth.v2.access: {data.get('error')}")
    return data


def store_grant(db: Session, user_id: uuid.UUID, grant: dict) -> None:
    TokenStore.set(db, user_id=user_id, provider=PROVIDER, key="bot_token",
                   value=grant["access_token"])
    team = grant.get("team") or {}
    if team.get("name"):
        TokenStore.set(db, user_id=user_id, provider=PROVIDER, key="team_name",
                       value=team["name"])


def list_channels(token: str) -> list[dict]:
    data = _api("conversations.list", token, params={
        "types": "public_channel,private_channel",
        "exclude_archived": "true",
        "limit": 500,
    })
    return [{"id": c["id"], "name": c.get("name"), "is_private": c.get("is_private", False)}
            for c in data.get("channels", [])]


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def connection_for_user(db: Session, user_id) -> dict | None:
    token = TokenStore.get(db, user_id=user_id, provider=PROVIDER, key="bot_token")
    if not token:
        return None
    return {
        "token": token,
        "channel_id": TokenStore.get(db, user_id=user_id, provider=PROVIDER,
                                     key="channel_id"),
        "channel_name": TokenStore.get(db, user_id=user_id, provider=PROVIDER,
                                       key="channel_name"),
        "team_name": TokenStore.get(db, user_id=user_id, provider=PROVIDER,
                                    key="team_name"),
    }


def post_message(token: str, channel: str, text: str,
                 blocks: list | None = None) -> dict:
    body: dict = {"channel": channel, "text": text[:3900], "unfurl_links": False}
    if blocks:
        body["blocks"] = blocks
    return _api("chat.postMessage", token, json_body=body)


def post_for_user(db: Session, user_id, text: str,
                  blocks: list | None = None) -> bool:
    """Post to the user's chosen channel. Never raises; False on any failure."""
    try:
        conn = connection_for_user(db, user_id)
        if not conn or not conn.get("channel_id"):
            return False
        post_message(conn["token"], conn["channel_id"], text, blocks)
        return True
    except Exception as exc:  # noqa: BLE001 -- see module docstring
        logger.warning("slack post for user %s failed: %s", user_id, exc)
        return False


def markdown_blocks(title: str, body: str, link: str | None = None,
                    link_label: str = "Open in Vectorae") -> list[dict]:
    """A heading + body section (+ a button). Slack mrkdwn, not Markdown."""
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": body[:2900] or " "}},
    ]
    if link:
        blocks.append({"type": "actions", "elements": [{
            "type": "button", "text": {"type": "plain_text", "text": link_label},
            "url": link,
        }]})
    return blocks
