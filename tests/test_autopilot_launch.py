"""Autopilot launch (docs/features/autopilot-launch.md section 3.1).

Founder report 2026-10-11: "when Pipeline is done it doesn't send Mails and
Messages". Pinned here, offline and deterministic:

  * end to end: pipeline complete -> send-time plan -> verification passes ->
    Autopilot sources leads through the real chain (fake source/verifier) ->
    builds the sequence -> enrolls VERIFIED leads -> step 1 at the best slot ->
    held by the daily send approval -> approved -> sent through the REAL
    send_message_impl;
  * idempotency: advancing / sweeping again never re-sources or re-enrolls;
    a claimed row is left alone;
  * a missing prerequisite: the ready channel launches, the owner is told by
    email + push (+ in-app state) once, and the waiting channel resumes when
    the prerequisite appears;
  * needs_human_review is never bypassed: Autopilot waits and notifies; a
    person's sign-off resumes it;
  * the lawful-basis confirmation is required (422, nothing stored) and
    audited (compliance_audit_log + security_audit_events); SDRs and API keys
    cannot opt in; the flag off refuses (409) and the sweep does nothing;
  * tenancy.

LEGAL/SAFETY GUARD added by this feature: the server-side lawful-basis
requirement for the Autopilot opt-in (TestOptIn).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.db import models as m
from app.services import autopilot, send_time, system_settings
from app.services import daily_send_approval as dsa
from app.workers import lead_tasks
from tests.conftest import auth_headers, entitle_owner, make_raw

_REAL_SEND_TIME = send_time.enabled
_REAL_DSA = dsa.enabled

NOW = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)      # Tuesday 13:00 UTC
DOC = ("# Client Acquisition Strategy\n## Phase 8: Execution\nCold email first, then "
       "LinkedIn connection requests for people without an address.")


@pytest.fixture(autouse=True)
def best_time_on(monkeypatch):
    monkeypatch.setattr(send_time, "enabled", _REAL_SEND_TIME)


@pytest.fixture(autouse=True)
def approval_on(monkeypatch):
    """Autopilot requires daily send approval (its confirmed text promises
    it): this module runs with the REAL flag, whose production default is on."""
    monkeypatch.setattr(dsa, "enabled", _REAL_DSA)


class _NoClose:
    def __init__(self, session):
        self._s = session

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._s, name)


def _opt_in(db, strategy, user):
    return autopilot.set_opt_in(db, strategy, enabled=True, confirmed=True,
                                text_version=autopilot.LAWFUL_BASIS_VERSION,
                                actor_id=user.id, owner_id=user.id, now=NOW)


@pytest.fixture()
def strategy(db_session, product_with_strategy, fake_claude):
    _, strategy = product_with_strategy(m.FlowType.WITH_CLIENTS)
    strategy.strategy_document = DOC
    strategy.status = m.StrategyStatus.VERIFIED
    db_session.commit()
    return strategy


def _run_chain(db, batch_id, source, verifier):
    lead_tasks.source_leads_impl(db, batch_id, source=source)
    lead_tasks.enrich_leads_impl(db, batch_id, source=source)
    lead_tasks.find_missing_emails_impl(db, batch_id, verifier=verifier)
    lead_tasks.verify_emails_impl(db, batch_id, verifier=verifier)
    lead_tasks.finalize_lead_batch_impl(db, batch_id)


@pytest.fixture()
def chain(db_session, fake_source, fake_verifier, fake_claude):
    """A start_chain that records the batch; `.run()` runs the real chain impls."""
    fake_source.feed = [make_raw(i, email=f"p{i}@acme{i}.com") for i in range(3)]

    class _Chain:
        def __init__(self):
            self.started: list[str] = []

        def __call__(self, batch_id, strategy_id):
            self.started.append(batch_id)

        def run(self):
            for batch_id in self.started:
                _run_chain(db_session, uuid.UUID(batch_id), fake_source, fake_verifier)

    return _Chain()


def _messages(db, status=None):
    q = db.query(m.Message)
    if status is not None:
        q = q.filter_by(status=status)
    return q.order_by(m.Message.created_at, m.Message.id).all()


def _events(jobs, name="autopilot_update"):
    return [e for e in jobs["events"] if e[1] == name]


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------


class TestEndToEnd:
    def test_pipeline_to_first_message_sent(self, client, db_session, test_user,
                                            product_with_strategy, chain, gmail_account,
                                            fake_channel, monkeypatch, approval_on,
                                            queued_jobs, mailbox):
        from app.workers import outreach_tasks, tasks as worker_tasks

        # 1. A strategy created with Autopilot ON (lawful basis confirmed).
        product, _ = product_with_strategy(m.FlowType.WITH_CLIENTS)
        resp = client.post(f"/products/{product.id}/strategies", json={
            "autopilot": {"enabled": True, "lawful_basis_confirmed": True,
                          "lawful_basis_text_version": autopilot.LAWFUL_BASIS_VERSION}})
        assert resp.status_code == 202, resp.text
        strategy = db_session.get(m.Strategy, uuid.UUID(resp.json()["id"]))
        row = autopilot.row_for(db_session, strategy.id)
        assert row.enabled and row.state == autopilot.WAITING_FOR_RESEARCH

        # 2. The pipeline completes: documents + the send-time plan (priors here).
        strategy.strategy_document = DOC
        strategy.pattern_inputs_json = {"icp": {"industries": ["fire protection"]}}
        send_time.ensure_plan(db_session, strategy, now=NOW)
        assert strategy.send_time_plan_json["channels"]["email"]["basis"] == "prior"
        strategy.status = m.StrategyStatus.VERIFYING
        db_session.commit()
        assert autopilot.advance(db_session, strategy.id, now=NOW,
                                 start_chain=chain) == autopilot.WAITING_FOR_RESEARCH

        # 3. Verification (the real loop, FakeClaude judges PASS) enqueues Autopilot.
        monkeypatch.setattr(worker_tasks, "SessionLocal", lambda: _NoClose(db_session))
        out = worker_tasks.run_verification.run(str(strategy.id))
        assert out == "verification finished: verified"
        assert queued_jobs["autopilot"] == [str(strategy.id)]

        # 4. Autopilot: sourcing through the real chain.
        assert autopilot.advance(db_session, strategy.id, now=NOW,
                                 start_chain=chain) == autopilot.SOURCING
        assert len(chain.started) == 1
        assert autopilot.advance(db_session, strategy.id, now=NOW,
                                 start_chain=chain) == autopilot.SOURCING
        chain.run()
        verified = db_session.query(m.Lead).filter_by(strategy_id=strategy.id,
                                                      status=m.LeadStatus.VERIFIED).count()
        assert verified == 3

        # 5. Sequence + enrollment of VERIFIED leads at the best slot.
        state = autopilot.advance(db_session, strategy.id, now=NOW, start_chain=chain)
        assert state == autopilot.LAUNCHED
        row = autopilot.row_for(db_session, strategy.id)
        seq = db_session.get(m.Sequence, uuid.UUID(row.sequences_json["email"]))
        assert seq.status is m.SequenceStatus.ACTIVE and seq.name == "Autopilot — Email"
        steps = sorted(seq.steps, key=lambda s: s.step_no)
        assert [s.delay_days for s in steps] == [0, 3, 4]
        assert row.channels_json["email"]["state"] == "launched"
        assert row.channels_json["email"]["enrolled"] == 3
        scheduled = _messages(db_session, m.MessageStatus.SCHEDULED)
        assert len(scheduled) == 3
        for msg in scheduled:
            when = msg.scheduled_at.replace(tzinfo=timezone.utc)
            # The leads carry no location, so their local time is the strategy's
            # target region (the ICP's US states -> a US zone). Tue 13:00 UTC is
            # before 09:00 there; the email prior opens Tue 09:00 local.
            from app.services import lead_timezone as lead_tz

            zone, source = lead_tz.resolve(db_session.get(m.Lead, msg.lead_id))
            assert source == lead_tz.SOURCE_STRATEGY and zone.startswith("America/")
            local = when.astimezone(ZoneInfo(zone))
            assert local.date().isoformat() == "2026-08-11" and local.hour == 9
        # LinkedIn is planned by the strategy but not in the Free plan: waiting, told once.
        assert row.channels_json["linkedin"]["state"] == "waiting"
        assert "plan does not include LinkedIn" in row.channels_json["linkedin"]["reason"]
        assert any("linkedin is waiting" in e[2]["title"] for e in _events(queued_jobs))
        assert any("outreach launched" in e[2]["title"] for e in _events(queued_jobs))

        # 6. Due: held by today's approval -- the real send path, nothing bypassed.
        monkeypatch.setattr(outreach_tasks, "_get_channel", lambda s, a: fake_channel)
        due = scheduled[0].scheduled_at.replace(tzinfo=timezone.utc)
        result = outreach_tasks.send_message_impl(db_session, scheduled[0].id, now=due)
        assert result == dsa.SEND_RESULT_AWAITING
        assert fake_channel.sent == []

        # 7. The owner approves today's email -> released -> sent.
        dsa.approve(db_session, test_user.id, actor_id=test_user.id, channels=["email"],
                    now=due + timedelta(minutes=1))
        result = outreach_tasks.send_message_impl(db_session, scheduled[0].id,
                                                  now=due + timedelta(minutes=2))
        assert result == "sent"
        assert len(fake_channel.sent) == 1
        lead = db_session.get(m.Lead, scheduled[0].lead_id)
        assert fake_channel.sent[0].to_address == lead.email
        assert "Unsubscribe" in fake_channel.sent[0].body      # the compliant footer
        db_session.refresh(scheduled[0])
        assert scheduled[0].status is m.MessageStatus.SENT

    def test_idempotent_advance_and_sweep(self, db_session, test_user, strategy, chain,
                                          gmail_account):
        _opt_in(db_session, strategy, test_user)
        autopilot.advance(db_session, strategy.id, now=NOW, start_chain=chain)
        chain.run()
        assert autopilot.advance(db_session, strategy.id, now=NOW,
                                 start_chain=chain) == autopilot.LAUNCHED
        counts = (db_session.query(m.LeadBatch).count(), db_session.query(m.Sequence).count(),
                  db_session.query(m.SequenceEnrollment).count(), len(_messages(db_session)))
        for _ in range(2):
            autopilot.advance(db_session, strategy.id, now=NOW + timedelta(hours=1),
                              start_chain=chain)
            out = autopilot.sweep(db_session, now=NOW + timedelta(hours=2), start_chain=chain)
            assert out["checked"] == 1
        assert len(chain.started) == 1, "never sources twice"
        assert (db_session.query(m.LeadBatch).count(), db_session.query(m.Sequence).count(),
                db_session.query(m.SequenceEnrollment).count(),
                len(_messages(db_session))) == counts

    def test_a_claimed_row_is_left_alone(self, db_session, test_user, strategy, chain):
        row = _opt_in(db_session, strategy, test_user)
        row.claim_token = "someone-else"
        row.claimed_at = NOW
        db_session.commit()
        assert autopilot.advance(db_session, strategy.id, now=NOW, start_chain=chain) == "busy"
        assert chain.started == []
        # A claim older than the TTL (a killed worker) is taken over.
        assert autopilot.advance(db_session, strategy.id, now=NOW + timedelta(minutes=11),
                                 start_chain=chain) == autopilot.SOURCING
        assert autopilot.row_for(db_session, strategy.id).claim_token is None

    def test_only_its_own_batch_is_ever_enrolled(self, db_session, test_user, strategy,
                                                 chain, gmail_account):
        """Founder decision 2026-10-12 (M6): a lead added by hand, a lead a
        person moved to verified, and a champion-move lead (F10 promises they
        are never auto-enrolled) all go through the manual launch."""
        from app.services import champion_tracker

        manual = m.LeadBatch(strategy_id=strategy.id, requested_leads=1, icp_criteria_json={},
                             source_provider="t", verifier_provider="t",
                             stage=m.BatchStage.FINALIZED)
        db_session.add(manual)
        db_session.flush()
        db_session.add(m.Lead(strategy_id=strategy.id, batch_id=manual.id, source="t",
                              external_id="hand", email="hand@co.com",
                              status=m.LeadStatus.VERIFIED))
        db_session.add(m.Lead(strategy_id=strategy.id, source=champion_tracker.LEAD_SOURCE,
                              email="champ@new.com", status=m.LeadStatus.VERIFIED,
                              enrichment_json={champion_tracker.CHAMPION_KEY: {"move_id": "x"}}))
        db_session.commit()
        _opt_in(db_session, strategy, test_user)
        autopilot.advance(db_session, strategy.id, now=NOW, start_chain=chain)
        assert len(chain.started) == 1                    # it sources its OWN batch
        chain.run()
        own = db_session.query(m.Lead).filter_by(batch_id=uuid.UUID(chain.started[0])).all()
        assert len(own) == 3
        assert autopilot.advance(db_session, strategy.id, now=NOW,
                                 start_chain=chain) == autopilot.LAUNCHED
        enrolled = {e.lead_id for e in db_session.query(m.SequenceEnrollment)}
        assert enrolled == {lead.id for lead in own}
        # A lead of its batch flipped to verified BY HAND after the snapshot.
        flipped = m.Lead(strategy_id=strategy.id, batch_id=uuid.UUID(chain.started[0]),
                         source="t", external_id="flip", email="flip@co.com",
                         status=m.LeadStatus.FLAGGED)
        db_session.add(flipped)
        db_session.commit()
        flipped.status = m.LeadStatus.VERIFIED
        db_session.commit()
        autopilot.advance(db_session, strategy.id, now=NOW + timedelta(hours=1),
                          start_chain=chain)
        assert {e.lead_id for e in db_session.query(m.SequenceEnrollment)} == enrolled
        row = autopilot.row_for(db_session, strategy.id)
        assert row.batch_ids_json == [chain.started[0]]
        assert sorted(row.eligible_json) == sorted(str(lead.id) for lead in own)


# --------------------------------------------------------------------------
# Prerequisites: partial launch, notification, resume
# --------------------------------------------------------------------------


def _own_batch(db, strategy, user, specs):
    """Opt in, and give Autopilot a FINISHED batch of its own with these
    (email, linkedin) leads, all VERIFIED."""
    row = _opt_in(db, strategy, user)
    batch = m.LeadBatch(strategy_id=strategy.id, requested_leads=len(specs),
                        icp_criteria_json={}, source_provider="fakesource",
                        verifier_provider="fakeverifier", stage=m.BatchStage.FINALIZED)
    db.add(batch)
    db.flush()
    for i, (email, li) in enumerate(specs):
        db.add(m.Lead(strategy_id=strategy.id, batch_id=batch.id, source="t",
                      external_id=f"e{i}", email=email, linkedin_url=li,
                      status=m.LeadStatus.VERIFIED))
    row.batch_id = batch.id
    row.batch_ids_json = [str(batch.id)]
    row.chain_published_at = NOW
    db.commit()
    return row


class TestPrerequisites:
    def _leads(self, db, strategy, user):
        return _own_batch(db, strategy, user, [("a@a.com", None), ("b@b.com", None),
                                               (None, "https://www.linkedin.com/in/c")])

    def test_ready_channel_launches_waiting_one_resumes(self, db_session, test_user, strategy,
                                                        chain, monkeypatch, queued_jobs,
                                                        mailbox):
        # LinkedIn (L0) needs no provider or connected account: its steps
        # become tasks the user sends (2026-11-02).
        entitle_owner(db_session, strategy, "growth")
        self._leads(db_session, strategy, test_user)

        # No Gmail: LinkedIn launches (lead c), email waits (a, b).
        state = autopilot.advance(db_session, strategy.id, now=NOW, start_chain=chain)
        row = autopilot.row_for(db_session, strategy.id)
        assert state == autopilot.LAUNCHED
        assert row.channels_json["linkedin"]["state"] == "launched"
        assert row.channels_json["linkedin"]["enrolled"] == 1
        assert row.channels_json["email"]["state"] == "waiting"
        assert row.channels_json["email"]["fix"][0]["link"] == "/settings?tab=integrations"
        assert "Waiting" in row.state_reason and "Gmail is not connected" in row.state_reason
        assert db_session.query(m.SequenceEnrollment).count() == 1
        # Told by email AND push, once.
        pushes = [e for e in _events(queued_jobs) if "email is waiting" in e[2]["title"]]
        assert len(pushes) == 1
        assert pushes[0][2]["deep_link"] == f"/strategies/detail?id={strategy.id}"
        sent_mail = [msg for msg in mailbox if "email is waiting" in msg["subject"]]
        assert len(sent_mail) == 1 and sent_mail[0]["to"] == test_user.email
        autopilot.advance(db_session, strategy.id, now=NOW + timedelta(minutes=15),
                          start_chain=chain)
        assert len([e for e in _events(queued_jobs)
                    if "email is waiting" in e[2]["title"]]) == 1

        # Gmail appears -> the sweep resumes email for a and b.
        db_session.add(m.GmailAccount(user_id=test_user.id, email_address="s@x.dev",
                                      token_ciphertext="x", scopes="send"))
        db_session.commit()
        autopilot.sweep(db_session, now=NOW + timedelta(minutes=30), start_chain=chain)
        row = autopilot.row_for(db_session, strategy.id)
        assert row.channels_json["email"]["state"] == "launched"
        assert row.channels_json["email"]["enrolled"] == 2
        assert db_session.query(m.SequenceEnrollment).count() == 3
        assert "Waiting" not in row.state_reason

    def test_nothing_ready_waits_with_reason(self, db_session, test_user, strategy, chain):
        self._leads(db_session, strategy, test_user)
        state = autopilot.advance(db_session, strategy.id, now=NOW, start_chain=chain)
        assert state == autopilot.WAITING_FOR_CHANNELS
        assert db_session.query(m.SequenceEnrollment).count() == 0
        assert "verified lead(s) are waiting" in autopilot.row_for(
            db_session, strategy.id).state_reason

    def test_whatsapp_only_reaches_opted_in_leads(self, db_session, test_user, strategy):
        lead = m.Lead(strategy_id=strategy.id, source="t", external_id="w", phone="+15550001",
                      status=m.LeadStatus.VERIFIED)
        db_session.add(lead)
        db_session.commit()
        assert autopilot._address_ok(db_session, lead, "whatsapp") is False

    def test_a_refused_launch_gate_is_recorded_and_notified(self, db_session, test_user,
                                                           strategy, chain, gmail_account,
                                                           monkeypatch, queued_jobs):
        from fastapi import HTTPException

        from app.api import sequences as sequences_api

        def _blocked(db, sequence, action):
            raise HTTPException(status_code=409, detail="SPF is not aligned for sender.dev")

        monkeypatch.setattr(sequences_api, "_deliverability_preflight", _blocked)
        self._leads(db_session, strategy, test_user)
        autopilot.advance(db_session, strategy.id, now=NOW, start_chain=chain)
        row = autopilot.row_for(db_session, strategy.id)
        assert row.channels_json["email"]["state"] == "blocked"
        assert "SPF is not aligned" in row.channels_json["email"]["reason"]
        assert db_session.query(m.SequenceEnrollment).count() == 0
        assert any("launch was refused" in e[2]["title"] for e in _events(queued_jobs))


# --------------------------------------------------------------------------
# Verification is never bypassed
# --------------------------------------------------------------------------


class TestWaits:
    def test_needs_human_review_waits_notifies_and_resumes_on_signoff(
            self, client, db_session, test_user, strategy, chain, queued_jobs):
        from app.verification.passes import build_passes

        strategy.status = m.StrategyStatus.NEEDS_HUMAN_REVIEW
        strategy.verified_passes_json = [
            {"pass_no": p.pass_no, "key": p.key, "name": p.name, "attempt": 1,
             "result": "FAIL" if p.pass_no == 3 else "PASS",
             "fix_description": "x" if p.pass_no == 3 else None, "fix_applied": False,
             "ts": NOW.isoformat()} for p in build_passes(strategy.flow_type)]
        db_session.commit()
        _opt_in(db_session, strategy, test_user)
        for _ in range(2):
            assert autopilot.advance(db_session, strategy.id, now=NOW,
                                     start_chain=chain) == autopilot.WAITING_FOR_REVIEW
        assert chain.started == [] and db_session.query(m.LeadBatch).count() == 0
        assert len([e for e in _events(queued_jobs) if "review" in e[2]["title"]]) == 1

        before = list(queued_jobs["autopilot"])
        resp = client.post(f"/strategies/{strategy.id}/override-passes",
                           json={"pass_nos": [3], "reason": "Checked the numbers by hand.",
                                 "acknowledge": True})
        assert resp.status_code == 200, resp.text
        assert queued_jobs["autopilot"] == before + [str(strategy.id)]
        assert autopilot.advance(db_session, strategy.id, now=NOW,
                                 start_chain=chain) == autopilot.SOURCING

    @pytest.mark.parametrize("status", [m.StrategyStatus.RESEARCHING,
                                        m.StrategyStatus.VERIFYING])
    def test_still_running_waits(self, db_session, test_user, strategy, chain, status):
        strategy.status = status
        db_session.commit()
        _opt_in(db_session, strategy, test_user)
        assert autopilot.advance(db_session, strategy.id, now=NOW,
                                 start_chain=chain) == autopilot.WAITING_FOR_RESEARCH
        assert chain.started == []

    def test_failed_research_is_blocked_with_reason(self, db_session, test_user, strategy,
                                                     chain):
        strategy.status = m.StrategyStatus.BUDGET_EXHAUSTED
        db_session.commit()
        _opt_in(db_session, strategy, test_user)
        assert autopilot.advance(db_session, strategy.id, now=NOW,
                                 start_chain=chain) == autopilot.BLOCKED
        assert "budget exhausted" in autopilot.row_for(db_session, strategy.id).state_reason

    def test_a_paused_campaign_is_not_launched(self, db_session, test_user, strategy, chain):
        strategy.campaign_state = "paused_manual"
        db_session.commit()
        _opt_in(db_session, strategy, test_user)
        assert autopilot.advance(db_session, strategy.id, now=NOW,
                                 start_chain=chain) == autopilot.BLOCKED
        assert chain.started == []

    def test_opt_out_stops_everything(self, client, db_session, test_user, strategy, chain):
        _opt_in(db_session, strategy, test_user)
        r = client.put(f"/strategies/{strategy.id}/autopilot", json={"enabled": False})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == autopilot.OFF
        assert autopilot.advance(db_session, strategy.id, now=NOW, start_chain=chain) == "off"
        assert chain.started == []


# --------------------------------------------------------------------------
# Opt-in: lawful basis required + audited (LEGAL GUARD)
# --------------------------------------------------------------------------


class TestOptIn:
    def _put(self, client, strategy, **body):
        return client.put(f"/strategies/{strategy.id}/autopilot", json=body)

    @pytest.mark.parametrize("body", [
        {"enabled": True},
        {"enabled": True, "lawful_basis_confirmed": False,
         "lawful_basis_text_version": autopilot.LAWFUL_BASIS_VERSION},
        {"enabled": True, "lawful_basis_confirmed": True},
        {"enabled": True, "lawful_basis_confirmed": True,
         "lawful_basis_text_version": "autopilot-lawful-basis-2020-01-01"},
    ])
    def test_turning_on_without_the_confirmation_is_422_and_stores_nothing(
            self, client, db_session, strategy, body):
        r = self._put(client, strategy, **body)
        assert r.status_code == 422, r.text
        assert autopilot.row_for(db_session, strategy.id) is None
        assert db_session.query(m.ComplianceAuditLog).filter_by(channel="autopilot").count() == 0

    def test_confirmed_opt_in_is_recorded_and_audited(self, client, db_session, test_user,
                                                      strategy, queued_jobs):
        r = self._put(client, strategy, enabled=True, lawful_basis_confirmed=True,
                      lawful_basis_text_version=autopilot.LAWFUL_BASIS_VERSION)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enabled"] is True and body["text_version"] == autopilot.LAWFUL_BASIS_VERSION
        assert body["lawful_basis"]["text"] == autopilot.LAWFUL_BASIS_TEXT
        row = autopilot.row_for(db_session, strategy.id)
        assert row.confirmed_by_user_id == test_user.id and row.confirmed_at is not None
        assert row.confirmed_text == autopilot.LAWFUL_BASIS_TEXT
        audit = db_session.query(m.ComplianceAuditLog).filter_by(channel="autopilot").one()
        assert audit.decision == "autopilot_opt_in" and audit.user_id == test_user.id
        assert audit.checks_json["text"] == autopilot.LAWFUL_BASIS_TEXT
        assert audit.checks_json["text_version"] == autopilot.LAWFUL_BASIS_VERSION
        event = db_session.query(m.SecurityAuditEvent).filter_by(action="autopilot.opt_in").one()
        assert event.user_id == test_user.id and event.target_id == str(strategy.id)
        # Already verified: it starts now rather than at the next sweep.
        assert queued_jobs["autopilot"] == [str(strategy.id)]

        off = self._put(client, strategy, enabled=False)
        assert off.status_code == 200
        decisions = [a.decision for a in db_session.query(m.ComplianceAuditLog)
                     .filter_by(channel="autopilot").order_by(m.ComplianceAuditLog.ts)]
        assert decisions == ["autopilot_opt_in", "autopilot_opt_out"]

    def test_never_on_by_default(self, client, db_session, product_with_strategy):
        product, _ = product_with_strategy(m.FlowType.WITH_CLIENTS)
        resp = client.post(f"/products/{product.id}/strategies", json={})
        assert resp.status_code == 202
        sid = uuid.UUID(resp.json()["id"])
        assert autopilot.row_for(db_session, sid) is None
        assert client.get(f"/strategies/{sid}/autopilot").json()["enabled"] is False

    def test_create_with_unconfirmed_autopilot_creates_nothing(self, client, db_session,
                                                               product_with_strategy, enqueued):
        product, _ = product_with_strategy(m.FlowType.WITH_CLIENTS)
        before = db_session.query(m.Strategy).count()
        resp = client.post(f"/products/{product.id}/strategies",
                           json={"autopilot": {"enabled": True}})
        assert resp.status_code == 422, resp.text
        assert db_session.query(m.Strategy).count() == before
        assert enqueued == []

    def test_flag_off_refuses_and_the_sweep_does_nothing(self, client, db_session, test_user,
                                                         strategy, chain):
        _opt_in(db_session, strategy, test_user)
        system_settings.set(db_session, autopilot.FLAG, False)
        r = self._put(client, strategy, enabled=True, lawful_basis_confirmed=True,
                      lawful_basis_text_version=autopilot.LAWFUL_BASIS_VERSION)
        assert r.status_code == 409
        assert autopilot.sweep(db_session, now=NOW, start_chain=chain) == {
            "checked": 0, "flag_off": True}
        assert autopilot.advance(db_session, strategy.id, now=NOW,
                                 start_chain=chain) == "flag_off"
        assert chain.started == []
        assert client.get(f"/strategies/{strategy.id}/autopilot").json()["available"] is False

    def test_an_sdr_cannot_turn_it_on(self, client, db_session, test_user, strategy):
        from app.services import workspaces

        ws = workspaces.personal_workspace(db_session, test_user)
        sdr = m.User(email="sdr@autopilot.dev", email_verified=True)
        db_session.add(sdr)
        db_session.flush()
        db_session.add(m.WorkspaceMember(workspace_id=ws.id, user_id=sdr.id, role="sdr"))
        db_session.commit()
        headers = {**auth_headers(sdr), "X-Workspace-Id": str(ws.id)}
        r = client.put(f"/strategies/{strategy.id}/autopilot", headers=headers, json={
            "enabled": True, "lawful_basis_confirmed": True,
            "lawful_basis_text_version": autopilot.LAWFUL_BASIS_VERSION})
        assert r.status_code == 403
        assert autopilot.row_for(db_session, strategy.id) is None
        assert client.get(f"/strategies/{strategy.id}/autopilot",
                          headers=headers).json()["can_change"] is False

    def test_a_manager_is_recorded_as_the_person_who_confirmed(self, client, db_session,
                                                               test_user, strategy):
        from app.services import workspaces

        ws = workspaces.personal_workspace(db_session, test_user)
        manager = m.User(email="mgr@autopilot.dev", email_verified=True)
        db_session.add(manager)
        db_session.flush()
        db_session.add(m.WorkspaceMember(workspace_id=ws.id, user_id=manager.id,
                                         role="manager"))
        db_session.commit()
        headers = {**auth_headers(manager), "X-Workspace-Id": str(ws.id)}
        r = client.put(f"/strategies/{strategy.id}/autopilot", headers=headers, json={
            "enabled": True, "lawful_basis_confirmed": True,
            "lawful_basis_text_version": autopilot.LAWFUL_BASIS_VERSION})
        assert r.status_code == 200, r.text
        row = autopilot.row_for(db_session, strategy.id)
        assert row.confirmed_by_user_id == manager.id
        audit = db_session.query(m.ComplianceAuditLog).filter_by(channel="autopilot").one()
        assert audit.user_id == test_user.id                      # the account's record
        assert audit.checks_json["actor_user_id"] == str(manager.id)

    def test_lawful_basis_text_endpoint(self, client):
        body = client.get("/autopilot/lawful-basis").json()
        assert body["version"] == autopilot.LAWFUL_BASIS_VERSION
        assert body["text"] == autopilot.LAWFUL_BASIS_TEXT and body["available"] is True


# --------------------------------------------------------------------------
# Briefs, tenancy, data lifecycle
# --------------------------------------------------------------------------


class TestBriefs:
    def test_a_valid_model_answer_is_used(self, db_session, test_user, strategy, monkeypatch):
        class _Model:
            def complete_json(self, system, prompt, **kw):
                assert "sequence planner" in system and "CHANNEL: email" in prompt
                return {"steps": [{"brief": "Open on the inspection backlog they mention.",
                                   "delay_days": 5},
                                  {"brief": "Share the one-page ITM checklist idea.",
                                   "delay_days": 2}]}

        monkeypatch.setattr("app.services.anthropic_client.get_client", lambda: _Model())
        steps, source = autopilot.plan_steps(db_session, strategy, "email")
        assert source == "model"
        assert steps == [{"brief": "Open on the inspection backlog they mention.",
                          "delay_days": 0},
                         {"brief": "Share the one-page ITM checklist idea.", "delay_days": 2}]

    @pytest.mark.parametrize("answer", [{}, {"steps": []}, {"steps": [{"brief": "short"}]},
                                        {"steps": [{"brief": "A long enough brief here.",
                                                    "delay_days": 0},
                                                   {"brief": "Another long enough brief.",
                                                    "delay_days": 99}]},
                                        RuntimeError("model down")])
    def test_bad_answers_fall_back_to_fixed_briefs(self, db_session, strategy, monkeypatch,
                                                   answer):
        class _Model:
            def complete_json(self, system, prompt, **kw):
                if isinstance(answer, Exception):
                    raise answer
                return answer

        monkeypatch.setattr("app.services.anthropic_client.get_client", lambda: _Model())
        steps, source = autopilot.plan_steps(db_session, strategy, "email")
        assert source == "fixed" and steps == autopilot.FIXED_STEPS["email"]

    def test_planned_channels_follow_the_strategy(self, db_session, strategy):
        assert autopilot.planned_channels(db_session, strategy) == ["email", "linkedin"]
        strategy.strategy_document = "Email only."
        assert autopilot.planned_channels(db_session, strategy) == ["email"]


class TestTenancyAndLifecycle:
    def test_another_account_cannot_read_or_change(self, client, db_session, test_user):
        other = m.User(email="o@leadpilot.dev", email_verified=True)
        db_session.add(other)
        db_session.flush()
        product = m.Product(user=other, name="X", description="x", type=m.ProductType.SKILL)
        db_session.add(product)
        db_session.flush()
        strategy = m.Strategy(product_id=product.id, flow_type=m.FlowType.NO_CLIENTS)
        db_session.add(strategy)
        db_session.commit()
        r = client.put(f"/strategies/{strategy.id}/autopilot", json={
            "enabled": True, "lawful_basis_confirmed": True,
            "lawful_basis_text_version": autopilot.LAWFUL_BASIS_VERSION})
        assert r.status_code == 404
        assert autopilot.row_for(db_session, strategy.id) is None

    def test_sweep_only_touches_enabled_rows(self, db_session, test_user, strategy, chain):
        row = _opt_in(db_session, strategy, test_user)
        row.enabled = False
        db_session.commit()
        assert autopilot.sweep(db_session, now=NOW, start_chain=chain)["checked"] == 0

    def test_ownership_registries(self):
        from app.services import account_erasure, account_export

        assert account_export.OWNERSHIP["autopilot_launches"] == [
            "strategy_id", "product_id", "user_id"]
        assert "autopilot_launches" in account_erasure.DELETED
        # The opt-in PROOF is kept on erasure.
        assert account_erasure.KEPT["compliance_audit_log"] == account_erasure.KEEP_PROOF

    def test_metering_purposes_are_capped(self):
        from app.services import usage_meter

        assert {"autopilot", "send_time"} <= set(usage_meter.ACCOUNT_CAPPED_PURPOSES)

    def test_the_event_is_registered(self):
        from app.services import event_bus

        assert autopilot.EVENT in event_bus.EVENTS
