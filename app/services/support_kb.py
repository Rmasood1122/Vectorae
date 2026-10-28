"""The ORIGINAL support knowledge base (Feature 3) — superseded in Phase 2.

SUPERSEDED BY THE HELP CENTER
-----------------------------
The assistant now answers from the fact-checked articles in docs/help-center/
via app/services/help_center.py. Nothing here is sent to the model or shown as
an answer any more. What still lives here, and why:

  REFUSAL_MESSAGE, TICKET_SUGGESTION, MOCK_NO_MATCH_MESSAGE
                  the canned copy support_chat.py returns -- unchanged.
  FAQ ids         permanent: stored chat messages reference them. Each one
                  resolves to its help-center chunk through
                  help_center.LEGACY_FAQ_ALIASES; GET /support/faq still lists
                  the nine questions (with answers taken from the corpus).
  FAQ answers     kept verbatim for the record and for the before/after
                  measurement (scripts/help_center/measure_golden.py). Several
                  are contradicted by the code ("A human clicks send every
                  time"; "72-step pipeline" only), which is exactly why they
                  are no longer served.

The text below describes the original design.

WHY A CURATED FAQ AND NOT RAG OR "THE MODEL JUST KNOWS"
------------------------------------------------------
A support bot that answers from the model's own knowledge invents features.
It will confidently describe a LinkedIn automation Vectorae does not have, or
quote a price nobody set, and the user believes it because it sounds like the
product's own voice. That is worse than no chat at all: it generates support
load instead of absorbing it, and it can make promises the product cannot keep.

So the answers below are WRITTEN BY A HUMAN and the model's job is narrowed to
one thing: pick the relevant entries and phrase them for this particular
question. It may compress, reorder and combine. It may not add facts.

CHANGING THE CONTENT
--------------------
Edit the entries here. No migration, no SQL, no deploy step beyond the normal
one — same reasoning as app/services/tutorials.py. `id` values are referenced
in stored chat messages (`faq_ids`) for auditing which answer was used, so
treat them as permanent: change text freely, change ids never.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["FaqEntry", "FAQ", "by_id", "search", "scored", "best_match",
           "as_prompt_context", "REFUSAL_MESSAGE", "TICKET_SUGGESTION",
           "MOCK_NO_MATCH_MESSAGE", "MIN_MOCK_MATCH_SCORE"]


@dataclass(frozen=True)
class FaqEntry:
    id: str
    question: str
    answer: str
    # Extra words a user might type that do not appear in the question or
    # answer. Used only by the keyword pre-filter, never shown to anyone.
    keywords: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"id": self.id, "question": self.question, "answer": self.answer}


# ---------------------------------------------------------------------------
# The knowledge base
# ---------------------------------------------------------------------------
# Content supplied by the product owner on 2026-08-29 and reproduced verbatim.
# Do not "improve" the wording here without checking with them — these answers
# are the product's public commitments, notably the compliance-relevant one
# about drafts and LinkedIn.

FAQ: tuple[FaqEntry, ...] = (
    FaqEntry(
        id="what-is-leadpilot",
        question="What is Vectorae?",
        answer=(
            "Vectorae is an AI-powered client acquisition system "
            "that replaces manual outbound prospecting. It runs a 72-step "
            "research pipeline, sources leads via Apollo.io, verifies emails "
            "via Hunter.io, sends outreach via Gmail and WhatsApp, and books "
            "meetings via Calendly. It runs 24/7 and gets smarter after every "
            "campaign."
        ),
        keywords=("what is", "overview", "product", "platform", "about"),
    ),
    FaqEntry(
        id="what-does-it-replace",
        question="What does Vectorae replace?",
        answer=(
            "It replaces Apollo.io + Hunter.io + Instantly + spreadsheets — "
            "all in one system with a learning loop."
        ),
        keywords=("replace", "instead of", "alternative", "vs", "compare",
                  "instantly", "spreadsheet"),
    ),
    FaqEntry(
        id="who-is-it-for",
        question="Who is Vectorae for?",
        answer=(
            "Boutique agency owners (3-30 staff), independent consultants and "
            "coaches, early-stage B2B SaaS founders doing founder-led sales, "
            "and fractional sales consultants who want to resell or "
            "white-label."
        ),
        keywords=("who", "for me", "agency", "consultant", "coach", "founder",
                  "saas", "white label", "resell"),
    ),
    FaqEntry(
        id="how-outreach-works",
        question="How does outreach work?",
        answer=(
            "Once you launch a campaign, Vectorae sends its sequences "
            "automatically from the Gmail, LinkedIn and WhatsApp accounts you "
            "connect, within compliance limits: only in permitted sending "
            "hours in the prospect's time zone, within daily per-account "
            "limits, and with an opt-out check right before every send. By "
            "default, messages to senior decision-makers, to prospects whose "
            "last reply was an objection, or whose copy trips the tone checks "
            "are held in the review queue for your approval first."
        ),
        keywords=("outreach", "send", "linkedin", "automation", "automated",
                  "email", "whatsapp", "draft", "compliance"),
    ),
    FaqEntry(
        id="learning-loop",
        question="How does the learning loop work?",
        answer=(
            "Every campaign outcome — replies, meetings booked, deals won — "
            "feeds back into the system. Future campaigns use this data to "
            "improve messaging and targeting automatically."
        ),
        keywords=("learning", "loop", "improve", "smarter", "playbook",
                  "optimis", "optimiz"),
    ),
    FaqEntry(
        id="first-campaign",
        question="How do I set up my first campaign?",
        answer=(
            "Go to Pipeline tab, enter your product or service description, "
            "and Vectorae runs the 72-step research pipeline to build your "
            "ICP, find leads, and create outreach messages."
        ),
        keywords=("setup", "set up", "start", "first campaign", "get started",
                  "begin", "onboarding", "pipeline tab"),
    ),
    FaqEntry(
        id="tutorial-section",
        question="What is the tutorial section?",
        answer=(
            "The Learn tab has 9 expert tutorials across Beginner, "
            "Intermediate, and Advanced levels. Progress is tracked per video "
            "and badges are awarded on completion."
        ),
        keywords=("tutorial", "learn", "video", "training", "course", "badge",
                  "lesson"),
    ),
    FaqEntry(
        id="contact-support",
        question="How do I contact support?",
        answer=(
            "Use this AI chat for instant answers. If the AI cannot answer, "
            "submit a ticket using the button below and the team will respond "
            "within 24 hours."
        ),
        keywords=("support", "help", "contact", "human", "ticket", "agent",
                  "talk to someone"),
    ),
    FaqEntry(
        id="data-storage",
        question="What data do you store?",
        answer=(
            "Leads, campaign results, outreach history, and your ICP profile. "
            "See the Privacy Policy for full details."
        ),
        keywords=("data", "store", "privacy", "gdpr", "retention", "delete",
                  "personal"),
    ),
)

_BY_ID = {entry.id: entry for entry in FAQ}
assert len(_BY_ID) == len(FAQ), "duplicate FAQ id"


# ---------------------------------------------------------------------------
# Canned copy
# ---------------------------------------------------------------------------
# These two strings are returned WITHOUT calling the model. Asking the model to
# phrase its own refusal is how a refusal turns into a partial answer: given the
# chance to write a sentence about a topic, it writes a sentence about that
# topic. A constant cannot drift.

REFUSAL_MESSAGE = (
    "I can only help with questions about Vectorae — how it works, setting up "
    "campaigns, outreach, the learning loop, tutorials, your data, and getting "
    "support. Ask me anything about the product and I'll do my best."
)

TICKET_SUGGESTION = (
    "I'm not confident enough to answer that accurately, and I'd rather not "
    "guess. Submit a ticket and the team will respond within 24 hours."
)

# Mock mode's version of the same thing. Worded differently on purpose: the
# sentence above is the model declining to trust itself, which is not what
# happened here. Here there is no model at all and the FAQ simply does not
# cover the question, so claiming a confidence judgement would be a lie about
# how the answer was produced.
MOCK_NO_MATCH_MESSAGE = (
    "I don't have an answer for that in my help topics yet. Submit a ticket "
    "and the team will respond within 24 hours."
)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def by_id(entry_id: str) -> FaqEntry | None:
    return _BY_ID.get(entry_id)


def search(query: str) -> list[FaqEntry]:
    """Keyword pre-filter, best match first.

    Deliberately crude, and deliberately NOT the thing that decides the answer.
    Its only job is ordering the context handed to the model; the model still
    sees the ENTIRE knowledge base either way (nine short entries cost almost
    nothing), so a bad ranking here degrades phrasing, never correctness.

    A retrieval step that dropped low-ranked entries would be a different and
    much riskier design: the model cannot cite what it was not shown, and
    "sorry, I don't know" for a question the FAQ answers is exactly the
    failure this feature exists to prevent.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return list(FAQ)

    scored: list[tuple[int, int, FaqEntry]] = []
    for index, entry in enumerate(FAQ):
        score = 0
        if needle in entry.question.lower():
            score += 10
        for word in needle.split():
            if len(word) < 3:
                continue  # "is", "do", "my" match everything and rank nothing
            if word in entry.question.lower():
                score += 3
            if word in entry.answer.lower():
                score += 2
            # BIDIRECTIONAL on purpose. `word in kw` alone meant the typed
            # word had to be a SUBSTRING of the keyword, so the keyword
            # "tutorial" did not match a user typing "tutorials" -- and
            # "what tutorials are there" ranked the generic overview entry
            # first, because "what" matched its question. Checking both
            # directions covers ordinary plurals and stems without dragging
            # in a stemmer for nine entries.
            if any(word in kw or kw in word for kw in entry.keywords):
                score += 4
        # index keeps the sort stable and the catalogue order as the tiebreak
        scored.append((-score, index, entry))

    scored.sort()
    return [entry for _score, _index, entry in scored]


