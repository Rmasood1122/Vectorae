"""SEALED guard tests moved unchanged from tests/test_linkedin_channel.py.

Listed in evals/compliance_testset.yaml: a failing or missing test here
blocks the release gate. Moved by the gate PR in
docs/program/plans/phase5-seal-ai-and-tenancy-guards.md; the test bodies
are byte-identical to the source. Helpers they need are copied here so
the file stands alone.
"""

from __future__ import annotations
import pytest
from app.db import models as m
from app.services import linkedin_outreach


def _li_lead(db_session, strategy, i, **kw):
    lead = m.Lead(strategy_id=strategy.id, source="apollo", external_id=f"LI{i}",
                  full_name=f"Li Lead {i}", company="Acme", status=m.LeadStatus.VERIFIED,
                  linkedin_url=f"https://www.linkedin.com/in/li-lead-{i}/", **kw)
    db_session.add(lead)
    db_session.commit()
    return lead


class TestRenderLinks:
    """The prompt's 'no links' rule is not a control: a link an injected
    profile or post asked for must not reach a LinkedIn note."""

    def _render(self, monkeypatch, db_session, strategy, lead, text, template):
        from types import SimpleNamespace

        from app.services import anthropic_client

        class _Writer:
            def complete_json(self, **_kw):
                return {"text": text}

        monkeypatch.setattr(anthropic_client, "get_client", lambda: _Writer())
        return linkedin_outreach.render(db_session, strategy, lead,
                                        SimpleNamespace(template=template), "message")

    def test_a_foreign_link_is_removed(self, monkeypatch, db_session, verified_strategy):
        lead = _li_lead(db_session, verified_strategy, 41)
        out = self._render(monkeypatch, db_session, verified_strategy, lead,
                           "Hi Dana, details at https://evil.example.test/book. Worth a chat?",
                           "ask for a short call")
        assert "evil.example.test" not in out["text"]
        assert "Worth a chat?" in out["text"]

    @pytest.mark.parametrize("link", ["www.bait.example.com", "bait.example.com/book",
                                      "bit.ly/x9q"])
    def test_a_link_without_a_scheme_is_removed(self, monkeypatch, db_session,
                                                verified_strategy, link):
        lead = _li_lead(db_session, verified_strategy, 44)
        out = self._render(monkeypatch, db_session, verified_strategy, lead,
                           f"Hi Dana, details at {link}. Worth a chat?",
                           "ask for a short call")
        assert "bait" not in out["text"] and "bit.ly" not in out["text"]
        assert "Worth a chat?" in out["text"]

    def test_a_scheme_less_link_the_brief_names_is_kept(self, monkeypatch, db_session,
                                                        verified_strategy):
        lead = _li_lead(db_session, verified_strategy, 45)
        out = self._render(monkeypatch, db_session, verified_strategy, lead,
                           "Hi Dana, our guide: guides.ours.com/payroll. Useful?",
                           "share guides.ours.com/payroll")
        assert out["text"] == "Hi Dana, our guide: guides.ours.com/payroll. Useful?"

    def test_a_link_the_brief_names_is_kept(self, monkeypatch, db_session,
                                            verified_strategy):
        lead = _li_lead(db_session, verified_strategy, 42)
        out = self._render(monkeypatch, db_session, verified_strategy, lead,
                           "Hi Dana, our guide: https://guides.ours.example/payroll. Useful?",
                           "share https://guides.ours.example/payroll")
        assert "https://guides.ours.example/payroll" in out["text"]

    def test_a_note_that_was_only_a_link_is_refused(self, monkeypatch, db_session,
                                                    verified_strategy):
        lead = _li_lead(db_session, verified_strategy, 43)
        with pytest.raises(ValueError, match="empty"):
            self._render(monkeypatch, db_session, verified_strategy, lead,
                         "https://evil.example.test/book", "ask for a short call")
