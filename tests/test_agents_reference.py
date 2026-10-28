"""Vectorae agents -- behaviour of the agent cores.

Runs against whichever core app/agents/native.py loaded (the native C++/Rust
modules when installed, else the Python reference), so the same expectations
hold for both. Mirrors native/rust (cargo test) and native/cpp (ctest).
"""

from __future__ import annotations

import pytest

from app.agents import native, reference

rs, cpp = native.rs, native.cpp


def _lead(**kw):
    base = dict(fit=0.5, opens=0, clicks=0, replied=False, bounced=False, unsub=False,
                touches=1, since_touch=3.0, since_engagement=-1.0)
    base.update(kw)
    return tuple(base.values())


class TestLeadPriority:
    def test_opted_out_or_bounced_is_never_contacted(self):
        for lead in (_lead(unsub=True), _lead(bounced=True, replied=True)):
            priority, action, _, _ = rs.prioritize_leads([lead])[0]
            assert (priority, action) == (0.0, "do_not_contact")

    def test_engagement_raises_priority_and_decays(self):
        cold = rs.prioritize_leads([_lead()])[0]
        fresh = rs.prioritize_leads([_lead(opens=3, clicks=1, since_engagement=0.0)])[0]
        stale = rs.prioritize_leads([_lead(opens=3, clicks=1, since_engagement=14.0)])[0]
        assert fresh[0] > stale[0] > cold[0]
        assert fresh[1] == "follow_up_now"
        assert stale[2] == pytest.approx(fresh[2] / 4.0)   # two half-lives

    @pytest.mark.parametrize("kw,action", [
        (dict(replied=True), "respond_now"), (dict(touches=0), "start_sequence"),
        (dict(touches=6), "rest"), ({}, "nurture"),
    ])
    def test_actions(self, kw, action):
        assert rs.prioritize_leads([_lead(**kw)])[0][1] == action

    def test_fit_is_clamped(self):
        assert rs.prioritize_leads([_lead(fit=7.0)]) == rs.prioritize_leads([_lead(fit=1.0)])


class TestFollowupTiming:
    def test_empty_history_prefers_business_hours(self):
        ahead, weekday, hour, weight, confidence = rs.next_followup_slot([], 5, 12, 0, False)
        assert (weekday, hour, ahead, confidence) == (0, 8, 44, 0.0)   # Saturday noon -> Monday 08

    def test_replies_pull_the_slot(self):
        replies = [(2, 15)] * 12
        slot = rs.next_followup_slot(replies, 0, 9, 24, False)
        assert slot[1:3] == (2, 15) and slot[3] == 1.0
        assert rs.top_reply_slots(replies, 1)[0] == (2, 15, 1.0)
        assert slot[4] == pytest.approx(12 / 32)

    def test_min_gap_and_business_only(self):
        ahead, weekday, hour, _, _ = rs.next_followup_slot([(0, 9)], 0, 9, 48, True)
        assert ahead >= 48 and weekday < 5 and 8 <= hour < 18
        _, weekday, hour, _, _ = rs.next_followup_slot([(6, 2)] * 30, 0, 0, 0, True)
        assert weekday < 5 and 8 <= hour < 18


class TestSupervisor:
    def test_idle_and_healthy(self):
        assert rs.evaluate_agent([])["status"] == "idle"
        h = rs.evaluate_agent([(True, 10.0, 1.0), (True, 10.0, 2.0)])
        assert (h["status"], h["circuit"], h["may_run"]) == ("healthy", "closed", True)

    def test_opens_then_half_opens_then_closes(self):
        fail = lambda age: (False, 10.0, age)  # noqa: E731
        h = rs.evaluate_agent([fail(10), fail(20), fail(30), (True, 10.0, 40.0)])
        assert (h["circuit"], h["status"], h["may_run"]) == ("open", "down", False)
        h = rs.evaluate_agent([fail(400), fail(410), fail(420)])
        assert (h["circuit"], h["may_run"]) == ("half_open", True)
        h = rs.evaluate_agent([(True, 10.0, 1.0), fail(400), fail(410), fail(420)])
        assert h["circuit"] == "closed" and h["status"] == "degraded"

    def test_slow_runs_degrade(self):
        h = rs.evaluate_agent([(True, 9000.0, float(i)) for i in range(20)])
        assert h["status"] == "degraded" and h["p95_latency_ms"] == 9000.0