# ---------------------------------------------------------------------------
# Mock-mode matching (Task 4)
# ---------------------------------------------------------------------------
# `search` above ranks the WHOLE knowledge base and never returns nothing,
# because the model is shown every entry regardless of ranking. Mock mode has
# no model, so it needs the opposite thing: a yes/no verdict on whether any
# entry is actually relevant, and a ticket when none is.
#
# That verdict needs a stricter score than `search` produces. `search` counts
# any word of three or more characters, so "the" -- which appears in most
# answers -- scores. Fine when the score only reorders a list the model sees in
# full; not fine when the score decides whether a user gets an answer at all:
# "what is the weather" would match on "the" and be answered from the FAQ.

STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "are", "was", "were", "this", "that", "with", "from",
    "has", "have", "had", "its", "but", "not", "you", "your", "yours", "can",
    "could", "would", "should", "does", "did", "doing", "what", "when",
    "where", "which", "who", "whom", "why", "how", "there", "their", "them",
    "they", "then", "than", "into", "onto", "over", "under", "after",
    "before", "between", "because", "while", "some", "any", "all", "also",
    "just", "very", "much", "many", "more", "most", "such", "only", "own",
    "same", "too", "get", "got", "let", "tell", "please", "thanks", "thank",
    "need", "want", "know", "about", "make", "made", "use", "used", "using",
    "give", "say", "see", "look", "take", "come",
})

