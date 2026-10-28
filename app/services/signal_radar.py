"""F02 Signal Radar -- dated "why now" signals with a verifiable source.

WHAT IT IS
A normalising layer over data Vectorae ALREADY stores. Nothing here fetches:
  newsapi        leads.company_news_json      (personalization_context, NewsAPI)
  linkedin_post  leads.linkedin_posts_json    (personalization_context, Unipile/RapidAPI)
  apollo         the organisation record in leads.enrichment_json
  market_intel   strategies.market_signals_json, only items naming THIS lead's company
Each distinct event becomes one `lead_signals` row with a type, a date, an
expiry, a source confidence and -- where one exists -- a public source link.

FRESHNESS
Every type has a lifetime (TTL_DAYS, none above 90 days). Freshness halves
every TTL/2 days and is 0 at expiry; score = confidence x freshness, used only
to rank. Age is reported next to confidence, never blended into it (same
reasoning as provenance.staleness).

CITING A SIGNAL
The email prompt is offered the single best CITABLE signal (unexpired, not
future-dated, a public http(s) link, confidence >= 0.5, claim engine on). A
signal counts as CITED only when the claim engine VERIFIED a sentence against
that signal's own stored evidence item (resolve_citation). The metric can
therefore undercount, never overcount.

NEVER BLOCKS A SEND. refresh() and prepare() swallow their own errors: an
email without a "why now" line is an ordinary email.

See docs/features/f02-signal-radar.md.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Lead, LeadSignal, Strategy
from app.services import prompt_safety, provenance

logger = logging.getLogger(__name__)

FLAG = "signal_radar_enabled"

FUNDING, HIRING, LAUNCH, NEWS, POST = "funding", "hiring", "launch", "news", "post"
TYPES = (FUNDING, HIRING, LAUNCH, NEWS, POST)
TYPE_LABELS = {FUNDING: "Funding", HIRING: "Hiring / headcount", LAUNCH: "Launch / partnership",
               NEWS: "In the news", POST: "Their own post"}

# Days a signal stays a reason to write NOW. None exceeds MAX_AGE_DAYS, so a
# citable signal is under 90 days old by construction (the success metric).
TTL_DAYS = {FUNDING: 90, LAUNCH: 60, HIRING: 60, NEWS: 45, POST: 30}
MAX_AGE_DAYS = 90

NEWSAPI, LINKEDIN_POST, APOLLO, MARKET_INTEL = "newsapi", "linkedin_post", "apollo", "market_intel"
SOURCE_LABELS = {NEWSAPI: "Company news", LINKEDIN_POST: "LinkedIn post",
                 APOLLO: "Apollo record", MARKET_INTEL: "Market intel headline"}

# The confidence belongs to the SOURCE (provenance.SOURCES), not the value.
# A market-intel Google News item is a bare headline matched to the lead by
# company name alone, so it sits below a NewsAPI article.
CONFIDENCE = {
    NEWSAPI: provenance.SOURCES[provenance.COMPANY_NEWS][0],
    LINKEDIN_POST: provenance.SOURCES[provenance.LINKEDIN][0],
    APOLLO: provenance.SOURCES[provenance.APOLLO][0],
}
MARKET_NEWS_CONFIDENCE = 0.6
MIN_CITABLE_CONFIDENCE = 0.5
FRESH_BAND = 0.5
EXCERPT_CHARS = 1000

_SUFFIXES = {"inc", "llc", "ltd", "limited", "co", "corp", "corporation", "company", "gmbh",
             "plc", "sa", "ag", "bv", "pty", "the"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def enabled(session: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(session, FLAG))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse_date(value) -> datetime | None:
    if not value or not isinstance(value, (str, datetime)):
        return None
    if isinstance(value, datetime):
        return _aware(value)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def _public_url(url) -> str | None:
    text = str(url or "").strip()
    return text[:1000] if re.match(r"^https?://[^\s/]+\.[^\s]+", text, re.I) else None


def normalise_company(name: str | None) -> str:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return " ".join(w for w in words if w not in _SUFFIXES)


def _names_company(text: str, company: str | None) -> bool:
    """Whole-word, case-insensitive mention of the lead's company. Names under
    four characters are refused: "Arc" is in too many headlines to mean them."""
    core = normalise_company(company)
    if len(core) < 4:
        return False
    pattern = r"\b" + r"\W+".join(re.escape(w) for w in core.split()) + r"\b"
    return re.search(pattern, text or "", re.I) is not None


def _dedupe_key(source: str, signal_type: str, url: str | None, title: str) -> str:
    basis = url or f"{source}|{signal_type}|{' '.join(title.lower().split())}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def evidence_key(signal) -> str | None:
    """The claim engine's Evidence.source string for this signal's stored
    source -- the ONLY thing a verified claim can be tied through. None for a
    source that has no public link (Apollo), which is never citable."""
    url = signal.source_url
    if not url:
        return None
    if signal.source == NEWSAPI:
        return f"newsapi:{url}"
    if signal.source == LINKEDIN_POST:
        return f"linkedin_post:{url}"
    if signal.source == MARKET_INTEL:
        return f"market_intel:{url}"
    return None


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def age_days(observed_at: datetime, now: datetime) -> float:
    return (now - _aware(observed_at)).total_seconds() / 86400.0


def freshness(signal_type: str, observed_at: datetime, now: datetime) -> float:
    """1.0 when observed, halves every TTL/2 days, 0 at or after expiry."""
    ttl = TTL_DAYS.get(signal_type, TTL_DAYS[NEWS])
    age = age_days(observed_at, now)
    if age >= ttl:
        return 0.0
    age = max(age, 0.0)
    return round(0.5 ** (age / (ttl / 2.0)), 4)


def status(signal, now: datetime) -> str:
    value = freshness(signal.signal_type, signal.observed_at, now)
    if value <= 0:
        return "expired"
    return "fresh" if value >= FRESH_BAND else "aging"


def not_citable_reason(signal, now: datetime, *, claim_engine_on: bool = True) -> str | None:
    """None when the signal may be cited; otherwise the reason, in words."""
    observed = _aware(signal.observed_at)
    if observed > now + timedelta(days=1):
        return "dated in the future"
    if _aware(signal.expires_at) <= now or freshness(signal.signal_type, observed, now) <= 0:
        return "expired"
    if not signal.source_url or evidence_key(signal) is None:
        return "no public source link"
    if (signal.confidence or 0) < MIN_CITABLE_CONFIDENCE:
        return "source confidence too low"
    if not claim_engine_on:
        return "claim verification is off"
    return None


def score(signal, now: datetime) -> float:
    return round((signal.confidence or 0) * freshness(signal.signal_type, signal.observed_at,
                                                      now), 4)


# ---------------------------------------------------------------------------
# Derivation (no network)
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    signal_type: str
    source: str
    title: str
    observed_at: datetime
    confidence: float
    source_url: str | None = None
    source_name: str | None = None
    excerpt: str | None = None


def _organisation(lead: Lead) -> dict | None:
    data = lead.enrichment_json if isinstance(lead.enrichment_json, dict) else {}
    enrichment = data.get("enrichment") if isinstance(data.get("enrichment"), dict) else {}
    person = enrichment.get("person") if isinstance(enrichment.get("person"), dict) else {}
    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
    for org in (person.get("organization"), enrichment.get("organization"),
                raw.get("organization"), data.get("organization")):
        if isinstance(org, dict) and org:
            return org
    return None


def _from_news(lead: Lead) -> list[Candidate]:
    from app.services.market_intel import classify_headline  # noqa: PLC0415

    out = []
    for item in lead.company_news_json or []:
        if not isinstance(item, dict) or not str(item.get("headline") or "").strip():
            continue
        when = _parse_date(item.get("published_at"))
        if when is None:
            continue
        headline = str(item["headline"]).strip()[:500]
        out.append(Candidate(classify_headline(headline), NEWSAPI, headline, when,
                             CONFIDENCE[NEWSAPI], _public_url(item.get("url")),
                             (str(item.get("source") or "").strip() or None),
                             (str(item.get("summary") or "").strip()[:EXCERPT_CHARS] or None)))
    return out


def _from_posts(lead: Lead) -> list[Candidate]:
    out = []
    for post in lead.linkedin_posts_json or []:
        if not isinstance(post, dict):
            continue
        text = str(post.get("text") or "").strip()
        when = _parse_date(post.get("posted_at"))
        if not text or when is None:
            continue
        first_line = " ".join(text.split())[:140]
        out.append(Candidate(POST, LINKEDIN_POST, f"Posted: {first_line}", when,
                             CONFIDENCE[LINKEDIN_POST], _public_url(post.get("url")),
                             "LinkedIn", text[:EXCERPT_CHARS]))
    return out


def _from_apollo(lead: Lead, now: datetime) -> list[Candidate]:
    """Apollo funding / headcount growth for the lead's own organisation.
    # TODO: verify against current Apollo docs (field names, as market_intel)."""
    from app.services.market_intel import apollo_signals  # noqa: PLC0415

    org = _organisation(lead)
    if not org:
        return []
    org = dict(org)
    org.setdefault("name", lead.company)
    if not org.get("name"):
        return []
    reported = _aware(lead.created_at) or now
    out = []
    for sig in apollo_signals([org], now=now):
        when = _parse_date(sig.get("published_at"))
        if sig["type"] == HIRING and when is None:
            # Six-month headcount growth carries no date of its own: it is
            # dated when Apollo reported it to us.
            when = reported
        if when is None or sig["type"] not in TYPES:
            continue
        out.append(Candidate(sig["type"], APOLLO, str(sig["title"])[:500], when,
                             CONFIDENCE[APOLLO], None, "Apollo", None))
    return out


