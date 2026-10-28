"""The single gateway for every embedding and rerank call in Vectorae.

Help AI v2 (docs/program/DECISIONS.md HELPAI-VENDOR) retrieves help-center
chunks with dense vectors and a cross-encoder reranker from Voyage AI. Like
app/services/anthropic_client.py for the model, this module is the ONLY place
that talks to the vendor (tests/help_center/test_embeddings_client.py fails on
a Voyage call anywhere else), so timeouts, retries, metering and the mock live
in one place.

ENDPOINTS (verified against docs.voyageai.com on 2026-10-22)
  POST /v1/contextualizedembeddings  {"inputs": [[chunk, ...], ...], "model",
      "input_type": "document"|"query", "output_dimension"}
      -> {"data": [{"data": [{"embedding": [...], "index"}], "index"}],
          "usage": {"total_tokens"}}
      One inner list per document: every chunk in it is embedded with the
      rest of that document as context. A query is an inner list of one.
      Limits: 1,000 inner lists, 16K chunks, 32K tokens per request (120K only
      with server-side auto-chunking, which we do not use).
  POST /v1/rerank  {"query", "documents", "model", "top_k"}
      -> {"data": [{"index", "relevance_score"}], "usage": {"total_tokens"}}

MODES (settings.embeddings_mode, AI_MODE-style)
  live  HTTPS to Voyage with VOYAGE_API_KEY.
  mock  Deterministic feature-hashed vectors, no network, no spend. Similar
        wording gives similar vectors, so a PostgreSQL vector-search test is
        meaningful offline; it is NOT a stand-in for Voyage's quality.
  ""    auto: live when VOYAGE_API_KEY is set, mock otherwise.

METERING
  Every live request is priced through usage_meter -- the same helper the
  Anthropic gateway uses -- as provider "voyage" (embeddings) or
  "voyage_rerank": check_budget() before, note() with the vendor's own
  usage.total_tokens after. Mock calls spend nothing and record nothing.

FAILURE
  Transient failures (network, timeout, 429, 5xx) are retried with doubling
  backoff up to settings.voyage_max_attempts; then, and on any other vendor
  error, EmbeddingsUnavailable is raised. Callers degrade to BM25 (the
  in-memory help_center path), which never depends on this module.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from collections import Counter
from dataclasses import dataclass

import httpx

from app.config import settings
from app.core.exceptions import ClientHunterError

logger = logging.getLogger(__name__)

API_BASE = "https://api.voyageai.com/v1"
EMBED_PATH = "/contextualizedembeddings"
RERANK_PATH = "/rerank"
MODES = ("mock", "live")
INPUT_TYPES = ("document", "query")
MOCK_MODEL = "mock-hash-v1"

# Request packing for /contextualizedembeddings. The vendor's limit is 32K
# tokens per request; the estimate below is chars/3 (over-counts English), and
# the budget leaves a further margin for the vendor's own input-type prompt.
MAX_TOKENS_PER_REQUEST = 24_000
MAX_INPUTS_PER_REQUEST = 1_000
MAX_CHUNKS_PER_REQUEST = 16_000
_CHARS_PER_TOKEN_CONSERVATIVE = 3

_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_BACKOFF_FIRST_SECONDS = 1.0

# Indirection points for tests: the HTTP transport and the backoff sleep.
_transport: httpx.BaseTransport | None = None
_sleep = time.sleep


class EmbeddingsUnavailable(ClientHunterError):
    """Voyage could not answer (down, timing out, refusing the key). The caller
    falls back to BM25; nothing about the user's question is retried later."""


@dataclass(frozen=True)
class RerankResult:
    index: int      # position in the `docs` list passed to rerank()
    score: float    # vendor relevance score, higher is better


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------


def resolve_mode() -> str:
    """"mock" or "live". An explicit EMBEDDINGS_MODE wins; empty or unknown
    resolves by key presence (unknown is logged, never raised -- this runs in
    front of the support widget)."""
    configured = (settings.embeddings_mode or "").strip().lower()
    if configured in MODES:
        return configured
    if configured:
        logger.warning("EMBEDDINGS_MODE=%r is not one of %s -- falling back to auto",
                       configured, MODES)
    return "live" if (settings.voyage_api_key or "").strip() else "mock"


