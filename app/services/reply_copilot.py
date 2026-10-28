"""F04 Reply Copilot -- a grounded, claim-verified answer to every reply worth
answering, queued for a person (docs/features/f04-reply-copilot.md).

WHAT THIS EXTENDS. Replies are already read three ways (routing class,
Feature 2 category + an ungrounded draft, Feature 1 positive-intent label) and
filed in the unified inbox (Feature 6). This module adds what was missing:

  draft     one model call grounded in the campaign's objection research
            (phase 7), the product's approved claims (F05) and the booking
            link, with the reply as delimited UNTRUSTED text (F-AI-02); the
            output is checked (no foreign links, no placeholders, no banned
            words / forbidden phrases) and claim-verified (Feature A1 + F05).
  queue     approve / edit / discard (email), copy (LinkedIn), in the inbox.
  send      an approved email becomes an ordinary `messages` row with
            origin "reply_copilot" and goes through
            outreach_tasks.send_message_impl -- the same chokepoint as every
            send. NOTHING in this module transmits anything.
  auto      optional, off at two levels: "send me a time" email replies only.

SAFETY GUARDS (each tested in tests/test_reply_copilot.py)
  * an opt-out reply never gets a draft -- routing class, intent label AND a
    deterministic phrase check (the classifier degrades unparseable answers to
    "question", so the model alone is not trusted with this);
  * a suppressed or excluded contact never gets a draft, and cannot have one
    approved;
  * an approval is BLOCKED when the final words carry a forbidden phrase or a
    claim the claim engine would strip;
  * at send time, a reply_copilot message is cancelled when the lead moved on
    (status / archive), wrote again, or opted out (send_time_hold), and may
    pass a stopped enrollment ONLY when its own conversation caused the stop
    (answers_the_stop) -- or, with the account's `answer_earlier_replies`
    setting off (strict), only when its OWN reply caused it;
  * every decision leaves "pending" through one conditional UPDATE (_claim):
    a stale second approval is 409 and never queues a second message.

TENANCY. Every draft carries the owning account's user_id (lead -> strategy ->
product -> user); every read in the API filters on it. The prompt only ever
holds the owner's own library entries, booking page and strategy research.
"""

from __future__ import annotations

import logging
import re
import statistics
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import (
    CalendarBookingPage,
    ChannelType,
    EnrollmentStatus,
    InboundReply,
    Lead,
    LeadStatus,
    Message,
    MessageStatus,
    PipelineKind,
    Product,
    ReplyCopilotSettings,
    ReplyDraft,
    ResearchStep,
    Sequence,
    SequenceEnrollment,
    Strategy,
    User,
)

logger = logging.getLogger(__name__)

SETTING = "reply_copilot_enabled"
AUTO_SEND_SETTING = "reply_copilot_auto_send_allowed"
PURPOSE = "reply_copilot"            # usage_meter purpose (ACCOUNT_CAPPED)
ORIGIN = "reply_copilot"             # messages.origin of an approved answer
OUTCOME_SOURCE = "reply_copilot"     # outcomes.meta_json["source"] of its SENT row

PENDING, APPROVED, COPIED, DISCARDED, SUPERSEDED = (
    "pending", "approved", "copied", "discarded", "superseded")
STATUSES = (PENDING, APPROVED, COPIED, DISCARDED, SUPERSEDED)

EMAIL, LINKEDIN = "email", "linkedin"
DRAFT_CHANNELS = (EMAIL, LINKEDIN)   # WhatsApp / phone: non-goals

POSITIVE, OBJECTION, QUESTION = "positive", "objection", "question"

#: routing class (reply_classification) -> draft intent
ELIGIBLE_ROUTING = {"interested": POSITIVE, "question": QUESTION, "objection": OBJECTION}
#: Feature 1 label -> draft intent, used ONLY while the routing class is not
#: stored yet (the LinkedIn webhook enqueues before it routes).
ELIGIBLE_INTENT_FALLBACK = {"interested": POSITIVE, "objection": OBJECTION}
#: routing classes that never get a draft
BLOCKING_ROUTING = frozenset({"unsubscribe_request", "not_interested", "bounce",
                              "out_of_office", "automated_response"})

#: The stop reasons a reply writes (outreach_tasks: f"replied_{class}") that an
#: approved answer is allowed to pass. Every other stop cancels it.
REPLY_STOP_REASONS = frozenset(f"replied_{c}" for c in ELIGIBLE_ROUTING)

#: Same guard the auto-follow-up uses: keeps the beat dispatcher off the row
#: while send_reply_draft sends it (so the outcome keeps its tag). If the
#: broker is down, the dispatcher picks it up once the guard has passed.
DISPATCH_GUARD = timedelta(minutes=5)

MAX_TOKENS = 700
MAX_DRAFT_WORDS = 140
MAX_FINAL_CHARS = 5000
MAX_REPLY_CHARS = 4000
MAX_PLAYBOOK_CHARS = 6000
MAX_CLAIMS = 20
#: Strategy pipeline, phase 7 ("Objection mapping, proof assets, case-study
#: angles"): Rebuttal scripts, Objection-handling FAQ, Phase synthesis.
PLAYBOOK_STEPS = (56, 62, 63)


class CopilotError(Exception):
    """A decision the API turns into an HTTP error (409 by default)."""

    def __init__(self, detail: str, status: int = 409, problems: dict | None = None):
        super().__init__(detail)
        self.detail = detail
        self.status = status
        self.problems = problems or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def enabled(session: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(session, SETTING))


def auto_send_allowed(session: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(session, AUTO_SEND_SETTING))


# --------------------------------------------------------------------------
# Deterministic language checks (pure; the model is never trusted with these)
# --------------------------------------------------------------------------

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'"})

#: Opt-out wording. "stop" alone is NOT enough ("stop by", "non-stop"): it
#: counts when it is the whole message / a whole line, "please stop", or
#: "stop <contacting verb>". Matched only against the prospect's NEW text
#: (see new_text) -- our own quoted footer says "Unsubscribe instantly".
_OPT_OUT = re.compile(
    r"(?:^\s*stop\s*[.!]*\s*$"
    r"|\bplease,? stop\b"
    r"|\bstop (?:e-?mailing|messaging|contacting|sending|writing|calling|texting|spamming|"
    r"reaching out|bothering|all (?:e-?mails?|messages?|contact))\b"
    r"|\b(?:unsubscribe|unsub|opt[\s-]?out|optout)\b"
    r"|\bremove (?:me|us|my (?:email|address|details))\b|\btake (?:me|us) off\b"
    r"|\b(?:do not|don'?t|dont|never) (?:contact|email|e-mail|message|call|write to|reach out)\b"
    r"|\bno more (?:emails?|messages?|contact)\b|\bleave (?:me|us) alone\b"
    r"|\b(?:delete|erase) (?:my|our) (?:data|details|information|info|personal data)\b)",
    re.I | re.M)

_MEETING_TIME = re.compile(
    r"(?:send (?:me |us |over )?(?:a |some |your |the |a few )?"
    r"(?:time|times|slot|slots|availability|invite|calendar invite|calendar link|"
    r"booking link|link to book|link)\b"
    r"|what (?:time|times|day|days) (?:work|works|suit|suits|would work)"
    r"|when (?:are|would|will) you (?:be )?(?:free|available|around)"
    r"|(?:find|grab|pick|set up|schedule|book) (?:a |some )?(?:time|call|meeting|chat|slot)"
    r"|(?:happy|glad|keen) to (?:chat|talk|meet|hop on a call|jump on a call)"
    r"|let'?s (?:chat|talk|meet|set (?:something )?up|schedule|book)"
    r"|your (?:calendar|calendly|availability))",
    re.I)

