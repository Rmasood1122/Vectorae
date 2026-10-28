"""What every Vectorae agent shares: its spec and the ownership query.

Every agent is ADVISORY. It reads one account's data and returns findings and
recommendations; it never sends, schedules, pauses, suppresses or edits
anything. The send-time gates (guardrails, claim verification, suppression,
daily approval) stay the only things that decide whether a message goes out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Lead, Product, Strategy, User


@dataclass(frozen=True)
class AgentResult:
    items: int      # how many records the agent examined
    result: dict


@dataclass(frozen=True)
class AgentSpec:
    name: str
    title: str
    language: str   # "rust" | "cpp"
    description: str
    run: Callable[[Session, User, dict], AgentResult]


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; every column here is stored as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def owned_lead_ids(user: User, strategy_id=None):
    """Subquery of the account's lead ids (lead -> strategy -> product -> user)."""
    q = (select(Lead.id).join(Strategy, Strategy.id == Lead.strategy_id)
         .join(Product, Product.id == Strategy.product_id)
         .where(Product.user_id == user.id))
    if strategy_id is not None:
        q = q.where(Strategy.id == strategy_id)
    return q


def int_param(params: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(params.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))
