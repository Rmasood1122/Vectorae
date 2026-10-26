"""M4 tests — template lifecycle (Chunk 4, item 6).

draft -> submitted -> approved | rejected; approved templates are
immutable (edits create version+1); sending by name resolves the approved
version even after a newer draft exists; rejected-only names never send.
"""

import pytest

from app.db import models as m
from app.integrations.whatsapp import ComplianceError
from app.services import whatsapp_templates as svc
from tests.conftest import wa_outbound


class _FakeAdapter:
    def __init__(self, status="APPROVED", reason=None):
        self.status, self.reason = status, reason
        self.submissions: list[dict] = []

    def submit_template(self, **kwargs):
        self.submissions.append(kwargs)
        return {"id": f"meta-{len(self.submissions)}"}

    def fetch_template_status(self, **kwargs):
        return {"id": "meta-1", "status": self.status,
                "rejected_reason": self.reason}


class TestLifecycle:
    def test_draft_submit_approve_flow(self, db_session):
        t = svc.create_draft(db_session, name="flow_t", language="en_US",
                             category=m.WhatsAppTemplateCategory.MARKETING,
                             body="Hi {{1}}. Sam here. Reply STOP to opt out.",
                             variable_descriptions={"1": "first name"})
        assert (t.status, t.version) == (m.WhatsAppTemplateStatus.DRAFT, 1)

        adapter = _FakeAdapter()
        svc.submit_template(db_session, t, adapter)
        assert t.status is m.WhatsAppTemplateStatus.SUBMITTED
        assert t.meta_template_id == "meta-1"
        assert adapter.submissions[0]["name"] == "flow_t"

        svc.sync_template(db_session, t, adapter)
        assert t.status is m.WhatsAppTemplateStatus.APPROVED

    def test_rejection_records_reason_and_allows_edit_resubmit(self, db_session):
        t = svc.create_draft(db_session, name="rej_t", language="en_US",
                             category=m.WhatsAppTemplateCategory.MARKETING,
                             body="Hi {{1}}. Reply STOP to opt out.",
                             variable_descriptions={"1": "first name"})
        svc.submit_template(db_session, t, _FakeAdapter())
        svc.sync_template(db_session, t,
                          _FakeAdapter(status="REJECTED", reason="SCAM"))
        assert t.status is m.WhatsAppTemplateStatus.REJECTED
        assert t.rejection_reason == "SCAM"
        # edit in place -> clean draft again
        svc.update_template(db_session, t,
                            body="Hello {{1}}, Sam from Victorae. "
                                 "Reply STOP to opt out.")
        assert t.status is m.WhatsAppTemplateStatus.DRAFT
        assert t.rejection_reason is None

    def test_submitted_template_cannot_be_edited_or_resubmitted(self, db_session):
        t = svc.create_draft(db_session, name="pend_t", language="en_US",
                             category=m.WhatsAppTemplateCategory.MARKETING,
                             body="Hi {{1}}. Reply STOP to opt out.",
                             variable_descriptions={"1": "first name"})
        svc.submit_template(db_session, t, _FakeAdapter())
        with pytest.raises(svc.TemplateError, match="review"):
            svc.update_template(db_session, t, body="new body")
        with pytest.raises(svc.TemplateError, match="draft or rejected"):
            svc.submit_template(db_session, t, _FakeAdapter())


