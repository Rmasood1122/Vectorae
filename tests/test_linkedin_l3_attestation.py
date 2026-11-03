"""L3: importing the user's OWN LinkedIn connections export, consent-first.

Phase 5B (evals/vectorae) rules, offline:
  * no lawful-basis attestation at the current text version -> 422 and the
    file is not read; nothing is stored;
  * the attestation is recorded (scope, purpose, text version, lawful basis,
    time, HASHED origin) and a changed text means re-consent;
  * imported people never become email leads;
  * suppressed people are not recommended and get no draft (before any model
    call); deleting the network withdraws the attestation;
  * duplicates across uploads are skipped.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import models as m
from app.services import linkedin_consent as lc

CSV = (
    "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
    "Ana,Lopez,https://www.linkedin.com/in/ana-lopez,ana@acme-fire.com,"
    "Acme Fire Protection,Owner,05 Oct 2026\n"
    "Cara,Diaz,https://www.linkedin.com/in/cara-diaz,,Summit Fire & Safety,"
    "VP of Operations,12 Jan 2024\n"
)


def _version():
    return lc.current_text(lc.CONNECTIONS_IMPORT).version


def _import(client, *, version="current", basis="legitimate_interests", text=CSV):
    body = {"csv_text": text}
    if version is not None:
        body["consent_text_version"] = _version() if version == "current" else version
    if basis is not None:
        body["lawful_basis"] = basis
    return client.post("/linkedin-network/import", json=body)


@pytest.fixture()
def growth(db_session, test_user):
    test_user.plan = m.PlanTier.GROWTH
    db_session.add(m.Product(user=test_user, name="Fire SEO", type=m.ProductType.SKILL,
                             description="Local SEO for fire protection companies"))
    db_session.commit()
    return test_user


def _count(db, model):
    return db.query(model).count()


class TestAttestationRequired:
    @pytest.mark.parametrize("version,basis", [
        (None, "legitimate_interests"),          # never saw the notice
        ("2020-01-01.0", "legitimate_interests"),  # saw an older notice
        ("current", None),                       # no lawful basis
        ("current", "because_i_can"),            # not a basis
    ])
    def test_refused_and_nothing_stored(self, client, db_session, growth, version, basis):
        r = _import(client, version=version, basis=basis)
        assert r.status_code == 422
        assert _count(db_session, m.LinkedInConnection) == 0
        assert _count(db_session, m.LinkedInConsent) == 0
        assert _count(db_session, m.LinkedInImport) == 0

    def test_status_serves_the_notice_and_its_version(self, client, growth):
        consent = client.get("/linkedin-network/status").json()["consent"]
        assert consent["version"] == _version() and consent["given"] is False
        assert "lawful basis" in consent["text"] and "never" in consent["text"]
        assert consent["lawful_bases"] == ["legitimate_interests", "consent"]


class TestRecorded:
    def test_consent_row_has_every_field_and_no_raw_ip(self, client, db_session, growth):
        assert _import(client).status_code == 200
        row = db_session.execute(select(m.LinkedInConsent)).scalar_one()
        assert (row.user_id, row.scope, row.consent_text_version, row.lawful_basis) == (
            growth.id, lc.CONNECTIONS_IMPORT, _version(), "legitimate_interests")
        assert row.purpose and row.granted_at and row.revoked_at is None
        assert row.ip_hash is None or (len(row.ip_hash) == 64 and "." not in row.ip_hash)
        assert client.get("/linkedin-network/status").json()["consent"]["given"] is True

    def test_a_new_text_version_needs_re_consent(self, client, db_session, growth, monkeypatch):
        assert _import(client).status_code == 200
        old = lc.TEXTS[lc.CONNECTIONS_IMPORT]
        monkeypatch.setitem(lc.TEXTS, lc.CONNECTIONS_IMPORT,
                            lc.ConsentText(old.scope, "2099-01-01.1", old.purpose, old.text))
        assert lc.current(db_session, growth.id, lc.CONNECTIONS_IMPORT) is None
        with pytest.raises(lc.ConsentRequired):
            lc.require(db_session, growth.id, lc.CONNECTIONS_IMPORT)

    def test_deleting_the_network_withdraws_it(self, client, db_session, growth):
        assert _import(client).status_code == 200
        out = client.delete("/linkedin-network").json()
        assert out["consents_revoked"] == 1
        assert lc.current(db_session, growth.id, lc.CONNECTIONS_IMPORT) is None


class TestNeverEmail:
    def test_import_creates_no_lead_and_keeps_no_email(self, client, db_session, growth):
        leads_before = _count(db_session, m.Lead)
        assert _import(client).json()["added"] == 2
        assert _count(db_session, m.Lead) == leads_before
        cols = {c.name for c in m.LinkedInConnection.__table__.columns}
        assert not {c for c in cols if "email" in c or "phone" in c}

    def test_duplicates_across_uploads_are_skipped(self, client, db_session, growth):
        assert _import(client).json()["added"] == 2
        again = _import(client).json()
        assert again["added"] == 0 and again["duplicates"] == 2
        assert _count(db_session, m.LinkedInConnection) == 2


class TestSuppression:
    def test_suppressed_people_are_not_recommended(self, client, db_session, growth):
        assert _import(client).status_code == 200
        db_session.add(m.LinkedInSuppression(profile="ana-lopez", reason="unsubscribe"))
        db_session.commit()
        names = {match["full_name"]
                 for p in client.get("/linkedin-network/recommendations?min_score=0").json()[
                     "products"] for match in p["matches"]}
        assert "Ana Lopez" not in names

    def test_no_draft_for_a_suppressed_person_and_no_model_call(
            self, client, db_session, growth, monkeypatch):
        from app.services import anthropic_client  # noqa: PLC0415

        def boom():
            raise AssertionError("model called for a suppressed person")
        monkeypatch.setattr(anthropic_client, "get_client", boom)
        assert _import(client).status_code == 200
        db_session.add(m.LinkedInSuppression(profile="ana-lopez", reason="unsubscribe"))
        db_session.commit()
        conn = db_session.execute(select(m.LinkedInConnection).where(
            m.LinkedInConnection.full_name == "Ana Lopez")).scalar_one()
        product = db_session.execute(select(m.Product)).scalars().first()
        r = client.post("/linkedin-network/messages/draft",
                        json={"connection_id": str(conn.id), "product_id": str(product.id)})
        assert r.status_code == 409
