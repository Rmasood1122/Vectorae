"""Private LinkedIn network: import, segment, recommend (Growth plan and up).

Hard rules this file pins (zero-tolerance compliance posture):
  - Every connection belongs to the account that uploaded it. It is NEVER shown
    to another account, whatever that account sells and whatever its plan.
    (A shared pool of third parties' personal data was explicitly NOT built.)
  - Email addresses in the export are never stored.
  - Growth, Scale, Pro and Enterprise only; Free, Starter and Pay-per-meeting
    get 402 with the standard upgrade body. Erasing your own data is never gated.
  - The weekly refresh re-scores stored data for eligible accounts only, and
    never calls a model (no spend).
Offline.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.core.plans import PLANS
from app.db import models as m
from app.services import linkedin_network as ln
from app.services import security_audit

CSV = (
    "Notes:\n"
    '"When exporting your connection data, you may notice that some of the email '
    'addresses are missing due to privacy settings."\n'
    "\n"
    "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
    "Ana,Lopez,https://www.linkedin.com/in/ana-lopez,ana@acme-fire.com,"
    "Acme Fire Protection,Owner,05 Oct 2026\n"
    "Ben,Ng,https://www.linkedin.com/in/ben-ng,,Pine Dental,Dental Hygienist,01 Sep 2025\n"
    "Cara,Diaz,https://www.linkedin.com/in/cara-diaz,,Summit Fire & Safety,"
    "VP of Operations,12 Jan 2024\n"
    ",,https://www.linkedin.com/in/nameless,,Ghost Co,CEO,01 Jan 2024\n"
)


def _user(db, email, plan):
    u = m.User(email=email, email_verified=True, plan=plan)
    db.add(u)
    db.commit()
    return u


def _product(db, user, name="SEO System X",
             description="Local SEO audits for fire protection companies"):
    p = m.Product(user=user, name=name, description=description,
                  type=m.ProductType.SKILL)
    db.add(p)
    db.commit()
    return p


def _set_plan(db, user, plan):
    user.plan = plan
    db.commit()


def _import(client, text=CSV):
    # L3 attestation is required since evals/vectorae Phase 5B; its own rules
    # are pinned in tests/test_linkedin_l3_attestation.py.
    from app.services import linkedin_consent  # noqa: PLC0415

    version = linkedin_consent.current_text(linkedin_consent.CONNECTIONS_IMPORT).version
    return client.post("/linkedin-network/import",
                       json={"csv_text": text, "consent_text_version": version,
                             "lawful_basis": "legitimate_interests"})


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


class TestParse:
    def test_skips_the_notes_preamble_and_nameless_rows(self):
        r = ln.parse_connections_csv(CSV)
        assert [c.full_name for c in r.rows] == ["Ana Lopez", "Ben Ng", "Cara Diaz"]
        assert r.skipped == 1

    def test_reads_company_position_and_date(self):
        ana = ln.parse_connections_csv(CSV).rows[0]
        assert ana.company == "Acme Fire Protection"
        assert ana.position == "Owner"
        assert ana.connected_on == date(2026, 10, 5)
        assert ana.profile_url == "https://www.linkedin.com/in/ana-lopez"

    def test_email_addresses_are_never_kept(self):
        rows = ln.parse_connections_csv(CSV).rows
        for r in rows:
            assert not hasattr(r, "email")
        assert "ana@acme-fire.com" not in repr(rows)

    def test_duplicate_rows_in_one_file_collapse(self):
        text = CSV + CSV.split("Connected On\n", 1)[1]
        assert len(ln.parse_connections_csv(text).rows) == 3

    def test_a_file_without_the_linkedin_header_is_rejected(self):
        with pytest.raises(ln.NetworkImportError):
            ln.parse_connections_csv("name,mail\nA,b@c.d\n")

    def test_too_many_rows_is_rejected(self, monkeypatch):
        monkeypatch.setattr(ln, "MAX_ROWS", 2)
        with pytest.raises(ln.NetworkImportError):
            ln.parse_connections_csv(CSV)


# --------------------------------------------------------------------------
# Segmentation and matching
# --------------------------------------------------------------------------


class TestSegments:
    @pytest.mark.parametrize("title,seniority,function", [
        ("Owner", "Founder / owner", "Leadership"),
        ("Chief Executive Officer", "C-level", "Leadership"),
        ("VP of Operations", "VP / Director", "Operations"),
        ("Head of Marketing", "VP / Director", "Marketing"),
        ("Sales Manager", "Manager", "Sales"),
        ("Dental Hygienist", "Individual contributor", "Other"),
        (None, "Unknown", "Other"),
    ])
    def test_classifies_title(self, title, seniority, function):
        assert ln.seniority(title) == seniority
        assert ln.function_of(title) == function


class TestMatching:
    def test_relevant_connections_outrank_irrelevant_ones(self, db_session, test_user):
        product = _product(db_session, test_user)
        ln.import_connections(db_session, test_user, ln.parse_connections_csv(CSV))
        ln.analyze_user(db_session, test_user)
        matches = db_session.execute(
            select(m.LinkedInConnectionMatch).where(
                m.LinkedInConnectionMatch.user_id == test_user.id)
        ).scalars().all()
        by_name = {
            db_session.get(m.LinkedInConnection, x.connection_id).full_name: x
            for x in matches
        }
        assert "Ana Lopez" in by_name and "Cara Diaz" in by_name
        assert "Ben Ng" not in by_name           # dental hygienist: not a fit
        assert by_name["Ana Lopez"].score >= by_name["Cara Diaz"].score
        assert all(x.product_id == product.id for x in matches)
        assert all(x.reasons_json for x in matches)

    def test_uses_the_icp_the_strategy_already_extracted(self, db_session, test_user,
                                                         product_with_strategy):
        product, strategy = product_with_strategy(m.FlowType.WITH_CLIENTS)
        db_session.add(m.LeadBatch(
            strategy_id=strategy.id, requested_leads=5,
            icp_criteria_json={"titles": ["Dental Hygienist"],
                               "industries": ["dental"], "keywords": ["dental"],
                               "locations": [], "company_size_ranges": []}))
        db_session.commit()
        ln.import_connections(db_session, test_user, ln.parse_connections_csv(CSV))
        ln.analyze_user(db_session, test_user)
        names = {
            db_session.get(m.LinkedInConnection, x.connection_id).full_name
            for x in db_session.execute(select(m.LinkedInConnectionMatch)).scalars()
        }
        assert "Ben Ng" in names

    def test_analysis_makes_no_model_call(self, db_session, test_user, fake_claude):
        _product(db_session, test_user)
        ln.import_connections(db_session, test_user, ln.parse_connections_csv(CSV))
        before = len(getattr(fake_claude, "calls", []))
        ln.analyze_user(db_session, test_user)
        assert len(getattr(fake_claude, "calls", [])) == before

    def test_reanalysis_replaces_rather_than_duplicates(self, db_session, test_user):
        _product(db_session, test_user)
        ln.import_connections(db_session, test_user, ln.parse_connections_csv(CSV))
        ln.analyze_user(db_session, test_user)
        first = db_session.execute(select(m.LinkedInConnectionMatch)).scalars().all()
        ln.analyze_user(db_session, test_user)
        second = db_session.execute(select(m.LinkedInConnectionMatch)).scalars().all()
        assert len(first) == len(second) > 0


# --------------------------------------------------------------------------
# Plan gate
# --------------------------------------------------------------------------


class TestPlanGate:
    @pytest.mark.parametrize("plan", [m.PlanTier.FREE, m.PlanTier.STARTER,
                                      m.PlanTier.PAY_PER_MEETING])
    def test_lower_plans_get_402_everywhere(self, client, db_session, test_user, plan):
        _set_plan(db_session, test_user, plan)
        for call in (
            lambda: _import(client),
            lambda: client.post("/linkedin-network/analyze"),
            lambda: client.get("/linkedin-network/recommendations"),
        ):
            r = call()
            assert r.status_code == 402, r.text
            body = r.json()["detail"]
            assert body["error"] == "plan_limit_exceeded"
            assert body["limit"] == "linkedin_network"
        assert db_session.execute(select(m.LinkedInConnection)).first() is None

    @pytest.mark.parametrize("plan", [m.PlanTier.GROWTH, m.PlanTier.SCALE,
                                      m.PlanTier.PRO, m.PlanTier.ENTERPRISE])
    def test_growth_and_higher_can_import(self, client, db_session, test_user, plan):
        _set_plan(db_session, test_user, plan)
        r = _import(client)
        assert r.status_code == 200, r.text
        assert r.json()["added"] == 3

    def test_the_plan_table_says_the_same(self):
        allowed = {k for k, v in PLANS.items() if v.get("linkedin_network")}
        assert allowed == {"growth", "scale", "pro", "enterprise"}

    def test_status_is_readable_on_free_so_the_page_can_explain_the_upgrade(
            self, client):
        r = client.get("/linkedin-network/status")
        assert r.status_code == 200
        body = r.json()
        assert body["eligible"] is False
        assert body["upgrade_to"] == "growth"
        assert body["connection_count"] == 0


# --------------------------------------------------------------------------
# API behaviour
# --------------------------------------------------------------------------


class TestImportApi:
    def test_import_stores_connections_and_recommends(self, client, db_session,
                                                      test_user):
        _set_plan(db_session, test_user, m.PlanTier.GROWTH)
        _product(db_session, test_user)
        r = _import(client)
        assert r.status_code == 200, r.text
        assert r.json() == {**r.json(), "added": 3, "skipped": 1, "total": 3}
        rec = client.get("/linkedin-network/recommendations").json()
        assert rec["products"][0]["product_name"] == "SEO System X"
        names = [x["full_name"] for x in rec["products"][0]["matches"]]
        assert names[0] == "Ana Lopez"
        assert "Ben Ng" not in names
        assert rec["products"][0]["segments"]

    def test_importing_the_same_file_twice_adds_nothing(self, client, db_session,
                                                       test_user):
        _set_plan(db_session, test_user, m.PlanTier.GROWTH)
        _import(client)
        again = _import(client).json()
        assert again["added"] == 0 and again["total"] == 3

    def test_oversized_upload_is_refused(self, client, db_session, test_user,
                                         monkeypatch):
        _set_plan(db_session, test_user, m.PlanTier.GROWTH)
        monkeypatch.setattr(ln, "MAX_BYTES", 100)
        assert _import(client).status_code == 413

    def test_a_file_that_is_not_a_linkedin_export_is_422(self, client, db_session,
                                                         test_user):
        _set_plan(db_session, test_user, m.PlanTier.GROWTH)
        assert _import(client, "a,b\n1,2\n").status_code == 422

    def test_the_import_is_audited_without_the_contents(self, client, db_session,
                                                        test_user):
        _set_plan(db_session, test_user, m.PlanTier.GROWTH)
        _import(client)
        ev = db_session.execute(select(m.SecurityAuditEvent).where(
            m.SecurityAuditEvent.action == security_audit.LINKEDIN_NETWORK_IMPORTED
        )).scalars().all()
        assert len(ev) == 1
        assert "ana@acme-fire.com" not in repr(ev[0].details_json)
        assert "Ana" not in repr(ev[0].details_json)


class TestPrivacy:
    def test_one_accounts_connections_never_reach_another(self, client, db_session,
                                                          test_user):
        """The exact scenario that was asked for and NOT built: account A uploads,
        account B (Growth, relevant product) must see nothing of A's."""
        uploader = _user(db_session, "uploader@x.test", m.PlanTier.GROWTH)
        _product(db_session, uploader)
        ln.import_connections(db_session, uploader, ln.parse_connections_csv(CSV))
        ln.analyze_user(db_session, uploader)

        _set_plan(db_session, test_user, m.PlanTier.GROWTH)
        _product(db_session, test_user)             # same kind of product
        ln.analyze_user(db_session, test_user)
        rec = client.get("/linkedin-network/recommendations").json()
        assert all(p["matches"] == [] for p in rec["products"])
        assert client.get("/linkedin-network/status").json()["connection_count"] == 0

    def test_matches_are_only_made_against_the_owners_own_products(self, db_session,
                                                                   test_user):
        other = _user(db_session, "o@x.test", m.PlanTier.GROWTH)
        _product(db_session, other)
        ln.import_connections(db_session, test_user, ln.parse_connections_csv(CSV))
        ln.analyze_user(db_session, test_user)       # test_user has no products
        assert db_session.execute(select(m.LinkedInConnectionMatch)).first() is None

    def test_erase_removes_only_my_data_and_is_not_plan_gated(self, client, db_session,
                                                              test_user):
        other = _user(db_session, "keep@x.test", m.PlanTier.GROWTH)
        _product(db_session, other)
        ln.import_connections(db_session, other, ln.parse_connections_csv(CSV))
        ln.analyze_user(db_session, other)

        _set_plan(db_session, test_user, m.PlanTier.GROWTH)
        _product(db_session, test_user)
        _import(client)
        _set_plan(db_session, test_user, m.PlanTier.FREE)   # downgraded
        r = client.delete("/linkedin-network")
        assert r.status_code == 200, r.text
        assert r.json()["deleted_connections"] == 3
        mine = db_session.execute(select(m.LinkedInConnection).where(
            m.LinkedInConnection.user_id == test_user.id)).first()
        theirs = db_session.execute(select(m.LinkedInConnection).where(
            m.LinkedInConnection.user_id == other.id)).all()
        assert mine is None and len(theirs) == 3
        assert db_session.execute(select(m.SecurityAuditEvent).where(
            m.SecurityAuditEvent.action == security_audit.LINKEDIN_NETWORK_ERASED
        )).scalars().first() is not None


