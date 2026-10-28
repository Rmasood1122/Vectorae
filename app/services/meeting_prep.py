"""Feature Group 7 — the automated meeting preparation brief.

WHEN IT RUNS
A lead reaches meeting_booked: a Calendly `invitee.created` delivery
(app/api/webhooks.py) or a booking on Vectorae's own calendar
(app/workers/calendar_tasks.py). Both call
app/workers/meeting_prep_tasks.py::request_prep, which writes a PENDING brief
row and enqueues generation on the `notifications` queue -- so a slow model
call never delays the webhook acknowledgement or the booking confirmation,
and never queues behind a 144-step strategy pipeline on the `pipeline` queue.

WHAT GOES IN, AND THE RULE ABOUT WHAT DOES NOT
Everything already known about this lead: the lead row and its Apollo
enrichment, LinkedIn posts and company news when Feature Group 2 fetched them,
every message we sent and the reply that converted them, the booking page's
answers, earlier meeting summaries, the strategy document, its ICP, and the
competitor steps of the research pipeline. Nothing else.

The system prompt forbids stating a fact that is not in that material, and the
one section that is PURE fact -- the lead profile -- is not written by the
model at all. `lead_profile` copies it from the records, because a model
paraphrasing an email address, a job title or a headcount can only ever make
it wrong. Every other section is generated.

"Likely pain points" are HYPOTHESES from the ICP and the strategy -- the prompt
requires each to say what it is inferred from, and the rendered brief labels
the section that way. The same rule the outreach personalizer follows for pain
signals applies here: a guess must never read as something the prospect said.

FAILURE
generate_brief never raises on a bad model answer. It marks the brief FAILED
with the reason and the lead page offers Regenerate. The booking that
triggered it is already committed and already notified; a missing brief is a
button to press, not a lost meeting.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    CalendarBooking,
    CrmActivityKind,
    InboundReply,
    Lead,
    Meeting,
    MeetingPrepBrief,
    MeetingPrepStatus,
    Message,
    MessageStatus,
    Product,
    ResearchStep,
    Strategy,
)
from app.services import anthropic_client, prompt_safety

logger = logging.getLogger(__name__)

SOURCE_CALENDLY = "calendly"
SOURCE_CALENDAR = "leadpilot_calendar"
SOURCE_MANUAL = "manual"

SYSTEM = (
    "You are Vectorae's meeting prep strategist. A seller is about to take a "
    "sales call with a prospect who booked a meeting. From ONLY the material "
    "provided, write the preparation the seller reads in the ten minutes "
    "before the call. Respond with ONLY a JSON object -- no prose, no "
    "markdown fences -- with exactly these keys:\n"
    '{"company_overview": "...", "recent_activity": "...", '
    '"why_they_booked": "...", "pain_points": ["..."], '
    '"likely_objections": [{"objection": "...", "response": "..."}], '
    '"talking_points": ["..."], "discovery_questions": ["..."], '
    '"competitive_landscape": "...", "next_steps": ["..."], '
    '"deal_structure": "...", "opening_60_seconds": "..."}\n'
    "RULES. Never state a fact, name, number, date or quote that is not in "
    "the material. When the material does not cover a section, say so "
    "plainly in that section (e.g. 'Nothing in our records about their "
    "funding.') instead of filling it. `why_they_booked` must point at the "
    "specific message or reply that converted them and quote their own words "
    "when a reply exists. `pain_points` are hypotheses: each one must say what "
    "it is inferred from (the F-P-T-A problem evidence when there is any, "
    "otherwise the ICP, the strategy, a post, their reply). "
    "`likely_objections` pairs each objection with a short, honest response. "
    "`discovery_questions` are open questions specific to THIS prospect, at "
    "most eight. `deal_structure` suggests how to package and price the "
    "offer, using only pricing that appears in the strategy. "
    "`opening_60_seconds` is one paragraph the seller speaks aloud, in the "
    "first person, under 150 words, that references why they booked. Plain "
    "text inside every string; no Markdown syntax."
)

_TEXT_KEYS = ("company_overview", "recent_activity", "why_they_booked",
              "competitive_landscape", "deal_structure", "opening_60_seconds")
_LIST_KEYS = {"pain_points": 8, "talking_points": 8,
              "discovery_questions": 8, "next_steps": 6}


# Output contract (Phase 3 item 2): every key clean_sections reads.
BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        **{key: {"type": "string"} for key in _TEXT_KEYS},
        **{key: {"type": "array", "items": {"type": "string"}} for key in _LIST_KEYS},
        "likely_objections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"objection": {"type": "string"},
                               "response": {"type": "string"}},
                "required": ["objection", "response"],
                "additionalProperties": False,
            },
        },
    },
    "required": [],
    "additionalProperties": False,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Facts from the records (never model-written)
# ---------------------------------------------------------------------------


def _person(enrichment: dict) -> dict:
    person = enrichment.get("person")
    return person if isinstance(person, dict) else {}


def _organization(enrichment: dict) -> dict:
    """Apollo's people/match payload nests the company under
    person.organization; other sources put it at the top level. Tolerant of
    both, because enrichment_json is whatever the provider returned."""
    org = _person(enrichment).get("organization") or enrichment.get("organization")
    return org if isinstance(org, dict) else {}


def _funding(org: dict) -> str | None:
    parts = []
    if org.get("latest_funding_stage"):
        parts.append(str(org["latest_funding_stage"]))
    total = org.get("total_funding_printed") or org.get("total_funding")
    if total:
        parts.append(f"total raised {total}")
    if org.get("latest_funding_round_date"):
        parts.append(f"latest round {str(org['latest_funding_round_date'])[:10]}")
    return ", ".join(parts) or None


def lead_profile(lead: Lead) -> dict:
    """The profile section, copied from the records. See the module docstring."""
    enrichment = lead.enrichment_json if isinstance(lead.enrichment_json, dict) else {}
    person = _person(enrichment)
    org = _organization(enrichment)
    location = ", ".join(str(p) for p in (person.get("city"), person.get("state"),
                                          person.get("country")) if p) or None
    return {
        "name": lead.full_name,
        "title": lead.title or person.get("title"),
        "company": lead.company or org.get("name"),
        "email": lead.email,
        "phone": lead.phone,
        "linkedin_url": (getattr(lead, "linkedin_url", None)
                         or person.get("linkedin_url")
                         or enrichment.get("linkedin_url")),
        "location": location,
        "industry": org.get("industry"),
        "company_size": org.get("estimated_num_employees"),
        "company_website": (org.get("website_url") or org.get("primary_domain")
                            or enrichment.get("company_domain")),
        "founded_year": org.get("founded_year"),
        "funding": _funding(org),
        "status": lead.status.value if lead.status else None,
    }


def _linkedin_posts(lead: Lead) -> list[dict]:
    """Feature Group 2 stores fetched posts on the lead; older leads may carry
    them in enrichment. Either way, at most the three most recent."""
    posts = getattr(lead, "linkedin_posts_json", None)
    if not posts and isinstance(lead.enrichment_json, dict):
        posts = lead.enrichment_json.get("linkedin_posts")
    if not isinstance(posts, list):
        return []
    out = []
    for post in posts[:3]:
        if isinstance(post, dict) and (post.get("text") or "").strip():
            out.append({"text": str(post["text"])[:1500],
                        "posted_at": post.get("posted_at"),
                        "url": post.get("url")})
    return out


def _company_news(lead: Lead) -> list[dict]:
    news = getattr(lead, "company_news_json", None)
    if not news and isinstance(lead.enrichment_json, dict):
        news = lead.enrichment_json.get("company_news")
    if isinstance(news, dict):
        news = [news]
    if not isinstance(news, list):
        return []
    return [{"headline": n.get("headline") or n.get("title"),
             "summary": n.get("summary") or n.get("description"),
             "published_at": n.get("published_at"), "url": n.get("url")}
            for n in news[:3] if isinstance(n, dict)]


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------


def _fpta_block(lead: Lead) -> dict:
    """The four sub-scores with their reasons and the evidence behind each.

    Returns {} when the prospect was never scored -- which the prompt renders
    as "(none on record)" rather than as a zero, because "not scored" and
    "scored badly" are different facts and the brief must not confuse them.
    """
    reasons = lead.fpta_reasons_json if isinstance(lead.fpta_reasons_json, dict) else {}
    if not reasons:
        return {}
    return {
        "overall": lead.fpta_overall,
        "dimensions": {
            name: {"score": entry.get("score"), "reason": entry.get("reason"),
                   "evidence": entry.get("signals") or []}
            for name, entry in reasons.items() if isinstance(entry, dict)
        },
    }


def collect_context(db: Session, lead: Lead, brief: MeetingPrepBrief | None) -> dict:
    """Everything the prompt may use, as plain data (JSON-safe)."""
    strategy = db.get(Strategy, lead.strategy_id)
    product = db.get(Product, strategy.product_id) if strategy else None

    sent = db.execute(
        select(Message)
        .where(Message.lead_id == lead.id,
               Message.status.in_([MessageStatus.SENT, MessageStatus.BOUNCED]))
        .order_by(Message.sent_at)
    ).scalars().all()
    replies = db.execute(
        select(InboundReply).where(InboundReply.lead_id == lead.id)
        .order_by(InboundReply.created_at)
    ).scalars().all()

    booking = (db.get(CalendarBooking, brief.booking_id)
               if brief is not None and brief.booking_id else None)
    competitor_steps = []
    if strategy is not None:
        competitor_steps = db.execute(
            select(ResearchStep)
            .where(ResearchStep.strategy_id == strategy.id,
                   ResearchStep.name.ilike("%compet%"))
            .order_by(ResearchStep.step_no).limit(3)
        ).scalars().all()
    past_meetings = db.execute(
        select(Meeting).where(Meeting.lead_id == lead.id,
                              Meeting.summary.isnot(None))
        .order_by(Meeting.start_at.desc()).limit(2)
    ).scalars().all()

    return {
        "profile": lead_profile(lead),
        "meeting": {
            "start_at": (_aware(brief.meeting_start_at).isoformat()
                         if brief is not None and brief.meeting_start_at else None),
            "url": brief.meeting_url if brief is not None else None,
            "source": brief.source if brief is not None else SOURCE_MANUAL,
            "booking_answers": (booking.answers or {}) if booking else {},
            "booking_notes": booking.notes if booking else None,
        },
        "linkedin_posts": _linkedin_posts(lead),
        "company_news": _company_news(lead),
        "enrichment": lead.enrichment_json if isinstance(lead.enrichment_json, dict) else {},
        "messages_sent": [
            {"step_no": m.step_no, "channel": m.channel.value if m.channel else None,
             "subject": m.subject, "body": (m.body or "")[:1200],
             "sent_at": _aware(m.sent_at).isoformat() if m.sent_at else None}
            for m in sent[-4:]
        ],
        "replies": [
            {"classification": r.classification, "channel": r.channel,
             "body": (r.body or "")[:1500],
             "received_at": (_aware(r.received_at or r.created_at).isoformat()
                             if (r.received_at or r.created_at) else None)}
            for r in replies[-3:]
        ],
        "product": ({"name": product.name, "description": product.description}
                    if product else None),
        "icp": (strategy.pattern_inputs_json if strategy else None) or {},
        # Part 2: the F-P-T-A signals (Part 1 Feature 2). The pains the seller
        # should probe for are exactly the evidence the problem sub-score was
        # built from, and the timing sub-score says whether this is urgent for
        # them -- so the brief reads the same signals the prospect was ranked
        # on rather than re-deriving a second, conflicting opinion.
        "fpta": _fpta_block(lead),
        "strategy_document": ((strategy.strategy_document or "")[:12_000]
                              if strategy else ""),
        "competitor_research": [
            {"name": s.name, "output": (s.output or "")[:2500]}
            for s in competitor_steps
        ],
        "past_meetings": [
            {"start_at": _aware(m.start_at).isoformat() if m.start_at else None,
             "summary": (m.summary or "")[:1500]}
            for m in past_meetings
        ],
    }


def _block(title: str, value: Any) -> str:
    if value in (None, "", [], {}):
        return f"## {title}\n(none on record)\n"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, indent=1, default=str)
    return f"## {title}\n{value}\n"


def _external(title: str, value: Any) -> str:
    """A section whose content came from outside (the prospect, a provider,
    a scraped page, or model output derived from those): the same layout as
    _block, but the content sits inside a prompt_safety block."""
    if value in (None, "", [], {}):
        return f"## {title}\n(none on record)\n"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, indent=1, default=str)
    return f"## {title}\n{prompt_safety.block(str(value))}\n"


def build_prompt(ctx: dict) -> str:
    enrichment = json.dumps(ctx["enrichment"], default=str)[:4000]
    # Outside text goes through _external; our own records of what we sent,
    # the product, ICP and strategy stay plain (wrapping trusted text would
    # tell the model to disregard it).
    return "\n".join([
        prompt_safety.NOTICE + "\n",
        _external("LEAD PROFILE (from our records)", ctx["profile"]),
        _external("THE MEETING", ctx["meeting"]),
        _external("THEIR MOST RECENT LINKEDIN POSTS", ctx["linkedin_posts"]),
        _external("RECENT COMPANY NEWS", ctx["company_news"]),
        _external("RAW ENRICHMENT DATA (provider payload, truncated)", enrichment),
        _block("MESSAGES WE SENT THEM (oldest first)", ctx["messages_sent"]),
        _external("THEIR REPLIES (oldest first)", ctx["replies"]),
        _external("EARLIER MEETINGS WITH THEM", ctx["past_meetings"]),
        _block("WHAT THE SELLER SELLS", ctx["product"]),
        _block("IDEAL CUSTOMER PROFILE", ctx["icp"]),
        _external("F-P-T-A SIGNALS (fit / problem / timing / access, with the "
                  "evidence each score was built from)", ctx.get("fpta")),
        _block("STRATEGY DOCUMENT (truncated)", ctx["strategy_document"]),
        _external("COMPETITOR RESEARCH", ctx["competitor_research"]),
        "Return the JSON object now.",
    ])


# ---------------------------------------------------------------------------
# Model output -> sections
# ---------------------------------------------------------------------------


def _clean_text(value: Any, limit: int = 3000) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""


def _clean_list(value: Any, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [item.strip()[:600] for item in value
            if isinstance(item, str) and item.strip()][:limit]


def clean_sections(data: Any) -> dict:
    """The model's JSON normalised to exactly the keys the brief renders.

    Anything malformed is dropped rather than coerced: a list of objects
    rendered as a bullet list is how "[object Object]" reaches a user.
    """
    data = data if isinstance(data, dict) else {}
    out: dict[str, Any] = {k: _clean_text(data.get(k)) for k in _TEXT_KEYS}
    for key, limit in _LIST_KEYS.items():
        out[key] = _clean_list(data.get(key), limit)
    objections = []
    for item in data.get("likely_objections") or []:
        if isinstance(item, str) and item.strip():
            item = {"objection": item, "response": ""}
        if not isinstance(item, dict):
            continue
        objection = _clean_text(item.get("objection"), 400)
        if objection:
            objections.append({"objection": objection,
                               "response": _clean_text(item.get("response"), 800)})
    out["likely_objections"] = objections[:6]
    return out


def ground_sections(sections: dict, ctx: dict, *, brief_id=None) -> dict:
    """`sections` without what the records do not support.

    The seller reads the brief right before the call and repeats it. A price
    the strategy never named or a quote the prospect never wrote is worse
    than a gap, so a sentence (in a text section) or an item (in a list) that
    states a number, round, offer or deadline, or quotes a passage, absent
    from everything the model was given is removed. Deterministic; logged."""
    from app.services import grounding  # noqa: PLC0415

    source = json.dumps(ctx, default=str)
    removed: list[str] = []
    out = dict(sections)
    for key in _TEXT_KEYS:
        out[key], reasons = grounding.drop_ungrounded_sentences(out.get(key), source)
        removed.extend(f"{key}: {r}" for r in reasons)
    for key in _LIST_KEYS:
        kept = []
        for item in out.get(key) or []:
            text, reasons = grounding.drop_ungrounded_sentences(item, source)
            if reasons:
                removed.extend(f"{key}: {r}" for r in reasons)
            if text and not reasons:
                kept.append(item)
        out[key] = kept
    objections = []
    for item in out.get("likely_objections") or []:
        joined = f"{item.get('objection', '')} {item.get('response', '')}"
        reasons = (grounding.problems(joined, source)
                   + grounding.unsupported_quotes(joined, source))
        if reasons:
            removed.extend(f"likely_objections: {r}" for r in reasons)
        else:
            objections.append(item)
    out["likely_objections"] = objections
    if removed:
        logger.warning("meeting prep brief %s: removed ungrounded content: %s",
                       brief_id, "; ".join(removed)[:2000])
    return out


def _fmt_when(start: datetime | None) -> str:
    if start is None:
        return "Time not on record"
    return _aware(start).strftime("%A %d %B %Y, %H:%M UTC")


_PROFILE_LABELS = (
    ("name", "Name"), ("title", "Title"), ("company", "Company"),
    ("email", "Email"), ("phone", "Phone"), ("linkedin_url", "LinkedIn"),
    ("location", "Location"), ("industry", "Industry"),
    ("company_size", "Company size"), ("company_website", "Website"),
    ("founded_year", "Founded"), ("funding", "Funding"),
)


def render_markdown(profile: dict, sections: dict, ctx: dict,
                    brief: MeetingPrepBrief | None) -> str:
    name = profile.get("name") or profile.get("email") or "Prospect"
    company = profile.get("company")
    lines = [f"# Meeting prep: {name}" + (f" — {company}" if company else ""), ""]
    when = _fmt_when(brief.meeting_start_at if brief else None)
    where = (brief.meeting_url if brief and brief.meeting_url else None)
    lines += [f"**When:** {when}" + (f"  ·  **Join:** {where}" if where else ""), ""]

    lines += ["## Lead profile", "", "| | |", "|---|---|"]
    for key, label in _PROFILE_LABELS:
        value = profile.get(key)
        if value not in (None, ""):
            lines.append(f"| {label} | {str(value).replace('|', '/')} |")
    lines.append("")

    def para(title: str, key: str) -> None:
        lines.extend([f"## {title}", "", sections.get(key) or "_Not covered._", ""])

    def bullets(title: str, key: str, note: str | None = None) -> None:
        lines.extend([f"## {title}", ""])
        if note:
            lines.extend([f"_{note}_", ""])
        items = sections.get(key) or []
        lines.extend([f"- {item}" for item in items] or ["_Not covered._"])
        lines.append("")

    para("Company overview", "company_overview")

    lines += ["## Recent LinkedIn activity", ""]
    posts = ctx.get("linkedin_posts") or []
    for post in posts:
        dated = f" ({str(post['posted_at'])[:10]})" if post.get("posted_at") else ""
        lines.append(f"- {post['text'][:400]}{dated}")
    if not posts:
        lines.append("_No LinkedIn posts on record._")
    if sections.get("recent_activity"):
        lines += ["", sections["recent_activity"]]
    lines.append("")

    para("Why they booked", "why_they_booked")
    bullets("Likely pain points", "pain_points",
            note="Hypotheses from the ICP, the strategy and their activity -- "
                 "confirm on the call before you lean on any of them.")

    lines += ["## Likely objections", ""]
    for item in sections.get("likely_objections") or []:
        lines.append(f"- **{item['objection']}** — {item['response'] or 'Listen first.'}")
    if not sections.get("likely_objections"):
        lines.append("_Not covered._")
    lines.append("")

    bullets("Talking points", "talking_points")
    bullets("Discovery questions", "discovery_questions")
    para("Competitive landscape", "competitive_landscape")
    bullets("Recommended next steps", "next_steps")
    para("Deal structure", "deal_structure")
    lines += ["## Your opening 60 seconds", "",
              f"> {sections.get('opening_60_seconds') or 'Not generated.'}", ""]
    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------------
# Brief lifecycle
# ---------------------------------------------------------------------------


def upsert_brief(db: Session, lead: Lead, *, user_id: uuid.UUID, source: str,
                 external_ref: str | None, meeting_start_at: datetime | None = None,
                 meeting_url: str | None = None, booking_id: uuid.UUID | None = None,
                 meeting_id: uuid.UUID | None = None) -> MeetingPrepBrief:
    """Find-or-create the brief for one booking. Does NOT commit.

    A reschedule (same booking, new time) moves meeting_start_at and re-arms
    both reminders; otherwise a meeting moved from Friday to Monday would have
    had its day-before reminder fire on Thursday and never again.
    """
    query = select(MeetingPrepBrief).where(
        MeetingPrepBrief.lead_id == lead.id,
        MeetingPrepBrief.source == source,
    )
    query = (query.where(MeetingPrepBrief.external_ref.is_(None)) if external_ref is None
             else query.where(MeetingPrepBrief.external_ref == external_ref))
    brief = db.execute(query).scalars().first()
    start = _aware(meeting_start_at)
    if brief is None:
        brief = MeetingPrepBrief(
            lead_id=lead.id, user_id=user_id, source=source,
            external_ref=external_ref, meeting_start_at=start,
            meeting_url=meeting_url, booking_id=booking_id,
            meeting_id=meeting_id, status=MeetingPrepStatus.PENDING,
        )
        db.add(brief)
        db.flush()
        return brief

    if start is not None and _aware(brief.meeting_start_at) != start:
        brief.meeting_start_at = start
        brief.reminder_24h_sent_at = None
        brief.reminder_1h_sent_at = None
    brief.meeting_url = meeting_url or brief.meeting_url
    brief.booking_id = booking_id or brief.booking_id
    brief.meeting_id = meeting_id or brief.meeting_id
    brief.cancelled_at = None
    return brief


def cancel_briefs(db: Session, lead_id: uuid.UUID, *, source: str | None = None,
                  external_ref: str | None = None,
                  booking_id: uuid.UUID | None = None,
                  now: datetime | None = None) -> int:
    """Stop the reminders for a cancelled meeting. The brief itself is kept --
    it is still true, and a rebooked call reuses it. Does NOT commit."""
    query = select(MeetingPrepBrief).where(MeetingPrepBrief.lead_id == lead_id,
                                           MeetingPrepBrief.cancelled_at.is_(None))
    if source:
        query = query.where(MeetingPrepBrief.source == source)
    if external_ref:
        query = query.where(MeetingPrepBrief.external_ref == external_ref)
    if booking_id:
        query = query.where(MeetingPrepBrief.booking_id == booking_id)
    rows = db.execute(query).scalars().all()
    for row in rows:
        row.cancelled_at = now or _now()
    return len(rows)


def latest_for_lead(db: Session, lead_id: uuid.UUID,
                    now: datetime | None = None) -> MeetingPrepBrief | None:
    """The brief the lead page should show: the next live meeting's, else the
    most recently created one."""
    now = now or _now()
    upcoming = db.execute(
        select(MeetingPrepBrief)
        .where(MeetingPrepBrief.lead_id == lead_id,
               MeetingPrepBrief.cancelled_at.is_(None),
               MeetingPrepBrief.meeting_start_at >= now - timedelta(hours=2))
        .order_by(MeetingPrepBrief.meeting_start_at)
    ).scalars().first()
    if upcoming is not None:
        return upcoming
    return db.execute(
        select(MeetingPrepBrief).where(MeetingPrepBrief.lead_id == lead_id)
        .order_by(MeetingPrepBrief.created_at.desc())
    ).scalars().first()


def generate_brief(db: Session, brief_id: uuid.UUID, *, notify: bool = True,
                   now: datetime | None = None) -> str:
    """Generate (or regenerate) one brief. Returns a status string; never
    raises on a model failure -- see the module docstring."""
    from app.services import crm_events  # noqa: PLC0415

    now = now or _now()
    brief = db.get(MeetingPrepBrief, brief_id)
    if brief is None:
        return "missing"
    lead = db.get(Lead, brief.lead_id)
    if lead is None:
        return "missing_lead"
    # Local business mode: no research brief about a business's own customer
    # (a consumer) -- no model call, no LinkedIn or news lookup.
    from app.services.local_business import is_customer  # noqa: PLC0415

    if is_customer(lead):
        brief.status = MeetingPrepStatus.FAILED
        brief.error = "not prepared: briefs are not written about a business's own customers"
        db.commit()
        return "refused"

    brief.status = MeetingPrepStatus.GENERATING
    brief.error = None
    db.commit()

    ctx = collect_context(db, lead, brief)
    try:
        data = anthropic_client.get_client().complete_json(
            system=SYSTEM, prompt=build_prompt(ctx),
            max_tokens=settings.meeting_prep_max_tokens,
            schema=BRIEF_SCHEMA,
        )
    except Exception as exc:  # noqa: BLE001 -- see the module docstring
        logger.warning("meeting prep generation failed for brief %s: %s: %s",
                       brief.id, type(exc).__name__, exc)
        brief.status = MeetingPrepStatus.FAILED
        brief.error = f"{type(exc).__name__}: {exc}"[:2000]
        db.commit()
        return "failed"

    sections = ground_sections(clean_sections(data), ctx, brief_id=brief.id)
    if not (sections["why_they_booked"] or sections["opening_60_seconds"]
            or sections["talking_points"]):
        brief.status = MeetingPrepStatus.FAILED
        brief.error = "the model returned an empty brief"
        db.commit()
        return "failed"

    brief.sections_json = sections
    # The posts and news are stored with the profile, verbatim, so the lead
    # page can show what they actually wrote next to the model's reading of
    # it -- the same facts-from-the-records rule as the profile itself.
    brief.profile_json = {**ctx["profile"],
                          "linkedin_posts": ctx["linkedin_posts"],
                          "company_news": ctx["company_news"]}
    brief.opening_script = sections["opening_60_seconds"] or None
    brief.content_md = render_markdown(ctx["profile"], sections, ctx, brief)
    brief.status = MeetingPrepStatus.READY
    brief.generated_at = now
    brief.model = settings.anthropic_model
    # Part 2: seed the editable call script from the sections just written --
    # and ONLY if the seller has not already written one. `ensure_script`
    # never overwrites, which is the rule the whole feature rests on: an edit
    # a regeneration silently discards is an edit nobody makes twice.
    from app.services import meeting_practice  # noqa: PLC0415

    meeting_practice.ensure_script(db, brief, commit=False)
    crm_events.record_activity(
        db, lead.id, CrmActivityKind.MEETING_PREP_READY,
        meta={"brief_id": str(brief.id), "source": brief.source,
              "meeting_start_at": ctx["meeting"]["start_at"]},
    )
    db.commit()

    if notify and brief.notified_at is None and brief.cancelled_at is None:
        notify_ready(db, brief, lead)
        brief.notified_at = now
        db.commit()
    return "ready"


def prep_link(lead_id) -> str:
    return f"/leads/detail?id={lead_id}&tab=prep"


def notify_ready(db: Session, brief: MeetingPrepBrief, lead: Lead) -> dict:
    """Push + Slack: the brief exists, here is the link (and, in Slack, the
    two sections worth reading in a notification)."""
    from app.services import event_bus  # noqa: PLC0415

    name = lead.full_name or lead.email or "your prospect"
    company = f" at {lead.company}" if lead.company else ""
    when = _fmt_when(brief.meeting_start_at)
    sections = brief.sections_json or {}
    why = (sections.get("why_they_booked") or "")[:700]
    opening = (brief.opening_script or "")[:900]
    slack_text = (f"*{name}*{company} · {when}\n\n"
                  + (f"*Why they booked:* {why}\n\n" if why else "")
                  + (f"*Your opening 60 seconds:*\n>{opening}" if opening else ""))
    return event_bus.emit(
        db, brief.user_id, "meeting_prep_ready",
        title=f"Prep brief ready: {name}",
        body=f"{when} — {name}{company}. Your meeting brief is ready.",
        deep_link=prep_link(lead.id),
        data={"leadId": str(lead.id), "briefId": str(brief.id)},
        slack_text=slack_text,
    )