def embedder_id() -> str:
    """What produces the vectors right now, as stored in help_chunks.model:
    "<model>@<dimension>". A row whose model differs is re-embedded."""
    model = settings.voyage_embed_model if resolve_mode() == "live" else MOCK_MODEL
    return f"{model}@{settings.voyage_embed_dimension}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def embed(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """One vector per text, each embedded on its own (no shared context).
    Use input_type="query" for questions."""
    if not texts:
        return []
    return [vectors[0] for vectors in embed_documents([[t] for t in texts], input_type)]


def embed_documents(documents: list[list[str]],
                    input_type: str = "document") -> list[list[list[float]]]:
    """Contextualized chunk embeddings: documents[i] is the ordered chunks of
    one document, and each chunk's vector carries that document's context.
    Returns vectors in the same nesting and order."""
    if input_type not in INPUT_TYPES:
        raise ValueError(f"input_type must be one of {INPUT_TYPES}, not {input_type!r}")
    if not documents:
        return []
    if any(not doc for doc in documents):
        raise ValueError("every document needs at least one chunk")
    if resolve_mode() == "mock":
        return [[_mock_vector(chunk) for chunk in doc] for doc in documents]
    return _live_embed(documents, input_type)


def rerank(query: str, docs: list[str], top_k: int) -> list[RerankResult]:
    """`docs` reordered by relevance to `query`, best first, at most top_k."""
    if not docs or top_k <= 0:
        return []
    top_k = min(top_k, len(docs))
    if resolve_mode() == "mock":
        q = _mock_vector(query)
        scored = [RerankResult(i, _dot(q, _mock_vector(d))) for i, d in enumerate(docs)]
        scored.sort(key=lambda r: (-r.score, r.index))
        return scored[:top_k]
    return _live_rerank(query, docs, top_k)


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text or "") / _CHARS_PER_TOKEN_CONSERVATIVE))


def _pack(documents: list[list[str]]) -> list[list[tuple[int, int, list[str]]]]:
    """Group documents into requests under the vendor's limits.

    Returns requests; each is a list of (document index, first chunk index,
    chunks). A document larger than one request is cut into consecutive
    slices, each embedded with its slice as context -- a fallback the help
    center never needs (its largest article is far below 24K tokens)."""
    pieces: list[tuple[int, int, list[str]]] = []
    for d, doc in enumerate(documents):
        start, current, size = 0, [], 0
        for c, chunk in enumerate(doc):
            tokens = _estimate_tokens(chunk)
            if current and size + tokens > MAX_TOKENS_PER_REQUEST:
                pieces.append((d, start, current))
                start, current, size = c, [], 0
            current.append(chunk)
            size += tokens
        pieces.append((d, start, current))

    requests: list[list[tuple[int, int, list[str]]]] = []
    size = chunks = 0
    for piece in pieces:
        tokens = sum(_estimate_tokens(c) for c in piece[2])
        full = requests and (size + tokens > MAX_TOKENS_PER_REQUEST
                             or len(requests[-1]) >= MAX_INPUTS_PER_REQUEST
                             or chunks + len(piece[2]) > MAX_CHUNKS_PER_REQUEST)
        if not requests or full:
            requests.append([])
            size = chunks = 0
        requests[-1].append(piece)
        size += tokens
        chunks += len(piece[2])
    return requests


def _live_embed(documents: list[list[str]], input_type: str) -> list[list[list[float]]]:
    out: list[list[list[float] | None]] = [[None] * len(doc) for doc in documents]
    dimension = settings.voyage_embed_dimension
    for request in _pack(documents):
        payload = {
            "inputs": [chunks for _, _, chunks in request],
            "model": settings.voyage_embed_model,
            "input_type": input_type,
            "output_dimension": dimension,
        }
        chars = sum(len(c) for _, _, chunks in request for c in chunks)
        body = _post(EMBED_PATH, payload, provider="voyage",
                     model=settings.voyage_embed_model, input_chars=chars)
        try:
            for item in body["data"]:
                d, start, chunks = request[int(item["index"])]
                vectors = item["data"]
                if len(vectors) != len(chunks):
                    raise ValueError(f"{len(vectors)} vectors for {len(chunks)} chunks")
                for vector in vectors:
                    values = [float(x) for x in vector["embedding"]]
                    if len(values) != dimension:
                        raise ValueError(f"vector of {len(values)} dims, expected {dimension}")
                    out[d][start + int(vector["index"])] = values
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            # Fixed text: the vendor's payload never becomes our error message.
            logger.error("voyage embeddings response unusable: %s", type(exc).__name__)
            raise EmbeddingsUnavailable("unexpected Voyage embeddings response") from exc
    if any(v is None for doc in out for v in doc):
        raise EmbeddingsUnavailable("Voyage returned fewer embeddings than requested")
    return out  # type: ignore[return-value]


def _live_rerank(query: str, docs: list[str], top_k: int) -> list[RerankResult]:
    payload = {"query": query, "documents": docs,
               "model": settings.voyage_rerank_model, "top_k": top_k}
    # The vendor bills query tokens x documents + document tokens.
    chars = len(query) * len(docs) + sum(len(d) for d in docs)
    body = _post(RERANK_PATH, payload, provider="voyage_rerank",
                 model=settings.voyage_rerank_model, input_chars=chars)
    try:
        results = [RerankResult(int(r["index"]), float(r["relevance_score"]))
                   for r in body["data"]]
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("voyage rerank response unusable: %s", type(exc).__name__)
        raise EmbeddingsUnavailable("unexpected Voyage rerank response") from exc
    if any(not 0 <= r.index < len(docs) for r in results):
        raise EmbeddingsUnavailable("Voyage rerank returned an index outside the documents")
    results.sort(key=lambda r: (-r.score, r.index))
    return results[:top_k]


def _post(path: str, payload: dict, *, provider: str, model: str, input_chars: int) -> dict:
    """One metered POST with retries. Raises EmbeddingsUnavailable on failure;
    a usage_meter budget refusal propagates unchanged (nothing was spent)."""
    from app.services import usage_meter  # noqa: PLC0415

    key = (settings.voyage_api_key or "").strip()
    if not key:
        raise EmbeddingsUnavailable("EMBEDDINGS_MODE is live but VOYAGE_API_KEY is not set")
    usage_meter.warn_if_unscoped(provider)
    usage_meter.check_budget(input_chars=input_chars, max_output_tokens=0, provider=provider)

    attempts = max(1, int(settings.voyage_max_attempts))
    last = "no attempt made"
    with httpx.Client(base_url=API_BASE, timeout=settings.voyage_timeout_seconds,
                      transport=_transport) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = client.post(path, json=payload,
                                       headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as exc:   # network, timeout, protocol
                last = f"{type(exc).__name__}"
            else:
                if response.status_code == 200:
                    try:
                        body = response.json()
                        tokens = int((body.get("usage") or {}).get("total_tokens") or 0)
                    except (ValueError, TypeError, AttributeError) as exc:
                        raise EmbeddingsUnavailable(
                            f"Voyage {path} returned a non-JSON body") from exc
                    usage_meter.note(provider, model, tokens, 0)
                    logger.info("voyage call ok path=%s model=%s tokens=%d", path, model, tokens)
                    return body
                last = f"HTTP {response.status_code}"
                if response.status_code not in _RETRY_STATUS:
                    # 400/401/403/404/...: the same request cannot succeed. The
                    # body is the vendor's error text, never our key.
                    logger.error("voyage %s refused: %s %s", path, last,
                                 response.text[:300])
                    raise EmbeddingsUnavailable(f"Voyage {path} refused the request ({last})")
            if attempt < attempts:
                _sleep(_BACKOFF_FIRST_SECONDS * 2 ** (attempt - 1))
    logger.warning("voyage %s unavailable after %d attempts (%s)", path, attempts, last)
    raise EmbeddingsUnavailable(f"Voyage {path} unavailable after {attempts} attempts ({last})")


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


def _mock_vector(text: str) -> list[float]:
    """Feature hashing: every normalised term (help_center's stemmed,
    stop-worded tokens) and adjacent-term bigram adds a signed weight to a
    hashed coordinate; the result is L2-normalised. Deterministic across
    processes and machines (blake2b, not Python's salted hash())."""
    from app.services.help_center import normalize  # noqa: PLC0415

    dimension = settings.voyage_embed_dimension
    tokens = normalize(text)
    features = Counter(tokens)
    features.update(f"{a} {b}" for a, b in zip(tokens, tokens[1:]))
    vector = [0.0] * dimension
    for feature, count in features.items():
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        slot = int.from_bytes(digest[:4], "big") % dimension
        sign = 1.0 if digest[4] & 1 else -1.0
        weight = (1.0 + math.log(count)) * (0.5 if " " in feature else 1.0)
        vector[slot] += sign * weight
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        # No content terms. A zero vector has no cosine; give every such text
        # the same unit vector, which is equally far from all real ones.
        vector[0] = 1.0
        return vector
    return [x / norm for x in vector]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