def _from_market_intel(lead: Lead, strategy: Strategy | None) -> list[Candidate]:
    payload = (strategy.market_signals_json if strategy is not None else None) or {}
    out = []
    lead_company = normalise_company(lead.company)
    for sig in payload.get("signals") or []:
        if not isinstance(sig, dict) or sig.get("type") not in TYPES:
            continue
        title = str(sig.get("title") or "").strip()
        when = _parse_date(sig.get("published_at"))
        if not title or when is None:
            continue
        if sig.get("source") == "apollo":
            if not lead_company or normalise_company(sig.get("company")) != lead_company:
                continue
            out.append(Candidate(sig["type"], MARKET_INTEL, title[:500], when,
                                 CONFIDENCE[APOLLO], None, "Apollo", None))
        elif sig.get("source") == "google_news":
            if not _names_company(title, lead.company):
                continue
            # market_intel stores the PUBLISHER in `company` for Google News.
            out.append(Candidate(sig["type"], MARKET_INTEL, title[:500], when,
                                 MARKET_NEWS_CONFIDENCE, _public_url(sig.get("url")),
                                 (str(sig.get("company") or "").strip() or None), None))
    return out


def candidates(lead: Lead, strategy: Strategy | None, now: datetime) -> list[Candidate]:
    return (_from_news(lead) + _from_posts(lead) + _from_apollo(lead, now)
            + _from_market_intel(lead, strategy))