class TestVersioning:
    def _approved(self, db):
        t = svc.create_draft(db, name="ver_t", language="en_US",
                             category=m.WhatsAppTemplateCategory.MARKETING,
                             body="Hi {{1}}, v1 body. Reply STOP to opt out.",
                             variable_descriptions={"1": "first name"})
        svc.submit_template(db, t, _FakeAdapter())
        svc.sync_template(db, t, _FakeAdapter())
        return t

    def test_editing_approved_creates_new_draft_original_untouched(
            self, db_session):
        v1 = self._approved(db_session)
        original_body = v1.body
        v2 = svc.update_template(
            db_session, v1,
            body="Hi {{1}}, v2 body. Reply STOP to opt out.")
        assert v2.id != v1.id
        assert (v2.version, v2.status) == (2, m.WhatsAppTemplateStatus.DRAFT)
        assert v2.supersedes_id == v1.id
        db_session.refresh(v1)
        # the approved original is byte-for-byte untouched
        assert v1.status is m.WhatsAppTemplateStatus.APPROVED
        assert v1.body == original_body

    def test_send_resolves_approved_version_despite_newer_draft(
            self, db_session, wa_lead, wa_channel):
        v1 = self._approved(db_session)
        svc.update_template(db_session, v1, body="Hi {{1}}, draft v2. "
                                                 "Reply STOP to opt out.")
        # sendable lookup by (name, language) still finds APPROVED v1
        found = svc.sendable_template(db_session, "ver_t", "en_US")
        assert found is not None and found.version == 1
        assert wa_channel.send(wa_outbound(wa_lead, template="ver_t")).ok

    def test_rejected_only_name_never_sends(self, db_session, wa_lead,
                                            approved_template, wa_channel):
        t = svc.create_draft(db_session, name="only_rej", language="en_US",
                             category=m.WhatsAppTemplateCategory.MARKETING,
                             body="Hi {{1}}. Reply STOP to opt out.",
                             variable_descriptions={"1": "first name"})
        svc.submit_template(db_session, t, _FakeAdapter())
        svc.sync_template(db_session, t,
                          _FakeAdapter(status="REJECTED", reason="POLICY"))
        assert svc.sendable_template(db_session, "only_rej", "en_US") is None
        with pytest.raises(ComplianceError):
            wa_channel.send(wa_outbound(wa_lead, template="only_rej"))


class TestValidation:
    @pytest.mark.parametrize("body,why", [
        ("", "empty"),
        ("Hi {{2}}, no one", "gap: starts at 2"),
        ("Hi {{1}} and {{3}}", "gap: missing 2"),
        ("Hi {{1}} {{1}}", "duplicate"),
        ("x" * 5000, "too long"),
    ])
    def test_invalid_bodies_rejected(self, body, why):
        with pytest.raises(svc.TemplateError):
            svc.validate_body(body)

    def test_variable_descriptions_must_cover_placeholders(self, db_session):
        with pytest.raises(svc.TemplateError, match="missing"):
            svc.create_draft(db_session, name="vd_t", language="en_US",
                             category=m.WhatsAppTemplateCategory.MARKETING,
                             body="Hi {{1}} {{2}}. Reply STOP to opt out.",
                             variable_descriptions={"1": "first name"})

    def test_generated_drafts_saved_as_drafts_only(self, db_session,
                                                   fake_claude,
                                                   verified_strategy):
        rows = svc.generate_drafts(db_session, verified_strategy)
        assert rows and all(
            r.status is m.WhatsAppTemplateStatus.DRAFT for r in rows)
        # never auto-submitted: no meta id, opt-out present in body
        assert all(r.meta_template_id is None for r in rows)
        assert all("STOP" in (r.body or "") for r in rows)

    def test_promotional_draft_labelled_utility_is_saved_as_marketing(
            self, db_session, fake_claude, verified_strategy, monkeypatch):
        """I1 consent hardening (T33): the model's category label is not
        trusted. A cold pitch labelled 'utility' is saved as MARKETING with the
        standard opt-out; a genuine confirmation stays UTILITY, unchanged."""
        # No figures: a drafted number the research lacks is dropped on its
        # own (generated_body_problems), which is not what this test is about.
        pitch = ("Hi {{1}}, this is Sam from Brightline Bookkeeping. We close agency "
                 "books fast. Want a short call?")
        confirm = ("Hi {{1}}, this is Sam from Brightline Bookkeeping. Your onboarding "
                   "call is confirmed for {{2}}.")

        class _Client:
            def complete_json(self, **kw):
                return {"templates": [
                    {"name": "sneaky_utility", "language": "en_US", "category": "utility",
                     "body": pitch, "variable_descriptions": {"1": "first name"}},
                    {"name": "booking_confirmation", "language": "en_US",
                     "category": "utility", "body": confirm,
                     "variable_descriptions": {"1": "first name", "2": "call time"}}]}

        monkeypatch.setattr(svc, "get_client", lambda: _Client())
        rows = {r.name: r for r in svc.generate_drafts(db_session, verified_strategy)}
        sneaky, real = rows["sneaky_utility"], rows["booking_confirmation"]
        assert sneaky.category is m.WhatsAppTemplateCategory.MARKETING
        assert sneaky.body == f"{pitch} {svc.STANDARD_OPT_OUT}"
        assert svc.has_opt_out_instruction(sneaky.body)
        assert real.category is m.WhatsAppTemplateCategory.UTILITY
        assert real.body == confirm
