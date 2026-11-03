"""Private LinkedIn network: parse an export, segment it, match it to products.

PRIVACY BOUNDARY (do not weaken without a reviewed decision)
  * A connection belongs to the account that uploaded it and is only ever read
    back for that account. Nothing here, or in app/api/linkedin_network.py,
    reads another account's connections. A shared pool of third parties'
    personal data was deliberately not built.
  * Email addresses and phone numbers in the export are discarded at parse
    time. The parsed type has no field for them.
  * Analysis is rule-based and offline. It calls no model, so a weekly refresh
    costs nothing and cannot send anyone's data to a third party.

LinkedIn offers no API for a member's connections, so new people only arrive
when the member uploads a fresh export ("Get a copy of your data"). The weekly
job re-scores what is already stored.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.plans import PLANS
from app.db.models import (
    LeadBatch, LinkedInConnection, LinkedInConnectionMatch, LinkedInImport,
    Product, Strategy, User,
)

__all__ = [
    "MAX_BYTES", "MAX_ROWS", "MATCH_THRESHOLD", "STALE_AFTER_DAYS",
    "NetworkImportError", "ParsedConnection", "ParseResult",
    "parse_connections_csv", "import_connections", "analyze_user",
    "erase_user_network", "refresh_all", "is_eligible", "seniority", "function_of",
]

MAX_BYTES = 5_000_000
MAX_ROWS = 20_000
MATCH_THRESHOLD = 30
MAX_MATCHES_PER_PRODUCT = 500
STALE_AFTER_DAYS = 7


class NetworkImportError(ValueError):
    """The uploaded file cannot be used. The message is user-facing."""


@dataclass(frozen=True)
class ParsedConnection:
    full_name: str
    company: str | None
    position: str | None
    profile_url: str | None
    connected_on: date | None
    dedupe_key: str


@dataclass
class ParseResult:
    rows: list[ParsedConnection] = field(default_factory=list)
    skipped: int = 0
    rows_in_file: int = 0


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _clean(value: str | None, limit: int) -> str | None:
    value = (value or "").strip()
    return value[:limit] or None


def _dedupe_key(name: str, company: str | None, url: str | None) -> str:
    if url:
        basis = url.lower().split("?")[0].rstrip("/")
    else:
        basis = f"{name}|{company or ''}".lower()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def _parse_date(raw: str | None) -> date | None:
    raw = (raw or "").strip()
    for fmt in ("%d %b %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_connections_csv(text: str) -> ParseResult:
    """Parse LinkedIn's Connections.csv. It starts with a few lines of "Notes:"
    before the real header, which are skipped."""
    lines = text.lstrip("﻿").splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().lower().startswith("first name")), None)
    if start is None:
        raise NetworkImportError(
            "This does not look like LinkedIn's Connections.csv "
            "(no 'First Name' header found).")
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    header = {(h or "").strip().lower(): h for h in (reader.fieldnames or [])}
    if "first name" not in header or "last name" not in header:
        raise NetworkImportError("The file is missing the First Name / Last Name columns.")

    def col(row: dict, name: str) -> str | None:
        key = header.get(name)
        return row.get(key) if key else None

    result = ParseResult()
    seen: set[str] = set()
    for row in reader:
        result.rows_in_file += 1
        if result.rows_in_file > MAX_ROWS:
            raise NetworkImportError(
                f"The file has more than {MAX_ROWS:,} connections. Split it and upload in parts.")
        name = " ".join(filter(None, [(col(row, "first name") or "").strip(),
                                      (col(row, "last name") or "").strip()]))
        if not name:
            result.skipped += 1
            continue
        company = _clean(col(row, "company"), 300)
        url = _clean(col(row, "url"), 500)
        key = _dedupe_key(name, company, url)
        if key in seen:
            continue
        seen.add(key)
        # The "Email Address" column is intentionally never read.
        result.rows.append(ParsedConnection(
            full_name=name[:300], company=company,
            position=_clean(col(row, "position"), 300), profile_url=url,
            connected_on=_parse_date(col(row, "connected on")), dedupe_key=key))
    return result


# --------------------------------------------------------------------------
# Segmentation
# --------------------------------------------------------------------------

_FOUNDER = re.compile(r"\b(founder|co-?founder|owner|proprietor|partner|principal)\b")
_CLEVEL = re.compile(r"\b(chief|ceo|cfo|coo|cto|cmo|cio|ciso|president|managing director)\b")
_VP = re.compile(r"\b(vp|vice president|svp|evp|director|head of)\b")
_MANAGER = re.compile(r"\b(manager|supervisor|team lead|lead)\b")

_FUNCTIONS = [
    ("Leadership", re.compile(r"\b(founder|co-?founder|owner|proprietor|ceo|chief executive|"
                              r"president|managing director)\b")),
    ("Sales", re.compile(r"\b(sales|business development|account executive|revenue)\b")),
    ("Marketing", re.compile(r"\b(marketing|growth|brand|seo|content)\b")),
    ("Operations", re.compile(r"\b(operations|ops|supply chain|logistics|facilities)\b")),
    ("Engineering", re.compile(r"\b(engineer|developer|software|technology|cto|data|it)\b")),
    ("Finance", re.compile(r"\b(finance|accounting|cfo|controller)\b")),
    ("People", re.compile(r"\b(hr|human resources|talent|recruit|people)\b")),
    ("Product", re.compile(r"\b(product)\b")),
]

_SENIORITY_WEIGHT = {
    "Founder / owner": 25, "C-level": 25, "VP / Director": 20, "Manager": 10,
    "Individual contributor": 0, "Unknown": 0,
}


def seniority(title: str | None) -> str:
    if not title or not title.strip():
        return "Unknown"
    t = title.lower()
    if _FOUNDER.search(t):
        return "Founder / owner"
    if _CLEVEL.search(t):
        return "C-level"
    if _VP.search(t):
        return "VP / Director"
    if _MANAGER.search(t):
        return "Manager"
    return "Individual contributor"


def function_of(title: str | None) -> str:
    t = (title or "").lower()
    for name, pattern in _FUNCTIONS:
        if pattern.search(t):
            return name
    return "Other"


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

_STOP = frozenset("""a an and the for with that this from your you our their into onto
over under are was were has have had not but can will all any more most than then
to of in on at by as or its it be is we us who whom which what when where how very
local companies company services service solutions system systems""".split())


def _tokens(text: str | None) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) >= 3 and t not in _STOP}


@dataclass
class _Profile:
    titles: list[set[str]]
    keywords: set[str]
    from_strategy: bool


def _latest_icp(db: Session, product: Product) -> dict | None:
    row = db.execute(
        select(LeadBatch.icp_criteria_json)
        .join(Strategy, Strategy.id == LeadBatch.strategy_id)
        .where(Strategy.product_id == product.id,
               LeadBatch.icp_criteria_json.is_not(None))
        .order_by(LeadBatch.created_at.desc())
    ).first()
    return row[0] if row and row[0] else None


def _profile(db: Session, product: Product) -> _Profile:
    icp = _latest_icp(db, product) or {}
    titles = [t for t in (_tokens(x) for x in icp.get("titles") or []) if t]
    words: set[str] = set()
    for key in ("industries", "keywords"):
        for item in icp.get(key) or []:
            words |= _tokens(item)
    if titles or words:
        return _Profile(titles, words, True)
    return _Profile([], _tokens(f"{product.name} {product.description}"), False)


def _score(profile: _Profile, position: str | None, company: str | None):
    pos_tokens = _tokens(position)
    reasons: list[str] = []
    content = 0

    best = 0.0
    best_title = ""
    for title in profile.titles:
        ratio = len(title & pos_tokens) / len(title)
        if ratio > best:
            best, best_title = ratio, " ".join(sorted(title))
    if best > 0:
        pts = round(45 * best)
        content += pts
        reasons.append(f"Role matches a target title ({best_title})")

    haystack = _tokens(position) | _tokens(company)
    hits = sorted(profile.keywords & haystack)
    if hits:
        content += min(40, 12 * len(hits))
        reasons.append("Company or role mentions " + ", ".join(hits[:4]))

    if content == 0:
        return 0, reasons
    level = seniority(position)
    bonus = _SENIORITY_WEIGHT[level]
    if bonus:
        reasons.append(f"Decision-maker level: {level}")
    return min(100, content + bonus), reasons


#: Shared with the email network (app/services/email_network.py), which scores
#: a contact's title / company against the same product ICP. Aliases only: the
#: LinkedIn behaviour is exactly the functions above.
icp_profile = _profile
icp_score = _score


def analyze_user(db: Session, user: User) -> dict:
    """Re-score the user's OWN connections against the user's OWN products."""
    now = datetime.now(timezone.utc)
    products = db.execute(select(Product).where(Product.user_id == user.id)).scalars().all()
    connections = db.execute(
        select(LinkedInConnection).where(LinkedInConnection.user_id == user.id)
    ).scalars().all()
    db.execute(delete(LinkedInConnectionMatch)
               .where(LinkedInConnectionMatch.user_id == user.id))
    created = 0
    for product in products:
        profile = _profile(db, product)
        scored = []
        for c in connections:
            score, reasons = _score(profile, c.position, c.company)
            if score >= MATCH_THRESHOLD:
                scored.append((score, c, reasons))
        scored.sort(key=lambda x: (-x[0], x[1].full_name))
        for score, c, reasons in scored[:MAX_MATCHES_PER_PRODUCT]:
            db.add(LinkedInConnectionMatch(
                user_id=user.id, connection_id=c.id, product_id=product.id,
                score=score, reasons_json=reasons, analyzed_at=now,
                segment=f"{seniority(c.position)} - {function_of(c.position)}"))
            created += 1
    db.commit()
    return {"products": len(products), "connections": len(connections), "matches": created}


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def import_connections(db: Session, user: User, parsed: ParseResult) -> dict:
    existing = set(db.execute(
        select(LinkedInConnection.dedupe_key).where(LinkedInConnection.user_id == user.id)
    ).scalars())
    added = duplicates = 0
    for row in parsed.rows:
        if row.dedupe_key in existing:
            duplicates += 1
            continue
        existing.add(row.dedupe_key)
        db.add(LinkedInConnection(
            user_id=user.id, dedupe_key=row.dedupe_key, full_name=row.full_name,
            company=row.company, position=row.position, profile_url=row.profile_url,
            connected_on=row.connected_on))
        added += 1
    db.add(LinkedInImport(user_id=user.id, rows_in_file=parsed.rows_in_file,
                          added=added, skipped=parsed.skipped))
    db.commit()
    total = db.execute(select(func.count(LinkedInConnection.id))
                       .where(LinkedInConnection.user_id == user.id)).scalar_one()
    return {"added": added, "skipped": parsed.skipped, "duplicates": duplicates,
            "total": total}


def erase_user_network(db: Session, user: User) -> dict:
    from app.db.models import LinkedInNetworkMessage  # noqa: PLC0415

    db.execute(delete(LinkedInNetworkMessage).where(LinkedInNetworkMessage.user_id == user.id))
    matches = db.execute(delete(LinkedInConnectionMatch)
                         .where(LinkedInConnectionMatch.user_id == user.id)).rowcount
    conns = db.execute(delete(LinkedInConnection)
                       .where(LinkedInConnection.user_id == user.id)).rowcount
    db.execute(delete(LinkedInImport).where(LinkedInImport.user_id == user.id))
    db.commit()
    return {"deleted_connections": conns, "deleted_matches": matches}


# --------------------------------------------------------------------------
# Plan + weekly refresh
# --------------------------------------------------------------------------


def is_eligible(user: User) -> bool:
    plan = getattr(user.plan, "value", user.plan) or "free"
    return bool(PLANS.get(plan, PLANS["free"]).get("linkedin_network"))


def needs_new_export(db: Session, user: User) -> bool:
    last = db.execute(select(func.max(LinkedInImport.created_at))
                      .where(LinkedInImport.user_id == user.id)).scalar_one()
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last > timedelta(days=STALE_AFTER_DAYS)


def refresh_all(db: Session) -> dict:
    """Weekly: re-score stored connections for eligible accounts only."""
    user_ids = db.execute(select(LinkedInConnection.user_id).distinct()).scalars().all()
    done = skipped = 0
    for uid in user_ids:
        user = db.get(User, uid)
        if user is None or user.is_suspended or not is_eligible(user):
            skipped += 1
            continue
        analyze_user(db, user)
        done += 1
    return {"users": done, "skipped_ineligible": skipped}