def refresh(session: Session, lead: Lead, *, owner_id, strategy: Strategy | None = None,
            now: datetime | None = None, commit: bool = True) -> dict:
    """Upsert the lead's signals from stored data. Never raises; returns
    {"created": n, "updated": n} or {"error": ...}."""
    now = now or _now()
    if owner_id is None:
        return {"created": 0, "updated": 0, "skipped": "no owner"}
    # Local business mode: no signals are derived about a business's own
    # customer (a consumer).
    from app.services.local_business import is_customer  # noqa: PLC0415

    if is_customer(lead):
        return {"created": 0, "updated": 0, "skipped": "customer"}
    try:
        if strategy is None and lead.strategy_id is not None:
            strategy = session.get(Strategy, lead.strategy_id)
        existing = {row.dedupe_key: row for row in session.execute(
            select(LeadSignal).where(LeadSignal.lead_id == lead.id,
                                     LeadSignal.user_id == owner_id)).scalars()}
        created = updated = 0
        for cand in candidates(lead, strategy, now):
            key = _dedupe_key(cand.source, cand.signal_type, cand.source_url, cand.title)
            ttl = timedelta(days=TTL_DAYS[cand.signal_type])
            row = existing.get(key)
            if row is None:
                row = LeadSignal(user_id=owner_id, lead_id=lead.id, dedupe_key=key,
                                 first_seen_at=now)
                session.add(row)
                existing[key] = row
                created += 1
            else:
                updated += 1
            row.signal_type = cand.signal_type
            row.source = cand.source
            row.source_url = cand.source_url
            row.source_name = cand.source_name[:200] if cand.source_name else None
            row.title = cand.title[:500]
            row.excerpt = cand.excerpt
            row.observed_at = cand.observed_at
            row.expires_at = cand.observed_at + ttl
            row.confidence = float(cand.confidence)
            row.last_seen_at = now
        if commit:
            session.commit()
        else:
            session.flush()
        return {"created": created, "updated": updated}
    except Exception as exc:  # noqa: BLE001 -- see NEVER BLOCKS A SEND
        session.rollback()
        logger.warning("signal radar refresh for lead %s failed: %s", lead.id, exc)
        return {"error": str(exc)[:200]}


