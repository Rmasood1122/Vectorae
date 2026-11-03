"""Local business mode, Part B -- consent-based customer win-back.

LEGAL / SAFETY GUARDS under test (docs/features/local-business-mode.md):
  * import: 409 while the flag is off; 403 for healthcare (HIPAA); 422
    without the consent columns, with a health-data column, or without the
    attestation; per-row refusal (not stored) without consent, on SMS/phone,
    or for anyone who opted out before;
  * consent recorded in the ledger (email) and the WhatsApp opt-in table;
  * customer sequences: email / approved WhatsApp template only, allowed
    placeholders, deterministic variables, no follow-ups;
  * enrollment never mixes prospects and customers;
  * the send guard, re-import after an opt-out and the phone/AI-call
    refusals live in tests/test_customer_winback_guards.py (sealable);
  * consumers are never profiled (no model-written copy, no champion checks).

Offline: SQLite, FakeClaude, FakeChannel, no network.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.db import models as m
from app.services import consent
from app.services import local_business as lb
from app.services import sequence_engine as engine
from app.services import system_settings
from app.workers.outreach_tasks import send_message_impl
from tests.conftest import NOW, auth_headers, entitle_owner

HEADER = "name,email,phone,consent_channels,consent_date,consent_source,consent_text"


def _csv(*rows: str, header: str = HEADER) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode()


def _import(client, strategy, data: bytes, attestation: str = lb.ATTESTATION, headers=None):
    return client.post(f"/strategies/{strategy.id}/customers/import",
                       files={"file": ("customers.csv", data, "text/csv")},
                       data={"attestation": attestation}, headers=headers or {})


@pytest.fixture()
def local_strategy(db_session, verified_strategy):
    product = db_session.get(m.Product, verified_strategy.product_id)
    product.business_type = "restaurant"
    product.name = "Bella Cafe"
    db_session.commit()
    return verified_strategy


@pytest.fixture()
def winback_on(db_session):
    system_settings.set(db_session, lb.WINBACK_FLAG, True)


def _customers(db, strategy):
    return db.execute(select(m.Lead).where(m.Lead.strategy_id == strategy.id,
                                           m.Lead.source == lb.CUSTOMER_SOURCE)
                      ).scalars().all()


def _nothing_stored(db, strategy):
    assert _customers(db, strategy) == []
    assert db.execute(select(m.ConsentEvent)).scalars().all() == []
    assert db.execute(select(m.CustomerImport)).scalars().all() == []


SARA = "Sara Khan,sara@example.com,,email,2026-03-01,loyalty sign-up form at the till,"


# --------------------------------------------------------------------------
# Import -- whole-file refusals
# --------------------------------------------------------------------------


class TestImportRefusals:
    def test_flag_off_by_default_is_409(self, client, db_session, local_strategy):
        r = _import(client, local_strategy, _csv(SARA))
        assert r.status_code == 409
        assert r.json()["detail"] == lb.WINBACK_DISABLED
        _nothing_stored(db_session, local_strategy)

    def test_healthcare_is_refused_entirely(self, client, db_session, local_strategy,
                                            winback_on):
        db_session.get(m.Product, local_strategy.product_id).business_type = "healthcare"
        db_session.commit()
        r = _import(client, local_strategy, _csv(SARA))
        assert r.status_code == 403
        assert "HIPAA" in r.json()["detail"] and "patient" in r.json()["detail"]
        _nothing_stored(db_session, local_strategy)

    def test_not_a_local_business_is_409(self, client, db_session, verified_strategy,
                                         winback_on):
        r = _import(client, verified_strategy, _csv(SARA))
        assert r.status_code == 409
        _nothing_stored(db_session, verified_strategy)

    def test_missing_consent_columns_is_422(self, client, db_session, local_strategy,
                                            winback_on):
        r = _import(client, local_strategy, _csv("Sara,sara@example.com",
                                                 header="name,email"))
        assert r.status_code == 422
        assert "consent_channels" in r.json()["detail"]
        _nothing_stored(db_session, local_strategy)

    @pytest.mark.parametrize("column", ["patient_id", "Date of Birth", "diagnosis",
                                        "insurance_provider", "last_appointment"])
    def test_health_data_column_is_refused_for_any_business(self, client, db_session,
                                                            local_strategy, winback_on,
                                                            column):
        r = _import(client, local_strategy,
                    _csv(SARA + ",x", header=f"{HEADER},{column}"))
        assert r.status_code == 422
        assert "health information" in r.json()["detail"]
        _nothing_stored(db_session, local_strategy)

    def test_attestation_must_be_exact(self, client, db_session, local_strategy, winback_on):
        r = _import(client, local_strategy, _csv(SARA), attestation="yes")
        assert r.status_code == 422
        _nothing_stored(db_session, local_strategy)

    def test_another_accounts_strategy_is_404(self, client, db_session, local_strategy,
                                              winback_on):
        other = m.User(email="intruder@x.dev", email_verified=True)
        db_session.add(other)
        db_session.commit()
        r = _import(client, local_strategy, _csv(SARA), headers=auth_headers(other))
        assert r.status_code == 404
        _nothing_stored(db_session, local_strategy)


# --------------------------------------------------------------------------
# Import -- rows, consent evidence
# --------------------------------------------------------------------------


class TestImportRows:
    def test_email_consent_is_recorded_in_the_ledger(self, client, db_session,
                                                     local_strategy, winback_on, test_user):
        r = _import(client, local_strategy, _csv(
            SARA + "I agree to receive offers by email"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"] == "1 imported, 0 refused"
        lead = db_session.get(m.Lead, uuid.UUID(body["accepted"][0]["lead_id"]))
        assert lead.source == lb.CUSTOMER_SOURCE and lead.email == "sara@example.com"
        assert lead.status is m.LeadStatus.SOURCED
        event = db_session.execute(select(m.ConsentEvent)
                                   .where(m.ConsentEvent.lead_id == lead.id)).scalar_one()
        assert (event.kind, event.channel, event.source) == ("granted", "email",
                                                            "customer_import")
        assert event.user_id == test_user.id
        assert event.detail == ("opted in to marketing on 2026-03-01 via loyalty sign-up "
                                "form at the till")
        assert event.meta_json["import_id"] == body["import_id"]
        assert event.meta_json["consent_text"] == "I agree to receive offers by email"
        record = db_session.get(m.CustomerImport, uuid.UUID(body["import_id"]))
        assert record.user_id == test_user.id and record.accepted_count == 1
        assert record.attestation == lb.ATTESTATION
        assert lb.has_email_consent(db_session, lead)

    def test_whatsapp_consent_goes_through_the_optin_service(self, client, db_session,
                                                             local_strategy, winback_on):
        r = _import(client, local_strategy, _csv(
            "Ali Raza,,+1 (212) 555-0100,whatsapp,2026-02-10,QR code opt-in at the counter,"))
        assert r.status_code == 200, r.text
        lead = db_session.get(m.Lead, uuid.UUID(r.json()["accepted"][0]["lead_id"]))
        assert lead.phone == "+12125550100" and lead.whatsapp_opted_in is True
        assert lead.email is None
        optin = db_session.execute(select(m.WhatsAppOptIn)
                                   .where(m.WhatsAppOptIn.lead_id == lead.id)).scalar_one()
        assert optin.source is m.OptInSource.MANUAL_IMPORT
        assert "opted in to marketing on 2026-02-10 via QR code" in optin.evidence
        grants = db_session.execute(select(m.ConsentEvent).where(
            m.ConsentEvent.lead_id == lead.id)).scalars().all()
        assert [(g.kind, g.channel) for g in grants] == [("granted", "whatsapp")]

    @pytest.mark.parametrize("row, reason", [
        ("A,a@example.com,,,2026-03-01,form,", "no consent"),
        ("A,a@example.com,,email,,form,", "consent_date"),
        ("A,a@example.com,,email,2999-01-01,form,", "future"),
        ("A,a@example.com,,email,2026-03-01,x,", "consent_source"),
        ("A,a@example.com,+12125550100,sms,2026-03-01,form,", "TCPA"),
        ("A,a@example.com,+12125550100,phone,2026-03-01,form,", "calls are not offered"),
        ("A,a@example.com,,fax,2026-03-01,form,", "unknown consent channel"),
        ("A,not-an-email,,email,2026-03-01,form,", "valid email"),
        ("A,,0300 1234,whatsapp,2026-03-01,form,", "international phone"),
    ])
    def test_rows_without_valid_consent_are_refused_and_not_stored(
            self, client, db_session, local_strategy, winback_on, row, reason):
        r = _import(client, local_strategy, _csv(row))
        assert r.status_code == 200, r.text
        assert r.json()["accepted"] == []
        assert reason in r.json()["rejected"][0]["reason"]
        assert _customers(db_session, local_strategy) == []
        assert db_session.execute(select(m.ConsentEvent)).scalars().all() == []

    def test_only_consented_identifiers_are_stored(self, client, db_session, local_strategy,
                                                   winback_on):
        r = _import(client, local_strategy, _csv(
            "Sara,sara@example.com,+12125550100,email,2026-03-01,web form,"))
        lead = db_session.get(m.Lead, uuid.UUID(r.json()["accepted"][0]["lead_id"]))
        assert lead.phone is None, "a number with no consent is not kept (minimisation)"

    def test_an_existing_prospect_is_never_turned_into_a_customer(
            self, client, db_session, local_strategy, verified_leads, winback_on):
        prospect = verified_leads[0]
        r = _import(client, local_strategy, _csv(
            f"X,{prospect.email},,email,2026-03-01,web form,"))
        assert r.json()["accepted"] == []
        assert "business prospect" in r.json()["rejected"][0]["reason"]
        assert db_session.execute(select(m.ConsentEvent)).scalars().all() == []

    def test_reimport_appends_consent_without_duplicating(self, client, db_session,
                                                          local_strategy, winback_on):
        _import(client, local_strategy, _csv(SARA))
        r = _import(client, local_strategy, _csv(
            "Sara Khan,sara@example.com,,email,2026-06-01,newsletter box,"))
        assert r.status_code == 200
        assert len(_customers(db_session, local_strategy)) == 1
        grants = db_session.execute(select(m.ConsentEvent)).scalars().all()
        assert len(grants) == 2

    def test_refused_rows_keep_no_personal_data(self, client, db_session, local_strategy,
                                                winback_on):
        r = _import(client, local_strategy, _csv(
            "Secret Person,secret@example.com,,,2026-03-01,form,"))
        record = db_session.get(m.CustomerImport, uuid.UUID(r.json()["import_id"]))
        assert record.rejected_count == 1
        assert "secret" not in str(record.rejected_json).lower()
        assert record.rejected_json[0]["line"] == 2

    def test_plan_room_is_respected(self, client, db_session, local_strategy, winback_on,
                                    monkeypatch):
        from app.workers import lead_tasks

        monkeypatch.setattr(lead_tasks, "strategy_lead_room", lambda db, sid: 1)
        r = _import(client, local_strategy, _csv(
            SARA, "Ali,ali@example.com,,email,2026-03-01,web form,"))
        assert len(r.json()["accepted"]) == 1
        assert "lead limit" in r.json()["rejected"][0]["reason"]

    def test_another_accounts_lead_with_the_same_email_is_untouched(
            self, client, db_session, local_strategy, winback_on):
        other = m.User(email="other@x.dev", email_verified=True)
        db_session.add(other)
        db_session.flush()
        product = m.Product(user_id=other.id, name="Other", description="d",
                            type=m.ProductType.PRODUCT, business_type="retail")
        db_session.add(product)
        db_session.flush()
        strategy = m.Strategy(product_id=product.id, flow_type=m.FlowType.NO_CLIENTS)
        db_session.add(strategy)
        db_session.flush()
        theirs = m.Lead(strategy_id=strategy.id, source=lb.CUSTOMER_SOURCE,
                        email="sara@example.com", status=m.LeadStatus.SOURCED)
        db_session.add(theirs)
        db_session.commit()
        r = _import(client, local_strategy, _csv(SARA))
        mine = uuid.UUID(r.json()["accepted"][0]["lead_id"])
        assert mine != theirs.id
        assert db_session.execute(select(m.ConsentEvent).where(
            m.ConsentEvent.lead_id == theirs.id)).scalars().all() == []


# --------------------------------------------------------------------------
# Customer sequences
# --------------------------------------------------------------------------

STEP = {"step_no": 1, "template": "Subject: We miss you, {first_name}\n"
                                  "Hi {first_name}, {business_name} has a new autumn "
                                  "menu this week. Come and say hello.",
        "delay_days": 0}


def _create_seq(client, strategy, **overrides):
    body = {"name": "Win-back", "audience": "customers", "steps": [STEP]}
    body.update(overrides)
    return client.post(f"/strategies/{strategy.id}/sequences", json=body)


class TestCustomerSequences:
    def test_flag_off_is_409(self, client, local_strategy):
        assert _create_seq(client, local_strategy).status_code == 409

    def test_healthcare_is_403(self, client, db_session, local_strategy, winback_on):
        db_session.get(m.Product, local_strategy.product_id).business_type = "healthcare"
        db_session.commit()
        assert _create_seq(client, local_strategy).status_code == 403

    def test_valid_sequence_has_no_followups(self, client, db_session, local_strategy,
                                             winback_on):
        r = _create_seq(client, local_strategy)
        assert r.status_code == 201, r.text
        assert r.json()["audience"] == "customers"
        assert r.json()["steps"][0]["followup_enabled"] is False

    def test_prospect_sequences_default_unchanged(self, client, local_strategy):
        r = client.post(f"/strategies/{local_strategy.id}/sequences",
                        json={"name": "B2B", "steps": [{"step_no": 1, "template": "brief"}]})
        assert r.status_code == 201
        assert r.json()["audience"] == "prospects"
        assert r.json()["steps"][0]["followup_enabled"] is True

    def test_linkedin_step_is_refused(self, client, local_strategy, winback_on):
        r = _create_seq(client, local_strategy, channel="linkedin")
        assert r.status_code == 422 and "email or WhatsApp only" in r.json()["detail"]

    def test_unknown_placeholder_is_refused(self, client, local_strategy, winback_on):
        r = _create_seq(client, local_strategy,
                        steps=[{**STEP, "template": "Hi {first_name}, your {last_order}!"}])
        assert r.status_code == 422 and "{last_order}" in r.json()["detail"]

    def test_model_written_whatsapp_variable_is_refused(self, client, db_session,
                                                        local_strategy, winback_on,
                                                        approved_template):
        entitle_owner(db_session, local_strategy, m.PlanTier.STARTER)
        step = {"step_no": 1, "template": "wa", "channel": "whatsapp",
                "whatsapp_template_id": str(approved_template.id),
                "variable_mapping": {"1": "lead.first_name", "2": "their favourite dish"}}
        r = _create_seq(client, local_strategy, steps=[step])
        assert r.status_code == 422 and "nothing model-written" in r.json()["detail"]


# --------------------------------------------------------------------------
# Enrollment + send path
# --------------------------------------------------------------------------


def _customer(db, strategy, email="sara@example.com", name="Sara Khan", consented=True,
              user=None):
    lead = m.Lead(strategy_id=strategy.id, source=lb.CUSTOMER_SOURCE, full_name=name,
                  email=email, status=m.LeadStatus.SOURCED)
    db.add(lead)
    db.commit()
    if consented:
        consent.record(db, lead=lead, kind=consent.GRANTED, channel=consent.EMAIL,
                       source=lb.CUSTOMER_SOURCE, identifier=email,
                       detail="opted in to marketing on 2026-03-01 via web form")
    return lead


def _customer_sequence(db, strategy):
    seq = m.Sequence(strategy_id=strategy.id, channel=m.ChannelType.EMAIL, name="Win-back",
                     status=m.SequenceStatus.ACTIVE, audience=lb.AUDIENCE_CUSTOMERS)
    db.add(seq)
    db.flush()
    db.add(m.SequenceStep(sequence_id=seq.id, step_no=1, template=STEP["template"],
                          delay_days=0, followup_enabled=False))
    db.commit()
    return seq


def _pending(db, lead):
    return db.execute(select(m.Message).where(
        m.Message.lead_id == lead.id,
        m.Message.status == m.MessageStatus.SCHEDULED)).scalars().first()


class TestEnrollment:
    def test_customer_sequence_enrolls_only_customers(self, db_session, local_strategy,
                                                      verified_leads, winback_on):
        customer = _customer(db_session, local_strategy)
        seq = _customer_sequence(db_session, local_strategy)
        assert engine.enroll_leads(db_session, seq, now=NOW) == 1
        enrolled = db_session.execute(select(m.SequenceEnrollment.lead_id).where(
            m.SequenceEnrollment.sequence_id == seq.id)).scalars().all()
        assert enrolled == [customer.id]

    def test_prospect_sequence_never_enrolls_customers(self, db_session, local_strategy,
                                                       verified_leads, email_sequence):
        customer = _customer(db_session, local_strategy)
        customer.status = m.LeadStatus.VERIFIED      # even if someone marks it verified
        db_session.commit()
        assert engine.enroll_leads(db_session, email_sequence, now=NOW) == 3
        assert db_session.execute(select(m.SequenceEnrollment).where(
            m.SequenceEnrollment.lead_id == customer.id)).scalars().all() == []

    def test_customers_are_not_fpta_scored(self, db_session, local_strategy, fake_claude,
                                           winback_on):
        _customer(db_session, local_strategy)
        engine.enroll_leads(db_session, _customer_sequence(db_session, local_strategy),
                            now=NOW)
        assert fake_claude.fpta_prompts == []


class TestWhatsAppCustomer:
    def test_template_variables_are_deterministic(self, db_session, local_strategy,
                                                  approved_template, winback_on, fake_claude):
        from app.workers.outreach_tasks import _render_whatsapp_outbound

        entitle_owner(db_session, local_strategy, m.PlanTier.STARTER)
        customer = _customer(db_session, local_strategy, consented=False)
        customer.phone = "+12125550100"
        seq = m.Sequence(strategy_id=local_strategy.id, channel=m.ChannelType.WHATSAPP,
                         name="WA", status=m.SequenceStatus.ACTIVE,
                         audience=lb.AUDIENCE_CUSTOMERS)
        db_session.add(seq)
        db_session.flush()
        db_session.add(m.SequenceStep(
            sequence_id=seq.id, step_no=1, template="wa",
            whatsapp_kind=m.WhatsAppStepKind.TEMPLATE,
            whatsapp_template_id=approved_template.id,
            variable_mapping_json={"1": "lead.first_name", "2": "lead.full_name"}))
        db_session.flush()
        msg = m.Message(lead_id=customer.id, sequence_id=seq.id, step_no=1,
                        channel=m.ChannelType.WHATSAPP, template="wa",
                        whatsapp_kind=m.WhatsAppStepKind.TEMPLATE,
                        whatsapp_template_id=approved_template.id,
                        status=m.MessageStatus.SENDING, scheduled_at=NOW)
        db_session.add(msg)
        db_session.commit()
        out = _render_whatsapp_outbound(db_session, local_strategy, customer, msg, NOW)
        params = out.metadata["components"][0]["parameters"]
        assert [p["text"] for p in params] == ["Sara", "Sara Khan"]
        # The only model call allowed is the existing claim check on the
        # values; nothing model-WRITTEN reaches a consumer.
        assert fake_claude.completions == len(fake_claude.claim_extraction_prompts)

    def test_a_brief_mapping_is_refused_at_render(self, db_session):
        from app.core.exceptions import ComplianceError

        lead = m.Lead(full_name="Sara Khan", source=lb.CUSTOMER_SOURCE)
        with pytest.raises(ComplianceError):
            lb.customer_whatsapp_values(lead, {"1": "their favourite dish"}, {"1": "x"})


class TestConsumersAreNotProfiled:
    def test_champion_tracker_never_holds_or_checks_a_customer(self, db_session,
                                                               local_strategy, test_user):
        from app.services import champion_tracker

        customer = _customer(db_session, local_strategy)
        customer.status = m.LeadStatus.CLOSED_WON
        db_session.commit()
        assert champion_tracker.staleness_hold(db_session, customer, NOW) is None
        assert customer.id not in champion_tracker.champion_lead_ids(db_session, test_user.id)

    def test_rescore_skips_customers(self, db_session, local_strategy, verified_leads,
                                     monkeypatch):
        from app.services import lead_scoring
        from app.workers.intelligence_tasks import rescore_strategy_impl

        customer = _customer(db_session, local_strategy)
        customer.status = m.LeadStatus.REPLIED
        db_session.commit()
        seen: list = []
        monkeypatch.setattr(lead_scoring, "score_leads",
                            lambda db, strategy, leads, **kw: seen.extend(leads) or len(leads))
        rescore_strategy_impl(db_session, local_strategy.id)
        assert customer not in seen and len(seen) == 3


class TestHelpers:
    def test_split_subject(self):
        assert lb.split_subject("Subject: Hello\nBody") == ("Hello", "Body")
        assert lb.split_subject("Just a body") == (None, "Just a body")

    def test_phi_columns(self):
        assert lb.phi_columns(["name", "email", "Health Notes"]) == ["Health Notes"]
        assert lb.phi_columns(["name", "consent_source", "country"]) == []

    def test_consent_date_today_is_accepted(self, db_session, local_strategy, winback_on,
                                            test_user):
        product = db_session.get(m.Product, local_strategy.product_id)
        out = lb.import_customers(
            db_session, test_user, local_strategy, product,
            _csv("S,s@example.com,,email,2026-10-11,web form,"), lb.ATTESTATION,
            today=date(2026, 10, 11))
        assert len(out["accepted"]) == 1


# --------------------------------------------------------------------------
# Review fixes (2026-10-12): M1 -- customers are never profiled
# --------------------------------------------------------------------------

#: Every per-lead POST/PUT that sends a lead's data to a model or to an
#: enrichment / verification / social provider. Each must refuse an imported
#: customer (409) before spending anything.
REFUSED_FOR_CUSTOMERS = [
    ("post", "/leads/{id}/fpta/rescore", None),
    ("post", "/leads/{id}/rescore", None),
    ("post", "/leads/{id}/personalization/refresh", None),
    ("put", "/leads/{id}/linkedin-url", {"linkedin_url": "https://www.linkedin.com/in/sara-k"}),
    ("post", "/leads/{id}/loom/script", None),
    ("post", "/leads/{id}/signals/refresh", None),
    ("post", "/leads/{id}/meeting-prep", {}),
    ("post", "/leads/{id}/meeting-outcome", {"outcome": "interested",
                                             "generate_followup": True}),
    ("post", "/leads/{id}/email-verdict/refresh", None),
    ("post", "/leads/{id}/role-check/refresh", None),
    # channel entry points (H2; see tests/test_customer_winback_guards.py)
    ("put", "/leads/{id}/phone-consent", {"source": "said yes"}),
    ("post", "/leads/{id}/call", {"brief": "win-back"}),
    ("post", "/leads/{id}/whatsapp-optin", {"source": "api", "evidence": "said yes"}),
]

#: Per-lead POST/PUT routes that do NOT send the person's data anywhere --
#: each with the reason. A new per-lead write route must be added to one of
#: the two lists, or test_every_lead_write_route_is_classified fails.
SAFE_FOR_CUSTOMERS = {
    "/leads/{lead_id}/email-override": "a manager's manual decision, no provider",
    "/leads/{lead_id}/role-check/confirm": "a person confirms a role; no lookup",
    "/leads/{lead_id}/consent/withdraw": "stops contact -- must always work",
    "/leads/{lead_id}/conversion/reactivate": "local state change",
    "/leads/{lead_id}/notes": "CRM note typed by the owner",
    "/leads/{lead_id}/tags/{tag_id}": "CRM tag",
    "/leads/{lead_id}/loom": "records a URL the owner pasted",
    "/leads/{lead_id}/loom/skip": "local state change",
    "/leads/{lead_id}/meeting-outcome": "logging is local; generate_followup is refused",
    "/leads/{lead_id}": ("status / owner edits -- must work, e.g. to remove a customer; a "
                         "manual move is mirrored only to the owner's OWN connected CRM"),
}


def _contacted_customer(db, strategy):
    lead = _customer(db, strategy)
    lead.status = m.LeadStatus.CONTACTED
    lead.linkedin_url = "https://www.linkedin.com/in/sara-k"
    db.commit()
    return lead


class TestCustomersAreNeverProfiled:
    @pytest.mark.parametrize("method, path, body", REFUSED_FOR_CUSTOMERS)
    def test_entry_point_refuses_a_customer(self, client, db_session, local_strategy,
                                            winback_on, fake_claude, method, path, body):
        customer = _contacted_customer(db_session, local_strategy)
        for flag in ("signal_radar_enabled", "champion_tracker_enabled",
                     "bounce_shield_enabled", "phone_calling_enabled"):
            if flag in system_settings.DEFAULTS:
                system_settings.set(db_session, flag, True)
        r = getattr(client, method)(path.format(id=customer.id),
                                    **({"json": body} if body is not None else {}))
        assert r.status_code == 409, (path, r.status_code, r.text)
        assert "customer" in str(r.json()["detail"]).lower()
        assert fake_claude.completions == 0, path

    def test_every_lead_write_route_is_classified(self):
        from app.main import app

        refused = {p.replace("{id}", "{lead_id}") for _, p, _ in REFUSED_FOR_CUSTOMERS}
        routes = {r.path for r in app.routes
                  if getattr(r, "path", "").startswith("/leads/{lead_id}")
                  and ({"POST", "PUT", "PATCH"} & set(getattr(r, "methods", ())))}
        unclassified = routes - refused - set(SAFE_FOR_CUSTOMERS)
        assert not unclassified, f"classify these for imported customers: {unclassified}"

    def test_strategy_fpta_backfill_skips_customers(self, client, db_session, local_strategy,
                                                    fake_claude):
        _contacted_customer(db_session, local_strategy)
        r = client.post(f"/strategies/{local_strategy.id}/leads/fpta/rescore")
        assert r.status_code == 200
        assert not any("Sara" in p for p in fake_claude.fpta_prompts)

    def test_scoring_services_skip_customers(self, db_session, local_strategy, fake_claude):
        from app.services import fpta_scoring, lead_scoring

        customer = _contacted_customer(db_session, local_strategy)
        assert fpta_scoring.score_leads(db_session, local_strategy, [customer]) == 0
        assert lead_scoring.score_leads(db_session, local_strategy, [customer]) == 0
        assert customer.fpta_scored_at is None and customer.ai_scored_at is None
        assert fake_claude.completions == 0

    def test_personalization_research_never_fetches_for_a_customer(
            self, db_session, local_strategy, monkeypatch):
        from app.integrations import newsapi
        from app.services import personalization_context

        def boom(*a, **k):
            raise AssertionError("a consumer's data was sent to a provider")

        # (No LinkedIn post reader exists since 2026-11-02.)
        monkeypatch.setattr(newsapi, "fetch_company_news", boom, raising=False)
        customer = _contacted_customer(db_session, local_strategy)
        out = personalization_context.ensure_fresh(db_session, customer, force=True)
        assert out == {"posts": "skipped_customer", "news": "skipped_customer"}

    def test_meeting_prep_brief_is_never_generated_for_a_customer(
            self, db_session, local_strategy, fake_claude, test_user):
        from app.services import meeting_prep

        customer = _contacted_customer(db_session, local_strategy)
        brief = meeting_prep.upsert_brief(db_session, customer, user_id=test_user.id,
                                          source=meeting_prep.SOURCE_MANUAL,
                                          external_ref="manual:x")
        assert meeting_prep.generate_brief(db_session, brief.id, notify=False) == "refused"
        assert fake_claude.completions == 0

    def test_displacement_watchlist_skips_customers(self, db_session, local_strategy):
        from app.services import displacement_monitor

        customer = _contacted_customer(db_session, local_strategy)
        assert customer not in displacement_monitor.watchlist(db_session, local_strategy.id)

    def test_reengagement_never_picks_a_customer(self, db_session, local_strategy, winback_on):
        from app.services import reengagement

        customer = _contacted_customer(db_session, local_strategy)
        seq = m.Sequence(strategy_id=local_strategy.id, channel=m.ChannelType.EMAIL,
                         name="WB", status=m.SequenceStatus.ACTIVE,
                         audience=lb.AUDIENCE_CUSTOMERS)
        db_session.add(seq)
        db_session.flush()
        db_session.add(m.SequenceEnrollment(sequence_id=seq.id, lead_id=customer.id,
                                            status=m.EnrollmentStatus.COMPLETED))
        db_session.commit()
        assert reengagement.candidates(db_session, local_strategy, NOW, 10,
                                       preview=True) == []

    def test_not_now_plan_is_never_made_for_a_customer(self, db_session, local_strategy,
                                                       fake_claude, monkeypatch):
        from app.services import reengagement_memory

        monkeypatch.setattr(reengagement_memory, "enabled", lambda db: True)
        customer = _contacted_customer(db_session, local_strategy)
        reply = m.InboundReply(lead_id=customer.id, channel="email",
                               from_address="sara@example.com", body="not now, maybe in spring",
                               intent_label="not_now")
        db_session.add(reply)
        db_session.commit()
        assert reengagement_memory.plan_for_reply(db_session, reply) is None
        assert fake_claude.completions == 0

    def test_champion_reverify_never_looks_up_a_customer(self, db_session, local_strategy):
        from app.services import champion_tracker

        class Source:
            def enrich(self, *a, **k):
                raise AssertionError("a consumer was looked up at a provider")

        customer = _contacted_customer(db_session, local_strategy)
        out = champion_tracker.reverify(db_session, customer, Source(), trigger="manual")
        assert out["outcome"] == "customer"

    def test_followup_sweep_skips_customer_sequences(self, client, db_session, local_strategy,
                                                     winback_on, gmail_account, fake_channel):
        from datetime import timedelta

        from app.workers import outreach_tasks

        customer = _customer(db_session, local_strategy)
        r = _create_seq(client, local_strategy,
                        steps=[STEP, {**STEP, "step_no": 2, "delay_days": 30}])
        seq = db_session.get(m.Sequence, uuid.UUID(r.json()["id"]))
        seq.status = m.SequenceStatus.ACTIVE
        db_session.commit()
        engine.enroll_leads(db_session, seq, now=NOW)
        msg = db_session.execute(select(m.Message).where(
            m.Message.lead_id == customer.id)).scalars().first()
        send_message_impl(db_session, msg.id, channel=fake_channel, now=NOW)
        # Step 2 is a month away; take it out so the sweep would otherwise act.
        for pending in db_session.execute(select(m.Message).where(
                m.Message.lead_id == customer.id,
                m.Message.status == m.MessageStatus.SCHEDULED)).scalars():
            db_session.delete(pending)
        db_session.commit()
        step = db_session.execute(select(m.SequenceStep).where(
            m.SequenceStep.sequence_id == seq.id, m.SequenceStep.step_no == 1)).scalar_one()
        step.followup_enabled = True                   # forced by hand, not via the API
        step.followup_delay_hours = 1
        db_session.commit()
        due = outreach_tasks.check_followup_due_impl(db_session, now=NOW + timedelta(hours=3),
                                                     enqueue=lambda e, s: None)
        assert due == []


# --------------------------------------------------------------------------
# M2 -- healthcare residual risk
# --------------------------------------------------------------------------


class TestHealthcareLock:
    def test_attestation_excludes_hipaa_entities(self, client, db_session, local_strategy,
                                                 winback_on):
        assert "not a HIPAA covered entity or business associate" in lb.ATTESTATION
        assert "contains no patients or patient information" in lb.ATTESTATION
        assert lb.ATTESTATION_VERSION == "2"
        r = _import(client, local_strategy, _csv(SARA))
        record = db_session.get(m.CustomerImport, uuid.UUID(r.json()["import_id"]))
        assert record.attestation_version == "2"

    def test_business_type_changes_are_audited(self, client, db_session, local_strategy,
                                               test_user):
        product = db_session.get(m.Product, local_strategy.product_id)
        r = client.put(f"/products/{product.id}/local-business",
                       json={"business_type": "retail"})
        assert r.status_code == 200
        rows = db_session.execute(select(m.SecurityAuditEvent).where(
            m.SecurityAuditEvent.action == "product.business_type_changed")).scalars().all()
        assert len(rows) == 1
        assert rows[0].user_id == test_user.id
        assert rows[0].details_json == {"from": "restaurant", "to": "retail"}
        assert rows[0].target_id == str(product.id)

    def test_once_healthcare_never_winback(self, client, db_session, local_strategy,
                                           winback_on):
        """The reviewer's P7: healthcare -> other -> import a patient list."""
        product = db_session.get(m.Product, local_strategy.product_id)
        assert client.put(f"/products/{product.id}/local-business",
                          json={"business_type": "healthcare"}).status_code == 200
        assert client.put(f"/products/{product.id}/local-business",
                          json={"business_type": "other"}).status_code == 200
        r = _import(client, local_strategy, _csv(SARA))
        assert r.status_code == 403 and "healthcare" in r.json()["detail"].lower()
        assert _customers(db_session, local_strategy) == []
        assert _create_seq(client, local_strategy).status_code == 403

    def test_leaving_healthcare_set_outside_the_api_still_locks(self, client, db_session,
                                                                local_strategy, winback_on):
        """The reviewer's P7 exactly: healthcare written straight to the row."""
        product = db_session.get(m.Product, local_strategy.product_id)
        product.business_type = "healthcare"
        db_session.commit()
        assert client.put(f"/products/{product.id}/local-business",
                          json={"business_type": "other"}).status_code == 200
        db_session.refresh(product)
        assert product.healthcare_locked_at is not None
        r = _import(client, local_strategy, _csv(SARA))
        assert r.status_code == 403

    def test_cannot_become_healthcare_while_holding_customers(self, client, db_session,
                                                              local_strategy, winback_on):
        _import(client, local_strategy, _csv(SARA))
        product = db_session.get(m.Product, local_strategy.product_id)
        r = client.put(f"/products/{product.id}/local-business",
                       json={"business_type": "healthcare"})
        assert r.status_code == 409 and "customer" in r.json()["detail"]
        db_session.refresh(product)
        assert product.business_type == "restaurant"

    def test_cannot_leave_healthcare_while_holding_customers(self, client, db_session,
                                                             local_strategy):
        product = db_session.get(m.Product, local_strategy.product_id)
        product.business_type = "healthcare"
        db_session.commit()
        _customer(db_session, local_strategy)          # e.g. data from before a lock
        r = client.put(f"/products/{product.id}/local-business",
                       json={"business_type": "other"})
        assert r.status_code == 409

    @pytest.mark.parametrize("source, text", [
        ("patient portal sign-up", ""),
        ("web form", "I agree to appointment reminders and health offers"),
        ("front desk after my treatment", ""),
    ])
    def test_health_wording_in_consent_refuses_the_file(self, client, db_session,
                                                        local_strategy, winback_on,
                                                        source, text):
        r = _import(client, local_strategy, _csv(
            f"Pat,pat@example.com,,email,2026-03-01,{source},{text}"))
        assert r.status_code == 422 and "health" in r.json()["detail"].lower()
        assert _customers(db_session, local_strategy) == []


