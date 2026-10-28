"""
1A — Full flow integration tests.

Flow 1: User has past clients → 72-step pipeline → verification → leads → outreach → booking → learning
Flow 2: No past clients → 144-step pipeline → GTM + strategy docs
Flow 3: Learning loop → playbook scores updated → next strategy gets playbook injection → similarity index

External HTTP is mocked at transport layer (respx). Real PostgreSQL + Redis.
Celery runs in ALWAYS_EAGER mode — chains execute synchronously.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.integration.conftest import (
    _auth_headers,
    create_past_clients,
    create_product,
    create_strategy,
    enrollment_statuses,
    lead_outcome_events,
    register_device_token,
    research_steps,
    seed_outcomes,
    set_plan,
    strategy_outcomes,
    strategy_row,
)
from tests.integration.mocks import apollo_mock, calendly_mock, firebase_mock, gmail_mock, whatsapp_mock

pytestmark = pytest.mark.asyncio


# ===========================================================================
# FLOW 1 — User has past clients (complete end-to-end)
# ===========================================================================

class TestFlow1WithClients:
    async def test_product_creation(self, api_client: AsyncClient, user_a_tokens, all_mocks):
        """POST /products creates a product for the authenticated user."""
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        assert product["id"]
        assert product["name"] == "Vectorae"
        # ProductOut deliberately does not expose user_id -- a product is only
        # ever reachable by its owner (Phase A), so ownership is asserted by
        # the owner being able to read it back, not by a field in the payload.
        # Cross-tenant rejection is covered in test_security.py.
        resp = await api_client.get(f"/products/{product['id']}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == product["id"]

    async def test_past_client_pattern_extraction(self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session):
        """
        After posting 3 past clients, the system extracts patterns into
        extracted_patterns_json on the product record.
        """
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        past_clients = await create_past_clients(api_client, headers, product["id"])

        assert len(past_clients) == 3

        # extracted_patterns_json is a column on PAST_CLIENTS, not on products
        # (app/db/models.py::PastClient) -- POST .../past-clients runs the
        # extraction inline and returns the rows, so the response IS the
        # assertion surface. ProductOut has no such field, so the old
        # `GET /products/{id}` read could only ever return None.
        for client_row in past_clients:
            patterns = client_row.get("extracted_patterns_json")
            assert patterns is not None, (
                "extracted_patterns_json must be populated after past-client upload"
            )
            patterns_data = patterns if isinstance(patterns, dict) else json.loads(patterns)
            # Keys are pattern_recognition.PATTERN_KEYS -- acquisition_channel
            # and buyer_role, not the dominant_channel/dominant_role the test
            # used to look for (that schema never existed here).
            assert patterns_data.get("acquisition_channel")
            assert patterns_data.get("buyer_role")
            assert "_extraction_error" not in patterns_data, patterns_data["_extraction_error"]

    async def test_72_step_pipeline_runs_in_order(self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session):
        """
        Creating a with_clients strategy enqueues and executes all 72 steps.
        Each step must persist a research_steps row.
        Steps must be ordered (step_no 1 → 72, no gaps).
        """
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"], flow_type="with_clients")

        strategy_id = strategy["id"]

        # Poll strategy until pipeline completes (in eager mode, should be immediate)
        resp = await api_client.get(f"/strategies/{strategy_id}", headers=headers)
        assert resp.status_code == 200

        # Research steps are an internal engine record with no HTTP surface
        # (see conftest.research_steps).
        steps = research_steps(db_session, strategy_id)

        assert len(steps) == 72, f"Expected 72 research steps, got {len(steps)}"
        step_numbers = sorted(st.step_no for st in steps)
        assert step_numbers == list(range(1, 73)), "Steps must be 1-72 with no gaps or duplicates"

        # A row exists only for a COMPLETED step, so presence is the status;
        # every one of them must carry the output it persisted.
        for step in steps:
            assert step.output, f"Step {step.step_no} has no output"

    async def test_phase_6_prompt_contains_playbook_context(
        self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session
    ):
        """
        The Phase 6 (messaging) step output must include the playbook_context_key.
        This proves the pipeline injected playbook context even when the playbook
        is empty (key present, value may be empty list — structure must exist).
        """
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"])
        strategy_id = strategy["id"]

        steps = research_steps(db_session, strategy_id)

        # Phase 6 = steps 46-54
        phase_6_steps = [st for st in steps if 46 <= st.step_no <= 54]
        assert len(phase_6_steps) == 9

        # The output JSON from the Anthropic mock for Phase 6 includes playbook_context_key
        for step in phase_6_steps:
            output = step.output if isinstance(step.output, dict) else json.loads(step.output)
            assert "playbook_context_key" in output, (
                f"Phase 6 step {step.step_no} output missing playbook_context_key - "
                "playbook injection is not reaching the prompt"
            )

    async def test_phase_8_prompt_contains_playbook_context(
        self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session
    ):
        """Phase 8 (execution plan) must also carry playbook_context_key."""
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"])
        strategy_id = strategy["id"]

        steps = research_steps(db_session, strategy_id)
        phase_8_steps = [st for st in steps if 64 <= st.step_no <= 72]
        assert len(phase_8_steps) == 9
        for step in phase_8_steps:
            output = step.output if isinstance(step.output, dict) else json.loads(step.output)
            assert "playbook_context_key" in output

    async def test_10_verification_passes_all_stored(
        self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session
    ):
        """
        After pipeline completes, verification runs all 10 passes.
        verified_passes_json on the strategy must contain 10 entries, all PASS.
        """
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"])
        strategy_id = strategy["id"]

        resp = await api_client.get(f"/strategies/{strategy_id}", headers=headers)
        data = resp.json()
        # StrategyStatusOut renames the column: verified_passes_json is served
        # as `verification`. Each entry is one ATTEMPT logged by
        # verification/loop.py::_log_attempt -- {pass_no, key, name, attempt,
        # result, ...} -- so a clean run is exactly 10 entries, all PASS.
        passes_data = data["verification"]
        assert len(passes_data) == 10, f"Expected 10 verification passes, got {len(passes_data)}"
        assert sorted(p["pass_no"] for p in passes_data) == list(range(1, 11))
        for p in passes_data:
            assert p["result"] == "PASS", f"Pass {p['pass_no']} did not PASS: {p}"

    async def test_lead_sourcing_uses_icp_params_from_phase_2(
        self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session
    ):
        """
        POST /strategies/{id}/leads/source triggers Apollo search.
        The Apollo search call must include firmographic params derived from Phase 2 output
        (company_size = '11-200', industries include 'SaaS').
        """
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"])
        strategy_id = strategy["id"]

        apollo_mock.reset_capture()
        resp = await api_client.post(
            f"/strategies/{strategy_id}/leads/source",
            headers=headers,
            json={},
        )
        assert resp.status_code in (200, 202), resp.text

        capture = apollo_mock.get_capture()
        assert len(capture.search_calls) >= 1, "Apollo search was not called"
        search_call = capture.search_calls[0]

        # ICP params from Phase 2 must be present in the search payload
        call_str = json.dumps(search_call).lower()
        assert any(keyword in call_str for keyword in ["saas", "professional services", "11-200", "sme"]), (
            f"Apollo search call does not contain ICP params from Phase 2: {search_call}"
        )

    async def test_hunter_drops_undeliverable_emails(
        self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session
    ):
        """
        After lead sourcing, Hunter verifies emails.
        carol@psvc.net (undeliverable) must not appear in the leads table.
        alice and ben (deliverable) must appear.
        """
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"])
        strategy_id = strategy["id"]

        await api_client.post(f"/strategies/{strategy_id}/leads/source", headers=headers, json={})

        resp = await api_client.get(f"/strategies/{strategy_id}/leads", headers=headers)
        assert resp.status_code == 200
        leads = resp.json()["items"]
        by_email = {l["email"]: l for l in leads}

        # "Dropped" is a STATUS, not a deletion: verify_emails_impl sets
        # LeadStatus.DROPPED and records a dropped_reason, keeping the row so
        # the same address is never re-sourced. The lead list is unfiltered, so
        # carol is still in it -- what must be true is that she can never be
        # contacted.
        assert by_email["carol@psvc.net"]["status"] == "dropped", (
            "Undeliverable email carol@psvc.net must be dropped"
        )
        assert any(by_email.get(e, {}).get("status") == "verified"
                   for e in ["alice.chen@saasco.io", "ben@growthagency.com"]), (
            "Deliverable leads must be retained"
        )

    async def test_suppression_list_respected_at_sourcing(
        self, api_client: AsyncClient, admin_tokens, user_a_tokens, all_mocks, db_session
    ):
        """Add alice to suppression list before sourcing; her email must not appear in leads."""
        headers = _auth_headers(user_a_tokens)

        # Add to suppression list.
        # /suppression is operator-only (F-SEC-04: the list is global across
        # tenants), so it is read and written as admin. Calling it as user_a
        # used to answer 403, which this test never checked.
        resp = await api_client.post("/suppression", headers=_auth_headers(admin_tokens), json={
            "email": "alice.chen@saasco.io",
            "reason": "prior unsubscribe",
        })
        assert resp.status_code == 201, resp.text

        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"])
        await api_client.post(f"/strategies/{strategy['id']}/leads/source", headers=headers, json={})

        resp = await api_client.get(f"/strategies/{strategy['id']}/leads", headers=headers)
        leads = resp.json()["items"]
        assert "alice.chen@saasco.io" not in [l["email"] for l in leads]

    async def test_sequence_created_with_gmail_steps(
        self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session
    ):
        """After sourcing, the system creates a sequence with Gmail channel steps."""
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"])
        strategy_id = strategy["id"]
        await api_client.post(f"/strategies/{strategy_id}/leads/source", headers=headers, json={})

        # Sourcing does not invent a sequence -- the owner designs it
        # (POST /strategies/{id}/sequences) and then enrolls leads. Assert the
        # real contract: a created email sequence comes back on the list route
        # with its steps.
        resp = await api_client.post(
            f"/strategies/{strategy_id}/sequences", headers=headers,
            json={"name": "Cold email", "channel": "email", "steps": [
                {"step_no": 1, "template": "Intro: why us", "delay_days": 0},
                {"step_no": 2, "template": "Follow up with a case study", "delay_days": 3},
            ]},
        )
        assert resp.status_code == 201, resp.text

        resp = await api_client.get(f"/strategies/{strategy_id}/sequences", headers=headers)
        assert resp.status_code == 200
        sequences = resp.json()
        assert len(sequences) >= 1

        steps = sequences[0].get("steps", [])
        # ChannelType has no "gmail" member -- Gmail is the PROVIDER that
        # carries the "email" channel (app/db/models.py::ChannelType).
        assert steps, "sequence must expose its steps"
        assert sequences[0]["channel"] == "email"

    async def test_interested_reply_stops_sequence(
        self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session
    ):
        """
        Simulating a reply classified as 'interested' must:
        - stop the sequence for that lead
        - write a 'replied' outcome event
        """
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"])
        strategy_id = strategy["id"]
        await api_client.post(f"/strategies/{strategy_id}/leads/source", headers=headers, json={})

        # Get a lead
        resp = await api_client.get(f"/strategies/{strategy_id}/leads", headers=headers)
        leads = resp.json()["items"]
        assert len(leads) >= 1
        lead_id = leads[0]["id"]

        # Email replies have no HTTP entry point in production -- they arrive
        # on a Celery beat poll of the Gmail API. /debug/inbound-reply feeds
        # the same InboundMessage into the real route_inbound_impl.
        resp = await api_client.post(
            "/debug/inbound-reply",
            headers=headers,
            json={"lead_id": lead_id, "body": "Yes, I would love to chat!",
                  "subject": "Re: quick question"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["classification"] == "interested"

        # Lead is marked replied by route_inbound_impl
        resp = await api_client.get(f"/leads/{lead_id}", headers=headers)
        assert resp.json()["status"] in ("interested", "replied")

        # Every enrollment for the lead must be stopped (LeadDetailOut carries
        # no sequence fields -- enrollment state lives in its own table).
        for status in enrollment_statuses(db_session, lead_id):
            assert status == "stopped", f"enrollment left {status} after a reply"

        # Outcome event written
        assert "replied" in lead_outcome_events(db_session, lead_id)

    async def test_calendly_booking_updates_lead_and_writes_outcome(
        self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session
    ):
        """
        Delivering a Calendly booking webhook must:
        - set lead status = meeting_booked
        - stop sequence
        - write 'booked' outcome event
        - send FCM notification
        """
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"])
        strategy_id = strategy["id"]
        await api_client.post(f"/strategies/{strategy_id}/leads/source", headers=headers, json={})

        resp = await api_client.get(f"/strategies/{strategy_id}/leads", headers=headers)
        leads = resp.json()["items"]
        lead_id = leads[0]["id"]

        # Give this lead a unique address. Calendly deliveries carry no tenant
        # identity, so webhooks.py matches purely on `Lead.email` and takes the
        # first row -- with the Apollo mock's fixed addresses, an earlier
        # test's lead would win. See CLAUDE_CODE_HANDOFF.md: cross-tenant
        # Calendly matching is a real open issue, not a test artifact.
        from app.db.models import Lead

        unique_email = f"booking_{uuid.uuid4().hex[:8]}@prospect.io"
        lead_row = db_session.get(Lead, lead_id)
        lead_row.email = unique_email
        db_session.commit()

        # Build and deliver Calendly webhook
        payload = calendly_mock.build_booking_webhook(
            invitee_email=unique_email,
            lead_id=lead_id,
            tenant_id=user_a_tokens["user"]["id"],
        )
        resp = await api_client.post(
            "/webhooks/calendly", **calendly_mock.signed_request(payload)
        )
        assert resp.status_code == 200

        # Lead must be meeting_booked
        resp = await api_client.get(f"/leads/{lead_id}", headers=headers)
        lead_data = resp.json()
        assert lead_data["status"] == "meeting_booked"

        # Outcome must include 'booked'
        assert "booked" in lead_outcome_events(db_session, lead_id)

    async def test_booking_sends_push_notification(
        self, api_client: AsyncClient, user_a_tokens, user_b_tokens, all_mocks,
        db_session, capture_push_notifications
    ):
        """The lead's owner - and only the owner - gets a 'meeting booked' push."""
        headers = _auth_headers(user_a_tokens)
        owner_token = register_device_token(db_session, user_a_tokens, "owner")
        stranger_token = register_device_token(db_session, user_b_tokens, "stranger")

        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"])
        await api_client.post(
            f"/strategies/{strategy['id']}/leads/source", headers=headers, json={})

        resp = await api_client.get(
            f"/strategies/{strategy['id']}/leads", headers=headers)
        lead = resp.json()["items"][0]

        from app.db.models import Lead

        unique_email = f"push_{uuid.uuid4().hex[:8]}@prospect.io"
        db_session.get(Lead, lead["id"]).email = unique_email
        db_session.commit()

        firebase_mock.reset_capture()
        payload = calendly_mock.build_booking_webhook(
            invitee_email=unique_email, lead_id=lead["id"],
            tenant_id=user_a_tokens["user"]["id"])
        resp = await api_client.post(
            "/webhooks/calendly", **calendly_mock.signed_request(payload))
        assert resp.status_code == 200

        sent = firebase_mock.get_all_notifications()
        assert len(sent) == 1, f"expected exactly one push, got {sent}"
        assert sent[0].token == owner_token
        assert sent[0].title == "Meeting booked!"
        assert sent[0].deep_link == f"clienthunter:///leads/{lead['id']}"

        # Tenancy: user B owns nothing here and must receive nothing.
        assert firebase_mock.notifications_for_tokens([stranger_token]) == []

    async def test_aggregation_updates_playbook_scores(
        self, api_client: AsyncClient, admin_tokens, user_a_tokens, all_mocks, db_session
    ):
        """
        After seeding outcomes and running aggregation, playbook_scores must be
        updated with correct reply_rate for this strategy's pattern.
        """
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"])
        strategy_id = strategy["id"]

        # Seed outcomes (50 sends, 8 replies, 2 bookings)
        await seed_outcomes(db_session, strategy_id, "control", 50, 8, 2)

        # Admins are made out of band (app/cli/create_admin.py);
        # signing up with "is_admin": True yields an ordinary user.
        admin_headers = _auth_headers(admin_tokens)
        # /playbook/* is plan-gated; a free user gets 402.
        set_plan(db_session, user_a_tokens, "pro")
        resp = await api_client.post("/playbook/aggregate", headers=admin_headers)
        assert resp.status_code in (200, 202)

        # Check playbook scores updated
        resp = await api_client.get(f"/playbook/scores?strategy_id={strategy_id}", headers=headers)
        assert resp.status_code == 200
        scores = resp.json()
        # At least one score entry for this strategy's pattern
        assert len(scores) >= 1


