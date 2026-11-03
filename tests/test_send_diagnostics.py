""""Why isn't this sending?" (docs/features/autopilot-launch.md section 3.3).

Every reason the panel can show is PINNED TO ITS GATE: each test creates the
condition, asserts the panel names it (with its fix / link), and drives the
real send path (send_message_impl, the launch checks, the channel factory)
into the matching result -- so the panel cannot say "sending" while the send
path refuses, or name a reason the send path does not act on.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.db import models as m
from app.services import daily_send_approval as dsa
from app.services import send_diagnostics as diag
from app.services import system_settings

NOW = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)   # Tuesday, in the window
_REAL_DSA = dsa.enabled


@pytest.fixture()
def wired(monkeypatch, fake_channel):
    from app.workers import outreach_tasks

    monkeypatch.setattr(outreach_tasks, "_get_channel", lambda s, a: fake_channel)
    return outreach_tasks


def _codes(out, channel="email"):
    general = [r["code"] for r in out["general"]]
    per = next((c for c in out["channels"] if c["channel"] == channel), {"reasons": []})
    return general + [r["code"] for r in per["reasons"]]


def _reason(out, code, channel="email"):
    for r in out["general"]:
        if r["code"] == code:
            return r
    per = next(c for c in out["channels"] if c["channel"] == channel)
    return next(r for r in per["reasons"] if r["code"] == code)


def _first(db):
    return (db.query(m.Message).filter_by(status=m.MessageStatus.SCHEDULED)
            .order_by(m.Message.created_at, m.Message.id).first())


def _as_linkedin(db, message):
    lead = db.get(m.Lead, message.lead_id)
    lead.linkedin_url = f"https://www.linkedin.com/in/{lead.external_id.lower()}"
    message.channel = m.ChannelType.LINKEDIN
    db.commit()
    return message


class TestHealthy:
    def test_a_running_campaign_has_no_blocking_reason(self, db_session, enrolled,
                                                       verified_strategy):
        out = diag.diagnose(db_session, verified_strategy, now=NOW)
        assert out["sending"] is True
        email = out["channels"][0]
        assert email["channel"] == "email" and email["sending"] is True
        assert email["scheduled"] == 3 and email["next_send_at"] is not None
        assert not [r for r in email["reasons"] if r["blocking"]]

    def test_the_api_returns_the_panel(self, client, enrolled, verified_strategy):
        r = client.get(f"/strategies/{verified_strategy.id}/send-diagnostics")
        assert r.status_code == 200, r.text
        assert r.json()["channels"][0]["channel"] == "email"
        assert r.json()["autopilot"] == {"enabled": False, "state": "off"}


class TestStrategyLevel:
    def test_not_verified(self, db_session, product_with_strategy):
        from app.api import sequences as sequences_api

        _, strategy = product_with_strategy(m.FlowType.WITH_CLIENTS)
        out = diag.diagnose(db_session, strategy, now=NOW)
        assert "strategy_not_verified" in _codes(out)
        seq = m.Sequence(strategy_id=strategy.id, channel=m.ChannelType.EMAIL, name="s")
        db_session.add(seq)
        db_session.commit()
        with pytest.raises(HTTPException) as exc:            # the launch gate agrees
            sequences_api._launch_checks(db_session, seq)
        assert exc.value.status_code == 409

    def test_needs_review(self, db_session, verified_strategy):
        verified_strategy.status = m.StrategyStatus.NEEDS_HUMAN_REVIEW
        db_session.commit()
        reason = _reason(diag.diagnose(db_session, verified_strategy, now=NOW),
                         "verification_needs_review")
        assert reason["link"] == f"/strategies/detail?id={verified_strategy.id}"

    def test_campaign_paused(self, db_session, enrolled, verified_strategy, wired):
        verified_strategy.campaign_state = "paused_manual"
        db_session.commit()
        assert "campaign_paused" in _codes(diag.diagnose(db_session, verified_strategy, now=NOW))
        assert wired.send_message_impl(db_session, _first(db_session).id,
                                       now=NOW) == "campaign_paused"

    def test_nothing_launched_and_no_leads(self, db_session, verified_strategy):
        out = diag.diagnose(db_session, verified_strategy, now=NOW)
        assert {"no_verified_leads", "not_launched"} <= set(_codes(out))
        assert out["sending"] is False

    def test_autopilot_on_but_flag_off(self, db_session, verified_strategy, test_user,
                                       monkeypatch):
        from app.services import autopilot

        monkeypatch.setattr(dsa, "enabled", _REAL_DSA)   # Autopilot needs daily approval

        autopilot.set_opt_in(db_session, verified_strategy, enabled=True, confirmed=True,
                             text_version=autopilot.LAWFUL_BASIS_VERSION,
                             actor_id=test_user.id, owner_id=test_user.id)
        system_settings.set(db_session, autopilot.FLAG, False)
        assert "autopilot_flag_off" in _codes(diag.diagnose(db_session, verified_strategy,
                                                            now=NOW))


class TestPrerequisites:
    def test_gmail_not_connected(self, db_session, fake_claude, email_sequence, verified_leads,
                                 verified_strategy, gmail_account):
        from app.services import sequence_engine as engine
        from app.workers import outreach_tasks

        engine.enroll_leads(db_session, email_sequence, now=NOW)
        db_session.delete(gmail_account)
        db_session.commit()
        reason = _reason(diag.diagnose(db_session, verified_strategy, now=NOW),
                         "gmail_not_connected")
        assert reason["link"] == "/settings?tab=integrations" and reason["blocking"]
        with pytest.raises(RuntimeError, match="no connected Gmail account"):
            outreach_tasks._account_for_strategy(db_session, verified_strategy)

    def test_linkedin_needs_nothing_configured(self, db_session, test_user):
        # L0 (2026-11-02): LinkedIn steps become tasks the user sends; there
        # is no provider key and no account to connect, so no prerequisite.
        user = db_session.get(m.User, test_user.id)
        user.plan = "growth"
        db_session.commit()
        assert diag.channel_readiness(db_session, test_user.id, "linkedin") == []

    def test_channel_not_in_plan_and_the_plan_hold(self, db_session, enrolled,
                                                   verified_strategy, wired):
        msg = _as_linkedin(db_session, _first(db_session))
        enrollment = db_session.query(m.SequenceEnrollment).filter_by(lead_id=msg.lead_id).one()
        enrollment.created_at = datetime(2026, 10, 13, tzinfo=timezone.utc)  # after the cutover
        db_session.commit()
        out = diag.diagnose(db_session, verified_strategy, now=NOW)
        assert _reason(out, "channel_not_in_plan", "linkedin")["link"] == "/pricing"
        from app.services import plan_holds

        assert wired.send_message_impl(db_session, msg.id, now=NOW) == plan_holds.SEND_RESULT
        out = diag.diagnose(db_session, verified_strategy, now=NOW)
        assert _reason(out, "plan_holds", "linkedin")["count"] == 1

    def test_whatsapp_not_configured(self, db_session, test_user, monkeypatch):
        from app.config import settings
        from app.integrations.whatsapp import WhatsAppChannel, WhatsAppNotConfigured

        monkeypatch.setattr(settings, "whatsapp_access_token", "")
        user = db_session.get(m.User, test_user.id)
        user.plan = "growth"
        db_session.commit()
        codes = [r["code"] for r in diag.channel_readiness(db_session, test_user.id, "whatsapp")]
        assert codes == ["whatsapp_not_configured"]
        with pytest.raises(WhatsAppNotConfigured):          # the channel factory agrees
            WhatsAppChannel(session=db_session)

    def test_whatsapp_needs_an_approved_template_with_opt_out(self, db_session, test_user,
                                                              monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "whatsapp_access_token", "t")
        monkeypatch.setattr(settings, "whatsapp_phone_number_id", "1")
        user = db_session.get(m.User, test_user.id)
        user.plan = "growth"
        db_session.commit()
        assert [r["code"] for r in diag.channel_readiness(db_session, test_user.id,
                                                          "whatsapp")] == ["whatsapp_no_template"]
        db_session.add(m.WhatsAppTemplate(name="intro", language="en_US", body="Hi {{1}}",
                                          status=m.WhatsAppTemplateStatus.APPROVED))
        db_session.commit()
        assert diag.whatsapp_template_ready(db_session) is None       # no opt-out line
        db_session.add(m.WhatsAppTemplate(name="intro2", language="en_US",
                                          body="Hi {{1}}. Reply STOP to opt out.",
                                          status=m.WhatsAppTemplateStatus.APPROVED))
        db_session.commit()
        assert diag.channel_readiness(db_session, test_user.id, "whatsapp") == []

    def test_phone_calling_off(self, db_session, test_user):
        user = db_session.get(m.User, test_user.id)
        user.plan = "pro"
        db_session.commit()
        codes = [r["code"] for r in diag.channel_readiness(db_session, test_user.id, "phone")]
        assert codes == ["phone_calling_off"]


class TestSendTimeGates:
    def test_send_approval_missing(self, db_session, enrolled, verified_strategy, wired,
                                   monkeypatch):
        monkeypatch.setattr(dsa, "enabled", _REAL_DSA)
        reason = _reason(diag.diagnose(db_session, verified_strategy, now=NOW),
                         "send_approval_missing")
        assert reason["link"] == "/send-approval" and reason["count"] == 0
        assert wired.send_message_impl(db_session, _first(db_session).id, now=NOW) == \
            dsa.SEND_RESULT_AWAITING
        assert _reason(diag.diagnose(db_session, verified_strategy, now=NOW),
                       "send_approval_missing")["count"] == 1

    def test_outside_sending_hours(self, db_session, enrolled, verified_strategy, wired):
        night = datetime(2026, 8, 11, 20, 0, tzinfo=timezone.utc)
        reason = _reason(diag.diagnose(db_session, verified_strategy, now=night),
                         "outside_sending_hours")
        assert "2026-08-12T09:00:00+00:00" in reason["fix"] and not reason["blocking"]
        assert wired.send_message_impl(db_session, _first(db_session).id,
                                       now=night) == "deferred_window"

    def test_daily_cap_reached(self, db_session, enrolled, verified_strategy, wired,
                               monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "gmail_daily_cap", 1)
        first, second = (db_session.query(m.Message)
                         .filter_by(status=m.MessageStatus.SCHEDULED).limit(2).all())
        assert wired.send_message_impl(db_session, first.id, now=NOW) == "sent"
        assert "daily_cap_reached" in _codes(diag.diagnose(db_session, verified_strategy,
                                                           now=NOW))
        assert wired.send_message_impl(db_session, second.id, now=NOW) == "deferred_cap"

    def test_mailbox_paused(self, db_session, enrolled, verified_strategy, wired,
                            gmail_account):
        db_session.add(m.MailboxHealth(user_id=gmail_account.user_id,
                                       mailbox_ref=str(gmail_account.id), channel="email",
                                       state="paused", reason="bounce rate 9%"))
        db_session.commit()
        reason = _reason(diag.diagnose(db_session, verified_strategy, now=NOW), "mailbox_paused")
        assert reason["fix"] == "bounce rate 9%" and reason["link"] == "/settings?tab=deliverability"
        assert wired.send_message_impl(db_session, _first(db_session).id,
                                       now=NOW) == "deferred_mailbox_paused"

    def test_held_in_review(self, db_session, enrolled, verified_strategy, wired, monkeypatch):
        from app.services import send_review

        monkeypatch.setattr(send_review, "enabled", lambda db, code=None: True)
        assert wired.send_message_impl(db_session, _first(db_session).id,
                                       now=NOW) == "held_for_review"
        reason = _reason(diag.diagnose(db_session, verified_strategy, now=NOW), "held_in_review")
        assert reason["count"] == 1
        assert reason["link"] == f"/campaigns?strategy={verified_strategy.id}&tab=review"

    def test_bounce_shield_hold(self, db_session, enrolled, verified_strategy, wired):
        from app.services import bounce_shield

        system_settings.set(db_session, bounce_shield.FLAG, True)
        assert wired.send_message_impl(db_session, _first(db_session).id,
                                       now=NOW) == bounce_shield.SEND_RESULT
        assert _reason(diag.diagnose(db_session, verified_strategy, now=NOW),
                       "bounce_shield_holds")["count"] == 1

    def test_suppressed(self, db_session, enrolled, verified_strategy, wired):
        msg = _first(db_session)
        lead = db_session.get(m.Lead, msg.lead_id)
        db_session.add(m.SuppressionEntry(email=lead.email, reason="unsubscribed"))
        db_session.commit()
        assert wired.send_message_impl(db_session, msg.id, now=NOW) == "cancelled_suppressed"
        reason = _reason(diag.diagnose(db_session, verified_strategy, now=NOW), "suppressed")
        assert reason["count"] == 1 and reason["blocking"] is False


class TestChampionHold:
    def test_champion_hold_marker(self, db_session, enrolled, verified_strategy):
        from app.services import champion_tracker

        msg = _first(db_session)
        champion_tracker.hold_message(msg, "role data is 200 days old")   # the gate's own writer
        db_session.commit()
        assert _reason(diag.diagnose(db_session, verified_strategy, now=NOW),
                       "champion_holds")["count"] == 1


class TestComplianceRules:
    """M4 (review probe P8): the panel reads the compliance rules the send path
    resolves -- never the baseline when a rule applies."""

    def test_consent_required_rule_is_not_shown_as_sending(self, db_session, enrolled,
                                                           verified_strategy, wired):
        from app.services import compliance_rules as rules

        db_session.add(m.ComplianceRule(scope=rules.GLOBAL, region="*", channel="*",
                                        consent_required=True))
        db_session.commit()
        out = diag.diagnose(db_session, verified_strategy, now=NOW)
        reason = _reason(out, "consent_required")
        assert reason["count"] == 3 and reason["blocking"] is True
        assert out["sending"] is False
        assert wired.send_message_impl(db_session, _first(db_session).id,
                                       now=NOW) == "skipped_consent_required"

    def test_an_empty_rule_window(self, db_session, enrolled, verified_strategy, wired):
        from app.services import compliance_rules as rules

        # A valid rule that sets only the start (18:00) meets the baseline's
        # 17:00 end: the resolved window is empty -- closed.
        db_session.add(m.ComplianceRule(scope=rules.GLOBAL, region="*", channel="email",
                                        send_start_hour=18))
        db_session.commit()
        assert _reason(diag.diagnose(db_session, verified_strategy, now=NOW),
                       "no_send_window")["blocking"] is True
        assert wired.send_message_impl(db_session, _first(db_session).id,
                                       now=NOW) == "deferred_no_send_window"

    def test_the_resolved_window_not_the_baseline(self, db_session, enrolled,
                                                  verified_strategy, wired):
        from app.services import compliance_rules as rules

        db_session.add(m.ComplianceRule(scope=rules.GLOBAL, region="*", channel="email",
                                        send_start_hour=13, send_end_hour=15))
        db_session.commit()
        reason = _reason(diag.diagnose(db_session, verified_strategy, now=NOW),
                         "outside_sending_hours")      # 10:00 is inside the baseline
        assert "2026-08-11T13:00:00+00:00" in reason["fix"]
        assert wired.send_message_impl(db_session, _first(db_session).id,
                                       now=NOW) == "deferred_window"


class TestGmailToken:
    def test_a_revoked_token_is_not_ready(self, db_session, enrolled, verified_strategy,
                                          gmail_account, monkeypatch):
        import httpx

        from app.workers import outreach_tasks

        class _Revoked:
            def send(self, outbound):
                request = httpx.Request("POST", "https://oauth2.googleapis.com/token")
                raise httpx.HTTPStatusError(
                    "Client error '400 Bad Request' for url "
                    "'https://oauth2.googleapis.com/token' (invalid_grant)",
                    request=request, response=httpx.Response(400, request=request))

        monkeypatch.setattr(outreach_tasks, "_get_channel", lambda s, a: _Revoked())
        assert "gmail_token_invalid" not in _codes(diag.diagnose(db_session,
                                                                 verified_strategy, now=NOW))
        with pytest.raises(httpx.HTTPStatusError):              # the real send path
            outreach_tasks.send_message_impl(db_session, _first(db_session).id, now=NOW)
        reason = _reason(diag.diagnose(db_session, verified_strategy, now=NOW),
                         "gmail_token_invalid")
        assert reason["link"] == "/settings?tab=integrations" and reason["blocking"]
        assert [r["code"] for r in diag.channel_readiness(
            db_session, gmail_account.user_id, "email")] == ["gmail_token_invalid"]


class TestAutopilotHold:
    def test_autopilot_message_held_when_approval_is_off(self, db_session, test_user,
                                                         verified_strategy, verified_leads,
                                                         gmail_account, fake_claude, wired,
                                                         monkeypatch):
        from app.services import autopilot, send_time

        monkeypatch.setattr(dsa, "enabled", _REAL_DSA)
        autopilot.set_opt_in(db_session, verified_strategy, enabled=True, confirmed=True,
                             text_version=autopilot.LAWFUL_BASIS_VERSION,
                             actor_id=test_user.id, owner_id=test_user.id)
        row = autopilot.row_for(db_session, verified_strategy.id)
        for lead in verified_leads:
            lead.batch_id = None
        batch = m.LeadBatch(strategy_id=verified_strategy.id, requested_leads=3,
                            icp_criteria_json={}, source_provider="t", verifier_provider="t",
                            stage=m.BatchStage.FINALIZED)
        db_session.add(batch)
        db_session.flush()
        for lead in verified_leads:
            lead.batch_id = batch.id
        row.batch_id, row.batch_ids_json = batch.id, [str(batch.id)]
        db_session.commit()
        autopilot.advance(db_session, verified_strategy.id, now=NOW,
                          start_chain=lambda b, s: None)
        system_settings.set(db_session, "daily_send_approval_enabled", False)
        msg = _first(db_session)
        due = max(NOW, msg.scheduled_at.replace(tzinfo=timezone.utc))
        assert wired.send_message_impl(db_session, msg.id, now=due) == \
            "deferred_autopilot_needs_daily_approval"
        assert _reason(diag.diagnose(db_session, verified_strategy, now=due),
                       "autopilot_approval_hold")["count"] == 1
        assert send_time is not None


class TestTimezoneFallback:
    def test_leads_without_a_location_are_named(self, db_session, enrolled, verified_strategy):
        reason = _reason(diag.diagnose(db_session, verified_strategy, now=NOW),
                         "timezone_fallback")
        assert reason["count"] == 3 and reason["blocking"] is False
        assert "deployment default (UTC)" in reason["title"]


class TestEveryCodeIsPinned:
    def test_codes_the_module_can_emit_are_all_tested_here(self):
        """A reason added to send_diagnostics without a pinned test here fails."""
        import inspect
        import re

        source = inspect.getsource(diag)
        emitted = set(re.findall(r'_reason\(\s*"([a-z_]+)"', source)) | set(
            re.findall(r'\("([a-z_]+_holds)", send_holds', source))
        tested = inspect.getsource(inspect.getmodule(TestEveryCodeIsPinned))
        untested = sorted(code for code in emitted if f'"{code}"' not in tested
                          and code not in ("owner_missing", "sequence_finished"))
        assert not untested, f"reasons without a pinned test: {untested}"