#: Top-level domains a bare host must end in to count as a link. A curated
#: list, not "any letters": "Node.js", "e.g.", "i.e." and "3.5x" must not read
#: as links. Missing an exotic TLD is covered by the scheme branch for
#: anything written with http(s)://.
_TLDS = (
    "com|net|org|io|co|ai|app|dev|info|biz|me|us|uk|ca|de|fr|au|in|xyz|site|online|tech|"
    "link|page|ly|gl|sh|cc|tv|eu|nl|es|ch|ru|cn|jp|br|mx|nz|za|ie|pl|dk|fi|pt|gr|cz|hu|"
    "ro|tr|il|sg|hk|kr|tw|id|my|ph|vn|pk|ae|sa|ng|ke|gg|club|shop|store|live|pro|email|"
    "cloud|digital|agency|space|website|services|solutions|marketing|news|blog|top|click|"
    "win|world|one|zip|mov|pay|bank|support|help|login|secure|work|team|tools|ws|su|cx|"
    "la|im|fm|am|vc|tk|ml|ga|cf|gq|ua|by|kz")
#: Any link: with a scheme, or a bare host (acme-pay.io/verify, www.x.com).
#: Not preceded by "@" or a word/host character, so an e-mail address and a
#: piece of a longer token are not read as a host.
_LINK = re.compile(
    r"(?<![@\w.\-/])(?:https?://[^\s<>()\"']+"
    r"|www\.[^\s<>()\"']+"
    r"|(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+(?:" + _TLDS + r")\b"
    r"(?:[/?#][^\s<>()\"']*)?)",
    re.I)
#: [Name], {first_name}, {{x}}, <name>
_PLACEHOLDER = re.compile(
    r"\[[^\]\n]{1,40}\]|\{[^{}\n]{0,40}\}|\{\{|\}\}|<\s*[A-Za-z_][A-Za-z0-9_ .\-]{0,30}\s*>")

# Where the prospect's own words end and the quoted thread begins.
_QUOTE_HEADERS = (
    re.compile(r"^\s*On\b.{0,400}\bwrote:\s*$", re.I | re.S),        # Gmail / Apple
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.I),   # Outlook
    re.compile(r"^\s*-{2,}\s*Forwarded message\s*-{2,}\s*$", re.I),
    re.compile(r"^\s*_{8,}\s*$"),                                     # Outlook web separator
    re.compile(r"^\s*Le\b.{0,400}\ba écrit\s*:\s*$", re.I | re.S),
    re.compile(r"^\s*Am\b.{0,400}\bschrieb\b.{0,200}:\s*$", re.I | re.S),
)
_OUTLOOK_FROM = re.compile(r"^\s*\**From:\**\s", re.I)
_OUTLOOK_NEXT = re.compile(r"^\s*\**(?:Sent|Date|To|Subject):\**\s", re.I)
#: Our own compliance footer / region notice, if a client quoted it without
#: ">" markers (sequence_engine.compliance_footer, compliance_audit._NOTICES).
_OUR_FOOTER = re.compile(
    r"(?:Don't want these emails\? Unsubscribe instantly:.*$"
    r"|You are receiving this because your professional role matches what we offer.*$"
    r"|You can unsubscribe at any time with the link below or by replying \"stop\"\.?.*$"
    r"|^.*/unsubscribe/\S+.*$)",
    re.I | re.M)


def new_text(text: str | None) -> str:
    """The prospect's NEW words: everything above the quoted thread.

    Cuts at the first quote header (Gmail "On ... wrote:", including one
    wrapped over two lines; Outlook "-----Original Message-----", a "From:"
    line followed by "Sent:/Date:/To:/Subject:", an underscore separator),
    drops ">"-quoted lines, and removes our own footer and region notice. Pure.

    FAIL-SAFE: this only ever REMOVES quoted material; opt-out wording the
    prospect wrote above the quote is always kept and always checked."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").translate(
        _APOSTROPHES).split("\n")
    kept: list[str] = []
    for i, line in enumerate(lines):
        pair = f"{line} {lines[i + 1]}" if i + 1 < len(lines) else line
        if any(h.match(line) for h in _QUOTE_HEADERS):
            break
        if (re.match(r"^\s*On\b", line, re.I) and not line.rstrip().endswith("wrote:")
                and any(h.match(pair) for h in _QUOTE_HEADERS[:1])):
            break
        if _OUTLOOK_FROM.match(line) and any(
                _OUTLOOK_NEXT.match(nxt) for nxt in lines[i + 1:i + 5]):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    out = _OUR_FOOTER.sub("", "\n".join(kept))
    out = re.sub(r"\n?^--\s*$\n?", "\n", out, flags=re.M)
    return out.strip()


def opt_out_language(text: str | None) -> bool:
    """True when the prospect's NEW text asks, in any common wording, to stop
    contact or to delete data. Broad on purpose: a false positive costs one
    draft (a person answers by hand); a false negative would answer an
    opt-out. Quoted text (our own footer included) is ignored."""
    return bool(_OPT_OUT.search(new_text(text)))


def meeting_time_language(text: str | None) -> bool:
    """True when the prospect's NEW text asks for a time / a link / availability."""
    return bool(_MEETING_TIME.search(new_text(text)))


def _clean_link(link: str) -> str:
    return link.rstrip(".,;:!?)]>'\"")


def urls_in(text: str | None) -> list[str]:
    """Every link in the text, with or without a scheme."""
    return [_clean_link(u) for u in _LINK.findall(text or "")]


def _host(link: str) -> str:
    bare = re.sub(r"^https?://", "", link.strip(), flags=re.I)
    host = re.split(r"[/?#:]", bare, maxsplit=1)[0].lower()
    return host[4:] if host.startswith("www.") else host


def _link_key(link: str) -> str:
    bare = re.sub(r"^https?://", "", _clean_link(link.strip()), flags=re.I)
    bare = bare[4:] if bare.lower().startswith("www.") else bare
    return bare.rstrip("/").lower()


def link_allowed(link: str, *, allowed_urls: set, own_domains: set) -> bool:
    """The booking link exactly (with or without its scheme), or anything on
    the sender's own domain (or a subdomain of it)."""
    if _link_key(link) in {_link_key(u) for u in allowed_urls if u}:
        return True
    host = _host(link)
    return any(host == d or host.endswith("." + d) for d in own_domains if d)


def _norm(text: str | None) -> str:
    return " ".join((text or "").split())


# --------------------------------------------------------------------------
# Eligibility, ownership, contact guard
# --------------------------------------------------------------------------


def eligibility(reply: InboundReply) -> tuple[str | None, str | None]:
    """(intent, None) when this reply should get a draft, else (None, reason).

    Pure over the reply row. The contact guard (suppression / exclusions) and
    "already drafted" need the database and are checked by the caller."""
    from app.services import inbox  # noqa: PLC0415

    if reply.lead_id is None:
        return None, "unmatched"
    channel = (reply.channel or "").lower()
    if channel not in DRAFT_CHANNELS:
        return None, f"channel_{channel or 'unknown'}"
    routing = (reply.classification or "").strip().lower() or None
    if routing in BLOCKING_ROUTING:
        return None, f"routing_{routing}"
    if (reply.intent_label or "") == "unsubscribe":
        return None, "intent_unsubscribe"
    # Only the prospect's NEW words (new_text strips the quoted thread and our
    # own footer); opt-out wording above the quote always blocks.
    if opt_out_language(reply.body) or opt_out_language(reply.subject):
        return None, "opt_out_language"
    if not inbox.is_from_a_person(reply):
        return None, "not_a_person"
    if routing is not None:
        intent = ELIGIBLE_ROUTING.get(routing)
    else:
        intent = ELIGIBLE_INTENT_FALLBACK.get(reply.intent_label or "")
    if intent is None:
        return None, f"not_eligible_{routing or reply.intent_label or 'unclassified'}"
    return intent, None


