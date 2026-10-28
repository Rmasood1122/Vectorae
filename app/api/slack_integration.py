"""Slack connect flow and channel choice (Feature Group 4).

    GET    /integrations/slack            status
    GET    /integrations/slack/auth-url   start OAuth v2
    GET    /integrations/slack/callback   (public; OAuth redirect)
    GET    /integrations/slack/channels   channels the picker offers
    PUT    /integrations/slack/channel    {"channel_id": "C123"}
    POST   /integrations/slack/send-test  post a test message
    DELETE /integrations/slack            disconnect -> 204

What gets posted is decided by the event hub (app/services/event_bus.py):
meeting booked, interested reply, campaign auto-paused, strategy mutation,
plus meeting prep / reminders, objection spikes and deals won.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.crm_integrations import frontend_redirect, redirect_uri
from app.api.deps import get_current_user
from app.db.base import get_db
from app.db.models import User
from app.integrations import slack
from app.integrations.token_store import TokenStore
from app.services import oauth_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrations/slack", tags=["integrations"])


def _app(db: Session) -> tuple[str, str]:
    client_id = TokenStore.get(db, None, slack.APP_PROVIDER, "client_id")
    client_secret = TokenStore.get(db, None, slack.APP_PROVIDER, "client_secret")
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail=(
            "The Slack app is not configured -- an admin must add it under "
            "Admin > Integrations."))
    return client_id, client_secret


def _connection(db: Session, user: User) -> dict:
    conn = slack.connection_for_user(db, user.id)
    if not conn:
        raise HTTPException(status_code=404, detail="Slack is not connected")
    return conn


@router.get("")
def slack_status(db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> dict:
    configured = TokenStore.get(db, None, slack.APP_PROVIDER, "client_id") is not None
    conn = slack.connection_for_user(db, current_user.id)
    if not conn:
        return {"configured": configured, "connected": False}
    return {"configured": configured, "connected": True, "team_name": conn.get("team_name"),
            "channel_id": conn.get("channel_id"), "channel_name": conn.get("channel_name")}


@router.get("/auth-url")
def slack_auth_url(db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)) -> dict:
    client_id, _ = _app(db)
    state = oauth_state.make(current_user.id, "slack")
    return {"auth_url": slack.build_auth_url(client_id, redirect_uri("slack"), state)}


@router.get("/callback", include_in_schema=False)
def slack_callback(code: str | None = None, state: str | None = None,
                   error: str | None = None, db: Session = Depends(get_db)):
    if error or not code:
        return frontend_redirect("slack", "error", error or "missing code")
    try:
        user_id = oauth_state.parse(state, "slack")
    except oauth_state.InvalidState as exc:
        return frontend_redirect("slack", "error", str(exc))
    from app.services.account_erasure import is_tombstone  # noqa: PLC0415

    user = db.get(User, user_id)
    if user is None or is_tombstone(user):   # an erased account stays erased
        return frontend_redirect("slack", "error", "unknown user")
    try:
        client_id, client_secret = _app(db)
        grant = slack.exchange_code(client_id, client_secret, code, redirect_uri("slack"))
        slack.store_grant(db, user_id, grant)
    except (slack.SlackError, HTTPException, KeyError) as exc:
        logger.warning("slack connect failed for %s: %s", user_id, exc)
        return frontend_redirect("slack", "error", "Slack rejected the connection")
    return frontend_redirect("slack", "connected")


@router.get("/channels")
def slack_channels(db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)) -> list[dict]:
    try:
        return slack.list_channels(_connection(db, current_user)["token"])
    except slack.SlackError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class ChannelIn(BaseModel):
    channel_id: str = Field(min_length=1, max_length=40)


@router.put("/channel")
def slack_set_channel(body: ChannelIn, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)) -> dict:
    conn = _connection(db, current_user)
    try:
        channels = {c["id"]: c for c in slack.list_channels(conn["token"])}
    except slack.SlackError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    channel = channels.get(body.channel_id)
    if channel is None:
        raise HTTPException(status_code=422, detail="that channel is not visible to the app")
    TokenStore.set(db, current_user.id, slack.PROVIDER, "channel_id", channel["id"], commit=False)
    TokenStore.set(db, current_user.id, slack.PROVIDER, "channel_name",
                   channel.get("name") or channel["id"])
    return slack_status(db, current_user)


# Not "/test": the settings page's generic POST /integrations/{provider}/test
# (registered earlier) would capture it and answer "unknown provider".
@router.post("/send-test")
def slack_test(db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)) -> dict:
    conn = _connection(db, current_user)
    if not conn.get("channel_id"):
        raise HTTPException(status_code=409, detail="choose a channel first")
    try:
        slack.post_message(conn["token"], conn["channel_id"],
                           "Vectorae is connected. Meeting bookings, interested replies, "
                           "campaign pauses and strategy changes will be posted here.")
    except slack.SlackError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("", status_code=204)
def slack_disconnect(db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)) -> Response:
    TokenStore.delete(db, current_user.id, slack.PROVIDER)
    return Response(status_code=204)