# --------------------------------------------------------------------------
# M3 / L3 / L4 -- atomic, bounded, safe import
# --------------------------------------------------------------------------


class TestImportIsAtomicAndBounded:
    def test_a_failure_mid_import_saves_nothing(self, client, db_session, local_strategy,
                                                winback_on, monkeypatch):
        from app.services import whatsapp_optin

        calls = {"n": 0}
        real = whatsapp_optin.record_opt_in

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("database went away")
            return real(*a, **k)

        monkeypatch.setattr(whatsapp_optin, "record_opt_in", flaky)
        r = _import(client, local_strategy, _csv(
            "A,,+12125550101,whatsapp,2026-03-01,web form,",
            "B,,+12125550102,whatsapp,2026-03-01,web form,"))
        assert r.status_code == 500
        assert "nothing was saved" in r.json()["detail"]
        assert _customers(db_session, local_strategy) == []
        assert db_session.execute(select(m.WhatsAppOptIn)).scalars().all() == []
        assert db_session.execute(select(m.ConsentEvent)).scalars().all() == []
        assert db_session.execute(select(m.CustomerImport)).scalars().all() == []

    def test_the_whole_import_commits_once(self, db_session, local_strategy, winback_on,
                                           test_user, monkeypatch):
        commits = {"n": 0}
        real = db_session.commit

        def counting():
            commits["n"] += 1
            return real()

        monkeypatch.setattr(db_session, "commit", counting)
        product = db_session.get(m.Product, local_strategy.product_id)
        lb.import_customers(db_session, test_user, local_strategy, product, _csv(
            SARA, "Ali,,+12125550101,whatsapp,2026-03-01,web form,"), lb.ATTESTATION)
        assert commits["n"] == 1

    @pytest.mark.parametrize("row, reason", [
        ("N" * 201 + ",a@example.com,,email,2026-03-01,web form,", "name"),
        ("A,a@example.com,,email,2026-03-01," + "s" * 201 + ",", "consent_source"),
        ("A,a@example.com,,email,2026-03-01,web form," + "t" * 1001, "consent_text"),
        ("A," + "e" * 320 + "@example.com,,email,2026-03-01,web form,", "email"),
        ("A,=cmd@example.com,,email,2026-03-01,web form,", "email"),
    ])
    def test_over_long_or_unsafe_fields_are_refused(self, client, db_session, local_strategy,
                                                    winback_on, row, reason):
        r = _import(client, local_strategy, _csv(row))
        assert r.status_code == 200, r.text
        assert r.json()["accepted"] == []
        assert reason in r.json()["rejected"][0]["reason"]

    @pytest.mark.parametrize("data, needle", [
        (b"name,email,consent_channels,consent_date,consent_source\nA\x00,a@example.com,"
         b"email,2026-03-01,web form\n", "NUL"),
        (b"name,email,consent_channels,consent_date,consent_source\n" + b"x" * 140_000
         + b",a@example.com,email,2026-03-01,web form\n", "CSV"),
        ("name,email,consent_channels,consent_date,consent_source\nJos\xe9,b@example.com,"
         "email,2026-03-01,web form\n".encode("latin-1"), "UTF-8"),
        (b"name,email,email,consent_channels,consent_date,consent_source\nA,a@x.com,b@x.com,"
         b"email,2026-03-01,web form\n", "duplicate"),
    ])
    def test_malformed_files_are_422(self, client, db_session, local_strategy, winback_on,
                                     data, needle):
        r = _import(client, local_strategy, data)
        assert r.status_code == 422, r.text
        assert needle.lower() in r.json()["detail"].lower()
        assert _customers(db_session, local_strategy) == []

    def test_unexpected_columns_are_refused(self, client, db_session, local_strategy,
                                            winback_on):
        """The reviewer's disguised health columns ("rx", "notes")."""
        r = _import(client, local_strategy, _csv(
            "A,e@example.com,metformin,diabetic,email,2026-03-01,web form",
            header="name,email,rx,notes,consent_channels,consent_date,consent_source"))
        assert r.status_code == 422
        r = _import(client, local_strategy, _csv(
            "A,e@example.com,vip,email,2026-03-01,web form",
            header="name,email,notes,consent_channels,consent_date,consent_source"))
        assert r.status_code == 422 and "unexpected column" in r.json()["detail"]
        assert _customers(db_session, local_strategy) == []

    def test_spreadsheet_formulas_are_neutralised(self, client, db_session, local_strategy,
                                                  winback_on):
        r = _import(client, local_strategy, _csv(
            '"=HYPERLINK(""http://x"")",a@example.com,,email,2026-03-01,'
            '@SUM(1),+cmd'))
        lead = db_session.get(m.Lead, uuid.UUID(r.json()["accepted"][0]["lead_id"]))
        assert lead.full_name.startswith("'=")
        event = db_session.execute(select(m.ConsentEvent)).scalar_one()
        assert "via '@SUM(1)" in event.detail
        assert event.meta_json["consent_text"] == "'+cmd"

    def test_local_business_mode_off_refuses_the_import(self, client, db_session,
                                                        local_strategy, winback_on):
        system_settings.set(db_session, lb.FLAG, False)
        r = _import(client, local_strategy, _csv(SARA))
        assert r.status_code == 409
        assert _customers(db_session, local_strategy) == []


class TestFollowupSettings:
    def test_customer_sequence_refuses_followups(self, client, db_session, local_strategy,
                                                 winback_on):
        r = _create_seq(client, local_strategy)
        seq_id = r.json()["id"]
        r = client.post(f"/sequences/{seq_id}/steps/1/followup-settings",
                        json={"enabled": True, "delay_hours": 1})
        assert r.status_code == 409
        step = db_session.execute(select(m.SequenceStep).where(
            m.SequenceStep.sequence_id == uuid.UUID(seq_id))).scalar_one()
        assert step.followup_enabled is False