def _context(session: Session, lead: Lead) -> tuple[Strategy | None, Product | None]:
    strategy = session.get(Strategy, lead.strategy_id) if lead.strategy_id else None
    product = session.get(Product, strategy.product_id) if strategy else None
    return strategy, product


#: The only lead statuses an answer to a reply is valid for. Everything else
#: means a later event overtook the conversation (meeting booked, proposal,
#: opportunity, won / lost / disqualified, dropped: bounced, erased or
#: undeliverable) or the lead was never contacted.
ANSWERABLE_LEAD_STATUSES = frozenset({LeadStatus.REPLIED, LeadStatus.CONTACTED,
                                      LeadStatus.VERIFIED, LeadStatus.FLAGGED})


def lead_status_block(lead: Lead) -> str | None:
    """Why the lead's current state rules out an answer, or None. Pure."""
    if lead.status not in ANSWERABLE_LEAD_STATUSES:
        status = lead.status.value if lead.status is not None else "unknown"
        return f"the lead is now '{status}' -- the conversation has moved on"
    if (lead.engagement_state or "") == "archived":
        return "the lead was archived by the conversion gate"
    return None


def contact_block(session: Session, lead: Lead, owner_id) -> str | None:
    """Why this contact must not be answered, or None.

    SAFETY GUARD. The lead's status (lead_status_block), suppression (email,
    phone, LinkedIn profile -- the same check the send path makes) and, when
    F05 is on, the account's own exclusion lists. A lookup error FAILS
    CLOSED."""
    from app.services import guardrails  # noqa: PLC0415
    from app.workers.lead_tasks import is_suppressed  # noqa: PLC0415

    try:
        status_problem = lead_status_block(lead)
        if status_problem:
            return status_problem
        if is_suppressed(session, lead.email, lead.phone, linkedin=lead.linkedin_url):
            return "the contact is on the suppression list (opted out)"
        if guardrails.enabled(session):
            match = guardrails.exclusion_for_lead(session, lead, user_id=owner_id)
            if match is not None:
                return f"the contact is excluded by your guardrails ({match.kind}: {match.reason})"
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("reply copilot: contact check failed for lead %s", lead.id)
        return "the contact could not be checked against suppression and exclusions"
    return None


# --------------------------------------------------------------------------
# Grounding
# --------------------------------------------------------------------------


def objection_playbook(session: Session, strategy: Strategy | None) -> tuple[str, list[str]]:
    """(text, step ids) of the strategy's objection research, truncated."""
    if strategy is None:
        return "", []
    rows = session.execute(
        select(ResearchStep).where(ResearchStep.strategy_id == strategy.id,
                                   ResearchStep.pipeline == PipelineKind.STRATEGY,
                                   ResearchStep.step_no.in_(PLAYBOOK_STEPS))
        .order_by(ResearchStep.step_no)
    ).scalars().all()
    text = "\n\n".join(f"[{r.name}]\n{(r.output or '').strip()}" for r in rows if r.output)
    if len(text) > MAX_PLAYBOOK_CHARS:
        text = text[:MAX_PLAYBOOK_CHARS].rstrip() + "\n(truncated)"
    return text, [r.step_id for r in rows if r.output]


def booking_links(session: Session, sequence: Sequence | None, owner_id) -> tuple[str | None, set]:
    """(the link to offer, every form of it a draft may contain).

    The sequence's booking URL first, tagged with the owner exactly as the
    send path tags it (calendly.tag_booking_url); else the owner's first
    active Vectorae booking page. None when there is neither."""
    from app.config import settings  # noqa: PLC0415
    from app.integrations.calendly import tag_booking_url  # noqa: PLC0415

    if sequence is not None and (sequence.booking_url or "").strip():
        raw = sequence.booking_url.strip()
        tagged = tag_booking_url(raw, owner_id) or raw
        return tagged, {raw, tagged}
    if owner_id is None:
        return None, set()
    page = session.execute(
        select(CalendarBookingPage)
        .where(CalendarBookingPage.user_id == owner_id,
               CalendarBookingPage.is_active.is_(True))
        .order_by(CalendarBookingPage.created_at, CalendarBookingPage.id).limit(1)
    ).scalars().first()
    if page is None:
        return None, set()
    url = f"{settings.frontend_url.rstrip('/')}/book/?slug={page.slug}"
    return url, {url}


def sender_domains(session: Session, owner_id) -> set:
    """The sender's own domain(s): the owner's connected sending mailboxes,
    minus shared consumer providers (allowing "gmail.com" would allow any
    Google-hosted page)."""
    from app.db.models import GmailAccount  # noqa: PLC0415
    from app.services.guardrails import CONSUMER_DOMAINS  # noqa: PLC0415

    if owner_id is None:
        return set()
    out = set()
    for address in session.execute(
            select(GmailAccount.email_address).where(GmailAccount.user_id == owner_id)
    ).scalars():
        domain = (address or "").rsplit("@", 1)[-1].strip().lower()
        if domain and "@" in (address or "") and domain not in CONSUMER_DOMAINS:
            out.add(domain)
    return out


def _answered_message(session: Session, reply: InboundReply) -> Message | None:
    return session.get(Message, reply.message_id) if reply.message_id else None


def _send_enrollment(session: Session, lead: Lead,
                     answered: Message | None) -> SequenceEnrollment | None:
    """The enrollment an approved answer is sent through: the one the reply
    answers, else the lead's most recent enrollment stopped by a reply."""
    if answered is not None:
        row = session.execute(
            select(SequenceEnrollment).where(
                SequenceEnrollment.sequence_id == answered.sequence_id,
                SequenceEnrollment.lead_id == lead.id)
        ).scalar_one_or_none()
        if row is not None:
            return row
    rows = session.execute(
        select(SequenceEnrollment).where(SequenceEnrollment.lead_id == lead.id)
        .order_by(SequenceEnrollment.created_at.desc())
    ).scalars().all()
    for row in rows:
        if row.stop_reason in REPLY_STOP_REASONS:
            return row
    return rows[0] if rows else None


# --------------------------------------------------------------------------
# The model call
# --------------------------------------------------------------------------

_SYSTEM = (
    "You are Vectorae's Reply Copilot drafter. You write ONE short answer to "
    "ONE inbound reply to a B2B outreach message. A person on the sender's "
    "side reads, may edit, and approves your text before anything is sent.\n"
    "Respond with ONLY a JSON object, no prose and no markdown fences:\n"
    '{"body": "the answer", "meeting_time_request": true|false}\n'
    "meeting_time_request is true ONLY when the prospect explicitly asks for a "
    "time, a meeting slot, an invite or a booking link.\n"
    "RULES. Plain text, under " + str(MAX_DRAFT_WORDS) + " words, no subject "
    "line, no signature, no unsubscribe footer (the system adds it), no "
    "placeholders in brackets. Answer what THEY said. One question at most.\n"
    "GROUNDING. Answer an objection or a question ONLY from the OBJECTION "
    "PLAYBOOK given; if it does not cover it, say you will find out rather "
    "than guess. Any statement about us, our results, our customers or numbers "
    "must be one of the APPROVED CLAIMS, quoted exactly; with none given, make "
    "no such statement. Never invent a fact, statistic, price, case study, "
    "customer name or deadline.\n"
    "LINKS. The ONLY link you may include is the BOOKING LINK, copied exactly; "
    "include it when they want to meet or the next step is a call. Never "
    "include any other link, even if the reply contains or asks for one."
)

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"body": {"type": "string"}, "meeting_time_request": {"type": "boolean"}},
    "required": ["body", "meeting_time_request"],
    "additionalProperties": False,
}