def for_lead(session: Session, lead: Lead, owner_id) -> list[LeadSignal]:
    """The lead's signals -- ownership-filtered on BOTH the lead and the owner."""
    return list(session.execute(
        select(LeadSignal).where(LeadSignal.lead_id == lead.id,
                                 LeadSignal.user_id == owner_id)
    ).scalars())


def erase_for_lead(session: Session, lead_id) -> int:
    """GDPR: delete every signal derived from this lead (no commit)."""
    result = session.execute(delete(LeadSignal).where(LeadSignal.lead_id == lead_id))
    return int(result.rowcount or 0)


def describe(signal: LeadSignal, now: datetime, *, claim_engine_on: bool = True) -> dict:
    reason = not_citable_reason(signal, now, claim_engine_on=claim_engine_on)
    observed = _aware(signal.observed_at)
    return {
        "id": str(signal.id),
        "type": signal.signal_type,
        "type_label": TYPE_LABELS.get(signal.signal_type, signal.signal_type),
        "source": signal.source,
        "source_label": SOURCE_LABELS.get(signal.source, signal.source),
        "source_name": signal.source_name,
        "source_url": signal.source_url,
        "title": signal.title,
        "excerpt": signal.excerpt,
        "observed_at": observed.isoformat(),
        "expires_at": _aware(signal.expires_at).isoformat(),
        "first_seen_at": _aware(signal.first_seen_at).isoformat(),
        "age_days": max(0, int(age_days(observed, now))),
        "confidence": round(float(signal.confidence or 0), 2),
        "freshness": freshness(signal.signal_type, observed, now),
        "score": score(signal, now),
        "status": status(signal, now),
        "citable": reason is None,
        "not_citable_reason": reason,
    }


def ranked(signals: list[LeadSignal], now: datetime) -> list[LeadSignal]:
    return sorted(signals, key=lambda s: (score(s, now), _aware(s.observed_at)), reverse=True)


# ---------------------------------------------------------------------------
# The send path
# ---------------------------------------------------------------------------


@dataclass
class Pick:
    """What prepare() hands the email render: the prompt block (or ""), the
    offered signal, and every citable signal by evidence key (resolve_citation
    matches against ALL of them, not only the offered one)."""
    block: str = ""
    offered: LeadSignal | None = None
    citable: dict[str, LeadSignal] = field(default_factory=dict)
    now: datetime = field(default_factory=_now)


def _claim_engine_on(session: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(session, "claim_verification_enabled"))


