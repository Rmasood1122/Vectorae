"""The WhatsApp adapter matches Meta's current Cloud API documentation.

Checked against Meta's public docs on 2026-10-27 (no account or key used):
  * Graph API versions: v20.0 expired 2026-09-24; v25.0 is supported until
    2028-07-29 (developers.facebook.com/docs/graph-api/changelog/versions).
  * Send messages: "If the plus sign is omitted, your business phone
    number's country calling code is prepended to the customer's phone
    number. This can result in undelivered or misdelivered messages."
    (.../whatsapp/messages/send-messages)
  * Text messages: text.body is at most 4096 characters.
  * Templates: a body parameter needs an example value
    ("example": {"body_text": [[...]]}) (.../whatsapp/templates/components).
  * Status webhook values: sent, delivered, read, failed, played.
  * Template status webhook events include PAUSED, DISABLED, ARCHIVED,
    DELETED, PENDING_DELETION (cannot be sent) and REINSTATED, UNARCHIVED.
  * GET /{template_id} returns one template; the list endpoint is paged
    (data + paging.cursors.after).

Founder choices (2026-10-27): send only to numbers stored in international
format (a "+" or "00" prefix), refuse the rest; default to v25.0.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db import models as m
from app.integrations import whatsapp as wa
from app.integrations.whatsapp import ComplianceError
from tests.conftest import wa_outbound


def _open_window(db_session, lead):
    lead.whatsapp_last_inbound_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()


# -- Graph API version ------------------------------------------------------

def test_default_graph_api_version_is_a_supported_one():
    from app.config import Settings
    assert Settings.model_fields["whatsapp_api_version"].default == "v25.0"


# -- recipient number format -------------------------------------------------

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


def test_send_uses_the_configured_version(db_session, wa_lead, approved_template,
                                          wa_channel, fake_graph_http):
    wa_channel.send(wa_outbound(wa_lead))
    (_method, endpoint, _payload), = fake_graph_http.requests
    assert endpoint.startswith(f"/{settings.whatsapp_api_version}/")


# -- free-form text length ---------------------------------------------------

def test_text_over_4096_characters_fails_before_the_api(db_session, wa_lead, wa_channel,
                                                        fake_graph_http):
    _open_window(db_session, wa_lead)
    result = wa_channel.send(wa_outbound(wa_lead, kind="text", body="x" * 4097))
    assert not result.ok and result.permanent_failure
    assert "4096" in result.error
    assert fake_graph_http.requests == []


def test_text_of_4096_characters_is_sent(db_session, wa_lead, wa_channel, fake_graph_http):
    _open_window(db_session, wa_lead)
    assert wa_channel.send(wa_outbound(wa_lead, kind="text", body="x" * 4096)).ok


# -- template submission -----------------------------------------------------

@pytest.fixture()
def waba(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_business_account_id", "waba-1")


def test_submit_sends_an_example_for_each_placeholder(wa_channel, fake_graph_http, waba):
    fake_graph_http.queue.append((200, {"id": "tpl-1", "status": "PENDING"}))
    wa_channel.submit_template(name="intro_v1", language="en_US", category="marketing",
                               body="Hi {{1}}, about {{2}}. Reply STOP to opt out.",
                               examples=["Sara", "slow onboarding"])
    (_method, _endpoint, payload), = fake_graph_http.requests
    assert payload["components"] == [{
        "type": "BODY", "text": "Hi {{1}}, about {{2}}. Reply STOP to opt out.",
        "example": {"body_text": [["Sara", "slow onboarding"]]}}]


def test_submit_without_placeholders_sends_no_example(wa_channel, fake_graph_http, waba):
    fake_graph_http.queue.append((200, {"id": "tpl-2"}))
    wa_channel.submit_template(name="hello_v1", language="en_US", category="utility",
                               body="Your booking is confirmed.")
    (_method, _endpoint, payload), = fake_graph_http.requests
    assert payload["components"] == [{"type": "BODY", "text": "Your booking is confirmed."}]


def test_submit_refuses_a_missing_example(wa_channel, fake_graph_http, waba):
    with pytest.raises(ValueError, match="example"):
        wa_channel.submit_template(name="intro_v1", language="en_US", category="marketing",
                                   body="Hi {{1}}, about {{2}}.", examples=["Sara"])
    assert fake_graph_http.requests == []


def test_service_submit_uses_the_variable_descriptions_as_examples(
        db_session, wa_channel, fake_graph_http, waba):
    from app.services import whatsapp_templates as tmpl_svc
    t = tmpl_svc.create_draft(
        db_session, name="intro_v2", language="en_US",
        category=m.WhatsAppTemplateCategory.MARKETING,
        body="Hi {{1}}, Sam from Vectorae about {{2}}. Reply STOP to opt out.",
        variable_descriptions={"1": "first name", "2": "pain point"})
    fake_graph_http.queue.append((200, {"id": "tpl-3"}))
    tmpl_svc.submit_template(db_session, t, wa_channel)
    (_method, _endpoint, payload), = fake_graph_http.requests
    assert payload["components"][0]["example"] == {"body_text": [["first name", "pain point"]]}


# -- template status lookup ---------------------------------------------------

class _PagedGraph:
    """Records params too, which the shared fake does not."""

    class _Resp:
        def __init__(self, data):
            self.status_code, self.headers, self._data = 200, {}, data
            self.text = str(data)

        def json(self):
            return self._data

    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def request(self, method, endpoint, params=None, json=None, headers=None):
        self.calls.append((method, endpoint, dict(params or {})))
        return self._Resp(self.pages.pop(0))


def _channel(db_session, fake_redis, http):
    return wa.WhatsAppChannel(session=db_session, redis=fake_redis, http=http)


def test_status_by_template_id_reads_that_template(db_session, fake_redis, waba):
    http = _PagedGraph([{"id": "tpl-9", "name": "intro_v1", "language": "en_US",
                         "status": "APPROVED"}])
    row = _channel(db_session, fake_redis, http).fetch_template_status(
        name="intro_v1", language="en_US", meta_template_id="tpl-9")
    assert row["status"] == "APPROVED"
    (method, endpoint, params), = http.calls
    assert (method, endpoint) == ("GET", f"/{settings.whatsapp_api_version}/tpl-9")
    assert params["fields"] == "id,name,language,status,rejected_reason"


def test_status_by_name_follows_the_pages(db_session, fake_redis, waba):
    http = _PagedGraph([
        {"data": [{"id": "a", "name": "other", "language": "en_US", "status": "APPROVED"}],
         "paging": {"cursors": {"after": "CUR1"}, "next": "https://graph/next"}},
        {"data": [{"id": "b", "name": "intro_v1", "language": "en_US", "status": "REJECTED"}],
         "paging": {"cursors": {"after": "CUR2"}}},
    ])
    row = _channel(db_session, fake_redis, http).fetch_template_status(
        name="intro_v1", language="en_US")
    assert row["id"] == "b"
    assert [c[2].get("after") for c in http.calls] == [None, "CUR1"]


def test_status_by_name_stops_when_there_is_no_next_page(db_session, fake_redis, waba):
    http = _PagedGraph([{"data": [], "paging": {"cursors": {"after": "X"}}}])
    assert _channel(db_session, fake_redis, http).fetch_template_status(
        name="intro_v1", language="en_US") is None
    assert len(http.calls) == 1


# -- status values -------------------------------------------------------------

def test_played_is_a_known_delivery_status():
    assert wa.META_STATUS_MAP["played"] is m.MessageStatus.SENT


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
