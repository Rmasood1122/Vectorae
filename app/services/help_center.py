"""Help-center retrieval for the support assistant (Phase 2, STAR_PROMPT A.4 2.3).

WHAT THIS IS
------------
The support assistant used to answer from nine hand-written FAQ entries
(app/services/support_kb.py). It now answers from the help center: the
fact-checked articles in docs/help-center/NN-*.md. Thirty articles are far too
much text to put in every prompt, so this module picks the passages that are
relevant to one question:

  load      parse every article once (lazily, on first use, then cached):
            YAML front matter + the markdown body.
  chunk     one chunk per H2 question heading. A chunk under ~120 tokens is
            merged with its neighbour (a two-sentence answer on its own is a
            poor retrieval unit and a poor prompt document); a chunk over ~700
            tokens is split at H3, else paragraph, boundaries. Chunk id is
            `<article-id>#<heading-slug>` and is STABLE: it is stored on chat
            messages (`faq_ids`), so changing a heading changes an id -- the
            drift tests and the FAQ alias map below exist to catch that.
  rank      pure-Python BM25 (k1=1.2, b=0.75) over title + heading + body,
            with light normalisation (lowercase, punctuation, simple plural
            stripping) and the curated synonym groups in
            docs/help-center/_synonyms.yaml. Returns the top 6 chunks.

            Three additions beyond that specification, each kept only
            because it moved a golden-set number (measurements in the
            docstrings where each is implemented, and in
            docs/program/help-center-eval.md):
              - -ed/-ing stripping next to the plural rule (_stem);
              - typo correction for words the corpus has never seen, edit
                distance 1-2 (Corpus._correct);
              - word pairs from headings, stopwords kept, as extra BM25
                terms (Corpus._heading_terms) -- without them "What is
                Vectorae?" reduces to "vectorae", which is in every chunk.
            All three are deterministic and dependency-free.

WHY LEXICAL AND NOT EMBEDDINGS
------------------------------
The stack has no embedding provider, and adding one is a vendor decision for
the founders. BM25 + synonyms is deterministic, free, offline, and measurable
against tests/help_center/golden_questions.yaml; if it misses the recall
target the misses are reported and embeddings become a separate decision.

WHAT IS NOT INDEXED
-------------------
`00-index.md` (the manifest -- its one-line scopes would match everything and
answer nothing) and every `## Related` section (a list of links, no facts).

WHERE THE FILES ARE
-------------------
`HELP_CENTER_DIR` (environment) wins; otherwise `<repo root>/docs/help-center`
resolved from this file; otherwise `./docs/help-center` from the working
directory. The Dockerfile sets HELP_CENTER_DIR=/code/docs/help-center and
.dockerignore re-includes docs/help-center/, because an image that lost the
articles would turn every answer into a ticket without failing anything else.
A missing directory is logged at ERROR and yields an EMPTY corpus -- never an
exception: this runs inside the support widget, which must not 500.

PYYAML
------
Not listed by name in requirements.txt or pyproject.toml; it is installed in
every environment by `uvicorn[standard]`, which both files declare.
tests/help_center/test_corpus.py pins that path so a change to the uvicorn
extra cannot silently remove it.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

__all__ = [
    "Article", "Section", "Chunk", "Hit", "Corpus",
    "TOP_K", "BM25_K1", "BM25_B", "MERGE_BELOW_TOKENS", "SPLIT_ABOVE_TOKENS",
    "LEGACY_FAQ_ALIASES", "help_center_dir", "load_corpus", "get_corpus",
    "reset_cache", "retrieve", "chunk_by_id", "resolve_citation",
    "sources_for", "answer_paragraph", "best_section", "opening_paragraph",
    "MockMatch", "mock_match", "MOCK_MIN_SCORE", "MOCK_MIN_HEADING_COVERAGE",
    "estimate_tokens", "slugify", "normalize",
]

TOP_K = 6
BM25_K1 = 1.2
BM25_B = 0.75
MERGE_BELOW_TOKENS = 120
SPLIT_ABOVE_TOKENS = 700

# A query term reached only through a synonym counts for half: "cost" should
# find the plans article, but a chunk that literally says "cost" should still
# outrank one that only says "plan".
SYNONYM_WEIGHT = 0.5
# A typo correction is a guess too, but a much narrower one (one or two
# edits from a word the user evidently meant): it counts for a little less
# than a typed term and more than a synonym.
CORRECTION_WEIGHT = 0.8

HELP_CENTER_DIR_ENV = "HELP_CENTER_DIR"

# ---------------------------------------------------------------------------
# Mock-mode gate (re-measured for the help center; replaces the role that
# support_kb.MIN_MOCK_MATCH_SCORE played for the nine-entry FAQ)
# ---------------------------------------------------------------------------
# Mock mode has no model, so it needs a yes/no verdict: is the best chunk an
# answer to THIS question, or merely a passage that shares its words? Two
# measurements of the top chunk's best section, both must clear:
#
#   score             the chunk's BM25 score.
#   heading coverage  IDF-weighted share of the question's own terms found in
#                     the article title + that section's question heading.
#                     Words the corpus has never seen count against it.
#
# The score alone cannot do this job. Must-refuse questions that borrow the
# product's vocabulary score as high as real ones ("Can you write the privacy
# policy for my own website?" scores 33.2, more than 76 of the 77 answerable
# questions), so a pure score floor that refuses all of them answers 1/77.
# Heading coverage separates them: a mock answer is the opening paragraph of a
# section, so it is only right when the question is close to the question
# that section answers.
#
# Chosen by measurement against tests/help_center/golden_questions.yaml (77
# answerable, 23 must-refuse); `python -m scripts.help_center.measure_golden`
# prints the full grid. correct/wrong are answerable items answered from an
# expected / unexpected chunk; refused-answered is must-refuse items answered.
#
#   Re-measured 2026-10-18 on the Help AI v2 golden set (151 answerable,
#   31 must-refuse, 44 articles). The 2026-10-11 numbers (88/23, 30
#   articles) are in docs/program/help-center-eval.md.
#
#   coverage floor (score floor 6.0)   correct  wrong  must-refuse answered
#     0.50                               72      11          1
#     0.55  (previous floor)             64       8          1
#     0.60  <- chosen                    57       5          0
#     0.65                               53       5          0
#     0.70                               46       5          0
#
#   score floor (coverage floor 0.60)  correct  wrong  must-refuse answered
#     4.0                                57       6          0
#     5.0                                57       5          0
#     6.0   <- chosen                    57       5          0
#     7.0                                57       5          0
#     7.5                                57       4          0
#     10.0                               55       4          0
#
# With 14 more articles the nearest must-refuse item is "How much will
# Vectorae cost next year?" at coverage 0.552 (it matches the plan-price
# heading), so the old 0.55 floor answered it with a price paragraph. 0.60 is
# the lowest tested floor that answers no must-refuse item, keeps a ~0.05
# margin above it, and has the lowest wrong-answer rate (5 of 62 answers) --
# the same trade the confidence floor makes: an unnecessary ticket is an
# annoyance, a confidently wrong answer is the failure that matters.
# The score floor is flat from 5.0 to 7.0; 4.0 admits a wrong answer, and the
# weakest correct answer scores 8.2 (s01), so 6.0 stays in the middle of the
# plateau and neither edge is one article edit away.
MOCK_MIN_SCORE = 6.0
MOCK_MIN_HEADING_COVERAGE = 0.60

# ---------------------------------------------------------------------------
# Legacy FAQ ids -> help-center chunk ids. PERMANENT.
# ---------------------------------------------------------------------------
# support_kb.py documents that its ids are stored on chat messages (`faq_ids`)
# and must never change. The nine entries were superseded by the help center,
# whose articles restate those topics with facts verified against the code
# (several old answers were wrong: "draft messages only, a human clicks send
# every time" is contradicted by the send path; "72-step pipeline" omits the
# 144-step one; booking is Vectorae's own calendar, not only Calendly). So an
# old id now resolves to the chunk that answers the same question. Rows
# already stored keep their old ids; resolve_citation() maps them. Never
# delete an entry; if a target heading is renamed, point the entry at the new
# chunk id (tests/help_center/test_corpus.py fails until you do).
LEGACY_FAQ_ALIASES: dict[str, str] = {
    "what-is-leadpilot": "what-is-leadpilot#what-does-vectorae-do-start-to-finish",
    "what-does-it-replace": "what-is-leadpilot#what-does-vectorae-do-start-to-finish",
    "who-is-it-for": "who-its-for-and-not-for#who-is-vectorae-designed-for",
    "how-outreach-works": "what-is-leadpilot#do-i-have-to-send-the-messages-myself",
    "learning-loop": "learning-loop-and-ab-tests#what-does-vectorae-learn-from",
    "first-campaign": "getting-started#how-do-i-create-my-first-product-and-strategy",
    "tutorial-section": "learn-tab-and-tutorials#where-do-i-find-the-training-videos",
    "contact-support": "contact-support-and-status#where-do-i-find-support-in-the-app",
    "data-storage": "privacy-data-retention-deletion#what-does-vectorae-store-about-me",
}


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """One heading and the markdown under it (H1 intro or one H2)."""

    heading: str
    slug: str
    body: str

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.heading + "\n" + self.body)


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    audience: str
    last_verified: str
    owner: str
    sources: tuple[str, ...]
    filename: str
    intro: str
    sections: tuple[Section, ...]   # H2 sections, `## Related` excluded
    related: str                    # the raw `## Related` block (not indexed)


@dataclass(frozen=True)
class Chunk:
    id: str
    article_id: str
    article_title: str
    heading: str
    # One or more sections: more than one only when short sections merged.
    sections: tuple[Section, ...]

    @property
    def body(self) -> str:
        """The chunk as markdown, every section under its own question
        heading (the article intro has none: its heading is the title, which
        travels separately)."""
        parts = []
        for section in self.sections:
            if section.heading == self.article_title:
                parts.append(section.body)
            else:
                parts.append(f"## {section.heading}\n\n{section.body}")
        return "\n\n".join(p for p in parts if p.strip())

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.heading + "\n" + self.body)


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """~4 characters per token: the usual rule of thumb for English with
    Claude's tokenizer. Used only for the merge/split boundaries, which are
    approximate by specification ("~120", "~700")."""
    return max(1, round(len(text or "") / 4))


def slugify(heading: str) -> str:
    """GitHub-style heading slug: lowercase, punctuation dropped, spaces to
    hyphens. Underscores survive ("needs_human_review")."""
    text = (heading or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "section"


# Function words only. Question words that carry meaning in this corpus
# ("who" -- "who is it for", "when", "why") are deliberately kept; IDF already
# discounts them where they are common.
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "am",
    "to", "of", "and", "or", "in", "on", "at", "it", "its", "i", "me", "my",
    "we", "our", "us", "you", "your", "do", "does", "did", "can", "could",
    "would", "should", "will", "shall", "this", "that", "these", "those",
    "as", "by", "if", "so", "what", "how", "which", "there", "here", "has",
    "have", "had", "im", "ive", "id", "dont", "doesnt", "pls", "please",
    "hi", "hello", "hey", "thanks", "thank", "just", "into", "about",
    # Low-information verbs: in "which plan do I NEED to USE for LinkedIn"
    # they matched "Do I need a plan?" harder than the LinkedIn plans answer.
    "need", "use", "want", "get", "got", "like", "let", "know", "tell",
})


def _stem(token: str) -> str:
    """Simple plural stripping, plus the two commonest verb endings.

    Applied identically to documents and queries, so an imperfect stem
    ("shared" -> "shar") is harmless: both sides agree. The cost is the odd
    conflation ("settings" and "set"), which BM25's IDF keeps small.

    -ed/-ing go beyond the specification's "simple plural stripping" on
    purpose: without them "track" never met "Are opens tracked?" and
    "booked" never met "booking" (measured on the golden set; see
    docs/program/help-center-eval.md). A stem must keep 3+ letters, and a
    doubled final consonant is undone ("stopped" -> "stop", "planned" ->
    "plan"), except l/s/z ("billing" -> "bill", "passed" -> "pass").
    """
    if len(token) <= 3 or token.isdigit():
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("sses"):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        token = token[:-1]
    if token.endswith("ied") and len(token) > 4:
        return token[:-3] + "y"
    for suffix in ("ing", "ed"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            stem = token[:-len(suffix)]
            if (len(stem) >= 4 and stem[-1] == stem[-2]
                    and stem[-1] not in "aeiouylsz"):
                stem = stem[:-1]
            return stem
    return token


_WORD = re.compile(r"[a-z0-9]+")


def _raw_tokens(text: str) -> list[str]:
    """Lowercased, apostrophes collapsed ("don't" -> "dont"), split on
    anything that is not a letter or digit, plural-stripped. Stopwords KEPT
    -- synonym phrases are matched against this stream. A stopword is
    never stemmed, so it is still recognised as one ("does" must not become
    the content word "doe")."""
    lowered = (text or "").lower().replace("'", "").replace("’", "")
    return [t if t in STOPWORDS else _stem(t) for t in _WORD.findall(lowered)]


def normalize(text: str) -> list[str]:
    """The index/query token stream: _raw_tokens minus stopwords."""
    return [t for t in _raw_tokens(text) if t not in STOPWORDS]


def _plain(markdown: str) -> str:
    """Markdown -> text for indexing and for mock answers: links keep their
    text, emphasis/code markers and table pipes go. No words are added or
    removed, so a mock answer built from this is still the article verbatim."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", markdown or "")
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return text