# --------------------------------------------------------------------------
# Weekly refresh
# --------------------------------------------------------------------------


class TestWeeklyRefresh:
    def test_refreshes_eligible_accounts_only(self, db_session, test_user):
        growth = _user(db_session, "g@x.test", m.PlanTier.GROWTH)
        free = _user(db_session, "f@x.test", m.PlanTier.FREE)
        for u in (growth, free):
            _product(db_session, u)
            ln.import_connections(db_session, u, ln.parse_connections_csv(CSV))
        out = ln.refresh_all(db_session)
        assert out == {"users": 1, "skipped_ineligible": 1}
        assert db_session.execute(select(m.LinkedInConnectionMatch).where(
            m.LinkedInConnectionMatch.user_id == growth.id)).first() is not None
        assert db_session.execute(select(m.LinkedInConnectionMatch).where(
            m.LinkedInConnectionMatch.user_id == free.id)).first() is None

    def test_beat_runs_it_weekly(self):
        from app.workers.celery_app import celery_app
        entry = celery_app.conf.beat_schedule["linkedin-network-weekly-refresh"]
        assert entry["task"] == "leadpilot.network.weekly_refresh"
        sched = entry["schedule"]
        assert sched.day_of_week == {1}          # Monday only: weekly, not daily
        assert sched.hour == {4} and sched.minute == {10}

    def test_status_says_when_a_fresh_export_is_needed(self, client, db_session,
                                                       test_user):
        """LinkedIn offers no API for connections: the weekly job can re-score what
        is stored, but only a new upload brings new people. Say so."""
        _set_plan(db_session, test_user, m.PlanTier.GROWTH)
        assert client.get("/linkedin-network/status").json()["needs_new_export"] is True
        _import(client)
        assert client.get("/linkedin-network/status").json()["needs_new_export"] is False
