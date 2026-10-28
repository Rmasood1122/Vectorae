"""Vectorae agents -- the native C++/Rust cores agree with the Python reference.

app/agents/reference.py is the fallback the offline gate runs on and the
oracle these tests hold the native modules to: the same inputs must give the
same answers, and the C++ phrase tables must equal the Python ones.

Skipped when the native modules are not installed (the sealed eval workflow
installs requirements.txt only). The verify-native-agents CI job builds both
wheels and sets VECTORAE_REQUIRE_NATIVE=1, which turns every skip here into a
failure, so the native cores are always proven somewhere.
"""

from __future__ import annotations

import importlib
import os
import random

import pytest

from app.agents import reference as ref


def _native(name):
    try:
        return importlib.import_module(name)
    except ImportError:
        if os.environ.get("VECTORAE_REQUIRE_NATIVE") == "1":
            pytest.fail(f"{name} must be installed when VECTORAE_REQUIRE_NATIVE=1")
        pytest.skip(f"{name} not installed")


@pytest.fixture(scope="module")
def rs():
    return _native("vectorae_native_rs")


@pytest.fixture(scope="module")
def cpp():
    return _native("vectorae_native_cpp")


RNG_SEED = 20261019


def _close(a, b):
    if isinstance(a, float) or isinstance(b, float):
        return a == pytest.approx(b, rel=1e-12, abs=1e-12)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    return a == b


def test_prioritize_leads(rs):
    rng = random.Random(RNG_SEED)
    leads = [(rng.uniform(-0.2, 1.2), rng.randint(0, 9), rng.randint(0, 4),
              rng.random() < 0.1, rng.random() < 0.05, rng.random() < 0.05,
              rng.randint(0, 9), rng.choice([-1.0, rng.uniform(0, 30)]),
              rng.choice([-1.0, rng.uniform(0, 60)])) for _ in range(2000)]
    assert _close(rs.prioritize_leads(leads), ref.prioritize_leads(leads))


def test_followup_timing(rs):
    rng = random.Random(RNG_SEED + 1)
    for _ in range(200):
        replies = [(rng.randint(0, 8), rng.randint(0, 26)) for _ in range(rng.randint(0, 60))]
        args = (replies, rng.randint(0, 6), rng.randint(0, 23), rng.randint(0, 200),
                rng.random() < 0.5)
        assert _close(rs.next_followup_slot(*args), ref.next_followup_slot(*args))
        assert _close(rs.top_reply_slots(replies, 5), ref.top_reply_slots(replies, 5))


def test_supervisor(rs):
    rng = random.Random(RNG_SEED + 2)
    for _ in range(500):
        runs = [(rng.random() < 0.7, rng.uniform(0, 9000), float(i * rng.randint(1, 60)))
                for i in range(rng.randint(0, 50))]
        assert _close(dict(rs.evaluate_agent(runs)), ref.evaluate_agent(runs))


def test_deliverability(cpp):
    rng = random.Random(RNG_SEED + 3)
    for _ in range(2000):
        sent = rng.choice([0, rng.randint(0, 30), rng.randint(0, 2000)])
        args = (sent, rng.randint(0, max(1, sent // 5)), rng.randint(0, max(1, sent // 30)),
                rng.randint(0, max(1, sent // 10)), rng.uniform(0, 120), rng.choice([20, 50, 100]))
        assert _close(dict(cpp.assess_mailbox(*args)), ref.assess_mailbox(*args))


def test_phrase_tables_are_identical(cpp):
    assert [tuple(g) for g in cpp.triage_groups()] == [tuple(g) for g in ref.TRIAGE_GROUPS]
    assert [tuple(r) for r in cpp.claim_rules()] == [tuple(r) for r in ref.CLAIM_RULES]


_SNIPPETS = ["Not interested", "remove me", "interested!", "Sounds good", "OUT OF OFFICE",
             "next quarter", "reach out to", "the right person", "Guaranteed", "100% ", "3x ",
             "40 %", "2.5%", "#1", "Act now!!", "click here", "$$$", "nonstop", "Ünsubscribe",
             "café demo", "\n> unsubscribe\n", "\nOn Tue, A wrote:\n", "\nFrom: x\n", "RE: ",
             "Fwd: ", "   ", "\r\n", "🙂 pricing", "world-class", "risk-free", "opt-out",
             "don't email", "urgent", "version 1.2.3", "abc4x", "10x."]


def _texts(seed, n):
    rng = random.Random(seed)
    for _ in range(n):
        yield "".join(rng.choice(_SNIPPETS) + rng.choice([" ", "", ", ", "\n"])
                      for _ in range(rng.randint(0, 8)))


def test_reply_triage(cpp):
    for subject, body in zip(_texts(RNG_SEED + 4, 1500), _texts(RNG_SEED + 5, 1500)):
        native, oracle = dict(cpp.triage_reply(subject, body)), ref.triage_reply(subject, body)
        native["matched"] = [tuple(x) for x in native["matched"]]
        assert _close(native, oracle), (subject, body)


def test_claim_check(cpp):
    rng = random.Random(RNG_SEED + 6)
    for subject, body in zip(_texts(RNG_SEED + 7, 1500), _texts(RNG_SEED + 8, 1500)):
        first = rng.random() < 0.5
        native, oracle = dict(cpp.check_claims(subject, body, first)), \
            ref.check_claims(subject, body, first)
        native["findings"] = [dict(f) for f in native["findings"]]
        assert _close(native, oracle), (subject, body, first)