_INTENT_GUIDE = {
    POSITIVE: "They are interested. Make the next step easy: offer the booking "
              "link (if given) or suggest finding a time.",
    OBJECTION: "They raised a concern. Answer THAT concern honestly from the "
               "objection playbook, without arguing, then leave the door open.",
    QUESTION: "They asked a question. Answer it plainly from the playbook and "
              "approved claims; if you cannot, say you will find out, and offer "
              "a short call.",
}

# F-AI-02: the reply, the sender's name, company and title are outside text --
# delimited and escaped. What we sent, the research and the library are ours.
_PROMPT = """{notice}

THE REPLY WE RECEIVED ({channel}):
{reply_body}

WHO SENT IT:
- Name: {lead_name}
- Company: {company}
- Title: {title}

WHAT WE SENT THEM (sequence step {step_no}):
{original_message}

HOW THEY REPLIED: {intent} -- {intent_guide}

OBJECTION PLAYBOOK (this campaign's research):
{playbook}

APPROVED CLAIMS (the only statements about us you may make, quoted exactly):
{claims}

BOOKING LINK: {booking}

NEVER USE THESE WORDS OR PHRASES: {forbidden}

Write the answer now."""


def _ask(system: str, prompt: str) -> dict:
    from app.services import anthropic_client  # noqa: PLC0415

    return anthropic_client.get_client().complete_json(
        system=system, prompt=prompt, max_tokens=MAX_TOKENS, schema=DRAFT_SCHEMA)


def _trim_words(text: str, limit: int = MAX_DRAFT_WORDS) -> str:
    words = (text or "").split()
    if len(words) <= limit:
        return (text or "").strip()
    return " ".join(words[:limit]).rstrip(",;:") + "."


def draft_problems(body: str, *, allowed_urls: set, forbidden: list[str],
                   own_domains: set = frozenset(), claims_on_file: bool = True,
                   product_name: str = "") -> list[str]:
    """Why a generated draft is unusable ([] = usable). Pure.

    `claims_on_file` False means the prompt said "make no claims about us"
    (no approved-claims library applies): a proof claim about us -- a metric,
    a count, 'trusted by' -- then breaks that rule, and nothing downstream
    would check it (T11)."""
    from app.services import guardrails, reply_intelligence  # noqa: PLC0415

    if not (body or "").strip():
        return ["the draft came back empty"]
    problems = []
    banned = reply_intelligence.find_banned_words(body)
    if banned:
        problems.append(f"it uses the banned words {', '.join(banned)}")
    hits = guardrails.forbidden_hits([body], forbidden) if forbidden else []
    if hits:
        problems.append(f"it uses the forbidden phrases {', '.join(hits)}")
    if _PLACEHOLDER.search(body):
        problems.append("it contains a placeholder in brackets")
    foreign = [u for u in urls_in(body)
               if not link_allowed(u, allowed_urls=allowed_urls, own_domains=own_domains)]
    if foreign:
        problems.append("it contains a link that is not the booking link")
    if not claims_on_file and any(guardrails.is_sender_claim(sentence, product_name)
                                  for sentence in re.split(r"(?<=[.!?])\s+", body)):
        problems.append("it makes a claim about us, and no approved claims are on file")
    return problems


def _reply_subject(reply: InboundReply, answered: Message | None) -> str:
    base = " ".join((reply.subject or (answered.subject if answered else "") or "").split())
    if not base:
        return "Re: your reply"
    return base[:500] if base.lower().startswith("re:") else f"Re: {base}"[:500]


def _generate(session: Session, reply: InboundReply, lead: Lead, strategy: Strategy | None,
              product: Product | None, owner_id, intent: str,
              answered: Message | None) -> dict | None:
    """One draft (with a single repair attempt), or None when unusable."""
    from app.services import guardrails, prompt_safety, reply_intelligence  # noqa: PLC0415

    playbook, playbook_steps = objection_playbook(session, strategy)
    claims: list[dict] = []
    forbidden: list[str] = []
    if product is not None and guardrails.enabled(session):
        claims = guardrails.approved_claims(session, owner_id, product.id)[:MAX_CLAIMS]
        forbidden = [r["text"] for r in guardrails.forbidden_phrases(session, owner_id,
                                                                     product.id)]
    enrollment = _send_enrollment(session, lead, answered)
    sequence = session.get(Sequence, enrollment.sequence_id) if enrollment else None
    booking, allowed_urls = booking_links(session, sequence, owner_id)
    own_domains = sender_domains(session, owner_id)
    original, step_no = reply_intelligence._original_message(session, reply)  # noqa: SLF001

    prompt = _PROMPT.format(
        notice=prompt_safety.NOTICE,
        channel=reply.channel,
        reply_body=prompt_safety.block(
            (new_text(reply.body) or reply.body or "")[:MAX_REPLY_CHARS]),
        lead_name=prompt_safety.inline(lead.full_name or "unknown"),
        company=prompt_safety.inline(lead.company or "unknown"),
        title=prompt_safety.inline(lead.title or "unknown"),
        step_no=step_no if step_no is not None else "unknown",
        original_message=prompt_safety.block(original),
        intent=intent,
        intent_guide=_INTENT_GUIDE[intent],
        playbook=playbook or "(no objection research on file -- make no claims; "
                             "offer to find out or to talk)",
        claims="\n".join(f"- {c['text']}" for c in claims) or "(none -- make no claims "
                                                                "about us)",
        booking=booking or "(none -- do not invent one; offer to find a time by reply)",
        forbidden=", ".join(list(reply_intelligence.BANNED_WORDS) + forbidden),
    )

    data = _ask(_SYSTEM, prompt)
    body = _trim_words(str((data or {}).get("body") or "").strip())
    problems = draft_problems(body, allowed_urls=allowed_urls, forbidden=forbidden,
                              own_domains=own_domains, claims_on_file=bool(claims),
                              product_name=product.name if product else "")
    if problems:
        data = _ask(_SYSTEM + "\nYOUR PREVIOUS ANSWER WAS REJECTED because "
                    + "; ".join(problems) + ". Rewrite it without that, same meaning.",
                    prompt)
        body = _trim_words(str((data or {}).get("body") or "").strip())
        problems = draft_problems(body, allowed_urls=allowed_urls, forbidden=forbidden,
                                  own_domains=own_domains, claims_on_file=bool(claims),
                                  product_name=product.name if product else "")
        if problems:
            logger.warning("reply copilot: draft for reply %s unusable after a repair "
                           "attempt (%s) -- no draft", reply.id, "; ".join(problems))
            return None
    return {
        "body": body,
        "meeting_time_request": bool((data or {}).get("meeting_time_request") is True),
        "booking_url": booking,
        "allowed_urls": allowed_urls,
        "grounding": {
            "playbook_steps": playbook_steps,
            "approved_claim_ids": [c["id"] for c in claims],
            "booking_url": booking,
            "forbidden_phrases": len(forbidden),
        },
    }


def _claim_flags(decisions: list) -> list[dict]:
    from app.services import claim_verification as cv  # noqa: PLC0415

    return [{"claim": d.claim_text[:500], "verdict": d.verdict, "category": d.category,
             "replacement": d.replacement}
            for d in decisions if d.verdict != cv.VERIFIED]


# --------------------------------------------------------------------------
# Draft creation (called from app/workers/reply_tasks.py)
# --------------------------------------------------------------------------


