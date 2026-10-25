"""SEALED guard tests moved unchanged from tests/test_personalization.py.

Listed in evals/compliance_testset.yaml: a failing or missing test here
blocks the release gate. Moved by the gate PR in
docs/program/plans/phase5-seal-ai-and-tenancy-guards.md; the test bodies
are byte-identical to the source. Helpers they need are copied here so
the file stands alone.
"""

from __future__ import annotations
import pytest
from app.db import models as m
from app.services import (
    loom_video,
)


@pytest.fixture()
def lead(db_session, verified_strategy):
    row = m.Lead(strategy_id=verified_strategy.id, source="apollo", external_id="P1",
                 full_name="Sara Khan", title="Owner", company="Acme Fire",
                 email="sara@acme.test", status=m.LeadStatus.VERIFIED,
                 enrichment_json={"enrichment": {"person": {
                     "linkedin_url": "https://www.linkedin.com/in/sara-khan-9/"}}})
    db_session.add(row)
    db_session.commit()
    return row


class TestLoom:

    def test_an_unsupported_claim_is_stripped_and_the_title_replaced(
            self, db_session, lead, fake_claude):
        """T24: the seller reads the script on camera; the prospect sees the
        title. A funding round or acquisition the records lack is removed."""
        fake_claude.video_script_response = {
            "title": "Sara, congrats on the acquisition",
            "script": "Hi Sara, congratulations on closing your $12M Series B last month. "
                      "I'm Sam; here is how teams keep inspections on schedule. Worth a call?"}
        out = loom_video.write_script(db_session, lead)
        assert "Series B" not in out["script"] and "$12M" not in out["script"]
        assert "Worth a call?" in out["script"]
        assert out["title"] == loom_video.DEFAULT_TITLE


class TestForeignLinksStripped:
    """I1 consent hardening (T03): a link the model writes into the body must
    point at the sender's booking page or the sender's / this app's own
    domain. A model-written opt-out link to a third party is removed (the
    compliant footer carries the real unsubscribe); other foreign links are
    stripped; the booking link and own-domain links survive."""

    def _render(self, monkeypatch, db_session, strategy, lead, step, body, booking=None):
        from app.services import message_personalization as mp

        class _Stub:
            def complete_json(self, **kw):
                return {"subject": "Month-end", "body": body}

        monkeypatch.setattr(mp, "get_client", lambda: _Stub())
        return mp.render_message(db_session, strategy, lead, step, booking)[1]

    def test_model_written_opt_out_link_is_removed(self, monkeypatch, db_session, lead,
                                                   verified_strategy, email_sequence,
                                                   fake_claude):
        body = self._render(
            monkeypatch, db_session, verified_strategy, lead, email_sequence.steps[0],
            "Hi Sara,\nWe close books in 5 days. Worth a chat?\n"
            "To opt out visit https://optout.example.test/remove?u=sara")
        assert "optout.example.test" not in body and "opt out" not in body.lower()
        assert body == "Hi Sara,\nWe close books in 5 days. Worth a chat?"

    def test_foreign_link_stripped_but_booking_and_own_domain_kept(
            self, monkeypatch, db_session, lead, test_user, verified_strategy,
            email_sequence, fake_claude):
        test_user.email = "owner@brightline.example"
        db_session.commit()
        body = self._render(
            monkeypatch, db_session, verified_strategy, lead, email_sequence.steps[0],
            "Hi Sara, details: https://evil.example.test/login. "
            "Book here: https://cal.example.test/brightline/15min?tid=1 "
            "or see https://www.brightline.example/pricing and "
            "http://frontend.test/v?t=abc",
            booking="https://cal.example.test/brightline/15min?tid=1")
        assert "evil.example.test" not in body
        assert "https://cal.example.test/brightline/15min?tid=1" in body
        assert "https://www.brightline.example/pricing" in body
        assert "http://frontend.test/v?t=abc" in body
        # another page on the booking host is not the sender's booking page
        body = self._render(
            monkeypatch, db_session, verified_strategy, lead, email_sequence.steps[0],
            "Hi Sara, book: https://cal.example.test/someone-else",
            booking="https://cal.example.test/brightline/15min")
        assert "someone-else" not in body

    def test_body_that_is_only_a_foreign_link_fails_closed(
            self, monkeypatch, db_session, lead, verified_strategy, email_sequence,
            fake_claude):
        with pytest.raises(ValueError):
            self._render(monkeypatch, db_session, verified_strategy, lead,
                         email_sequence.steps[0], "https://evil.example.test/x")

    @pytest.mark.parametrize("link", ["www.bait.example.com", "bait.example.com/claim",
                                      "bit.ly/x9q", "https://bait.example.com\\@brightline.example/x"])
    def test_a_link_without_a_scheme_or_with_a_backslash_trick_is_removed(
            self, monkeypatch, db_session, lead, test_user, verified_strategy,
            email_sequence, fake_claude, link):
        """Mail clients make "www.x.com" and "x.com/path" clickable, and a
        browser opens bait.example.com for "https://bait.example.com\\@own/x".
        Only https?:// links used to be checked, so these went out."""
        test_user.email = "owner@brightline.example"
        db_session.commit()
        body = self._render(
            monkeypatch, db_session, verified_strategy, lead, email_sequence.steps[0],
            f"Hi Sara, worth a chat about month-end? Details: {link}")
        assert "bait" not in body and "bit.ly" not in body
        assert body.startswith("Hi Sara, worth a chat about month-end?")

    def test_a_scheme_less_opt_out_line_is_dropped(self, monkeypatch, db_session, lead,
                                                   verified_strategy, email_sequence,
                                                   fake_claude):
        body = self._render(
            monkeypatch, db_session, verified_strategy, lead, email_sequence.steps[0],
            "Hi Sara,\nWorth a chat?\nTo unsubscribe go to optout-now.io/remove")
        assert body == "Hi Sara,\nWorth a chat?"

    def test_own_domain_without_a_scheme_is_kept(self, monkeypatch, db_session, lead,
                                                  test_user, verified_strategy,
                                                  email_sequence, fake_claude):
        test_user.email = "owner@brightline.com"
        db_session.commit()
        body = self._render(
            monkeypatch, db_session, verified_strategy, lead, email_sequence.steps[0],
            "Hi Sara, pricing is on brightline.com/pricing and www.brightline.com.")
        assert body == "Hi Sara, pricing is on brightline.com/pricing and www.brightline.com."