class TestDeliverability:
    def test_wilson(self):
        assert cpp.wilson_lower(0, 100) == 0.0
        assert cpp.wilson_lower(10, 100) == pytest.approx(0.0552, abs=1e-3)

    @pytest.mark.parametrize("args,risk,cap", [
        ((10, 0, 0, 0, 90.0, 100), "insufficient_data", 100),
        ((10, 3, 0, 0, 90.0, 100), "elevated", 50),
        ((200, 2, 0, 6, 90.0, 100), "healthy", 100),
        ((200, 8, 0, 0, 90.0, 100), "elevated", 50),
        ((200, 30, 0, 0, 90.0, 100), "critical", 0),
        ((400, 0, 12, 0, 90.0, 100), "critical", 0),
        ((200, 0, 0, 0, 10.0, 100), "healthy", 20),
        ((200, 0, 0, 0, 45.0, 100), "healthy", 70),
    ])
    def test_risk_and_cap(self, args, risk, cap):
        v = cpp.assess_mailbox(*args)
        assert (v["risk"], v["recommended_cap"]) == (risk, cap)
        assert 0.0 <= v["health"] <= 100.0


class TestReplyTriage:
    @pytest.mark.parametrize("body,label", [
        ("Please remove me from your list", "opt_out"),
        ("Yes interested, but unsubscribe my colleague", "opt_out"),
        ("Not interested, thanks.", "negative"),
        ("Sounds good, when are you free?", "interested"),
        ("Not right now, maybe next quarter", "not_now"),
        ("You should reach out to Dana, she's the right person", "referral"),
        ("Mail Delivery Subsystem: address not found", "bounce"),
        ("ok", "unclear"),
    ])
    def test_labels(self, body, label):
        assert cpp.triage_reply("Re: hi", body)["label"] == label

    def test_out_of_office_subject(self):
        assert cpp.triage_reply("Automatic reply: out of office", "Back Monday")["label"] \
            == "out_of_office"

    def test_quoted_text_is_ignored(self):
        body = "Tell me more\n> please unsubscribe\nOn Mon, Bob wrote:\nunsubscribe"
        t = cpp.triage_reply("Re: hi", body)
        assert t["label"] == "interested" and t["opt_out"] is False

    def test_word_boundaries(self):
        assert cpp.triage_reply("", "a nonstop demonstration")["label"] == "unclear"


class TestClaimCheck:
    def _codes(self, r):
        return {f["code"] for f in r["findings"]}

    def test_clean_draft_passes(self):
        r = cpp.check_claims("Quick question", "We help agencies book meetings.", True)
        assert r == {"verdict": "pass", "risk": 0, "findings": []}

    def test_risky_draft(self):
        r = cpp.check_claims("RE: your growth",
                             "Guaranteed 3x more leads, 40% cheaper. Act now!!!", True)
        assert r["verdict"] == "block_recommended"
        assert {"unverifiable_guarantee", "deceptive_subject", "numeric_claim",
                "false_urgency", "excessive_exclamation"} <= self._codes(r)

    def test_re_subject_only_deceptive_on_first_touch(self):
        assert "deceptive_subject" not in self._codes(cpp.check_claims("Re: follow up", "Thanks",
                                                                       False))

    def test_low_findings_alone_pass(self):
        r = cpp.check_claims("BIG NEWS FOR YOU", "the #1 tool", False)
        assert {"shouting_subject", "superlative"} <= self._codes(r)
        assert (r["verdict"], r["risk"]) == ("pass", 16)

    def test_numbers_glued_to_words_are_not_claims(self):
        assert "numeric_claim" not in self._codes(cpp.check_claims("hi", "version 2.5 and abc3x",
                                                                   False))


def test_reference_rounding_is_half_up_not_bankers():
    assert reference._round1(0.25) == 0.3
    assert reference._round1(2.45) == 2.5
