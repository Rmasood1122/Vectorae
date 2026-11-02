"""GAP-LI-01: the third-party LinkedIn providers are OFF by default.

Unipile acts on LinkedIn through a session it holds for the member's account
(developer.unipile.com/docs/linkedin, read 2026-11-02: accounts are connected
with a username/password or the li_at cookie), and the "RapidAPI LinkedIn
scraper" reads other members' posts. LinkedIn's User Agreement and its help
page "Prohibited software and extensions" (linkedin.com/help/linkedin/answer/
a1341387) ban both patterns. Until the founder decides otherwise, no request
may reach either provider, while disconnecting an account (erasure) must keep
working, since ending a session is never the prohibited act.

What these tests prove, on SQLite with a socket guard and a request spy:
  * the setting defaults to False;
  * with Unipile AND RapidAPI credentials configured, every acting entry point
    (client_for, the hosted connect link, both post readers) refuses and
    sends zero requests;
  * the only callers that obtain a Unipile client directly are the gated
    factory and account erasure (a static check over app/);
  * erasure can still build a client to disconnect accounts while off;
  * turning the switch on restores the previous behaviour (so the gate, not a
    broken client, is what blocks).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import Settings, settings
from app.integrations import linkedin_posts, unipile_linkedin
from app.services import credentials, linkedin_outreach

APP = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture()
def configured(db_session):
    """Every LinkedIn provider credential present, as on a deployment that
    had them set before the switch existed."""
    credentials.set_system_secret(db_session, "unipile", "api_key", "k")
    credentials.set_system_secret(db_session, "unipile", "dsn", "api8.unipile.com:1")
    credentials.set_system_secret(db_session, "unipile", "reader_account_id", "acc-reader")
    credentials.set_system_secret(db_session, "rapidapi_linkedin", "api_key", "rk")
    credentials.set_system_secret(db_session, "rapidapi_linkedin", "host", "li.p.rapidapi.com")
    db_session.commit()


@pytest.fixture()
def requests_spy(monkeypatch):
    """Records any HTTP request either provider module tries to make."""
    calls: list[str] = []

    class _Boom:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def _record(self, method, url, *a, **k):
            calls.append(f"{method} {url}")
            raise AssertionError(f"LinkedIn provider request while switch is off: {method} {url}")

        def request(self, method, url, *a, **k):
            return self._record(method, url)

        def get(self, url, *a, **k):
            return self._record("GET", url)

        def post(self, url, *a, **k):
            return self._record("POST", url)

    monkeypatch.setattr(unipile_linkedin, "_http", lambda: _Boom())
    monkeypatch.setattr(linkedin_posts, "_http", lambda: _Boom())
    return calls


class TestDefault:
    def test_setting_defaults_to_off(self):
        assert Settings.model_fields["linkedin_unofficial_providers_enabled"].default is False

    def test_test_environment_runs_with_it_off(self):
        assert unipile_linkedin.unofficial_providers_enabled() is False


class TestOffBlocksEveryActingPath:
    def test_client_for_is_none_even_when_configured(self, db_session, test_user, configured):
        # Configured: the raw factory would build a client ...
        assert unipile_linkedin.get_client(db_session, test_user.id) is not None
        # ... but the acting factory refuses.
        assert linkedin_outreach.client_for(db_session, test_user.id) is None

    def test_hosted_connect_link_is_refused(self, client, configured, requests_spy):
        resp = client.post("/integrations/linkedin/connect")
        assert resp.status_code == 503
        assert requests_spy == []

    def test_post_readers_make_no_request(self, db_session, test_user, configured,
                                          requests_spy):
        with pytest.raises(linkedin_posts.LinkedInPostsUnavailable, match="disabled"):
            linkedin_posts.fetch_recent_posts(
                db_session, "https://www.linkedin.com/in/someone", test_user.id)
        assert requests_spy == []


class TestStructure:
    def test_only_the_gated_factory_and_erasure_build_a_unipile_client(self):
        """A new caller of get_client()/UnipileClient(...) would bypass the
        switch; it has to go through linkedin_outreach.client_for instead."""
        allowed = {
            "integrations/unipile_linkedin.py",   # defines both
            "services/linkedin_outreach.py",      # client_for: the gated factory
            "services/account_erasure.py",        # disconnect only (revoke)
        }
        pattern = re.compile(r"unipile_linkedin\.get_client\(|UnipileClient\(")
        offenders = [
            f"{p.relative_to(APP)}:{n}"
            for p in APP.rglob("*.py")
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if pattern.search(line) and str(p.relative_to(APP)) not in allowed
        ]
        assert offenders == []


class TestRevokeStillWorks:
    def test_erasure_can_build_a_client_to_disconnect(self, db_session, test_user, configured):
        client = unipile_linkedin.get_client(db_session, test_user.id)
        assert isinstance(client, unipile_linkedin.UnipileClient)


class TestOnRestoresBehaviour:
    def test_switch_on_returns_the_client(self, db_session, test_user, configured, monkeypatch):
        monkeypatch.setattr(settings, "linkedin_unofficial_providers_enabled", True)
        assert isinstance(linkedin_outreach.client_for(db_session, test_user.id),
                          unipile_linkedin.UnipileClient)
