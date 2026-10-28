"""Feature Group 1 — real-time competitor and market intelligence.

WHEN
Once per strategy, before pipeline Phase 1 (app/workers/tasks.py::run_pipeline
calls ensure_signals). The result is stored on the strategy and injected into
the Phase 1 (value proposition) and Phase 3 (market segmentation) prompts by
pipeline/engine.py::_context_block. Fetched once rather than per step so
seventy steps do not make seventy sets of network calls, and so every step of
one strategy reasons from the same snapshot.

SOURCES
  Google News   the public RSS search feed (news.google.com/rss/search).
                Needs no key -- which is why it is Google News and not a paid
                news API here -- and returns headline, source, link and date
                for the last 30 days.
  Apollo        organization search for the ICP's industries: recent funding
                rounds and headcount growth, reported by Apollo itself.

SIGNAL CLASSIFICATION IS DETERMINISTIC
funding / hiring / launch are recognised by keyword rules over the headline,
not by a model. It is cheaper, it is testable, and a classifier that
occasionally invents "Acme raised a Series B" from a headline about a product
launch is exactly the fabricated signal this system is not allowed to put in a
prompt. Anything that matches no rule is kept as plain `news`.

The prompt block labels everything as HEADLINES, dated, with sources, and tells
the model to treat them as leads to check rather than facts to quote.

NEVER BLOCKS A STRATEGY
Every failure is recorded in the payload's `errors` list and the pipeline
proceeds -- a strategy without live signals is the strategy it was before
this feature existed.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FlowType, PastClient, Product, Strategy
from app.services import prompt_safety
from app.services.icp_extraction import stored_icp

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
MAX_QUERIES = 3
MAX_SIGNALS = 25
NEWS_WINDOW_DAYS = 30
FUNDING_WINDOW_DAYS = 365

_RULES: list[tuple[str, re.Pattern]] = [
    ("funding", re.compile(
        r"\b(raises?|raised|funding|series [a-f]\b|seed round|pre-seed|"
        r"investment from|backed by|valuation|acquir(es|ed|ition))", re.I)),
    ("hiring", re.compile(
        r"\b(hiring|hires|hired|appoints?|appointed|names .* (ceo|cto|cfo|vp|"
        r"head)|new (ceo|cto|cfo)|expands? (its )?team|headcount|layoffs?|"
        r"job cuts)", re.I)),
    ("launch", re.compile(
        r"\b(launch(es|ed)?|unveils?|introduc(es|ed)|rolls? out|debuts?|"
        r"releases?|announces? new|partners? with|partnership)", re.I)),
]


def classify_headline(title: str) -> str:
    for kind, pattern in _RULES:
        if pattern.search(title or ""):
            return kind
    return "news"


def _http() -> httpx.Client:
    """Factory tests monkeypatch."""
    return httpx.Client(timeout=15.0, follow_redirects=True,
                        headers={"User-Agent": "Vectorae/1.0 market-intel"})


# ---------------------------------------------------------------------------
# What to search for
# ---------------------------------------------------------------------------


def build_queries(session: Session, strategy: Strategy) -> list[str]:
    """The ICP as search queries: the industries past clients came from
    (Flow 1), else the product's most distinctive terms (Flow 2)."""
    from app.services.similarity import tokenize  # noqa: PLC0415

    queries: list[str] = []
    if strategy.flow_type is FlowType.WITH_CLIENTS:
        for client in session.execute(
            select(PastClient).where(PastClient.product_id == strategy.product_id)
        ).scalars():
            industry = str((client.extracted_patterns_json or {}).get("industry") or "").strip()
            if industry and industry.lower() not in (q.lower() for q in queries):
                queries.append(industry)
    icp = stored_icp(strategy)
    for industry in (icp.get("industries") or []):
        if isinstance(industry, str) and industry.lower() not in (q.lower() for q in queries):
            queries.append(industry)
    if not queries:
        product = session.get(Product, strategy.product_id)
        if product is not None:
            tokens = [t for t in tokenize(f"{product.name} {product.description}")
                      if len(t) > 3]
            common = [t for t, _ in Counter(tokens).most_common(4)]
            if common:
                queries.append(" ".join(common[:3]))
    return queries[:MAX_QUERIES]


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def fetch_google_news(query: str, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    url = (f"{GOOGLE_NEWS_RSS}?q={quote_plus(query)}+when:{NEWS_WINDOW_DAYS}d"
           f"&hl=en-US&gl=US&ceid=US:en")
    with _http() as client:
        resp = client.get(url)
    resp.raise_for_status()
    return parse_rss(resp.text, now=now)


def parse_rss(xml_text: str, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    root = ET.fromstring(xml_text)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        published = None
        raw_date = item.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                published = None
        if published and published < now - timedelta(days=NEWS_WINDOW_DAYS):
            continue
        source = item.find("source")
        items.append({
            "title": title[:300],
            "url": (item.findtext("link") or "").strip() or None,
            "source_name": (source.text or "").strip() if source is not None else None,
            "published_at": published.isoformat() if published else None,
        })
    return items


def _apollo_org_search(keywords: list[str]) -> list[dict]:
    """Apollo organization search. Tests monkeypatch this."""
    from app.integrations.apollo import ApolloAdapter  # noqa: PLC0415

    data = ApolloAdapter().call(
        "POST", "/mixed_companies/search",  # TODO: verify against current Apollo docs
        json_body={"q_organization_keyword_tags": keywords, "page": 1, "per_page": 15},
        cache_ttl=86400, cost_units=1,
    )
    return data.get("organizations") or data.get("accounts") or []


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def apollo_signals(orgs: list[dict], now: datetime | None = None) -> list[dict]:
    """Funding and hiring signals from Apollo organization records.
    # TODO: verify against current Apollo docs (field names below)"""
    now = now or datetime.now(timezone.utc)
    out = []
    for org in orgs:
        name = org.get("name")
        if not name:
            continue
        funded = _parse_date(org.get("latest_funding_round_date"))
        if funded and funded >= now - timedelta(days=FUNDING_WINDOW_DAYS):
            stage = org.get("latest_funding_stage") or "a funding round"
            amount = org.get("total_funding_printed")
            out.append({
                "type": "funding", "company": name,
                "title": f"{name} closed {stage}" + (f" (total raised {amount})" if amount else ""),
                "source": "apollo", "url": org.get("website_url"),
                "published_at": funded.isoformat(),
            })
        growth = org.get("organization_headcount_six_month_growth")
        try:
            growth = float(growth) if growth is not None else None
        except (TypeError, ValueError):
            growth = None
        if growth is not None and growth >= 0.10:
            out.append({
                "type": "hiring", "company": name,
                "title": f"{name} headcount up {growth:.0%} in six months",
                "source": "apollo", "url": org.get("website_url"),
                "published_at": None,
            })
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def gather(session: Session, strategy: Strategy, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    queries = build_queries(session, strategy)
    signals: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()

    for query in queries:
        try:
            for item in fetch_google_news(query, now=now):
                key = item["title"].lower()
                if key in seen:
                    continue
                seen.add(key)
                signals.append({"type": classify_headline(item["title"]),
                                "company": item.get("source_name"),
                                "title": item["title"], "source": "google_news",
                                "url": item.get("url"),
                                "published_at": item.get("published_at"),
                                "query": query})
        except Exception as exc:  # noqa: BLE001 -- recorded, never raised
            errors.append(f"google_news[{query}]: {type(exc).__name__}: {exc}"[:300])

    if queries:
        try:
            for sig in apollo_signals(_apollo_org_search(queries), now=now):
                if sig["title"].lower() not in seen:
                    seen.add(sig["title"].lower())
                    signals.append(sig)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"apollo: {type(exc).__name__}: {exc}"[:300])

    # Specific signals first (they are what the prompts can use), news last.
    order = {"funding": 0, "hiring": 1, "launch": 2, "news": 3}
    signals.sort(key=lambda s: (order.get(s["type"], 9), s.get("published_at") or ""),
                 reverse=False)
    return {"queries": queries, "fetched_at": now.isoformat(),
            "signals": signals[:MAX_SIGNALS], "errors": errors}


def ensure_signals(session: Session, strategy: Strategy, *, force: bool = False,
                   now: datetime | None = None) -> dict | None:
    """Fetch once per strategy (or again with force). Never raises."""
    from app.services import system_settings  # noqa: PLC0415

    try:
        if not force and strategy.market_signals_fetched_at is not None:
            return strategy.market_signals_json
        if not system_settings.get(session, "competitor_intel_enabled"):
            return None
        payload = gather(session, strategy, now=now)
        strategy.market_signals_json = payload
        strategy.market_signals_fetched_at = now or datetime.now(timezone.utc)
        session.commit()
        return payload
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("market intel for strategy %s failed: %s", strategy.id, exc)
        return None


def signals_block(strategy: Strategy) -> str | None:
    """The prompt block for Phase 1 and 3, or None when there is nothing."""
    payload = strategy.market_signals_json or {}
    signals = payload.get("signals") or []
    if not signals:
        return None
    labels = {"funding": "Funding", "hiring": "Hiring / leadership",
              "launch": "Product launches / partnerships", "news": "Other news"}
    # Headlines, publisher names and dates are written by outsiders: each one
    # goes into the prompt only inside prompt_safety markers, under NOTICE.
    # The queries came from past-client data and earlier model output, so
    # they are wrapped too.
    queries = ", ".join(str(q) for q in (payload.get("queries") or []))
    lines = [f"[Live market signals — fetched {str(payload.get('fetched_at'))[:10]} "
             f"for: {prompt_safety.inline(queries)}]",
             "These are HEADLINES, not verified facts. Use them to spot buying "
             "triggers and competitor moves; do not quote a figure from them as "
             "established.",
             prompt_safety.NOTICE]
    for kind in ("funding", "hiring", "launch", "news"):
        group = [s for s in signals if s.get("type") == kind][:8]
        if not group:
            continue
        lines.append(f"{labels[kind]}:")
        for sig in group:
            date = f" ({str(sig['published_at'])[:10]})" if sig.get("published_at") else ""
            src = f" — {sig['company']}" if sig.get("company") and sig["source"] == "google_news" else ""
            lines.append(f"- {prompt_safety.inline(str(sig.get('title') or '') + src + date)}")
    return "\n".join(lines)