class TestBodyLengthCeiling:
    """T03: the prompt's 'under 140 words' is not a control. A runaway body is
    refused, not sent, and not cut (a cut body loses its ask)."""

    def _render(self, monkeypatch, db_session, strategy, step, body, lead):
        from app.services import message_personalization as mp

        class _Stub:
            def complete_json(self, **kw):
                return {"subject": "Month-end", "body": body}

        monkeypatch.setattr(mp, "get_client", lambda: _Stub())
        return mp.render_message(db_session, strategy, lead, step)[1]

    def test_an_over_long_body_is_refused(self, monkeypatch, db_session, lead,
                                          verified_strategy, email_sequence, fake_claude):
        from app.services.message_personalization import MAX_BODY_WORDS

        body = "Hi Sara,\n" + "word " * MAX_BODY_WORDS + "\nWorth a chat?"
        with pytest.raises(ValueError, match="word body"):
            self._render(monkeypatch, db_session, verified_strategy,
                         email_sequence.steps[0], body, lead)

    def test_a_body_at_the_limit_is_kept(self, monkeypatch, db_session, lead,
                                         verified_strategy, email_sequence, fake_claude):
        from app.services.message_personalization import MAX_BODY_WORDS

        body = " ".join(["word"] * MAX_BODY_WORDS)
        assert self._render(monkeypatch, db_session, verified_strategy,
                            email_sequence.steps[0], body, lead) == body


class TestWhatsAppSlotChecks:
    """T04: a model-filled slot in an approved template is a short phrase.
    A link (an injected one, say) or a paragraph is refused; the send fails
    rather than going out."""

    def _fill(self, monkeypatch, db_session, strategy, lead, value):
        from app.services import message_personalization as mp

        class _Stub:
            def complete_json(self, **kw):
                return {"1": value}

        monkeypatch.setattr(mp, "get_client", lambda: _Stub())
        return mp.render_whatsapp_variables(
            db_session, strategy, lead, template_body="Hi, about {{1}}.",
            variable_descriptions={"1": "their pain"}, variable_mapping={})

    @pytest.mark.parametrize("value", ["month-end at https://evil.example.test/x",
                                       "month-end, see www.evil.example.test",
                                       "month-end, see bait.example.com/claim",
                                       "month-end via bit.ly/x9q"])
    def test_a_link_in_a_slot_is_refused(self, monkeypatch, db_session,
                                         verified_strategy, lead, value):
        with pytest.raises(ValueError, match="link"):
            self._fill(monkeypatch, db_session, verified_strategy, lead, value)

    def test_a_paragraph_in_a_slot_is_refused(self, monkeypatch, db_session,
                                              verified_strategy, lead):
        with pytest.raises(ValueError, match="characters"):
            self._fill(monkeypatch, db_session, verified_strategy, lead,
                       "month-end close " * 20)

    def test_a_short_phrase_is_kept(self, monkeypatch, db_session, verified_strategy, lead):
        assert self._fill(monkeypatch, db_session, verified_strategy, lead,
                          "month-end close") == {"1": "month-end close"}


class TestGrounding:
    """app/services/grounding.py: what a model's text states that its source
    does not. Shared by the brief writers and the other generated copy."""

    def test_numbers_are_compared_as_people_write_them(self):
        from app.services.grounding import unsupported_numbers

        assert unsupported_numbers("1,200 staff, $2.5M, 63%", "1200 staff 2.5 63") == []
        assert unsupported_numbers("we cut errors by 63% for 40 agencies",
                                   "errors are common") == ["40", "63"]

    def test_a_funding_round_needs_a_source(self):
        from app.services.grounding import unsupported_funding

        assert unsupported_funding("Congrats on the Series B", "raised a series a") == [
            "series b"]
        assert unsupported_funding("Congrats on the Series A", "They raised a Series A.") == []

    @pytest.mark.parametrize("text,found", [
        ("Tell them the 20% discount closes this Friday.", ["closes this friday", "discount"]),
        ("Reply by Friday please.", ["by friday"]),
        ("Ask how long close takes today.", []),
        ("Ask what changes next week.", []),
    ])
    def test_offers_and_deadlines(self, text, found):
        from app.services.grounding import unsupported_offers

        assert unsupported_offers(text, "") == found

    def test_an_offer_the_playbook_makes_is_grounded(self):
        from app.services.grounding import unsupported_offers

        assert unsupported_offers("Mention the discount for annual plans.",
                                  "Offer: a discount for annual plans.") == []