def prompt_block(signal: LeadSignal, now: datetime) -> str:
    observed = _aware(signal.observed_at)
    days = max(0, int(age_days(observed, now)))
    text = signal.title if not signal.excerpt else f"{signal.title}\n{signal.excerpt}"
    publisher = (f" | source: {prompt_safety.inline(signal.source_name)}"
                 if signal.source_name else "")
    return "\n".join([
        "WHY NOW -- ONE DATED SIGNAL FROM A STORED SOURCE. You MAY give this as "
        "the reason for writing now, in one sentence, stating ONLY what the text "
        "below says -- no figure, name or detail it does not contain. Skip it if "
        "it does not plausibly concern this lead's company. Do not present any "
        "other event as the reason for writing now:",
        f"- type: {TYPE_LABELS.get(signal.signal_type, signal.signal_type)} | "
        f"observed {observed.date().isoformat()} ({days} days ago){publisher}",
        # F-AI-02: a headline, summary or post body is written outside this
        # system -- delimited and escaped so it cannot continue as prompt.
        prompt_safety.block(text[:1200]),
    ])


def prepare(session: Session, lead: Lead, *, owner_id, strategy: Strategy | None,
            now: datetime | None = None) -> Pick:
    """Refresh the lead's signals and choose the one to offer. Flag off, claim
    engine off, or no citable signal -> an empty Pick. Never raises."""
    now = now or _now()
    try:
        if not enabled(session) or owner_id is None:
            return Pick(now=now)
        refresh(session, lead, owner_id=owner_id, strategy=strategy, now=now)
        if not _claim_engine_on(session):
            return Pick(now=now)
        citable = {}
        for sig in ranked(for_lead(session, lead, owner_id), now):
            if not_citable_reason(sig, now) is None:
                citable[evidence_key(sig)] = sig
        if not citable:
            return Pick(now=now)
        offered = next(iter(citable.values()))
        return Pick(block=prompt_block(offered, now), offered=offered, citable=citable, now=now)
    except Exception as exc:  # noqa: BLE001 -- see NEVER BLOCKS A SEND
        logger.warning("signal radar prepare for lead %s failed: %s", lead.id, exc)
        return Pick(now=now)


def _summary(signal: LeadSignal, now: datetime) -> dict:
    observed = _aware(signal.observed_at)
    return {"id": str(signal.id), "type": signal.signal_type, "source": signal.source,
            "source_url": signal.source_url, "observed_at": observed.isoformat(),
            "age_days": max(0, int(age_days(observed, now)))}


def resolve_citation(pick: Pick, decisions: list) -> dict | None:
    """The record for messages.personalization_json["signal_radar"], or None
    when nothing was offered. `cited` is set ONLY from a VERIFIED claim-engine
    decision whose evidence item is a citable signal's own stored source."""
    if pick.offered is None:
        return None
    from app.services.claim_verification import VERIFIED  # noqa: PLC0415

    cited = None
    for decision in decisions or []:
        evidence = getattr(decision, "evidence", None)
        if getattr(decision, "verdict", None) != VERIFIED or evidence is None:
            continue
        signal = pick.citable.get(evidence.source)
        if signal is not None:
            cited = {**_summary(signal, pick.now), "evidence_source": evidence.source,
                     "claim": str(decision.claim_text)[:500]}
            break
    return {"offered": _summary(pick.offered, pick.now), "cited": cited}


def extra_evidence(session: Session, lead: Lead, owner_id, now: datetime | None = None) -> list:
    """Claim-engine evidence for the lead's unexpired, linked MARKET-INTEL
    signals (the other sources are already in its ledger). Flag off -> [].
    Kind "news", source "market_intel:<url>" -- what evidence_key() expects."""
    from app.services.claim_verification import Evidence  # noqa: PLC0415

    if owner_id is None or not enabled(session):
        return []
    now = now or _now()
    out = []
    for sig in session.execute(
        select(LeadSignal).where(LeadSignal.lead_id == lead.id,
                                 LeadSignal.user_id == owner_id,
                                 LeadSignal.source == MARKET_INTEL,
                                 LeadSignal.source_url.isnot(None))
    ).scalars():
        if _aware(sig.expires_at) <= now:
            continue
        out.append(Evidence("news", f"market_intel:{sig.source_url}", sig.title))
    return out


