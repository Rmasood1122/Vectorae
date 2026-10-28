"""A Calendly reschedule is not a cancellation.

Calendly's docs ("Webhook Payloads for Rescheduled Events", checked
2026-10-28, no account used): when an invitee reschedules, Calendly sends an
`invitee.canceled` for the old event with `"rescheduled": true` and an
`invitee.created` for the new one. The two can arrive in either order.

Before this fix the canceled half moved the lead back to Replied and wrote a
"replied" outcome, so a lead who merely moved the call looked like a lost
meeting -- and if the new booking had already arrived, it stayed that way.
"""

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import models as m
from tests.test_calendly import SIGNING_KEY, _owner_of, booking_payload, signed_post

OLD = "https://api.calendly.com/scheduled_events/OLD/invitees/INV-OLD"
NEW = "https://api.calendly.com/scheduled_events/NEW/invitees/INV-NEW"


@pytest.fixture(autouse=True)
def calendly_key(monkeypatch):
    monkeypatch.setattr(settings, "calendly_webhook_signing_key", SIGNING_KEY)
    monkeypatch.setattr("app.integrations.calendly._now", lambda: 1723300000.0)


def _canceled(email, tenant, uri=OLD, rescheduled=None):
    payload = booking_payload(email, event="invitee.canceled", uri=uri, tenant_id=tenant)
    if rescheduled is not None:
        payload["payload"]["rescheduled"] = rescheduled
    return payload


def _outcomes(db_session, lead):
    return [o.event for o in db_session.execute(
        select(m.Outcome).where(m.Outcome.lead_id == lead.id)).scalars()]


class TestReschedule:
    def test_new_booking_first_then_old_canceled(self, leads_client, db_session,
                                                 enrolled, verified_leads):
        lead = verified_leads[0]
        tenant = _owner_of(db_session, lead)
        signed_post(leads_client, booking_payload(lead.email, uri=OLD, tenant_id=tenant))
        signed_post(leads_client, booking_payload(lead.email, uri=NEW, tenant_id=tenant))
        r = signed_post(leads_client, _canceled(lead.email, tenant, rescheduled=True))
        assert r.json()["action"] == "rescheduled"
        db_session.refresh(lead)
        assert lead.status is m.LeadStatus.MEETING_BOOKED
        assert m.OutcomeEvent.REPLIED not in _outcomes(db_session, lead)

    def test_old_canceled_first_then_new_booking(self, leads_client, db_session,
                                                 enrolled, verified_leads):
        lead = verified_leads[1]
        tenant = _owner_of(db_session, lead)
        signed_post(leads_client, booking_payload(lead.email, uri=OLD, tenant_id=tenant))
        r = signed_post(leads_client, _canceled(lead.email, tenant, rescheduled=True))
        assert r.json()["action"] == "rescheduled"
        db_session.refresh(lead)
        assert lead.status is m.LeadStatus.MEETING_BOOKED
        signed_post(leads_client, booking_payload(lead.email, uri=NEW, tenant_id=tenant))
        db_session.refresh(lead)
        assert lead.status is m.LeadStatus.MEETING_BOOKED
        assert m.OutcomeEvent.REPLIED not in _outcomes(db_session, lead)

    def test_the_old_meetings_reminders_still_stop(self, leads_client, db_session,
                                                   enrolled, verified_leads, monkeypatch):
        calls = []
        monkeypatch.setattr("app.services.meeting_prep.cancel_briefs",
                            lambda db, lead_id, **kw: calls.append(kw) or 0)
        lead = verified_leads[2]
        tenant = _owner_of(db_session, lead)
        signed_post(leads_client, _canceled(lead.email, tenant, rescheduled=True))
        assert len(calls) == 1 and calls[0]["source"] == "calendly"

    @pytest.mark.parametrize("flag", [None, False])
    def test_a_real_cancellation_still_reverts(self, leads_client, db_session,
                                               enrolled, verified_leads, flag):
        lead = verified_leads[2]
        tenant = _owner_of(db_session, lead)
        signed_post(leads_client, booking_payload(lead.email, uri=OLD, tenant_id=tenant))
        r = signed_post(leads_client, _canceled(lead.email, tenant, rescheduled=flag))
        assert r.json()["action"] == "canceled"
        db_session.refresh(lead)
        assert lead.status is m.LeadStatus.REPLIED
        assert m.OutcomeEvent.REPLIED in _outcomes(db_session, lead)

    def test_a_truthy_non_boolean_is_not_a_reschedule(self, leads_client, db_session,
                                                      enrolled, verified_leads):
        lead = verified_leads[0]
        tenant = _owner_of(db_session, lead)
        signed_post(leads_client, booking_payload(lead.email, uri=OLD, tenant_id=tenant))
        r = signed_post(leads_client, _canceled(lead.email, tenant, rescheduled="false"))
        assert r.json()["action"] == "canceled"
