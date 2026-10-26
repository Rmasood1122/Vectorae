"""SEALED guard tests moved unchanged from tests/test_whatsapp_template_lifecycle.py.

Listed in evals/compliance_testset.yaml: a failing or missing test here
blocks the release gate. Moved by the gate PR in
docs/program/plans/phase5-seal-ai-and-tenancy-guards.md; the test bodies
are byte-identical to the source. Helpers they need are copied here so
the file stands alone.
"""

import pytest
from app.db import models as m
from app.services import whatsapp_templates as svc


def _row(db, status, name):
    row = svc.create_draft(db, name=name, language="en_US",
                           category=m.WhatsAppTemplateCategory.MARKETING,
                           body="Hi {{1}}, Sam here. Reply STOP to opt out.",
                           variable_descriptions={"1": "first name"})
    row.status = status
    db.commit()
    return row


_NEW = {"name": "new_t", "language": "en_US", "category": "marketing",
        "body": "Hi {{1}}, Sam here. Reply STOP to opt out.",
        "variable_descriptions": {"1": "first name"}}


class TestGeneratedBodyRules:
    """T33: the drafter's hard rules are checked, not trusted. A candidate with
    a link, a guarantee or urgency, or a number the research lacks is dropped."""

    SOURCE = "Owners of 5-20 person agencies.\nOffer: books closed in 5 working days."

    @pytest.mark.parametrize("body,problem", [
        ("Hi {{1}}, Sam here. See https://evil.example.test/offer.", "link"),
        ("Hi {{1}}, Sam here. Visit www.offer.example.", "link"),
        ("Hi {{1}}, Sam here. Results guaranteed.", "promises"),
        ("Hi {{1}}, Sam here. Today only: a free review.", "promises"),
        ("Hi {{1}}, Sam here. We cut month-end by 73% for 200+ agencies.", "numbers"),
    ])
    def test_a_broken_rule_is_named(self, body, problem):
        from app.services.whatsapp_templates import generated_body_problems

        assert any(problem in p for p in generated_body_problems(body, self.SOURCE))

    def test_a_faithful_body_passes(self):
        from app.services.whatsapp_templates import generated_body_problems

        body = "Hi {{1}}, Sam from Brightline. We close books in 5 working days. {{2}}?"
        assert generated_body_problems(body, self.SOURCE) == []

    def test_generate_drops_the_broken_candidate_and_keeps_the_rest(
            self, db_session, fake_claude, verified_strategy, monkeypatch):
        from app.services import whatsapp_templates as svc

        class _Client:
            def complete_json(self, **kw):
                return {"templates": [
                    {"name": "with_link", "category": "marketing",
                     "body": "Hi {{1}}, Sam from Brightline. See https://evil.example.test.",
                     "variable_descriptions": {"1": "first name"}},
                    {"name": "plain", "category": "marketing",
                     "body": "Hi {{1}}, Sam from Brightline. Open to a short call?",
                     "variable_descriptions": {"1": "first name"}}]}

        monkeypatch.setattr(svc, "get_client", lambda: _Client())
        names = [r.name for r in svc.generate_drafts(db_session, verified_strategy)]
        assert names == ["plain"]


# ---------------------------------------------------------------------------
# F-SEC-05: templates belong to the whole install, so only an admin manages
# them. Anyone signed in sees the approved ones (all a sender needs) and
# nothing else: a generated draft is written from one account's research.
# ---------------------------------------------------------------------------


class TestWhoManagesTemplates:
    def test_a_non_admin_cannot_change_any_template(self, client, db_session,
                                                    verified_strategy):
        draft = _row(db_session, m.WhatsAppTemplateStatus.DRAFT, "draft_t")
        submitted = _row(db_session, m.WhatsAppTemplateStatus.SUBMITTED, "sub_t")

        calls = (
            ("post", "/whatsapp/templates", _NEW),
            ("put", f"/whatsapp/templates/{draft.id}", {"body": "Hi {{1}}. Reply STOP to opt out."}),
            ("post", f"/whatsapp/templates/{draft.id}/submit", None),
            ("post", f"/whatsapp/templates/{submitted.id}/sync", None),
            ("post", "/whatsapp/templates/generate",
             {"strategy_id": str(verified_strategy.id)}),
        )
        for method, path, payload in calls:
            response = getattr(client, method)(path, json=payload)
            assert response.status_code == 403, (method, path, response.status_code)

        assert db_session.query(m.WhatsAppTemplate).count() == 2
        db_session.refresh(draft)
        assert draft.status is m.WhatsAppTemplateStatus.DRAFT
        assert draft.body == "Hi {{1}}, Sam here. Reply STOP to opt out."

    def test_a_non_admin_sees_only_approved_templates(self, client, db_session):
        approved = _row(db_session, m.WhatsAppTemplateStatus.APPROVED, "ok_t")
        hidden = [_row(db_session, status, f"hidden_{i}")
                  for i, status in enumerate((m.WhatsAppTemplateStatus.DRAFT,
                                              m.WhatsAppTemplateStatus.SUBMITTED,
                                              m.WhatsAppTemplateStatus.REJECTED))]

        listed = client.get("/whatsapp/templates").json()["templates"]

        assert [t["id"] for t in listed] == [str(approved.id)]
        assert client.get(f"/whatsapp/templates/{approved.id}").status_code == 200
        for row in hidden:
            assert client.get(f"/whatsapp/templates/{row.id}").status_code == 404

    def test_an_admin_manages_and_sees_every_template(self, client, db_session,
                                                      test_user):
        test_user.is_admin = True
        db_session.commit()
        draft = _row(db_session, m.WhatsAppTemplateStatus.DRAFT, "draft_t")

        assert client.post("/whatsapp/templates", json=_NEW).status_code == 201
        edited = client.put(f"/whatsapp/templates/{draft.id}",
                            json={"body": "Hi {{1}}. Reply STOP to opt out."})
        assert edited.status_code == 200
        assert {t["name"] for t in client.get("/whatsapp/templates").json()["templates"]} \
            == {"draft_t", "new_t"}
        assert client.get(f"/whatsapp/templates/{draft.id}").status_code == 200

    def test_an_admin_still_cannot_generate_from_someone_elses_strategy(
            self, client, db_session, test_user, verified_strategy):
        test_user.is_admin = True
        other = m.User(email="other-wa@example.test", email_verified=True)
        db_session.add(other)
        db_session.flush()
        db_session.get(m.Product, verified_strategy.product_id).user_id = other.id
        db_session.commit()

        response = client.post("/whatsapp/templates/generate",
                               json={"strategy_id": str(verified_strategy.id)})

        assert response.status_code == 404
        assert db_session.query(m.WhatsAppTemplate).count() == 0
