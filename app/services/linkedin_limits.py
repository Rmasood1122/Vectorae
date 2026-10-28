"""Feature Group 5 — LinkedIn per-account daily limits and account rotation.

WHY THIS EXISTS
LinkedIn restricts accounts that send too many connection requests; the
widely observed safe ceiling is ~20 a day, and the admin can tune it
(`linkedin_daily_connection_limit`). A user can connect several LinkedIn
accounts, and a campaign's sends ROTATE across them so no single account
exceeds its ceiling.

COUNTERS LIVE IN REDIS, ONE KEY PER ACCOUNT PER UTC DAY PER KIND
  li:usage:<account_id>:<YYYY-MM-DD>:<connect|message|inmail>
with a 36-hour TTL, so yesterday's counters expire on their own and nothing
ever has to reset them. INMAIL counts against the MESSAGE ceiling too -- it is
a message, and LinkedIn sees it as one.

Connection requests also have a WEEKLY ceiling (li:usage:<id>:<ISO week>:
connect_week, 8-day TTL): LinkedIn allows about 200 a week with Premium and
about 150 without (Unipile's provider-limits page, read 2026-10-27), and
Unipile enforces none of it. Default 180, at most 150 without Premium
(founder choice, 2026-10-27).

Only accounts whose Unipile status is "ok" are used: one that needs its
credentials again, or is restricted, would fail every send.

RESERVE, THEN SEND, THEN (ON FAILURE) RELEASE
reserve() increments first and only then compares, rolling back if over the
limit, so two workers racing for an account's 20th slot cannot both get it
(INCR is atomic). The send path releases the slot if the send fails, because
a failed request LinkedIn never received is not usage.

THE RELATIONSHIP RULE
Once an account has sent a lead a connection request or a message, every
later touch to that lead goes from THE SAME account (leads.linkedin_account_id)
-- a follow-up from a stranger's account to someone who accepted a different
person's connection request would be both useless and odd. Rotation only
chooses the account for a lead's FIRST touch.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LinkedInAccount

KINDS = ("connect", "message", "inmail")
TTL_SECONDS = 36 * 3600
WEEK_TTL_SECONDS = 8 * 24 * 3600
FREE_WEEKLY_CONNECTION_CAP = 150


def _redis():
    from app.core import redis_client  # noqa: PLC0415

    return redis_client.get_sync_redis()


def _day(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")


def _key(account_id, kind: str, now: datetime | None) -> str:
    return f"li:usage:{account_id}:{_day(now)}:{kind}"


def _week_key(account_id, now: datetime | None) -> str:
    year, week, _ = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isocalendar()
    return f"li:usage:{account_id}:{year}-W{week:02d}:connect_week"


def weekly_connection_limit(session: Session, account: LinkedInAccount) -> int:
    from app.services import system_settings  # noqa: PLC0415

    limit = int(system_settings.get(session, "linkedin_weekly_connection_limit"))
    return limit if account.has_premium else min(limit, FREE_WEEKLY_CONNECTION_CAP)


def limits(session: Session) -> dict[str, int]:
    from app.services import system_settings  # noqa: PLC0415

    return {"connect": system_settings.get(session, "linkedin_daily_connection_limit"),
            "message": system_settings.get(session, "linkedin_daily_message_limit")}


def usage(account_id, now: datetime | None = None) -> dict[str, int]:
    redis = _redis()
    return {kind: int(redis.get(_key(account_id, kind, now)) or 0) for kind in KINDS}


def _ceiling_kinds(kind: str) -> list[str]:
    # Which counters a send of `kind` consumes.
    return ["connect"] if kind == "connect" else (["message", "inmail"] if kind == "inmail"
                                                  else ["message"])


def _over(session: Session, account_id, kind: str, now) -> bool:
    lim = limits(session)
    used = usage(account_id, now)
    if kind == "connect":
        return used["connect"] >= lim["connect"]
    return used["message"] >= lim["message"]


def reserve(session: Session, account: LinkedInAccount, kind: str,
            now: datetime | None = None) -> bool:
    """Take one slot of `kind` on `account`. False when it is at its limit."""
    if kind not in KINDS:
        raise ValueError(kind)
    lim = limits(session)
    redis = _redis()
    ceiling_key = "connect" if kind == "connect" else "message"
    key = _key(account.id, ceiling_key, now)
    count = redis.incr(key)
    redis.expire(key, TTL_SECONDS)
    if count > lim[ceiling_key]:
        redis.decr(key)
        return False
    if kind == "connect":
        week_key = _week_key(account.id, now)
        weekly = redis.incr(week_key)
        redis.expire(week_key, WEEK_TTL_SECONDS)
        if weekly > weekly_connection_limit(session, account):
            redis.decr(week_key)
            redis.decr(key)
            return False
    if kind == "inmail":
        inmail_key = _key(account.id, "inmail", now)
        redis.incr(inmail_key)
        redis.expire(inmail_key, TTL_SECONDS)
    return True


def release(account: LinkedInAccount, kind: str, now: datetime | None = None) -> None:
    redis = _redis()
    keys = [_key(account.id, ceiling, now) for ceiling in _ceiling_kinds(kind)]
    if kind == "connect":
        keys.append(_week_key(account.id, now))
    for key in keys:
        if int(redis.get(key) or 0) > 0:
            redis.decr(key)


def _can_inmail(account: LinkedInAccount) -> bool:
    return bool(account.has_premium) and (account.inmail_credits is None
                                          or account.inmail_credits > 0)


def pick_account(session: Session, user_id, kind: str, *,
                 required_account_id=None,
                 now: datetime | None = None) -> LinkedInAccount | None:
    """The account to send `kind` from, with its slot already reserved.

    `required_account_id` pins the relationship account (see module
    docstring); otherwise the active account with the fewest sends of this
    kind today wins, least-recently-used breaking ties.
    """
    query = select(LinkedInAccount).where(LinkedInAccount.user_id == user_id,
                                          LinkedInAccount.is_active.is_(True),
                                          LinkedInAccount.status == "ok")
    if required_account_id is not None:
        query = query.where(LinkedInAccount.id == required_account_id)
    accounts = list(session.execute(query).scalars().all())
    if kind == "inmail":
        accounts = [a for a in accounts if _can_inmail(a)]
    ceiling = "connect" if kind == "connect" else "message"
    accounts.sort(key=lambda a: (usage(a.id, now)[ceiling],
                                 a.last_used_at or datetime.min.replace(tzinfo=timezone.utc)))
    for account in accounts:
        if reserve(session, account, kind, now=now):
            return account
    return None