# ---------------------------------------------------------------------------
# Loading and chunking
# ---------------------------------------------------------------------------


def help_center_dir() -> Path:
    override = os.environ.get(HELP_CENTER_DIR_ENV, "").strip()
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[2]
    candidate = repo_root / "docs" / "help-center"
    if candidate.is_dir():
        return candidate
    return Path.cwd() / "docs" / "help-center"


_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_article(path: Path) -> Article:
    raw = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(raw)
    if not match:
        raise ValueError(f"{path.name}: no YAML front matter")
    meta = yaml.safe_load(match.group(1)) or {}
    body = raw[match.end():]

    lines = body.splitlines()
    # Drop the H1: the title comes from front matter.
    lines = [ln for ln in lines if not re.match(r"^#\s", ln)]

    intro_lines: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in lines:
        heading = re.match(r"^##\s+(.*?)\s*$", line)
        if heading and not line.startswith("###"):
            sections.append((heading.group(1), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            intro_lines.append(line)

    related = ""
    kept: list[Section] = []
    for heading, body_lines in sections:
        text = "\n".join(body_lines).strip()
        if heading.strip().lower() == "related":
            related = text
            continue
        kept.append(Section(heading=heading, slug=slugify(heading), body=text))

    return Article(
        id=str(meta.get("id", "")).strip(),
        title=str(meta.get("title", "")).strip(),
        audience=str(meta.get("audience", "")).strip(),
        last_verified=str(meta.get("last_verified", "")).strip(),
        owner=str(meta.get("owner", "")).strip(),
        sources=tuple(str(s) for s in (meta.get("sources") or [])),
        filename=path.name,
        intro="\n".join(intro_lines).strip(),
        sections=tuple(kept),
        related=related,
    )


def _blocks(markdown: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n\s*\n", markdown or "") if b.strip()]


def _split_large(section: Section) -> list[Section]:
    """Split a section over SPLIT_ABOVE_TOKENS at H3 boundaries, else at
    paragraph boundaries, packing greedily. Continuation parts get the ids
    `<slug>-2`, `<slug>-3`, ..."""
    if section.tokens <= SPLIT_ABOVE_TOKENS:
        return [section]
    if re.search(r"^###\s", section.body, re.MULTILINE):
        units = [u.strip() for u in re.split(r"(?=^###\s)", section.body,
                                              flags=re.MULTILINE) if u.strip()]
    else:
        units = _blocks(section.body)
    parts: list[list[str]] = [[]]
    for unit in units:
        candidate = "\n\n".join(parts[-1] + [unit])
        if parts[-1] and estimate_tokens(section.heading + "\n" + candidate) > SPLIT_ABOVE_TOKENS:
            parts.append([unit])
        else:
            parts[-1].append(unit)
    out = []
    for index, part in enumerate(parts):
        suffix = "" if index == 0 else f"-{index + 1}"
        heading = section.heading if index == 0 else f"{section.heading} (continued)"
        out.append(Section(heading=heading, slug=section.slug + suffix,
                           body="\n\n".join(part)))
    return out


def _chunk_article(article: Article) -> list[Chunk]:
    units: list[Section] = []
    if article.intro:
        units.append(Section(heading=article.title, slug=slugify(article.title),
                             body=article.intro))
    for section in article.sections:
        units.extend(_split_large(section))

    # Merge small units forward (into the next one); the last unit, if small,
    # merges backward. A merged chunk is named after its LARGEST unit (first
    # one on a tie): the id should name the content that dominates the chunk,
    # not a two-line section that happened to precede it.
    groups: list[list[Section]] = [[u] for u in units]

    def size(group: list[Section]) -> int:
        return estimate_tokens("\n".join(s.heading + "\n" + s.body for s in group))

    index = 0
    while index < len(groups) and len(groups) > 1:
        if size(groups[index]) < MERGE_BELOW_TOKENS:
            if index + 1 < len(groups):
                groups[index] = groups[index] + groups.pop(index + 1)
                continue          # re-check: still small -> absorb another
            groups[index - 1] = groups[index - 1] + groups.pop(index)
            break
        index += 1

    chunks = []
    for group in groups:
        lead = max(enumerate(group), key=lambda item: (item[1].tokens, -item[0]))[1]
        chunks.append(Chunk(
            id=f"{article.id}#{lead.slug}",
            article_id=article.id,
            article_title=article.title,
            heading=lead.heading,
            sections=tuple(group),
        ))
    return chunks


def _load_synonyms(directory: Path) -> tuple[tuple[tuple[str, ...], ...], ...]:
    """Each group -> tuple of terms; each term -> its raw token tuple."""
    path = directory / "_synonyms.yaml"
    if not path.is_file():
        return ()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    groups = []
    for group in data.get("groups") or []:
        terms = tuple(t for t in (tuple(_raw_tokens(str(term))) for term in group) if t)
        if len(terms) >= 2:
            groups.append(terms)
    return tuple(groups)


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------


class Corpus:
    """Parsed articles, their chunks, and a BM25 index over the chunks."""

    def __init__(self, articles: list[Article],
                 synonyms: tuple[tuple[tuple[str, ...], ...], ...] = ()):
        self.articles: tuple[Article, ...] = tuple(articles)
        self.synonyms = synonyms
        chunks: list[Chunk] = []
        for article in self.articles:
            chunks.extend(_chunk_article(article))
        self.chunks: tuple[Chunk, ...] = tuple(chunks)
        self.by_id: dict[str, Chunk] = {c.id: c for c in self.chunks}
        self.articles_by_id: dict[str, Article] = {a.id: a for a in self.articles}

        self._tf: list[Counter] = []
        self._len: list[int] = []
        df: Counter = Counter()
        for chunk in self.chunks:
            terms = normalize(self._index_text(chunk)) + self._heading_terms(chunk)
            counts = Counter(terms)
            self._tf.append(counts)
            self._len.append(len(terms))
            df.update(counts.keys())
        self.n = len(self.chunks)
        self.avgdl = (sum(self._len) / self.n) if self.n else 0.0
        self.df = df
        self.idf = {
            term: math.log(1.0 + (self.n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }
        self._synonym_tokens = frozenset(
            token for group in synonyms for term in group for token in term)
        self._corrections: dict[str, str | None] = {}

    @staticmethod
    def _index_text(chunk: Chunk) -> str:
        """Title + every section heading + body (markdown markers stripped).

        Headings are counted once. Counting them twice was measured on the
        golden set (with the heading bigrams below in place): recall@6 0.974
        once vs 0.961 twice, top-1 hits 61 vs 62 of 77, identical mock-mode
        outcomes -- so the simpler form stays."""
        headings = " ".join(s.heading for s in chunk.sections)
        return f"{chunk.article_title}\n{headings}\n{_plain(chunk.body)}"

    @staticmethod
    def _heading_terms(chunk: Chunk) -> list[str]:
        """Phrase terms from the chunk's section headings, STOPWORDS KEPT:
        every adjacent word pair, plus the whole heading as one term.

        Unigram BM25 cannot see "What is Vectorae?": once "what" and "is"
        are dropped only "vectorae" is left, and it occurs in nearly every
        chunk. A pair ("is_vectorae") narrows that to a few headings; the
        whole-heading term ("=what_is_vectorae") to exactly one, so a user
        who types -- or clicks -- a question the help center literally asks
        gets that section. Only the chunk's OWN section headings count (the
        article title enters through the intro section, whose heading it
        is); giving the title to every chunk of an article made all nine
        chunks of article 01 match equally. Body text is not paired: it
        would add thousands of noisy pairs for no measured gain."""
        out: list[str] = []
        for section in chunk.sections:
            tokens = _raw_tokens(section.heading)
            out.extend(_bigrams(tokens))
            out.append(_phrase(tokens))
        return out

    def _known(self, token: str) -> bool:
        return token in self.df or token in self._synonym_tokens

    def _correct(self, token: str) -> str | None:
        """The vocabulary term a misspelt token most likely meant, or None.

        Only for tokens the corpus has never seen, alphabetic, 4+ letters
        ("gmial" -> "gmail", "linkdin" -> "linkedin"). Allowed distance is 1
        (2 from 8 letters up), counting a swap of two adjacent letters as one
        edit. Ties go to the more common term, then alphabetical order, so
        the result is deterministic. A word that is merely unknown -- most
        off-topic vocabulary -- finds nothing within that distance and stays
        unmatched, which is what keeps this from making off-topic questions
        look on-topic (measured by the must-refuse items in the golden set).
        """
        if token in self._corrections:
            return self._corrections[token]
        best: tuple[int, int, str] | None = None
        if len(token) >= 4 and token.isalpha():
            limit = 2 if len(token) >= 8 else 1
            for term in self.df:
                if abs(len(term) - len(token)) > limit or not term.isalpha():
                    continue
                distance = _osa_distance(token, term, limit)
                if distance <= limit:
                    key = (distance, -self.df[term], term)
                    if best is None or key < best:
                        best = key
        result = best[2] if best else None
        self._corrections[token] = result
        return result

    def analyse(self, query: str) -> tuple[dict[str, float], list[str]]:
        """(weighted query terms, unknown terms).

        Weights: 1.0 for a typed term, CORRECTION_WEIGHT for a typo
        correction, SYNONYM_WEIGHT for a term reached through a synonym
        group. Unknown terms are the ones neither the corpus, the synonym
        file nor a correction could place; the mock-mode coverage gate
        counts them against an answer."""
        raw: list[str] = []
        corrected: set[str] = set()
        unknown: list[str] = []
        for token in _raw_tokens(query):
            if token in STOPWORDS or self._known(token):
                raw.append(token)
                continue
            fixed = self._correct(token)
            if fixed is None:
                unknown.append(token)
                raw.append(token)
            else:
                corrected.add(fixed)
                raw.append(fixed)
        weights: dict[str, float] = {}
        for term in raw:
            if term in STOPWORDS:
                continue
            weight = CORRECTION_WEIGHT if term in corrected else 1.0
            weights[term] = max(weights.get(term, 0.0), weight)
        for phrase in _bigrams(raw) + [_phrase(raw)]:
            if phrase in self.df:
                weights.setdefault(phrase, 1.0)
        for group in self.synonyms:
            typed_terms = [term for term in group if _contains(raw, term)]
            if not typed_terms:
                continue
            # When the user's own word never occurs in the help center ("ten"
            # where every article writes "10", "2fa" where they write
            # "two-factor"), the synonym is not a guess layered on a match --
            # it is the ONLY way the question can match, so it counts in full.
            stands_in = all(
                all(token in STOPWORDS or token not in self.df for token in term)
                for term in typed_terms)
            expansion = 1.0 if stands_in else SYNONYM_WEIGHT
            for term in group:
                for token in term:
                    if token in STOPWORDS:
                        continue
                    if weights.get(token, 0.0) < expansion:
                        weights[token] = expansion
        return weights, unknown

    def query_weights(self, query: str) -> dict[str, float]:
        return self.analyse(query)[0]

    def coverage(self, query: str, section: Section, article_title: str) -> float:
        """Share of the question's information that `section` contains.

        IDF-weighted over the TYPED terms only (synonyms excluded -- they are
        guesses about intent, not things the user said). A term the corpus
        has never seen counts at the corpus's highest IDF: an unknown word is
        the strongest available sign the question is about something the
        help center does not cover ("quantum", "biryani", "bitcoin").
        """
        weights, unknown = self.analyse(query)
        typed = {t for t, w in weights.items()
                 if w > SYNONYM_WEIGHT and not _is_phrase(t)} - set(unknown)
        if not typed and not unknown:
            return 0.0
        present = set(normalize(f"{article_title}\n{section.heading}\n{_plain(section.body)}"))
        ceiling = max(self.idf.values()) if self.idf else 1.0
        total = sum(self.idf.get(t, ceiling) for t in typed) + ceiling * len(set(unknown))
        matched = sum(self.idf.get(t, 0.0) for t in typed if t in present)
        return matched / total if total else 0.0

    def _score(self, weights: dict[str, float], tf: Counter, length: int) -> float:
        if not self.n:
            return 0.0
        norm = BM25_K1 * (1.0 - BM25_B + BM25_B * (length / self.avgdl if self.avgdl else 0.0))
        score = 0.0
        for term, weight in weights.items():
            freq = tf.get(term, 0)
            if not freq:
                continue
            score += weight * self.idf.get(term, 0.0) * (freq * (BM25_K1 + 1.0)) / (freq + norm)
        return score

    def search(self, query: str, k: int = TOP_K) -> list[Hit]:
        weights = self.query_weights(query)
        if not weights or not self.n:
            return []
        scored = []
        for index, chunk in enumerate(self.chunks):
            score = self._score(weights, self._tf[index], self._len[index])
            if score > 0.0:
                # index as tiebreak: corpus order, deterministic.
                scored.append((-score, index, chunk))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [Hit(chunk=chunk, score=-neg) for neg, _i, chunk in scored[:k]]

    def section_score(self, query: str, section: Section, article_title: str) -> float:
        weights = self.query_weights(query)
        terms = (normalize(f"{article_title}\n{section.heading}\n{_plain(section.body)}")
                 + _bigrams(_raw_tokens(section.heading))
                 + [_phrase(_raw_tokens(section.heading))])
        return self._score(weights, Counter(terms), len(terms))


def _osa_distance(a: str, b: str, limit: int) -> int:
    """Optimal-string-alignment edit distance (insert, delete, substitute,
    swap adjacent), returning limit + 1 as soon as it cannot be <= limit."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous2: list[int] | None = None
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if (previous2 is not None and i > 1 and j > 1
                    and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]):
                current[j] = min(current[j], previous2[j - 2] + 1)
        if min(current) > limit:
            return limit + 1
        previous2, previous = previous, current
    return previous[len(b)]


def _phrase(tokens: list[str]) -> str:
    """A whole heading or question as one term. '=' cannot occur in a token."""
    return "=" + "_".join(tokens)


def _is_phrase(term: str) -> bool:
    return "_" in term or term.startswith("=")


def _bigrams(tokens: list[str]) -> list[str]:
    """Adjacent pairs with at least one content word: "is_vectorae" is a
    signal, "how_do" is in half the headings and only adds noise."""
    return [f"{a}_{b}" for a, b in pairwise(tokens)
            if a not in STOPWORDS or b not in STOPWORDS]


def _contains(haystack: list[str], needle: tuple[str, ...]) -> bool:
    width = len(needle)
    if not width or width > len(haystack):
        return False
    return any(tuple(haystack[i:i + width]) == needle
               for i in range(len(haystack) - width + 1))


# ---------------------------------------------------------------------------
# Loading entry points (lazy, cached)
# ---------------------------------------------------------------------------


def load_corpus(directory: Path | None = None) -> Corpus:
    """Parse every NN-*.md article under `directory` (00-index.md excluded).

    Raises on a malformed article -- callers that must not raise go through
    get_corpus(), which degrades to an empty corpus."""
    directory = directory or help_center_dir()
    if not directory.is_dir():
        raise FileNotFoundError(f"help center directory not found: {directory}")
    articles = []
    for path in sorted(directory.glob("[0-9][0-9]-*.md")):
        if path.name.startswith("00-"):
            continue
        articles.append(_parse_article(path))
    return Corpus(articles, _load_synonyms(directory))


_lock = threading.Lock()
_cached: Corpus | None = None


def get_corpus() -> Corpus:
    """The process-wide corpus, loaded on first use. NEVER raises."""
    global _cached
    if _cached is not None:
        return _cached
    with _lock:
        if _cached is None:
            try:
                _cached = load_corpus()
                logger.info("help center loaded: %d articles, %d chunks",
                            len(_cached.articles), len(_cached.chunks))
            except Exception as exc:  # noqa: BLE001 -- must not break the widget
                logger.error(
                    "help center could not be loaded (%s: %s) -- the support "
                    "assistant has NO documents and will offer tickets. "
                    "Check %s / the image contents.",
                    type(exc).__name__, exc, HELP_CENTER_DIR_ENV)
                _cached = Corpus([])
    return _cached


def reset_cache() -> None:
    global _cached
    with _lock:
        _cached = None


def retrieve(query: str, k: int = TOP_K) -> list[Hit]:
    """Top-k chunks for `query`, best first, with BM25 scores. NEVER raises:
    it runs inside the support widget, where an exception is a 500."""
    if not (query or "").strip():
        return []
    try:
        return get_corpus().search(query, k)
    except Exception:
        logger.exception("help center retrieval failed for %r", query[:200])
        return []


def chunk_by_id(chunk_id: str) -> Chunk | None:
    return get_corpus().by_id.get(chunk_id)


def resolve_citation(cited_id: str) -> Chunk | None:
    """A stored or model-cited id -> its chunk. Accepts current chunk ids and
    the permanent legacy FAQ ids."""
    if not isinstance(cited_id, str):
        return None
    chunk = chunk_by_id(cited_id)
    if chunk is not None:
        return chunk
    alias = LEGACY_FAQ_ALIASES.get(cited_id)
    return chunk_by_id(alias) if alias else None


def sources_for(cited_ids) -> list[dict]:
    """The articles behind a list of cited ids, for display under an answer.

    One entry per ARTICLE (several chunks of one article are one source),
    in citation order. Unknown ids are skipped, never an error -- a stored row
    from an older corpus must still render."""
    out: list[dict] = []
    seen: set[str] = set()
    for cited in cited_ids or ():
        chunk = resolve_citation(cited)
        if chunk is None or chunk.article_id in seen:
            continue
        seen.add(chunk.article_id)
        out.append({"id": chunk.id, "article_id": chunk.article_id,
                    "title": chunk.article_title})
    return out


def best_section(chunk: Chunk, query: str) -> Section:
    """The section of `chunk` that best matches `query` (first on a tie).

    Matters only for merged chunks: a short section merged into its
    neighbour must not be answered with the neighbour's paragraph."""
    if len(chunk.sections) == 1:
        return chunk.sections[0]
    corpus = get_corpus()
    scored = [(corpus.section_score(query, s, chunk.article_title), -i, s)
              for i, s in enumerate(chunk.sections)]
    return max(scored, key=lambda item: (item[0], item[1]))[2]


def opening_paragraph(section: Section) -> str:
    """The section's first paragraph as plain text, verbatim from the article.
    A paragraph ending in ':' introduces a list or table, so that next block
    is included -- the lead-in alone answers nothing."""
    blocks = _blocks(section.body)
    if not blocks:
        return ""
    picked = [blocks[0]]
    if blocks[0].rstrip().endswith(":") and len(blocks) > 1:
        picked.append(blocks[1])
    return _plain("\n\n".join(picked)).strip()


def answer_paragraph(chunk: Chunk, query: str) -> str:
    return opening_paragraph(best_section(chunk, query))


@dataclass(frozen=True)
class MockMatch:
    """The best chunk for a question, scored for mock mode's yes/no gate."""

    chunk: Chunk
    section: Section
    score: float              # the chunk's BM25 score
    heading_coverage: float   # share of the question found in title + heading
    text: str                 # what mock mode would answer, verbatim


def mock_match(query: str) -> MockMatch | None:
    """Top chunk, its best section and both gate measurements, or None when
    nothing matches at all. Applies NO threshold -- the caller decides, so
    the measurement script and the answerer use one code path."""
    hits = retrieve(query, 1)
    if not hits:
        return None
    hit = hits[0]
    try:
        section = best_section(hit.chunk, query)
        heading_only = Section(heading=section.heading, slug=section.slug, body="")
        coverage = get_corpus().coverage(query, heading_only, hit.chunk.article_title)
        text = opening_paragraph(section)
    except Exception:   # never raise into the widget
        logger.exception("help center mock match failed for %r", query[:200])
        return None
    return MockMatch(chunk=hit.chunk, section=section, score=hit.score,
                     heading_coverage=coverage, text=text)