def draft_for_reply(session: Session, reply: InboundReply, now: datetime | None = None) -> dict:
    """Create the draft for one reply, and auto-send it when every auto-send
    condition holds.

    RETURNS {"status": "drafted"|"exists"|"skipped"|"failed", "reason",
    "draft_id", "auto": None|"queued"|<why not>}.

    NEVER RAISES. Runs inside the reply-processing task; a model outage, a
    budget cap or a bad row must leave the reply stored and routed exactly as
    before, with no draft.
    """
    now = now or _now()
    try:
        existing = session.execute(
            select(ReplyDraft.id).where(ReplyDraft.reply_id == reply.id)).scalar_one_or_none()
        if existing is not None:
            return {"status": "exists", "reason": None, "draft_id": str(existing), "auto": None}
        intent, reason = eligibility(reply)
        if intent is None:
            return {"status": "skipped", "reason": reason, "draft_id": None, "auto": None}
        lead = session.get(Lead, reply.lead_id)
        if lead is None:
            return {"status": "skipped", "reason": "unmatched", "draft_id": None, "auto": None}
        strategy, product = _context(session, lead)
        owner_id = product.user_id if product else None
        if owner_id is None:
            return {"status": "skipped", "reason": "no_owner", "draft_id": None, "auto": None}
        blocked = contact_block(session, lead, owner_id)
        if blocked:
            return {"status": "skipped", "reason": "contact_blocked", "draft_id": None,
                    "auto": None}

        from app.services import claim_verification, usage_meter  # noqa: PLC0415

        answered = _answered_message(session, reply)
        with usage_meter.scope(session, strategy_id=strategy.id if strategy else None,
                               user_id=owner_id, purpose=PURPOSE):
            generated = _generate(session, reply, lead, strategy, product, owner_id,
                                  intent, answered)
            if generated is None:
                return {"status": "failed", "reason": "unusable_output", "draft_id": None,
                        "auto": None}
            decisions: list = []
            checked = claim_verification.enforce(
                session, strategy=strategy, lead=lead, message=None, channel=reply.channel,
                fields={"body": generated["body"]}, decisions_out=decisions)
        body = (checked.get("body") or "").strip()
        if not body:
            session.rollback()
            return {"status": "failed", "reason": "empty_after_verification",
                    "draft_id": None, "auto": None}
        flags = _claim_flags(decisions)

        # An answer to an older message from the same person is stale. A
        # conditional UPDATE, so a draft a person approves at this moment is
        # never flipped back to "superseded" (its send is then cancelled at
        # send time by the newer-reply rule instead).
        session.execute(
            update(ReplyDraft)
            .where(ReplyDraft.lead_id == lead.id, ReplyDraft.user_id == owner_id,
                   ReplyDraft.status == PENDING)
            .values(status=SUPERSEDED, decided_at=now)
            .execution_options(synchronize_session=False))

        channel = (reply.channel or "").lower()
        draft = ReplyDraft(
            user_id=owner_id, reply_id=reply.id, lead_id=lead.id,
            strategy_id=strategy.id if strategy else None,
            channel=channel, intent=intent,
            meeting_time_request=bool(generated["meeting_time_request"]
                                      and meeting_time_language(reply.body)),
            status=PENDING,
            draft_subject=_reply_subject(reply, answered) if channel == EMAIL else None,
            draft_body=body, claim_flags_json=flags or None,
            grounding_json=generated["grounding"],
            reply_received_at=_aware(reply.received_at or reply.created_at) or now,
        )
        session.add(draft)
        session.commit()
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.warning("reply copilot: no draft for reply %s: %s: %s",
                       getattr(reply, "id", None), type(exc).__name__, exc)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"status": "failed", "reason": type(exc).__name__, "draft_id": None,
                "auto": None}

    auto = _maybe_auto_send(session, draft, reply, generated, now)
    return {"status": "drafted", "reason": None, "draft_id": str(draft.id), "auto": auto}


# --------------------------------------------------------------------------
# Auto-send ("send me a time", email, opt-in)
# --------------------------------------------------------------------------


def account_settings(session: Session, user_id) -> ReplyCopilotSettings | None:
    return session.execute(
        select(ReplyCopilotSettings).where(ReplyCopilotSettings.user_id == user_id)
    ).scalar_one_or_none()


def auto_send_opted_in(session: Session, user_id) -> bool:
    row = account_settings(session, user_id)
    return bool(row and row.auto_send_meeting_time)


def set_auto_send(session: Session, user_id, on: bool, *, actor_user_id=None) -> ReplyCopilotSettings:
    if on and not auto_send_allowed(session):
        raise CopilotError("auto-send is not allowed on this deployment "
                           f"(admin setting {AUTO_SEND_SETTING} is off)")
    row = account_settings(session, user_id)
    if row is None:
        row = ReplyCopilotSettings(user_id=user_id)
        session.add(row)
    row.auto_send_meeting_time = bool(on)
    row.updated_by_user_id = actor_user_id
    session.commit()
    return row


def answer_earlier_replies(session: Session, user_id) -> bool:
    """The account's stop-attribution setting. No row = True (the behaviour
    before the setting existed)."""
    row = account_settings(session, user_id)
    return True if row is None else bool(row.answer_earlier_replies)


def set_answer_earlier_replies(session: Session, user_id, on: bool, *,
                               actor_user_id=None) -> ReplyCopilotSettings:
    """Founder decision 2026-10-11 ("User decide this"): the account chooses.
    Either value is always allowed -- strict only ever cancels more."""
    row = account_settings(session, user_id)
    if row is None:
        row = ReplyCopilotSettings(user_id=user_id)
        session.add(row)
    row.answer_earlier_replies = bool(on)
    row.updated_by_user_id = actor_user_id
    session.commit()
    return row


def settings_out(session: Session, user_id) -> dict:
    return {"auto_send_meeting_time": auto_send_opted_in(session, user_id),
            "auto_send_allowed": auto_send_allowed(session),
            "answer_earlier_replies": answer_earlier_replies(session, user_id),
            "enabled": enabled(session)}


def auto_send_decision(session: Session, draft: ReplyDraft, generated: dict) -> str | None:
    """None when this draft may be auto-sent, else why not. Every condition
    must hold; anything else leaves the draft PENDING for a person."""
    if not auto_send_allowed(session):
        return "deployment_not_allowed"
    if not auto_send_opted_in(session, draft.user_id):
        return "account_not_opted_in"
    if draft.channel != EMAIL:
        return "not_email"
    if draft.intent not in (POSITIVE, QUESTION):
        return "intent"
    if not draft.meeting_time_request:
        return "not_a_meeting_time_request"
    if not any(url in draft.draft_body for url in generated.get("allowed_urls") or ()):
        return "no_booking_link_in_draft"
    if draft.claim_flags_json:
        return "claim_verification_changed_the_draft"
    lead = session.get(Lead, draft.lead_id)
    # Local business mode: a business's own customer (a consumer) is only ever
    # answered by a person -- never automatically.
    from app.services.local_business import is_customer  # noqa: PLC0415

    if is_customer(lead):
        return "customer_needs_a_person"
    reply = session.get(InboundReply, draft.reply_id)
    enrollment = _send_enrollment(session, lead, _answered_message(session, reply))
    # The routing stops the sequence for this reply; until it has, its own
    # stop would cancel the queued message. A person approves instead.
    if enrollment is None or enrollment.stop_reason not in REPLY_STOP_REASONS:
        return "routing_not_finished"
    # Strict accounts: an answer the send path would cancel is not queued.
    if (not answer_earlier_replies(session, draft.user_id)
            and not stop_explained_by(session, reply, enrollment, strict=True)):
        return "stop_not_from_this_reply"
    return None