# Chosen by measurement, not by feel. Against 20 on-topic questions and 15
# off-topic ones (the battery in tests/test_support_chat.py::TestMockMatching):
#
#   threshold 2  ->  18/20 answered,  2/15 off-topic WRONGLY answered
#   threshold 3  ->  18/20 answered,  0/15 off-topic wrongly answered
#   threshold 4  ->  18/20 answered,  0/15 off-topic wrongly answered
#   threshold 5  ->  16/20 answered,  0/15 off-topic wrongly answered
#
# 4 is the highest threshold that costs no recall, and it lands on a
# meaningful boundary: exactly one keyword hit. Below it, a single incidental
# word shared with an answer would be enough to answer.
#
# The two on-topic questions it does NOT answer are "pricing plans" -- correct,
# the FAQ has no pricing entry, so a ticket is the right outcome -- and "who is
# it for", which is genuinely in the FAQ but consists entirely of stopwords
# once "who" and "for" are removed. Admitting "who" would score
# "who won the world cup" against the who-is-it-for entry, so the miss is
# deliberate: it costs one unnecessary ticket, and the alternative costs a
# confidently wrong answer. That is the same trade the confidence floor makes.
MIN_MOCK_MATCH_SCORE = 4


def _tokens(query: str) -> list[str]:
    """Meaningful words only: lowercase, punctuation stripped, stopwords gone."""
    words = re.findall(r"[a-z0-9']+", (query or "").lower())
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS]


def _score(entry: FaqEntry, query: str, tokens: list[str]) -> int:
    needle = (query or "").strip().lower()
    score = 0
    if needle and needle in entry.question.lower():
        score += 10
    question = entry.question.lower()
    answer = entry.answer.lower()
    for word in tokens:
        if word in question:
            score += 3
        if word in answer:
            score += 2
        # Bidirectional, for the same plural/stem reason as `search`.
        if any(word in kw or kw in word for kw in entry.keywords):
            score += 4
    return score


def scored(query: str) -> list[tuple[int, FaqEntry]]:
    """Every entry with its match score, best first. Case-insensitive."""
    tokens = _tokens(query)
    ranked = [
        (-_score(entry, query, tokens), index, entry)
        for index, entry in enumerate(FAQ)
    ]
    ranked.sort()
    return [(-neg, entry) for neg, _index, entry in ranked]


def best_match(query: str) -> FaqEntry | None:
    """The one entry that answers `query`, or None when nothing does.

    None is the whole point of this function: it is what makes mock mode offer
    a ticket instead of answering from an entry that merely shares a word.
    """
    if not (query or "").strip():
        return None
    top_score, entry = scored(query)[0]
    return entry if top_score >= MIN_MOCK_MATCH_SCORE else None


def as_prompt_context(query: str | None = None) -> str:
    """The knowledge base rendered for the system prompt.

    Every entry is included, ordered by relevance to `query`. Ids are included
    so the model can report which entries it used, which is what makes an
    answer auditable after the fact.
    """
    entries = search(query) if query else list(FAQ)
    blocks = [
        f"[{entry.id}]\nQ: {entry.question}\nA: {entry.answer}"
        for entry in entries
    ]
    return "\n\n".join(blocks)
