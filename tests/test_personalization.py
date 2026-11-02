"""Feature Group 2 — posts, news, voice, and the personal-video workflow.

What must hold:
  * the email's prompt carries the lead's own recent posts (first line must
    reference one) and last-30-day company news, and the SYSTEM prompt
    carries the sender's voice profile -- and the samples never reach it;
  * news is kept only when it names the company, and only if recent;
  * fetching happens at send time, is cached, and never blocks a send;
  * a video CTA appears on step 2 only once a real video was recorded, and
    the public page behind it reveals nothing beyond a first name, a company
    and an embed id.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models as m
from app.integrations import newsapi
from app.services import linkedin_profile
from app.services import (
    credentials,
    loom_video,
    personalization_context,
    style_profile,
)
from app.services.message_personalization import render_message

from .conftest import NOW, auth_headers

UTC = timezone.utc
SAMPLE = ("Hey Sam, quick one - did the new rota land okay with the team? "
          "Happy to jump on a call if it's easier. Cheers, Rehan")


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data, self.text = status, data, str(data)

    def json(self):
        return self._data


class _Client:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params))
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        return _Resp(404, {})


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


def _posts(n=3):
    return [{"text": f"Post {i}: our inspection backlog is brutal", "posted_at":
             (NOW - timedelta(days=i)).isoformat(), "url": f"https://li/p/{i}"}
            for i in range(1, n + 1)]


# --------------------------------------------------------------------------
# Voice profile
# --------------------------------------------------------------------------


class TestVoice:
    def test_sample_validation(self):
        with pytest.raises(style_profile.InvalidSamples):
            style_profile.clean_samples([])
        with pytest.raises(style_profile.InvalidSamples):
            style_profile.clean_samples(["too short"])
        with pytest.raises(style_profile.InvalidSamples):
            style_profile.clean_samples([SAMPLE] * 6)
        assert style_profile.clean_samples(["  " + SAMPLE + "  ", ""]) == [SAMPLE]

    def test_extract_and_suffix(self, db_session, test_user, fake_claude):
        profile = style_profile.extract(db_session, test_user, [SAMPLE])
        assert profile["formality"] == 2 and profile["humor"] == "light"
        assert test_user.style_samples_json == [SAMPLE]
        suffix = style_profile.system_suffix(profile)
        assert "SENDER'S OWN VOICE" in suffix and "2/5 (casual)" in suffix
        assert "use contractions" in suffix
        assert style_profile.system_suffix(None) == ""

    def test_malformed_profile_is_normalised(self, db_session, test_user, fake_claude):
        fake_claude.style_response = {"tone": "x", "formality": "eleven",
                                      "humor": "hilarious", "do": [1, "ok"]}
        profile = style_profile.extract(db_session, test_user, [SAMPLE])
        assert profile["formality"] == 3 and profile["humor"] == "none"
        assert profile["do"] == ["ok"]

    def test_api(self, client, db_session, test_user, fake_claude):
        assert client.get("/me/style-profile").json()["profile"] is None
        resp = client.put("/me/style-profile", json={"samples": [SAMPLE]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["profile"]["tone"] == "warm and direct"
        assert client.put("/me/style-profile",
                          json={"samples": ["short"]}).status_code == 422
        assert client.delete("/me/style-profile").status_code == 204
        assert client.get("/me/style-profile").json()["profile"] is None


# --------------------------------------------------------------------------
# The render prompt
# --------------------------------------------------------------------------


class TestRenderPrompt:
    def test_posts_news_and_voice_reach_the_prompt(self, db_session, test_user, lead,
                                                   verified_strategy, email_sequence,
                                                   fake_claude):
        test_user.style_profile_json = {"tone": "warm", "formality": 2,
                                        "vocabulary_level": "plain",
                                        "sentence_length": "short", "humor": "light"}
        test_user.style_samples_json = ["PRIVATE SAMPLE TEXT about client Globex"]
        lead.linkedin_posts_json = _posts()
        lead.company_news_json = [{"headline": "Acme Fire wins state contract",
                                   "summary": "Big win", "url": "https://n/1",
                                   "source": "Local", "published_at":
                                   (datetime.now(UTC) - timedelta(days=3)).isoformat()},
                                  {"headline": "Old Acme Fire news", "summary": "",
                                   "url": "https://n/2", "source": "x", "published_at":
                                   (datetime.now(UTC) - timedelta(days=60)).isoformat()}]
        db_session.commit()
        used: dict = {}
        render_message(db_session, verified_strategy, lead, email_sequence.steps[0],
                       inputs_out=used)
        system, prompt = fake_claude.personalization_calls[-1]
        assert "SENDER'S OWN VOICE" in system
        assert "PRIVATE SAMPLE TEXT" not in system + prompt     # samples never leak
        assert "FIRST LINE of the email must reference" in prompt
        assert "Post 1: our inspection backlog is brutal" in prompt
        assert "Acme Fire wins state contract" in prompt
        assert "Old Acme Fire news" not in prompt                # outside 30 days
        # `voice_profile` is Feature 3: False here because this product has no
        # analysed founder voice. It is recorded either way so
        # messages.personalization_json always answers "was a voice applied?".
        assert used == {"linkedin_post_url": "https://li/p/1", "news_url": "https://n/1",
                        "loom_cta": False, "style_profile": True,
                        "voice_profile": False}

    def test_plain_lead_renders_as_before(self, db_session, lead, verified_strategy,
                                          email_sequence, fake_claude):
        used: dict = {}
        render_message(db_session, verified_strategy, lead, email_sequence.steps[0],
                       inputs_out=used)
        system, prompt = fake_claude.personalization_calls[-1]
        assert "SENDER'S OWN VOICE" not in system
        assert "LINKEDIN POSTS" not in prompt and "RECENT NEWS" not in prompt
        assert used["style_profile"] is False

    def test_video_cta_only_on_step_2_once_recorded(self, db_session, lead,
                                                    verified_strategy, email_sequence,
                                                    fake_claude):
        step1, step2 = email_sequence.steps[0], email_sequence.steps[1]
        lead.loom_video_json = {"status": "suggested", "script": "..."}
        db_session.commit()
        render_message(db_session, verified_strategy, lead, step2)
        assert "PERSONAL VIDEO" not in fake_claude.personalization_calls[-1][1]

        loom_video.record(db_session, lead,
                          "https://www.loom.com/share/0123456789abcdef0123456789abcdef")
        render_message(db_session, verified_strategy, lead, step1)
        assert "PERSONAL VIDEO" not in fake_claude.personalization_calls[-1][1]
        used: dict = {}
        render_message(db_session, verified_strategy, lead, step2, inputs_out=used)
        prompt = fake_claude.personalization_calls[-1][1]
        assert "PERSONAL VIDEO" in prompt and "http://frontend.test/v?t=" in prompt
        assert used["loom_cta"] is True

    def test_whatsapp_variables_carry_the_voice(self, db_session, test_user,
                                                verified_strategy, lead, fake_claude,
                                                monkeypatch):
        from app.services.message_personalization import render_whatsapp_variables

        test_user.style_profile_json = {"tone": "warm", "formality": 2}
        db_session.commit()
        seen = []
        real = fake_claude.complete_json
        monkeypatch.setattr(fake_claude, "complete_json",
                            lambda system, prompt, max_tokens=None, schema=None:
                            seen.append(system) or real(system, prompt, max_tokens))
        render_whatsapp_variables(db_session, verified_strategy, lead,
                                  template_body="Hi {{1}}", variable_descriptions={"1": "pain"},
                                  variable_mapping={})
        assert "SENDER'S OWN VOICE" in seen[-1]


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


class TestNews:
    def test_mentions_is_whole_word(self):
        assert newsapi.mentions("Acme", "Acme Corp raises money")
        assert not newsapi.mentions("Acme", "Acmetron expands")
        assert newsapi.mentions("Acme Fire", None, "news about acme fire")

    def test_fetch_filters(self, monkeypatch):
        now = datetime.now(UTC)
        client = _Client({"newsapi.org": _Resp(200, {"status": "ok", "articles": [
            {"title": "Acme Fire wins award", "description": "", "url": "u1",
             "publishedAt": (now - timedelta(days=2)).isoformat(), "source": {"name": "S"}},
            {"title": "Unrelated story", "description": "about someone else", "url": "u2",
             "publishedAt": (now - timedelta(days=1)).isoformat(), "source": {}},
            {"title": "Acme Fire old news", "description": "", "url": "u3",
             "publishedAt": (now - timedelta(days=45)).isoformat(), "source": {}},
        ]})})
        monkeypatch.setattr(newsapi, "_http", lambda: client)
        items = newsapi.fetch_company_news("key", "Acme Fire", now=now)
        assert [i["url"] for i in items] == ["u1"]
        assert client.calls[0][1]["q"] == '"Acme Fire"'

    def test_error_status_raises(self, monkeypatch):
        monkeypatch.setattr(newsapi, "_http",
                            lambda: _Client({"newsapi.org": _Resp(401, {"message": "bad key"})}))
        with pytest.raises(newsapi.NewsUnavailable):
            newsapi.fetch_company_news("key", "Acme")


class TestLinkedInPostsAreNotFetched:
    """2026-11-02 (evals/vectorae, option a): no LinkedIn post reader exists."""

    def test_profile_identifier_helper_remains(self):
        assert linkedin_profile.public_identifier(
            "https://www.linkedin.com/in/sara-khan-9/?x=1") == "sara-khan-9"
        assert linkedin_profile.public_identifier("https://example.com") is None

    def test_the_readers_are_gone(self):
        import importlib.util  # noqa: PLC0415

        assert importlib.util.find_spec("app.integrations.linkedin_posts") is None
        assert importlib.util.find_spec("app.integrations.unipile_linkedin") is None


# --------------------------------------------------------------------------
# Freshness + the send path
# --------------------------------------------------------------------------


class TestEnsureFresh:
    def test_backfills_url_fetches_news_and_caches_but_never_posts(
            self, db_session, test_user, lead, monkeypatch):
        credentials.set_system_secret(db_session, "newsapi", "api_key", "n-key")
        calls = {"news": 0}

        def _news_fetch(key, company, now=None):
            calls["news"] += 1
            return []

        monkeypatch.setattr(newsapi, "fetch_company_news", _news_fetch)
        result = personalization_context.ensure_fresh(db_session, lead, owner_id=test_user.id)
        assert lead.linkedin_url == "https://www.linkedin.com/in/sara-khan-9/"
        assert result == {"posts": "skipped", "news": "fetched:0"}
        assert lead.linkedin_posts_fetched_at is None and lead.linkedin_posts_json is None
        personalization_context.ensure_fresh(db_session, lead, owner_id=test_user.id)
        assert calls == {"news": 1}                     # cached
        personalization_context.ensure_fresh(db_session, lead, owner_id=test_user.id, force=True)
        assert calls == {"news": 2}

    def test_send_path_works_without_any_linkedin_reader(self, db_session, enrolled,
                                                         fake_channel):
        from app.workers.outreach_tasks import send_message_impl

        msg = db_session.query(m.Message).filter_by(step_no=1).first()
        db_session.get(m.Lead, msg.lead_id).linkedin_url = "https://linkedin.com/in/x"
        db_session.commit()
        assert send_message_impl(db_session, msg.id, channel=fake_channel, now=NOW) == "sent"
        db_session.refresh(msg)
        assert not (msg.personalization_json or {}).get("linkedin_post_url")


# --------------------------------------------------------------------------
# Loom
# --------------------------------------------------------------------------


class TestLoom:
    def test_embed_id_and_token(self, lead):
        assert loom_video.loom_embed_id(
            "https://www.loom.com/share/0123456789ABCDEF0123456789abcdef?sid=1") \
            == "0123456789abcdef0123456789abcdef"
        assert loom_video.loom_embed_id("https://youtube.com/watch?v=1") is None
        assert loom_video.parse_token(loom_video.make_token(lead.id)) == lead.id

    def test_suggest_respects_threshold_and_is_idempotent(self, db_session, lead,
                                                          fake_claude, monkeypatch):
        emitted = []
        from app.services import event_bus
        monkeypatch.setattr(event_bus, "emit",
                            lambda db, uid, event, **kw: emitted.append(event))
        low = m.Lead(strategy_id=lead.strategy_id, source="apollo", external_id="P2",
                     full_name="Low", ai_booking_likelihood=60)
        db_session.add(low)
        lead.ai_booking_likelihood = 80
        # The fake script cites her post about inspections; the claim check
        # (T24) keeps that only when such a post is on record.
        lead.linkedin_posts_json = [{"text": "Inspections are eating our month.",
                                     "url": "https://linkedin.com/posts/1"}]
        db_session.commit()
        assert loom_video.suggest_for_leads(db_session, [lead, low]) == 1
        assert lead.loom_video_json["status"] == "suggested"
        assert lead.loom_video_json["script"].startswith("Hi Sara")
        assert low.loom_video_json is None
        assert emitted == ["loom_requested"]
        assert loom_video.suggest_for_leads(db_session, [lead]) == 0      # idempotent


    def test_record_validates(self, db_session, lead):
        with pytest.raises(ValueError):
            loom_video.record(db_session, lead, "https://vimeo.com/1")

    def test_api_and_public_page(self, client, db_session, lead, fake_claude):
        # NOT the anon_client fixture: it is this same TestClient with its
        # Authorization header already popped, so the owner calls below would
        # 401. The header is dropped only for the public calls.
        resp = client.post(f"/leads/{lead.id}/loom/script")
        assert resp.status_code == 200 and resp.json()["loom"]["status"] == "suggested"
        assert client.put(f"/leads/{lead.id}/loom",
                          json={"share_url": "https://vimeo.com/xyz123"}).status_code == 422
        resp = client.put(f"/leads/{lead.id}/loom", json={
            "share_url": "https://www.loom.com/share/0123456789abcdef0123456789abcdef"})
        page_url = resp.json()["loom_page_url"]
        token = page_url.split("t=", 1)[1]

        client.headers.pop("Authorization", None)     # the prospect has no session
        public = client.get(f"/public/video?t={token}")
        assert public.status_code == 200
        body = public.json()
        assert body == {"first_name": "Sara", "company": "Acme Fire",
                        "title": "A quick idea for your team",
                        "embed_url": "https://www.loom.com/embed/0123456789abcdef0123456789abcdef"}
        assert "sara@acme.test" not in public.text
        assert client.get("/public/video?t=" + "x" * 40).status_code == 404

    def test_skip_hides_the_page(self, client, db_session, lead):
        loom_video.record(db_session, lead,
                          "https://www.loom.com/share/0123456789abcdef0123456789abcdef")
        token = loom_video.make_token(lead.id)
        assert client.post(f"/leads/{lead.id}/loom/skip").status_code == 200
        client.headers.pop("Authorization", None)
        assert client.get(f"/public/video?t={token}").status_code == 404


class TestLeadPersonalizationApi:
    def test_get_refresh_and_url(self, client, db_session, lead, test_user, monkeypatch):
        body = client.get(f"/leads/{lead.id}/personalization").json()
        assert body["linkedin_url"] == "https://www.linkedin.com/in/sara-khan-9/"
        body = client.post(f"/leads/{lead.id}/personalization/refresh").json()
        assert not body["linkedin_posts"]           # never fetched
        assert client.put(f"/leads/{lead.id}/linkedin-url",
                          json={"linkedin_url": "https://example.com"}).status_code == 422
        body = client.put(f"/leads/{lead.id}/linkedin-url",
                          json={"linkedin_url": "https://linkedin.com/in/other"}).json()
        assert body["linkedin_url"] == "https://linkedin.com/in/other"
        assert body["linkedin_posts"] is None        # another person's posts dropped

    def test_other_tenant_404(self, client, db_session, lead):
        other = m.User(email="p2@x.test", email_verified=True)
        db_session.add(other)
        db_session.commit()
        assert client.get(f"/leads/{lead.id}/personalization",
                          headers=auth_headers(other)).status_code == 404


def test_sourcing_captures_linkedin_url(db_session, lead_batch, fake_source, fake_verifier):
    from app.integrations.base import RawLead
    from app.workers.lead_tasks import source_leads_impl

    fake_source.feed = [RawLead(source="fakesource", external_id="x1", full_name="A B",
                                company="C", raw={"id": "x1",
                                                  "linkedin_url": "https://linkedin.com/in/ab"})]
    source_leads_impl(db_session, lead_batch.id, source=fake_source)
    assert db_session.query(m.Lead).filter_by(external_id="x1").one().linkedin_url \
        == "https://linkedin.com/in/ab"