def _maybe_auto_send(session: Session, draft: ReplyDraft, reply: InboundReply,
                     generated: dict, now: datetime) -> str | None:
    """Never raises. None when auto-send does not apply (the common case)."""
    try:
        why_not = auto_send_decision(session, draft, generated)
        if why_not is not None:
            return why_not
        approve(session, draft, actor=None, now=now, auto=True)
        return "queued"
    except CopilotError as exc:
        session.rollback()
        logger.info("reply copilot: auto-send refused for draft %s: %s", draft.id, exc.detail)
        return "refused"
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("reply copilot: auto-send failed for draft %s", draft.id)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return "failed"


# --------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------


def final_copy_problems(session: Session, *, strategy: Strategy | None, lead: Lead,
                        owner_id, channel: str, subject: str | None, body: str) -> dict:
    """{"forbidden": [...], "claims": [...]} for the words a person approved.

    Forbidden phrases: the F05 check the send path makes (only when F05 is
    on). Claims: claim_verification.enforce over the same fields the send path
    renders -- any claim it would strip or rewrite is listed. Adds claim log
    rows to the session (the caller commits or rolls back)."""
    from app.services import claim_verification, guardrails  # noqa: PLC0415

    forbidden: list[str] = []
    if strategy is not None and guardrails.enabled(session):
        forbidden = guardrails.check_copy(session, owner_id, strategy.product_id, subject, body)
    fields = {"body": body}
    if channel == EMAIL and subject:
        fields["subject"] = subject
    decisions: list = []
    claim_verification.enforce(session, strategy=strategy, lead=lead, message=None,
                               channel=channel, fields=fields, decisions_out=decisions)
    return {"forbidden": forbidden, "claims": _claim_flags(decisions)}


def _final_words(draft: ReplyDraft, subject: str | None, body: str | None) -> tuple:
    final_body = (body if body is not None else draft.draft_body or "").strip()
    if not final_body:
        raise CopilotError("the answer is empty", status=422)
    if len(final_body) > MAX_FINAL_CHARS:
        raise CopilotError(f"the answer is longer than {MAX_FINAL_CHARS} characters",
                           status=422)
    final_subject = None
    if draft.channel == EMAIL:
        # A blank or whitespace subject falls back to the drafted one
        # ("Re: <their subject>"): an email answer never goes out subjectless.
        final_subject = (" ".join((subject or "").split())[:500]
                         or " ".join((draft.draft_subject or "").split())[:500]
                         or "Re: your reply")
    edited = (_norm(final_body) != _norm(draft.draft_body)
              or (draft.channel == EMAIL and _norm(final_subject) != _norm(draft.draft_subject)))
    return final_subject, final_body, edited


def _check_and_decide(session: Session, draft: ReplyDraft, *, subject, body,
                      channel_required: str) -> tuple:
    if draft.status != PENDING:
        raise CopilotError(f"this draft is already {draft.status}")
    if draft.channel != channel_required:
        raise CopilotError("LinkedIn drafts are copy-only -- use copy"
                           if draft.channel == LINKEDIN
                           else "only LinkedIn drafts are copied; approve this one to send it")
    final_subject, final_body, edited = _final_words(draft, subject, body)
    lead = session.get(Lead, draft.lead_id)
    if lead is None:
        raise CopilotError("the lead no longer exists", status=404)
    strategy, _product = _context(session, lead)
    blocked = contact_block(session, lead, draft.user_id)
    if blocked:
        raise CopilotError(f"cannot answer: {blocked}")

    from app.services import usage_meter  # noqa: PLC0415

    with usage_meter.scope(session, strategy_id=strategy.id if strategy else None,
                           user_id=draft.user_id, purpose=PURPOSE):
        problems = final_copy_problems(session, strategy=strategy, lead=lead,
                                       owner_id=draft.user_id, channel=draft.channel,
                                       subject=final_subject, body=final_body)
    if problems["forbidden"] or problems["claims"]:
        session.rollback()
        parts = []
        if problems["forbidden"]:
            parts.append("forbidden phrases: " + ", ".join(problems["forbidden"]))
        if problems["claims"]:
            parts.append(f"{len(problems['claims'])} claim(s) cannot be backed by stored "
                         "data or an approved claim")
        raise CopilotError("cannot approve -- " + "; ".join(parts), status=422,
                           problems=problems)
    return lead, strategy, final_subject, final_body, edited


