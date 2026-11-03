"""Feature Group 2 — the lead's own recent words and their company's news.

ensure_fresh() runs in the send path, just before an email is rendered
(outreach_tasks._render_email_outbound), and refreshes what is stale:
  company news    cached 3 days   (NewsAPI, last 30 days, must name the company)

LinkedIn posts are NOT fetched (founder decision 2026-11-02, evals/vectorae):
reading other members' posts needs LinkedIn partner access Vectorae does not
have; the Unipile / RapidAPI readers were removed and
scripts/linkedin_unipile_offboard.py purges what they stored.

Fetching at SEND time rather than at sourcing time is deliberate. A sequence's
step 3 goes out a week after step 1; posts fetched at sourcing would be two
weeks stale by then, and "your post last week" about a month-old post is the
opposite of personal. The cache keeps it to one fetch per lead per window.

NEVER BLOCKS A SEND. An unconfigured provider is skipped silently (nothing is
stamped, so configuring it later takes effect immediately); a failing one is
logged and the email is written without that input. An email without a post
hook is an ordinary email; an email that did not send because a scraper
timed out is a lost touch.

prompt_block() renders what was fetched for the personalization prompt, with
the instruction that the FIRST line must reference a specific detail from a
post (accurately -- never invent or embellish one) and that news goes in the
hook only if it plausibly concerns this lead's company.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.models import Lead
from app.services import prompt_safety

logger = logging.getLogger(__name__)

POSTS_TTL = timedelta(days=7)
NEWS_TTL = timedelta(days=3)
NEWS_WINDOW = timedelta(days=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def linkedin_url_from(payload: dict | None) -> str | None:
    """Apollo puts it at person.linkedin_url; raw search rows at top level."""
    if not isinstance(payload, dict):
        return None
    person = payload.get("person") if isinstance(payload.get("person"), dict) else {}
    url = payload.get("linkedin_url") or person.get("linkedin_url")
    return str(url)[:500] if url else None


def linkedin_url_for(lead: Lead) -> str | None:
    if lead.linkedin_url:
        return lead.linkedin_url
    enrichment = lead.enrichment_json if isinstance(lead.enrichment_json, dict) else {}
    return (linkedin_url_from(enrichment.get("enrichment"))
            or linkedin_url_from(enrichment.get("raw"))
            or linkedin_url_from(enrichment))


def _stale(fetched_at: datetime | None, ttl: timedelta, now: datetime) -> bool:
    fetched_at = _aware(fetched_at)
    return fetched_at is None or fetched_at <= now - ttl


def ensure_fresh(session: Session, lead: Lead, *, owner_id=None, force: bool = False,
                 now: datetime | None = None) -> dict:
    """Refresh stale posts/news on `lead`. Returns what happened. Never raises."""
    from app.integrations import newsapi  # noqa: PLC0415
    from app.services import credentials, system_settings  # noqa: PLC0415

    now = now or _now()
    result = {"posts": "skipped", "news": "skipped"}
    # Local business mode: a business's imported customer is a consumer --
    # their posts and news are never fetched from any provider.
    from app.services.local_business import is_customer  # noqa: PLC0415

    if is_customer(lead):
        return {"posts": "skipped_customer", "news": "skipped_customer"}
    try:
        if not lead.linkedin_url:
            lead.linkedin_url = linkedin_url_for(lead)

        key = credentials.get_secret(session, "newsapi", "api_key", user_id=owner_id)
        if (key and lead.company and system_settings.get(session, "company_news_enabled")
                and (force or _stale(lead.company_news_fetched_at, NEWS_TTL, now))):
            try:
                news = newsapi.fetch_company_news(key, lead.company, now=now)
                lead.company_news_json = news
                lead.company_news_fetched_at = now
                result["news"] = f"fetched:{len(news)}"
                if news:
                    from app.services import provenance  # noqa: PLC0415

                    provenance.record(session, lead, "intent_signal",
                                      provenance.COMPANY_NEWS,
                                      detail=f"{len(news)} article(s) in the last 30 days",
                                      observed_at=now, commit=False)
            except Exception as exc:  # noqa: BLE001
                result["news"] = f"unavailable: {exc}"[:200]
        session.commit()
    except Exception as exc:  # noqa: BLE001 -- see the module docstring
        session.rollback()
        logger.warning("personalization refresh for lead %s failed: %s", lead.id, exc)
        result["error"] = str(exc)[:200]
    return result


def _recent_news(lead: Lead, now: datetime) -> list[dict]:
    out = []
    for item in lead.company_news_json or []:
        if not isinstance(item, dict) or not item.get("headline"):
            continue
        try:
            when = datetime.fromisoformat(str(item.get("published_at")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if when >= now - NEWS_WINDOW:
            out.append(item)
    return out[:2]


def prompt_block(lead: Lead, now: datetime | None = None) -> tuple[str, dict]:
    """(text for the personalization prompt, which inputs it used)."""
    now = now or _now()
    parts: list[str] = []
    used: dict = {"linkedin_post_url": None, "news_url": None}

    posts = [p for p in (lead.linkedin_posts_json or [])
             if isinstance(p, dict) and (p.get("text") or "").strip()][:3]
    if posts:
        lines = ["RECENT LINKEDIN POSTS BY THIS LEAD (their own words). The FIRST "
                 "LINE of the email must reference one specific detail from one of "
                 "these, accurately -- never invent, embellish or misattribute a "
                 "post, and do not quote more than a few words:"]
        for i, post in enumerate(posts, 1):
            date = f"({str(post.get('posted_at'))[:10]}) " if post.get("posted_at") else ""
            # F-AI-02: the post body is written by the prospect. Delimited and
            # escaped so it cannot close its own block and continue as prompt.
            lines.append(f"{i}. {date}{prompt_safety.block(post['text'][:700])}")
        parts.append("\n".join(lines))
        used["linkedin_post_url"] = posts[0].get("url") or "unknown"

    news = _recent_news(lead, now)
    if news:
        lines = ["RECENT NEWS ABOUT THEIR COMPANY (last 30 days). Reference it "
                 "naturally in the opening hook if it plausibly concerns this "
                 "lead's company -- a same-name company is possible, so skip it "
                 "if it does not fit. Congratulate only on clearly good news:"]
        for item in news:
            # F-AI-02: headline, summary and source name all come from the feed,
            # which is outside this system. Folded onto one line each so a
            # newline inside them cannot start a new 'section' of the prompt.
            src = (f" ({prompt_safety.inline(item.get('source'))}, "
                   f"{str(item.get('published_at'))[:10]})")
            lines.append(f"- {prompt_safety.inline(item['headline'])} — "
                         f"{prompt_safety.inline(item.get('summary') or '')}{src}")
        parts.append("\n".join(lines))
        used["news_url"] = news[0].get("url") or "unknown"

    if not parts:
        return "", used

    # One notice for the whole block, ahead of everything it governs. Our own
    # instructions above are deliberately NOT wrapped -- wrapping trusted text
    # would tell the model to disregard it.
    return f"{prompt_safety.NOTICE}\n\n" + "\n\n".join(parts), used
