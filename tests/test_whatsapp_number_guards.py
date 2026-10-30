"""SEALED legal/safety guard (evals/compliance_testset.yaml): WhatsApp is sent
only to numbers in international format, and a template Meta stopped is never
sent.

Meta: "If the plus sign is omitted, your business phone number's country
calling code is prepended to the customer's phone number. This can result in
undelivered or misdelivered messages." A message to the wrong person in the
wrong country is a privacy and consent failure, so a number without "+" or
"00" is refused before any call. Templates Meta PAUSED / DISABLED / ARCHIVED /
DELETED / PENDING_DELETION must not be sent.

Moved byte-identically from tests/test_whatsapp_cloud_contract.py (#146) by
the 2026-10-29 gate PR (docs/program/plans/phase5-seal-vendor-guards.md).
"""

import pytest

from app.db import models as m
from app.integrations import whatsapp as wa
from app.integrations.whatsapp import ComplianceError
from tests.conftest import wa_outbound


@pytest.mark.parametrize("stored, sent", [
    ("+923001234567", "+923001234567"),
    ("+92 300-123 4567", "+923001234567"),
    ("+1 (631) 555-1234", "+16315551234"),
    ("0092 300 1234567", "+923001234567"),
])
def test_to_e164_keeps_the_country_code(stored, sent):
    assert wa.to_e164(stored) == sent


@pytest.mark.parametrize("stored", ["923001234567", "(631) 555-1234", "", "+", "abc"])
def test_to_e164_refuses_a_number_without_international_prefix(stored):
    assert wa.to_e164(stored) is None


def test_send_puts_the_plus_on_the_wire(db_session, wa_lead, approved_template,
                                        wa_channel, fake_graph_http):
    wa_lead.phone = "+92 300-123 4567"
    db_session.commit()
    assert wa_channel.send(wa_outbound(wa_lead)).ok
    (_method, _endpoint, payload), = fake_graph_http.requests
    assert payload["to"] == "+923001234567"


def test_send_refuses_a_national_format_number(db_session, wa_lead, approved_template,
                                               wa_channel, fake_graph_http):
    wa_lead.phone = "3001234567"
    db_session.commit()
    with pytest.raises(ComplianceError, match="international format") as exc:
        wa_channel.send(wa_outbound(wa_lead))
    assert exc.value.rule == "whatsapp_phone_not_international"
    assert fake_graph_http.requests == []


@pytest.mark.parametrize("meta_status", ["PAUSED", "DISABLED", "ARCHIVED", "DELETED",
                                         "PENDING_DELETION"])
def test_a_template_meta_stopped_cannot_be_sent(db_session, approved_template, meta_status):
    from app.services import whatsapp_templates as tmpl_svc
    tmpl_svc.apply_meta_status(db_session, approved_template, meta_status)
    assert approved_template.status is not m.WhatsAppTemplateStatus.APPROVED
    assert meta_status in (approved_template.rejection_reason or "")
    assert tmpl_svc.sendable_template(db_session, "intro_v1", "en_US") is None


@pytest.mark.parametrize("meta_status", ["REINSTATED", "UNARCHIVED"])
def test_a_reinstated_template_is_sendable_again(db_session, approved_template, meta_status):
    from app.services import whatsapp_templates as tmpl_svc
    tmpl_svc.apply_meta_status(db_session, approved_template, "PAUSED")
    tmpl_svc.apply_meta_status(db_session, approved_template, meta_status)
    assert approved_template.status is m.WhatsAppTemplateStatus.APPROVED
    assert tmpl_svc.sendable_template(db_session, "intro_v1", "en_US") is not None