# ---------------------------------------------------------------------------
# The success metric
# ---------------------------------------------------------------------------

MIN_COMPARABLE_SENDS = 30


def report(session: Session, user_id, *, days: int = 90, now: datetime | None = None) -> dict:
    """Share of sent emails citing a verified signal under 90 days old, and the
    reply rate split signal vs no-signal. Owner-scoped through
    message -> sequence -> strategy -> product. Reported, never promised."""
    from app.db.models import (  # noqa: PLC0415
        ChannelType, Message, MessageStatus, Outcome, OutcomeEvent, Product, Sequence,
    )

    now = now or _now()
    since = now - timedelta(days=days)
    rows = session.execute(
        select(Message.id, Message.lead_id, Message.sent_at, Message.personalization_json)
        .join(Sequence, Sequence.id == Message.sequence_id)
        .join(Strategy, Strategy.id == Sequence.strategy_id)
        .join(Product, Product.id == Strategy.product_id)
        .where(Product.user_id == user_id,
               Message.channel == ChannelType.EMAIL,
               Message.status.in_((MessageStatus.SENT, MessageStatus.BOUNCED)),
               Message.sent_at.isnot(None), Message.sent_at >= since)
    ).all()

    by_lead: dict = {}
    cited_ids: set = set()
    offered = 0
    for message_id, lead_id, sent_at, personalization in rows:
        by_lead.setdefault(lead_id, []).append((_aware(sent_at), message_id))
        record = (personalization or {}).get("signal_radar") if isinstance(
            personalization, dict) else None
        if isinstance(record, dict):
            offered += 1
            cited = record.get("cited")
            if isinstance(cited, dict) and (cited.get("age_days") or 0) < MAX_AGE_DAYS:
                cited_ids.add(message_id)

    replied: set = set()
    if by_lead:
        message_ids = {mid for sends in by_lead.values() for _, mid in sends}
        for out_lead, out_message, ts in session.execute(
            select(Outcome.lead_id, Outcome.message_id, Outcome.ts)
            .where(Outcome.lead_id.in_(list(by_lead)), Outcome.event == OutcomeEvent.REPLIED,
                   Outcome.channel == "email", Outcome.ts >= since)
        ).all():
            if out_message is not None and out_message in message_ids:
                replied.add(out_message)
                continue
            # No message named: the lead's latest email sent before the reply.
            earlier = [(at, mid) for at, mid in by_lead.get(out_lead, [])
                       if at is not None and at <= _aware(ts)]
            if earlier:
                replied.add(max(earlier, key=lambda pair: pair[0])[1])

    total = len(rows)
    signal_sends = len(cited_ids)
    other_sends = total - signal_sends
    signal_replies = len(replied & cited_ids)
    other_replies = len(replied - cited_ids)

    def _rate(part: int, whole: int) -> float | None:
        return round(part / whole, 4) if whole else None

    comparable = min(signal_sends, other_sends) >= MIN_COMPARABLE_SENDS
    return {
        "days": days,
        "emails_sent": total,
        "emails_offered_a_signal": offered,
        "emails_citing_verified_signal": signal_sends,
        "share_citing_verified_signal": _rate(signal_sends, total),
        "reply_rate": {
            "signal": {"sends": signal_sends, "replies": signal_replies,
                       "rate": _rate(signal_replies, signal_sends)},
            "no_signal": {"sends": other_sends, "replies": other_replies,
                          "rate": _rate(other_replies, other_sends)},
        },
        "comparable": comparable,
        "note": (None if comparable else
                 f"Fewer than {MIN_COMPARABLE_SENDS} sends in a group -- too few to compare. "
                 "Reported, not a promise of lift."),
    }