def _claim(session: Session, draft: ReplyDraft, new_status: str, *, actor: User | None,
           now: datetime) -> None:
    """Move a draft out of PENDING atomically.

    A conditional UPDATE (id AND owner AND status = 'pending'), never an ORM
    attribute write: the status check in _check_and_decide is a read on an
    object that may be stale, so two sessions can both see "pending". The
    database arbitrates; the loser rolls back and gets 409. Not committed
    here -- the caller commits it together with what the decision creates."""
    result = session.execute(
        update(ReplyDraft)
        .where(ReplyDraft.id == draft.id, ReplyDraft.user_id == draft.user_id,
               ReplyDraft.status == PENDING)
        .values(status=new_status, decided_at=now,
                decided_by_user_id=actor.id if actor is not None else None)
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        session.rollback()
        session.refresh(draft)
        raise CopilotError(f"this draft is already {draft.status}")
    # Keep the in-memory row honest (the Core UPDATE bypassed it).
    draft.status = new_status
    draft.decided_at = now
    draft.decided_by_user_id = actor.id if actor is not None else None


def _mark_reply_handled(session: Session, draft: ReplyDraft, actor: User | None,
                        now: datetime) -> None:
    from app.services import inbox  # noqa: PLC0415

    reply = session.get(InboundReply, draft.reply_id)
    if reply is not None:
        inbox.mark_handled(session, [reply], actor=actor, handled=True, now=now)


def approve(session: Session, draft: ReplyDraft, *, actor: User | None,
            subject: str | None = None, body: str | None = None,
            now: datetime | None = None, auto: bool = False) -> ReplyDraft:
    """Approve an EMAIL draft (optionally edited) and hand it to the send
    chokepoint. Raises CopilotError (409 / 422 / 404) without changing
    anything when a check fails. NOTHING is transmitted here."""
    now = now or _now()
    lead, strategy, final_subject, final_body, edited = _check_and_decide(
        session, draft, subject=subject, body=body, channel_required=EMAIL)
    reply = session.get(InboundReply, draft.reply_id)
    answered = _answered_message(session, reply) if reply else None
    enrollment = _send_enrollment(session, lead, answered)
    if enrollment is None:
        session.rollback()
        raise CopilotError("there is no campaign enrollment to send this answer through")
    # The claim comes FIRST and in the same transaction as the message, so two
    # concurrent approvals (two tabs, a person racing auto-send) can never
    # create two messages: the loser's UPDATE matches no row -> 409.
    _claim(session, draft, APPROVED, actor=actor, now=now)
    message = Message(
        sequence_id=enrollment.sequence_id, lead_id=lead.id, channel=ChannelType.EMAIL,
        step_no=(answered.step_no if answered is not None else max(enrollment.current_step, 1)),
        template="Reply Copilot answer (approved)", subject=final_subject, body=final_body,
        origin=ORIGIN, status=MessageStatus.SCHEDULED, scheduled_at=now + DISPATCH_GUARD,
    )
    session.add(message)
    session.flush()
    draft.final_subject = final_subject
    draft.final_body = final_body
    draft.edited = edited
    draft.auto_sent = bool(auto)
    draft.message_id = message.id
    session.commit()
    _mark_reply_handled(session, draft, actor, now)

    from app.workers import reply_tasks  # noqa: PLC0415

    reply_tasks.enqueue_send(message.id)
    return draft


def copy(session: Session, draft: ReplyDraft, *, actor: User, body: str | None = None,
         now: datetime | None = None) -> ReplyDraft:
    """Record that a LinkedIn draft was copied (the browser writes the
    clipboard). Vectorae sends nothing. Same checks as approve."""
    now = now or _now()
    _lead, _strategy, _subject, final_body, edited = _check_and_decide(
        session, draft, subject=None, body=body, channel_required=LINKEDIN)
    _claim(session, draft, COPIED, actor=actor, now=now)
    draft.final_body = final_body
    draft.edited = edited
    session.commit()
    _mark_reply_handled(session, draft, actor, now)
    return draft


def discard(session: Session, draft: ReplyDraft, *, actor: User,
            now: datetime | None = None) -> ReplyDraft:
    """Discard a pending draft. The reply stays in "needs reply": discarding
    a draft is not answering the prospect."""
    if draft.status != PENDING:
        raise CopilotError(f"this draft is already {draft.status}")
    _claim(session, draft, DISCARDED, actor=actor, now=now or _now())
    session.commit()
    return draft


# --------------------------------------------------------------------------
# The send path (called from app/workers/outreach_tasks.send_message_impl)
# --------------------------------------------------------------------------


def draft_for_message(session: Session, message: Message) -> ReplyDraft | None:
    return session.execute(
        select(ReplyDraft).where(ReplyDraft.message_id == message.id)).scalar_one_or_none()


def _stored_at(reply: InboundReply) -> datetime | None:
    return _aware(reply.created_at or reply.received_at)


def _said_at(reply: InboundReply) -> datetime | None:
    return _aware(reply.received_at or reply.created_at)


def stop_explained_by(session: Session, answered: InboundReply,
                      enrollment: SequenceEnrollment | None, *, strict: bool) -> bool:
    """Is the enrollment's reply-class stop explained by the conversation of
    `answered`?

    The stop must be "replied_<class>", have a stopped_at, and be at or after
    the explaining reply was stored. Lenient (strict=False): the explaining
    reply is `answered` OR an earlier reply of the same lead (a prospect who
    wrote twice: the first reply stopped the sequence, the second is not
    re-stopped). Strict: only `answered` itself, and only if no other reply
    of the lead stored at or before it could have caused the same stop -- a
    reply-class stop is written ONCE, by the first reply that stops, so an
    earlier matching reply means the stop was that reply's (and a tie is
    ambiguous, so it fails closed)."""
    if (enrollment is None or enrollment.status is not EnrollmentStatus.STOPPED
            or enrollment.stop_reason not in REPLY_STOP_REASONS
            or enrollment.stopped_at is None or answered is None):
        return False
    stopped = _aware(enrollment.stopped_at)
    answered_at = _stored_at(answered)
    if answered_at is None:
        return False

    def _matches(reply: InboundReply) -> bool:
        at = _stored_at(reply)
        return (at is not None and at <= answered_at and at <= stopped
                and f"replied_{(reply.classification or '').lower()}" == enrollment.stop_reason)

    if strict:
        if not _matches(answered):
            return False
        others = session.execute(
            select(InboundReply).where(InboundReply.lead_id == answered.lead_id,
                                       InboundReply.id != answered.id)).scalars()
        return not any(_matches(other) for other in others)
    return any(_matches(reply) for reply in session.execute(
        select(InboundReply).where(InboundReply.lead_id == answered.lead_id)).scalars())


def answers_the_stop(session: Session, message: Message,
                     enrollment: SequenceEnrollment | None) -> bool:
    """May this message pass a STOPPED enrollment?

    Only a Reply Copilot answer, and only when the stop is attributable to the
    conversation it answers (stop_explained_by). Which replies count is the
    account's `answer_earlier_replies` setting (default on = this draft's
    reply OR an earlier reply of the same lead; off = only THIS draft's
    reply). A stop for any other reason, or one that cannot be tied to such a
    reply (no stopped_at), keeps the message cancelled. Newer replies are
    handled by send_time_hold."""
    if (message.origin != ORIGIN or enrollment is None
            or enrollment.status is not EnrollmentStatus.STOPPED
            or enrollment.stop_reason not in REPLY_STOP_REASONS
            or enrollment.stopped_at is None):
        return False
    draft = draft_for_message(session, message)
    answered = session.get(InboundReply, draft.reply_id) if draft else None
    if answered is None:
        return False
    strict = not answer_earlier_replies(session, draft.user_id)
    return stop_explained_by(session, answered, enrollment, strict=strict)


def send_time_hold(session: Session, message: Message, lead: Lead) -> str | None:
    """Extra send-time checks for a Reply Copilot message, ON TOP of every
    gate in send_message_impl. Returns a status that ends the send (the
    message is cancelled) or None. In order:

      flag switched off since approval              cancelled_reply_copilot_off
      draft or its reply gone (erased)              cancelled_draft_missing
      lead no longer answerable: status not in
        ANSWERABLE_LEAD_STATUSES (meeting booked,
        proposal, opportunity, won / lost /
        disqualified, dropped) or archived           cancelled_lead_status
      a LATER reply declines or opts out
        (routing unsubscribe_request /
        not_interested, intent unsubscribe, or
        opt-out wording in its new text)            cancelled_prospect_declined
      any LATER reply from a person (the answer
        is to a message that is no longer the
        latest -- spec 4.1)                         cancelled_newer_reply

    Stops by later events never re-stop an already-stopped enrollment
    (webhooks / crm_sync / phone_calls / meeting_followup /
    conversion_probability skip stopped ones), which is why the lead's own
    state is checked here. Suppression and exclusions are checked right after
    by the chokepoint itself."""
    from app.services import inbox  # noqa: PLC0415

    reason = None
    status = None
    if not enabled(session):
        reason, status = "Reply Copilot was switched off", "cancelled_reply_copilot_off"
    else:
        draft = draft_for_message(session, message)
        reply = session.get(InboundReply, draft.reply_id) if draft else None
        if draft is None or reply is None:
            reason, status = "the approved draft no longer exists", "cancelled_draft_missing"
        elif lead_status_block(lead):
            reason, status = lead_status_block(lead), "cancelled_lead_status"
        else:
            since = _said_at(reply)
            for later in session.execute(
                    select(InboundReply).where(InboundReply.lead_id == lead.id,
                                               InboundReply.id != reply.id)).scalars():
                at = _said_at(later)
                if since is not None and at is not None and at <= since:
                    continue
                if ((later.classification or "") in ("unsubscribe_request", "not_interested")
                        or (later.intent_label or "") == "unsubscribe"
                        or opt_out_language(later.body)):
                    reason = "the prospect declined or opted out in a later reply"
                    status = "cancelled_prospect_declined"
                    break
                if inbox.is_from_a_person(later) and status is None:
                    reason = "the prospect wrote again; this answer is to an older message"
                    status = "cancelled_newer_reply"
    if status is None:
        return None
    message.status = MessageStatus.CANCELLED
    message.error = reason
    session.commit()
    return status


def approved_copy(session: Session, message: Message) -> tuple[str | None, str, str | None]:
    """(subject, body, thread_ref) a Reply Copilot message sends: the words a
    person approved -- never a re-render -- in the prospect's reply thread.

    Read from the DRAFT, not the message row, because the message row holds
    the footered copy after a first attempt and a retry must not footer it
    twice. Raises ComplianceError when the draft is gone (never retried)."""
    from app.core.exceptions import ComplianceError  # noqa: PLC0415

    draft = draft_for_message(session, message)
    if draft is None or not (draft.final_body or "").strip():
        raise ComplianceError("the approved Reply Copilot draft no longer exists")
    reply = session.get(InboundReply, draft.reply_id)
    thread = reply.thread_ref if reply is not None else None
    if not thread and reply is not None and reply.message_id:
        answered = session.get(Message, reply.message_id)
        thread = answered.thread_ref if answered is not None else None
    return draft.final_subject, draft.final_body, thread


def thread_headers(session: Session, message: Message) -> dict[str, str]:
    """In-Reply-To / References for an approved answer: the prospect's reply
    is the parent (RFC 5322 3.6.4), so the answer threads in THEIR mail client
    too, not only in the sender's Gmail (threadId). {} when the reply's
    Message-ID was never stored (replies received before migration 0086) --
    the answer then goes out as before."""
    from app.services import email_threading  # noqa: PLC0415

    draft = draft_for_message(session, message)
    reply = session.get(InboundReply, draft.reply_id) if draft is not None else None
    if reply is None or reply.channel != "email":
        return {}
    return email_threading.reply_headers(reply.rfc_message_id, reply.rfc_references)


#: Message states after which an approved answer is NOT on its way: held for
#: review, cancelled, failed, skipped... The reply goes back to "needs reply".
_STILL_QUEUED = (MessageStatus.SCHEDULED, MessageStatus.SENDING)


def sync_handled(session: Session, draft: ReplyDraft, message: Message | None) -> None:
    """Keep the reply's inbox "handled" marker true to what happened to the
    answer: SENT -> handled; still queued (scheduled / sending, e.g. deferred
    to the send window) -> unchanged; anything else (held for review,
    cancelled, blocked, failed) -> back in "needs reply". Commits via
    inbox.mark_handled."""
    from app.services import inbox  # noqa: PLC0415

    if message is None or message.status in _STILL_QUEUED:
        return
    reply = session.get(InboundReply, draft.reply_id)
    if reply is None:
        return
    if message.status is MessageStatus.SENT:
        actor = (session.get(User, draft.decided_by_user_id)
                 if draft.decided_by_user_id else None)
        inbox.mark_handled(session, [reply], actor=actor, handled=True)
    else:
        inbox.mark_handled(session, [reply], handled=False)


def after_send_attempt(session: Session, message_id, result: str) -> None:
    """Called after every send attempt of a message (both the send_reply_draft
    task and the beat dispatcher's send task). A no-op for anything that is not
    a Reply Copilot answer. Never raises."""
    try:
        message = session.get(Message, message_id)
        if message is None or message.origin != ORIGIN:
            return
        draft = draft_for_message(session, message)
        if draft is None:
            return
        draft.send_result = (result or "")[:60]
        session.commit()
        sync_handled(session, draft, message)
    except Exception:  # noqa: BLE001
        logger.exception("reply copilot: post-send bookkeeping failed for %s", message_id)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------

_DELIVERY = {
    MessageStatus.SCHEDULED: "queued", MessageStatus.SENDING: "queued",
    MessageStatus.SENT: "sent", MessageStatus.AWAITING_REVIEW: "held_for_review",
}


def delivery(message: Message | None) -> dict | None:
    if message is None:
        return None
    from app.services import send_holds  # noqa: PLC0415

    state = _DELIVERY.get(message.status, "blocked")
    return {"state": state, "status": message.status.value,
            "error": message.error if state == "blocked" else None,
            # A guard HOLD (Bounce Shield, plan limit...) is not a failure:
            # the UI shows "on hold: <label>" (send_holds.hold_of).
            "hold": send_holds.hold_of(message.status, message.error),
            "sent_at": _aware(message.sent_at).isoformat() if message.sent_at else None}


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value else None


def draft_out(session: Session, draft: ReplyDraft) -> dict:
    lead = session.get(Lead, draft.lead_id)
    reply = session.get(InboundReply, draft.reply_id)
    message = session.get(Message, draft.message_id) if draft.message_id else None
    return {
        "id": str(draft.id), "reply_id": str(draft.reply_id), "lead_id": str(draft.lead_id),
        "lead": ({"full_name": lead.full_name, "company": lead.company} if lead else None),
        "channel": draft.channel, "intent": draft.intent,
        "meeting_time_request": draft.meeting_time_request, "status": draft.status,
        "draft_subject": draft.draft_subject, "draft_body": draft.draft_body,
        "final_subject": draft.final_subject, "final_body": draft.final_body,
        "edited": draft.edited, "auto_sent": draft.auto_sent,
        "claim_flags": draft.claim_flags_json or [],
        "grounding": draft.grounding_json or {},
        "reply_preview": (reply.body or "")[:280] if reply else None,
        "reply_received_at": _iso(draft.reply_received_at),
        "decided_at": _iso(draft.decided_at),
        "created_at": _iso(draft.created_at),
        "delivery": delivery(message), "send_result": draft.send_result,
    }


def owned_draft(session: Session, user_id, draft_id) -> ReplyDraft | None:
    """The draft when `user_id` owns it, else None (the API answers 404)."""
    try:
        draft_id = draft_id if isinstance(draft_id, uuid.UUID) else uuid.UUID(str(draft_id))
    except (TypeError, ValueError):
        return None
    return session.execute(
        select(ReplyDraft).where(ReplyDraft.id == draft_id, ReplyDraft.user_id == user_id)
    ).scalar_one_or_none()


def list_drafts(session: Session, user_id, *, status: str = PENDING, lead_id=None,
                limit: int = 50, offset: int = 0) -> dict:
    from sqlalchemy import func  # noqa: PLC0415

    query = select(ReplyDraft).where(ReplyDraft.user_id == user_id)
    if status != "all":
        query = query.where(ReplyDraft.status == status)
    if lead_id is not None:
        query = query.where(ReplyDraft.lead_id == lead_id)
    total = session.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    order = (ReplyDraft.created_at.asc() if status == PENDING else ReplyDraft.created_at.desc())
    rows = session.execute(query.order_by(order, ReplyDraft.id).offset(offset)
                           .limit(limit)).scalars().all()
    return {"total": int(total), "limit": limit, "offset": offset, "status": status,
            "items": [draft_out(session, d) for d in rows]}


def metrics(session: Session, user_id, *, days: int = 30, now: datetime | None = None) -> dict:
    """The F04 success metric -- measured from rows, never estimated.

    median_reply_to_approval_seconds: decided_at - reply_received_at over
      drafts a PERSON approved or copied (auto-sends are counted separately).
    unedited_share: of person-approved drafts whose message reached `sent`,
      the share approved without edits. null (not 0) when there are none.
    """
    now = now or _now()
    since = now - timedelta(days=days)
    rows = session.execute(
        select(ReplyDraft).where(ReplyDraft.user_id == user_id,
                                 ReplyDraft.created_at >= since)
    ).scalars().all()
    counts = {s: 0 for s in STATUSES}
    durations: list[float] = []
    sent = sent_unedited = auto_sent = 0
    for draft in rows:
        counts[draft.status] = counts.get(draft.status, 0) + 1
        if draft.auto_sent:
            auto_sent += 1
            continue
        if draft.status in (APPROVED, COPIED) and draft.decided_at and draft.reply_received_at:
            durations.append((_aware(draft.decided_at)
                              - _aware(draft.reply_received_at)).total_seconds())
        if draft.status == APPROVED and draft.message_id:
            message = session.get(Message, draft.message_id)
            if message is not None and message.status is MessageStatus.SENT:
                sent += 1
                sent_unedited += int(not draft.edited)
    return {
        "window_days": days, "drafts": len(rows), **counts,
        "auto_sent": auto_sent,
        "approvals_measured": len(durations),
        "median_reply_to_approval_seconds": (round(statistics.median(durations), 1)
                                             if durations else None),
        "sent": sent, "sent_unedited": sent_unedited,
        "unedited_share": round(sent_unedited / sent, 4) if sent else None,
    }


def erase_for_lead(session: Session, lead_id) -> int:
    """GDPR erasure: drafts quote the person. No commit (the caller's)."""
    rows = session.execute(select(ReplyDraft).where(ReplyDraft.lead_id == lead_id)).scalars().all()
    for row in rows:
        session.delete(row)
    return len(rows)
