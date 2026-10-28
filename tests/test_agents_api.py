"""Vectorae agents -- API, tenant isolation, advisory-only, and the master
supervisor's circuit breaker (app/agents/, app/api/agents.py)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.agents import supervisor
from app.db import models as m

NOW = datetime.now(timezone.utc)


def _account(db, user, *, name="Acme"):
    product = m.Product(user_id=user.id, name=name, description="SEO audits",
                        type=m.ProductType.SKILL)
    db.add(product)
    db.flush()
    strategy = m.Strategy(product_id=product.id, flow_type=m.FlowType.WITH_CLIENTS)
    db.add(strategy)
    db.flush()
    seq = m.Sequence(strategy_id=strategy.id, channel=m.ChannelType.EMAIL, name="s",
                     status=m.SequenceStatus.ACTIVE)
    db.add(seq)
    db.flush()
    return strategy, seq


def _lead(db, strategy, name, *, score=None, email=None, status=m.LeadStatus.VERIFIED):
    lead = m.Lead(strategy_id=strategy.id, source="apollo", external_id=uuid.uuid4().hex,
                  full_name=name, email=email or f"{name.lower()}@prospect.io",
                  status=status, ai_booking_likelihood=score)
    db.add(lead)
    db.flush()
    return lead


def _sent(db, seq, lead, *, step=1, sender=None, days_ago=1.0):
    msg = m.Message(sequence_id=seq.id, lead_id=lead.id, step_no=step,
                    channel=m.ChannelType.EMAIL, template="t", subject="Hello", body="b",
                    status=m.MessageStatus.SENT, sender_ref=sender,
                    sent_at=NOW - timedelta(days=days_ago))
    db.add(msg)
    db.flush()
    return msg


@pytest.fixture()
def other_user(db_session):
    user = m.User(email="agents-other@leadpilot.dev", email_verified=True)
    db_session.add(user)
    db_session.commit()
    return user


def _run(client, name, **params):
    return client.post(f"/agents/{name}/run", json={"params": params})


class TestListing:
    def test_lists_five_agents_and_the_master(self, client):
        body = client.get("/agents").json()
        assert [a["name"] for a in body["agents"]] == [
            "lead_priority", "followup_timing", "deliverability_guard", "reply_triage",
            "claim_check"]
        assert {a["language"] for a in body["agents"]} == {"rust", "cpp"}
        assert body["master"]["language"] == "rust"
        assert body["master"]["status"] == "healthy"
        assert all(a["health"]["status"] == "idle" for a in body["agents"])
        assert all(a["backend"] in ("native", "python") for a in body["agents"])

    def test_requires_auth(self, anon_client):
        assert anon_client.get("/agents").status_code == 401

    def test_unknown_agent_is_404(self, client):
        assert _run(client, "nope").status_code == 404


class TestLeadPriority:
    def test_ranks_and_never_contacts_suppressed(self, client, db_session, test_user):
        strategy, seq = _account(db_session, test_user)
        hot = _lead(db_session, strategy, "Hot", score=80)
        _sent(db_session, seq, hot, days_ago=3)
        db_session.add(m.Outcome(lead_id=hot.id, event=m.OutcomeEvent.OPENED, channel="email",
                                 ts=NOW - timedelta(hours=5)))
        cold = _lead(db_session, strategy, "Cold", score=20)
        _sent(db_session, seq, cold, days_ago=3)
        gone = _lead(db_session, strategy, "Gone", score=95, email="gone@prospect.io")
        db_session.add(m.SuppressionEntry(email="GONE@prospect.io", reason="unsubscribed"))
        db_session.commit()

        r = _run(client, "lead_priority")
        assert r.status_code == 200, r.text
        rows = {row["name"]: row for row in r.json()["result"]["leads"]}
        assert rows["Hot"]["priority"] > rows["Cold"]["priority"]
        assert rows["Gone"]["action"] == "do_not_contact" and rows["Gone"]["priority"] == 0.0
        assert r.json()["items"] == 3

    def test_only_the_callers_leads(self, client, db_session, test_user, other_user):
        mine, _ = _account(db_session, test_user)
        theirs, _ = _account(db_session, other_user, name="Other")
        _lead(db_session, mine, "Mine")
        _lead(db_session, theirs, "Theirs")
        db_session.commit()
        names = [row["name"] for row in _run(client, "lead_priority").json()["result"]["leads"]]
        assert names == ["Mine"]
        # another account's strategy id returns nothing, not their leads
        r = _run(client, "lead_priority", strategy_id=str(theirs.id))
        assert r.json()["result"]["leads"] == []

    def test_bad_strategy_id_is_422_and_not_a_failure(self, client, db_session):
        assert _run(client, "lead_priority", strategy_id="not-a-uuid").status_code == 422
        assert db_session.execute(select(func.count(m.AgentRun.id))).scalar_one() == 0


class TestFollowupTiming:
    def test_learns_from_replies(self, client, db_session, test_user):
        strategy, _ = _account(db_session, test_user)
        lead = _lead(db_session, strategy, "Ann")
        wednesday_15 = datetime(2026, 10, 14, 15, 7, tzinfo=timezone.utc)
        for i in range(10):
            db_session.add(m.InboundReply(lead_id=lead.id, channel="email", from_address="a@b.io",
                                          body="thanks", received_at=wednesday_15
                                          - timedelta(weeks=i)))
        db_session.commit()
        result = _run(client, "followup_timing", min_gap_hours=0).json()["result"]
        assert result["replies_seen"] == 10
        assert result["top_slots"][0] == {"weekday": "Wednesday", "hour_utc": 15, "weight": 1.0}
        assert result["next_slot"]["confidence"] == pytest.approx(10 / 30, abs=1e-3)


class TestDeliverabilityGuard:
    def test_flags_a_bouncing_mailbox(self, client, db_session, test_user, other_user):
        strategy, seq = _account(db_session, test_user)
        box = m.GmailAccount(user_id=test_user.id, email_address="me@acme.io",
                             token_ciphertext="x", scopes="send",
                             created_at=NOW - timedelta(days=120))
        db_session.add(box)
        db_session.add(m.GmailAccount(user_id=other_user.id, email_address="them@other.io",
                                      token_ciphertext="x", scopes="send"))
        db_session.flush()
        for i in range(200):
            lead = _lead(db_session, strategy, f"P{i}")
            msg = _sent(db_session, seq, lead, sender=str(box.id))
            if i < 30:
                db_session.add(m.Outcome(lead_id=lead.id, message_id=msg.id,
                                         event=m.OutcomeEvent.BOUNCED, channel="email", ts=NOW))
        db_session.commit()
        result = _run(client, "deliverability_guard").json()["result"]
        assert [mb["address"] for mb in result["mailboxes"]] == ["me@acme.io"]
        mb = result["mailboxes"][0]
        assert (mb["sent_30d"], mb["bounced_30d"], mb["risk"], mb["recommended_cap"]) == \
            (200, 30, "critical", 0)
        assert result["overall_risk"] == "critical"


class TestReplyTriage:
    def test_flags_opt_out_the_stored_classification_missed(self, client, db_session,
                                                            test_user):
        strategy, _ = _account(db_session, test_user)
        lead = _lead(db_session, strategy, "Bo")
        db_session.add(m.InboundReply(lead_id=lead.id, channel="email", from_address="bo@x.io",
                                      body="Please take me off this list",
                                      classification="interested"))
        db_session.add(m.InboundReply(lead_id=lead.id, channel="email", from_address="bo@x.io",
                                      body="unsubscribe", classification="unsubscribe_request"))
        db_session.add(m.InboundReply(lead_id=lead.id, channel="email", from_address="bo@x.io",
                                      body="Sounds good, send me more"))
        db_session.commit()
        before = db_session.execute(select(m.InboundReply.classification)).scalars().all()
        result = _run(client, "reply_triage").json()["result"]
        assert result["labels"] == {"opt_out": 2, "interested": 1}
        assert [r["stored_classification"] for r in result["needs_review"]] == ["interested"]
        db_session.expire_all()
        # advisory: nothing re-filed
        assert db_session.execute(select(m.InboundReply.classification)).scalars().all() == before


class TestClaimCheck:
    def test_scans_pending_drafts_only(self, client, db_session, test_user):
        strategy, seq = _account(db_session, test_user)
        lead = _lead(db_session, strategy, "Cy")
        db_session.add(m.Message(sequence_id=seq.id, lead_id=lead.id, step_no=1,
                                 channel=m.ChannelType.EMAIL, template="t",
                                 subject="RE: growth", body="Guaranteed results",
                                 status=m.MessageStatus.SCHEDULED, scheduled_at=NOW))
        db_session.add(m.Message(sequence_id=seq.id, lead_id=lead.id, step_no=2,
                                 channel=m.ChannelType.EMAIL, template="t", subject="Hi",
                                 body="A short note", status=m.MessageStatus.AWAITING_REVIEW))
        sent = _sent(db_session, seq, lead)
        sent.body = "Guaranteed!!!"
        db_session.commit()
        result = _run(client, "claim_check").json()["result"]
        assert result["checked"] == 2
        assert result["verdicts"] == {"block_recommended": 1, "pass": 1}
        codes = {f["code"] for f in result["flagged"][0]["findings"]}
        assert {"unverifiable_guarantee", "deceptive_subject"} <= codes
        db_session.refresh(sent)
        assert sent.status is m.MessageStatus.SENT

    def test_ad_hoc_check(self, client):
        r = client.post("/agents/claim_check/check",
                        json={"subject": "Quick question", "body": "40% fewer no-shows",
                              "first_touch": True})
        assert r.status_code == 200
        assert r.json()["verdict"] == "pass"
        assert [f["code"] for f in r.json()["findings"]] == ["numeric_claim"]


class TestSupervisor:
    def test_records_runs(self, client, db_session, test_user):
        _run(client, "claim_check")
        run = db_session.execute(select(m.AgentRun)).scalar_one()
        assert (run.agent, run.ok, run.user_id) == ("claim_check", True, test_user.id)
        assert run.backend in ("native", "python")
        health = next(a for a in client.get("/agents").json()["agents"]
                      if a["name"] == "claim_check")["health"]
        assert health["status"] == "healthy" and health["runs"] == 1

    def test_circuit_opens_after_three_failures_then_recovers(self, client, db_session,
                                                              monkeypatch, caplog):
        def boom(db, user, params):
            raise RuntimeError("core crashed")

        spec = supervisor.AGENTS["reply_triage"]
        monkeypatch.setitem(supervisor.AGENTS, "reply_triage",
                            supervisor.AgentSpec(spec.name, spec.title, spec.language,
                                                 spec.description, boom))
        codes = [_run(client, "reply_triage").status_code for _ in range(4)]
        assert codes == [502, 502, 502, 503]
        assert "agent_circuit_opened agent=reply_triage" in caplog.text
        body = client.get("/agents").json()
        assert body["master"]["status"] == "down"
        triage = next(a for a in body["agents"] if a["name"] == "reply_triage")
        assert triage["health"]["circuit"] == "open"
        assert db_session.execute(select(func.count(m.AgentRun.id))).scalar_one() == 3

        # after the cooldown the next run is a trial; success closes the circuit
        monkeypatch.setitem(supervisor.AGENTS, "reply_triage", spec)
        for run in db_session.execute(select(m.AgentRun)).scalars():
            run.started_at = NOW - timedelta(seconds=supervisor.COOLDOWN_S + 60)
        db_session.commit()
        assert _run(client, "reply_triage").status_code == 200
        triage = next(a for a in client.get("/agents").json()["agents"]
                      if a["name"] == "reply_triage")
        assert triage["health"]["circuit"] == "closed"

    def test_failure_errors_hold_no_request_data(self, client, db_session, monkeypatch):
        def boom(db, user, params):
            raise RuntimeError("x" * 1000)

        spec = supervisor.AGENTS["claim_check"]
        monkeypatch.setitem(supervisor.AGENTS, "claim_check",
                            supervisor.AgentSpec(spec.name, spec.title, spec.language,
                                                 spec.description, boom))
        assert _run(client, "claim_check").status_code == 502
        run = db_session.execute(select(m.AgentRun)).scalar_one()
        assert run.ok is False and len(run.error) == 300
