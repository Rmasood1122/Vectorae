"""app/services/embeddings_client.py -- the only Voyage gateway (Help AI v2, Phase 2).

Offline: live mode is exercised through httpx.MockTransport (no socket), the
mock mode directly. Pinned here:
  * mode resolution is AI_MODE-style (explicit wins; empty = key present?);
  * mock vectors are deterministic, unit-length, 1024-wide, and similar text
    is closer than unrelated text;
  * the live request bodies match the vendor contract verified on
    docs.voyageai.com (contextualizedembeddings / rerank), and responses are
    mapped back to the right document and chunk;
  * every live call is metered through usage_meter -- the same helper as the
    Anthropic gateway -- as "voyage" / "voyage_rerank", with the vendor's
    token count and the admin price; a budget refusal stops the call before
    any request;
  * transient failures retry with backoff, permanent ones do not, and both
    end in EmbeddingsUnavailable (callers fall back to BM25);
  * no other module calls the vendor.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.db import models as m
from app.services import embeddings_client as ec
from app.services import usage_meter

REPO = Path(__file__).resolve().parents[2]
DIM = 1024


@pytest.fixture()
def mock_mode(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_mode", "mock")


@pytest.fixture()
def live(monkeypatch):
    """Live mode against a scripted transport. `live.responses` is consumed in
    order; each item is an httpx.Response, an exception to raise, or a
    callable(request) -> Response."""
    class Live:
        requests: list[httpx.Request] = []
        responses: list = []
        sleeps: list[float] = []

    state = Live()
    state.requests, state.responses, state.sleeps = [], [], []

    def handler(request: httpx.Request) -> httpx.Response:
        state.requests.append(request)
        item = state.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(request) if callable(item) else item

    monkeypatch.setattr(settings, "embeddings_mode", "live")
    monkeypatch.setattr(settings, "voyage_api_key", "test-voyage-key")
    monkeypatch.setattr(settings, "voyage_max_attempts", 3)
    monkeypatch.setattr(ec, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(ec, "_sleep", state.sleeps.append)
    return state


def _embedding_response(request: httpx.Request, tokens: int = 40) -> httpx.Response:
    """Echo a contract-shaped body: vector j of document i is constant i+j/10."""
    body = json.loads(request.content)
    data = [{"object": "list", "index": i,
             "data": [{"object": "embedding", "index": j,
                       "embedding": [i + j / 10] * body["output_dimension"]}
                      for j in range(len(doc))]}
            for i, doc in enumerate(body["inputs"])]
    return httpx.Response(200, json={"object": "list", "data": data,
                                     "model": body["model"], "usage": {"total_tokens": tokens}})


# --------------------------------------------------------------------------
# Mode
# --------------------------------------------------------------------------

class TestMode:
    @pytest.mark.parametrize("configured,key,expected", [
        ("", "", "mock"), ("", "k", "live"), ("mock", "k", "mock"),
        ("live", "", "live"), ("LIVE ", "", "live"), ("bogus", "", "mock"), ("bogus", "k", "live"),
    ])
    def test_resolution(self, monkeypatch, configured, key, expected):
        monkeypatch.setattr(settings, "embeddings_mode", configured)
        monkeypatch.setattr(settings, "voyage_api_key", key)
        assert ec.resolve_mode() == expected

    def test_embedder_id_names_the_vector_source(self, monkeypatch):
        monkeypatch.setattr(settings, "embeddings_mode", "mock")
        assert ec.embedder_id() == f"{ec.MOCK_MODEL}@{DIM}"
        monkeypatch.setattr(settings, "embeddings_mode", "live")
        assert ec.embedder_id() == f"voyage-context-4@{DIM}"

    def test_defaults_are_the_decided_models(self):
        # DECISIONS HELPAI-VENDOR; alembic 0094 sizes the column to the dimension.
        from app.config import Settings

        fresh = Settings(_env_file=None)
        assert fresh.voyage_embed_model == "voyage-context-4"
        assert fresh.voyage_embed_dimension == DIM
        assert fresh.voyage_rerank_model == "rerank-3-lite"


# --------------------------------------------------------------------------
# Mock vectors
# --------------------------------------------------------------------------

class TestMock:
    def test_deterministic_unit_vectors_of_the_configured_width(self, mock_mode):
        a = ec.embed(["How do I connect Gmail?"], "query")[0]
        b = ec.embed(["How do I connect Gmail?"], "query")[0]
        assert a == b and len(a) == DIM
        assert abs(sum(x * x for x in a) - 1.0) < 1e-9

    def test_similar_text_is_closer_than_unrelated_text(self, mock_mode):
        q, near, far = ec.embed(["connect my gmail mailbox",
                                 "How do I connect a Gmail mailbox to Vectorae?",
                                 "Annual billing and plan downgrades"], "query")
        assert ec._dot(q, near) > ec._dot(q, far)

    def test_text_without_content_terms_still_has_a_direction(self, mock_mode):
        vector = ec.embed(["the and of"], "query")[0]
        assert sum(x * x for x in vector) == pytest.approx(1.0)

    def test_documents_keep_their_nesting(self, mock_mode):
        out = ec.embed_documents([["a one", "a two"], ["b one"]])
        assert [len(d) for d in out] == [2, 1]
        assert out[0][1] == ec.embed(["a two"])[0]

    def test_rerank_orders_by_similarity_and_caps_top_k(self, mock_mode):
        docs = ["Annual billing", "Connect Gmail to Vectorae", "Gmail connection keeps asking"]
        out = ec.rerank("gmail connect", docs, top_k=2)
        assert len(out) == 2 and out[0].index in (1, 2) and out[0].score >= out[1].score

    def test_invalid_arguments(self, mock_mode):
        with pytest.raises(ValueError):
            ec.embed(["x"], "passage")
        with pytest.raises(ValueError):
            ec.embed_documents([["x"], []])
        assert ec.embed([]) == [] and ec.rerank("q", [], 3) == []


# --------------------------------------------------------------------------
# Live: the vendor contract
# --------------------------------------------------------------------------

class TestLiveContract:
    def test_contextualized_request_and_mapping(self, live):
        live.responses.append(_embedding_response)
        out = ec.embed_documents([["c0", "c1"], ["d0"]])
        request = live.requests[0]
        assert request.url == httpx.URL("https://api.voyageai.com/v1/contextualizedembeddings")
        assert request.headers["authorization"] == "Bearer test-voyage-key"
        assert json.loads(request.content) == {
            "inputs": [["c0", "c1"], ["d0"]], "model": "voyage-context-4",
            "input_type": "document", "output_dimension": DIM}
        assert [[v[0] for v in doc] for doc in out] == [[0.0, 0.1], [1.0]]

    def test_queries_are_single_element_documents(self, live):
        live.responses.append(_embedding_response)
        ec.embed(["q1", "q2"], "query")
        body = json.loads(live.requests[0].content)
        assert body["inputs"] == [["q1"], ["q2"]] and body["input_type"] == "query"

    def test_large_input_is_packed_into_requests_under_the_limit(self, live, monkeypatch):
        monkeypatch.setattr(ec, "MAX_TOKENS_PER_REQUEST", 100)
        big = "x" * 240                      # 80 estimated tokens each
        live.responses.extend([_embedding_response] * 4)
        out = ec.embed_documents([[big, big], [big], [big]])
        assert len(live.requests) == 4
        assert [len(d) for d in out] == [2, 1, 1]
        for request in live.requests:
            chunks = [c for doc in json.loads(request.content)["inputs"] for c in doc]
            assert sum(ec._estimate_tokens(c) for c in chunks) <= 100

    def test_wrong_dimension_is_unavailable_not_stored(self, live):
        live.responses.append(httpx.Response(200, json={
            "data": [{"index": 0, "data": [{"index": 0, "embedding": [0.1] * 8}]}],
            "usage": {"total_tokens": 3}}))
        with pytest.raises(ec.EmbeddingsUnavailable):
            ec.embed(["x"])

    def test_rerank_request_and_order(self, live):
        live.responses.append(httpx.Response(200, json={
            "object": "list", "model": "rerank-3-lite", "usage": {"total_tokens": 12},
            "data": [{"index": 2, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.4}]}))
        out = ec.rerank("query", ["a", "b", "c"], top_k=2)
        assert json.loads(live.requests[0].content) == {
            "query": "query", "documents": ["a", "b", "c"], "model": "rerank-3-lite", "top_k": 2}
        assert live.requests[0].url.path == "/v1/rerank"
        assert [(r.index, r.score) for r in out] == [(2, 0.9), (0, 0.4)]

    def test_rerank_index_outside_the_documents_is_rejected(self, live):
        live.responses.append(httpx.Response(200, json={
            "data": [{"index": 7, "relevance_score": 0.9}], "usage": {"total_tokens": 1}}))
        with pytest.raises(ec.EmbeddingsUnavailable):
            ec.rerank("q", ["a"], top_k=1)

    def test_live_without_a_key_is_unavailable(self, live, monkeypatch):
        monkeypatch.setattr(settings, "voyage_api_key", "")
        with pytest.raises(ec.EmbeddingsUnavailable):
            ec.embed(["x"])
        assert live.requests == []


# --------------------------------------------------------------------------
# Live: retries and failure
# --------------------------------------------------------------------------

class TestLiveFailure:
    def test_transient_failures_retry_with_doubling_backoff(self, live):
        live.responses.extend([httpx.Response(503), httpx.ConnectTimeout("slow"),
                               _embedding_response])
        assert len(ec.embed(["x"])[0]) == DIM
        assert len(live.requests) == 3
        assert live.sleeps == [1.0, 2.0]

    def test_exhausted_retries_are_unavailable(self, live):
        live.responses.extend([httpx.Response(429)] * 3)
        with pytest.raises(ec.EmbeddingsUnavailable, match="after 3 attempts"):
            ec.embed(["x"])
        assert len(live.requests) == 3

    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    def test_permanent_errors_are_not_retried(self, live, status):
        live.responses.append(httpx.Response(status, json={"detail": "nope"}))
        with pytest.raises(ec.EmbeddingsUnavailable, match=f"HTTP {status}"):
            ec.embed(["x"])
        assert len(live.requests) == 1 and live.sleeps == []

    def test_malformed_body_is_unavailable(self, live):
        live.responses.append(httpx.Response(200, json={"unexpected": True}))
        with pytest.raises(ec.EmbeddingsUnavailable):
            ec.embed(["x"])

    def test_the_key_never_appears_in_the_error(self, live):
        live.responses.extend([httpx.Response(500)] * 3)
        with pytest.raises(ec.EmbeddingsUnavailable) as caught:
            ec.embed(["x"])
        assert "test-voyage-key" not in str(caught.value)


# --------------------------------------------------------------------------
# Metering: the same helper as the Anthropic gateway
# --------------------------------------------------------------------------

class TestMetering:
    def test_embeddings_are_metered_as_voyage(self, live, db_session, test_user):
        live.responses.append(lambda r: _embedding_response(r, tokens=2_000))
        with usage_meter.user_scope(db_session, test_user.id, "support_chat"):
            ec.embed(["How do I connect Gmail?"], "query")
        row = db_session.execute(select(m.ApiUsage)).scalar_one()
        assert (row.provider, row.model, row.purpose) == ("voyage", "voyage-context-4",
                                                          "support_chat")
        assert (row.user_id, row.input_tokens, row.output_tokens) == (test_user.id, 2_000, 0)
        assert row.cost_micros == 240        # 2,000 tokens x $0.12 / Mtok

    def test_rerank_is_metered_as_voyage_rerank(self, live, db_session):
        live.responses.append(httpx.Response(200, json={
            "data": [{"index": 0, "relevance_score": 0.5}], "usage": {"total_tokens": 5_000}}))
        with usage_meter.scope(db_session, purpose="help_index"):
            ec.rerank("q", ["a"], top_k=1)
        row = db_session.execute(select(m.ApiUsage)).scalar_one()
        assert (row.provider, row.model, row.input_tokens) == ("voyage_rerank", "rerank-3-lite", 5_000)
        assert row.cost_micros == 100        # 5,000 tokens x $0.02 / Mtok
        assert row.user_id is None           # the index belongs to no account

    def test_prices_are_admin_settings_with_no_output_price(self, db_session):
        table = usage_meter.price_table(db_session)
        assert table["voyage"] == (0.12, 0.0) and table["voyage_rerank"] == (0.02, 0.0)

    def test_budget_refusal_stops_the_call_before_any_request(self, live, monkeypatch):
        seen = {}

        def refuse(*, input_chars, max_output_tokens, provider="anthropic"):
            seen.update(chars=input_chars, out=max_output_tokens, provider=provider)
            raise usage_meter.AccountBudgetExceededError("cap", budget_micros=1, spent_micros=1)

        monkeypatch.setattr(usage_meter, "check_budget", refuse)
        with pytest.raises(usage_meter.AccountBudgetExceededError):
            ec.embed(["twelve chars"])
        assert live.requests == []
        assert seen == {"chars": 12, "out": 0, "provider": "voyage"}

    def test_mock_mode_records_no_spend(self, mock_mode, db_session):
        with usage_meter.scope(db_session, purpose="help_index"):
            ec.embed(["x"])
            ec.rerank("x", ["y"], 1)
        assert db_session.execute(select(m.ApiUsage)).first() is None

    def test_help_index_is_a_counted_only_purpose(self):
        assert "help_index" in usage_meter.COUNTED_ONLY_PURPOSES
        assert "help_index" not in usage_meter.ACCOUNT_CAPPED_PURPOSES


# --------------------------------------------------------------------------
# One gateway
# --------------------------------------------------------------------------

def test_no_other_module_calls_voyage():
    """"No embedding call outside embeddings_client.py" (Help AI v2 rules)."""
    pattern = re.compile(r"voyageai\.com|import voyageai|from voyageai")
    offenders = []
    for root in ("app", "scripts"):
        for path in (REPO / root).rglob("*.py"):
            if path.name == "embeddings_client.py":
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(REPO)))
    assert offenders == []