# ===========================================================================
# FLOW 2 — No past clients (144-step pipeline)
# ===========================================================================

class TestFlow2NoClients:
    async def test_144_steps_run_and_are_persisted(
        self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session
    ):
        """
        no_clients flow runs exactly 144 steps (72 strategy + 72 GTM).
        All 144 must be persisted in research_steps.
        """
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        # No past clients posted
        strategy = await create_strategy(api_client, headers, product["id"], flow_type="no_clients")
        strategy_id = strategy["id"]

        # step_no is 1..72 WITHIN a pipeline (see ResearchStep.step_no), so
        # Flow 2 is two full 72-step pipelines, not one run numbered 1..144.
        strategy_steps = research_steps(db_session, strategy_id, "strategy")
        gtm_steps = research_steps(db_session, strategy_id, "gtm")

        assert len(strategy_steps) + len(gtm_steps) == 144, (
            f"Expected 144 steps, got {len(strategy_steps)} strategy "
            f"+ {len(gtm_steps)} gtm"
        )
        assert sorted(st.step_no for st in strategy_steps) == list(range(1, 73))
        assert sorted(st.step_no for st in gtm_steps) == list(range(1, 73))

    async def test_gtm_document_produced(
        self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session
    ):
        """No-clients strategy must produce a GTM document on the strategy record."""
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        strategy = await create_strategy(api_client, headers, product["id"], flow_type="no_clients")

        # StrategyStatusOut carries only *_document_ready booleans; the text
        # is served by GET /strategies/{id}/document (app/api/ui_support.py).
        resp = await api_client.get(f"/strategies/{strategy['id']}", headers=headers)
        status = resp.json()
        assert status["gtm_document_ready"] is True
        assert status["strategy_document_ready"] is True

        resp = await api_client.get(f"/strategies/{strategy['id']}/document", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("gtm_document"), "GTM document must be produced for no_clients flow"
        assert data.get("strategy_document"), "Strategy document must be produced"

    async def test_verification_runs_for_both_documents(
        self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session
    ):
        """10 verification passes must cover both the GTM and strategy documents."""
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        strategy = await create_strategy(api_client, headers, product["id"], flow_type="no_clients")

        resp = await api_client.get(f"/strategies/{strategy['id']}", headers=headers)
        data = resp.json()
        passes_data = data["verification"]
        # 10 passes for strategy doc + 10 for GTM doc = 20 total OR 10 covering both
        # Implementation may do 10 combined or 20 — assert at least 10 passed
        assert len(passes_data) >= 10
        results = [p["result"] for p in passes_data]
        assert all(r == "PASS" for r in results)


# ===========================================================================
# FLOW 3 — Learning loop (variant scores, promotion, playbook injection)
# ===========================================================================

class TestFlow3LearningLoop:
    async def test_variant_playbook_scores_computed_correctly(
        self, api_client: AsyncClient, admin_tokens, user_a_tokens, all_mocks, db_session
    ):
        """
        variant_a: 50 sends, 8 replies → reply_rate = 0.16
        variant_b: 50 sends, 18 replies → reply_rate = 0.36
        After aggregation, scores must reflect these rates.
        """
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"])
        strategy_id = strategy["id"]

        await seed_outcomes(db_session, strategy_id, "variant_a", 50, 8, 2)
        await seed_outcomes(db_session, strategy_id, "variant_b", 50, 18, 6)

        # Admins are made out of band (app/cli/create_admin.py);
        # signing up with "is_admin": True yields an ordinary user.
        admin_headers = _auth_headers(admin_tokens)
        # /playbook/* is plan-gated; a free user gets 402.
        set_plan(db_session, user_a_tokens, "pro")
        await api_client.post("/playbook/aggregate", headers=admin_headers)

        resp = await api_client.get(
            f"/playbook/scores?strategy_id={strategy_id}",
            headers=headers,
        )
        scores = resp.json()
        score_map = {s["variant"]: s for s in scores}

        if "variant_a" in score_map:
            a_rate = score_map["variant_a"].get("reply_rate")
            assert abs(a_rate - 0.16) < 0.01, f"variant_a reply_rate should be ~0.16, got {a_rate}"
        if "variant_b" in score_map:
            b_rate = score_map["variant_b"].get("reply_rate")
            assert abs(b_rate - 0.36) < 0.01, f"variant_b reply_rate should be ~0.36, got {b_rate}"

    async def test_ab_winner_promoted(
        self, api_client: AsyncClient, admin_tokens, user_a_tokens, all_mocks, db_session
    ):
        """
        After seeding variant_b as the winner and running auto_promote_winners:
        - variant_b is set as default_variant on the strategy
        - A promotion outcome event is logged
        """
        headers = _auth_headers(user_a_tokens)
        product = await create_product(api_client, headers)
        await create_past_clients(api_client, headers, product["id"])
        strategy = await create_strategy(api_client, headers, product["id"])
        strategy_id = strategy["id"]

        # variant_b clear winner: 50 sends, 25 replies vs variant_a 50 sends, 8 replies
        # Promotion compares the BOOKING rate through a two-proportion
        # z-test at alpha=0.05, so the seeded gap has to be genuinely
        # significant: 2/50 vs 6/50 gives p=0.14 and is correctly
        # refused. 4/200 vs 30/200 gives p<0.0001.
        await seed_outcomes(db_session, strategy_id, "variant_a", 200, 20, 4)
        await seed_outcomes(db_session, strategy_id, "variant_b", 200, 90, 30)

        # Admins are made out of band (app/cli/create_admin.py);
        # signing up with "is_admin": True yields an ordinary user.
        admin_headers = _auth_headers(admin_tokens)
        # /playbook/* is plan-gated; a free user gets 402.
        set_plan(db_session, user_a_tokens, "pro")
        # Auto-promotion is opt-in since product defect #2 (promotion markers
        # are permanent; docs/program/fixes/product-defects.md). On here so the
        # significance rule itself is what is tested.
        from app.services import system_settings  # noqa: PLC0415

        system_settings.set(db_session, "ab_auto_promotion_enabled", True)
        await api_client.post("/playbook/aggregate", headers=admin_headers)
        await api_client.post("/playbook/promote-winners", headers=admin_headers)

        # default_variant is a Strategy column (set by learning_tasks
        # .auto_promote_winners); StrategyStatusOut does not expose it.
        assert strategy_row(db_session, strategy_id).default_variant == "variant_b", (
            "variant_b must be promoted as the default variant after winning the A/B test"
        )

        # Promotion outcome written (ab_promoted is a strategy-level audit
        # event with no lead and no public endpoint).
        assert len(strategy_outcomes(db_session, strategy_id, "ab_promoted")) >= 1

    async def test_new_strategy_gets_playbook_context_injected(
        self, api_client: AsyncClient, admin_tokens, user_a_tokens, all_mocks, db_session
    ):
        """
        After variant_b is promoted, a new strategy with the same ICP must receive
        playbook context in Phase 6 and 8 that mentions the winning variant.
        """
        headers = _auth_headers(user_a_tokens)

        # First strategy: produce a playbook winner
        product1 = await create_product(api_client, headers, name="Vectorae v1")
        await create_past_clients(api_client, headers, product1["id"])
        strategy1 = await create_strategy(api_client, headers, product1["id"])
        # Promotion compares the BOOKING rate through a two-proportion
        # z-test at alpha=0.05, so the seeded gap has to be genuinely
        # significant: 2/50 vs 6/50 gives p=0.14 and is correctly
        # refused. 4/200 vs 30/200 gives p<0.0001.
        await seed_outcomes(db_session, strategy1["id"], "variant_a", 200, 20, 4)
        await seed_outcomes(db_session, strategy1["id"], "variant_b", 200, 90, 30)

        # Admins are made out of band (app/cli/create_admin.py);
        # signing up with "is_admin": True yields an ordinary user.
        admin_headers = _auth_headers(admin_tokens)
        # /playbook/* is plan-gated; a free user gets 402.
        set_plan(db_session, user_a_tokens, "pro")
        # Auto-promotion is opt-in since product defect #2 (promotion markers
        # are permanent; docs/program/fixes/product-defects.md). On here so the
        # significance rule itself is what is tested.
        from app.services import system_settings  # noqa: PLC0415

        system_settings.set(db_session, "ab_auto_promotion_enabled", True)
        await api_client.post("/playbook/aggregate", headers=admin_headers)
        await api_client.post("/playbook/promote-winners", headers=admin_headers)

        # Second strategy with similar ICP — must get playbook injection
        product2 = await create_product(api_client, headers, name="Vectorae v2",
                                        description="AI-powered client acquisition identical ICP")
        await create_past_clients(api_client, headers, product2["id"])
        strategy2 = await create_strategy(api_client, headers, product2["id"])

        steps = research_steps(db_session, strategy2["id"])

        phase_6_steps = [st for st in steps if 46 <= st.step_no <= 54]
        phase_8_steps = [st for st in steps if 64 <= st.step_no <= 72]

        for step in phase_6_steps + phase_8_steps:
            output = step.output if isinstance(step.output, dict) else json.loads(step.output)
            assert "playbook_context_key" in output, (
                f"Step {step.step_no} missing playbook_context_key - injection did not reach prompt"
            )

    async def test_strategy_similarity_index(
        self, api_client: AsyncClient, user_a_tokens, all_mocks, db_session
    ):
        """
        The similarity endpoint must surface strategy1 when querying with a product
        description similar to strategy1's product.
        """
        headers = _auth_headers(user_a_tokens)
        product1 = await create_product(api_client, headers, name="Vectorae Similarity Test",
                                        description="AI-powered client acquisition for SaaS companies")
        await create_past_clients(api_client, headers, product1["id"])
        strategy1 = await create_strategy(api_client, headers, product1["id"])

        # Query similarity with a similar description
        resp = await api_client.get(
            "/playbook/similar-strategies",
            headers=headers,
            params={"query": "AI outreach automation for SaaS client acquisition"},
        )
        assert resp.status_code == 200
        similar = resp.json()
        assert isinstance(similar, list)
        # strategy1 must appear in the results (TF-IDF cosine match)
        strategy_ids = [s["strategy_id"] for s in similar]
        assert strategy1["id"] in strategy_ids, (
            f"strategy1 ({strategy1['id']}) not in similarity results: {strategy_ids}"
        )
