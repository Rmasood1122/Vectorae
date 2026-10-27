"""F07 -- Spend Guard + Cost Forecast (docs/features/f07-spend-guard.md).

Offline: SQLite, FakeClaude, httpx.MockTransport for the adapter path (no
socket), the in-memory mail transport. Covers the flag-off contract, the
forecast model, the pre-run refusal, the per-call AI and data gates, the
50/80/100% alerts (idempotent), reconciliation (the +/-25% metric), the API,
tenancy and the no-send-path guarantee.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.config import settings
from app.db import models as m
from app.pipeline import budget_plan
from app.services import spend_guard as sg
from app.services import system_settings, usage_meter
from tests.conftest import auth_headers

T0 = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _mid_month() -> datetime:
    """A moment inside the CURRENT UTC month and in the past (for rows the
    API reads with the real clock)."""
    now = datetime.now(timezone.utc)
    start = sg.month_start(now)
    return start + (now - start) / 2


def usage_row(db, user, micros, *, purpose="pipeline", strategy=None, ts=None,
              calls=1, tin=0, tout=0):
    row = m.ApiUsage(user_id=user.id, strategy_id=getattr(strategy, "id", strategy),
                     provider="anthropic", model="fake", purpose=purpose, calls=calls,
                     input_tokens=tin, output_tokens=tout, cost_micros=int(micros),
                     ts=ts or _mid_month())
    db.add(row)
    db.commit()
    return row


def attempt_row(db, user, provider, units, *, batch=None, strategy=None, ts=None,
                step="spend_guard", operation="search", outcome="ok"):
    at = ts or _mid_month()
    row = m.EnrichmentAttempt(user_id=user.id, strategy_id=getattr(strategy, "id", strategy),
                              batch_id=getattr(batch, "id", batch), step=step,
                              provider=provider, operation=operation, outcome=outcome,
                              cached=False, cost_units=units, created_at=at, updated_at=at)
    db.add(row)
    db.commit()
    return row


def make_user(db, email="other@spend.dev"):
    user = m.User(email=email, email_verified=True)
    db.add(user)
    db.commit()
    return user


def make_strategy(db, user, *, status=m.StrategyStatus.VERIFIED, flow=m.FlowType.WITH_CLIENTS):
    product = m.Product(user=user, name="P", description="d", type=m.ProductType.PRODUCT)
    db.add(product)
    db.flush()
    strategy = m.Strategy(product_id=product.id, flow_type=flow, status=status)
    db.add(strategy)
    db.commit()
    return strategy


def make_batch(db, strategy, *, requested=10, stage=m.BatchStage.SOURCING, verifier="hunter",
               summary=None):
    batch = m.LeadBatch(strategy_id=strategy.id, requested_leads=requested,
                        icp_criteria_json={"titles": ["Owner"]}, source_provider="apollo",
                        verifier_provider=verifier, stage=stage, summary_json=summary)
    db.add(batch)
    db.commit()
    return batch


@pytest.fixture()
def on(db_session):
    system_settings.set(db_session, sg.FLAG, True)
    return db_session


def cap(db, user, usd, email_alerts=True):
    return sg.set_cap(db, user.id, cap_usd=usd, email_alerts=email_alerts)


# --------------------------------------------------------------------------
# Flag
# --------------------------------------------------------------------------


class TestFlag:
    def test_declared_off_and_prices_default_to_zero(self):
        assert system_settings.DEFAULTS[sg.FLAG][0] is False
        for key in sg.PRICE_SETTINGS.values():
            assert system_settings.DEFAULTS[key][0] == 0.0

    def test_flag_off_changes_nothing(self, client, db_session, test_user, enqueued):
        # A cap that is already exceeded -- with the flag off it must not matter.
        cap(db_session, test_user, 1.0)
        usage_row(db_session, test_user, 5_000_000)
        product = m.Product(user=test_user, name="P", description="d",
                            type=m.ProductType.PRODUCT)
        db_session.add(product)
        db_session.commit()
        r = client.post(f"/products/{product.id}/strategies", json={"flow_type": "no_clients"})
        assert r.status_code == 202, r.text
        assert db_session.query(m.SpendForecast).count() == 0
        assert client.get("/spend-guard/usage").status_code == 409
        assert client.put("/spend-guard/cap", json={"monthly_cap_usd": 5}).status_code == 409
        assert client.post("/spend-guard/forecast",
                           json={"kind": "strategy_run", "flow_type": "no_clients"}
                           ).status_code == 409
        with usage_meter.scope(db_session, user_id=test_user.id, purpose="pipeline") as active:
            assert active.monthly_guard is None
        assert sg.evaluate_alerts(db_session, test_user.id) == []
        assert sg.sweep_alerts(db_session) == 0

    def test_flag_off_data_hook_is_inert(self, db_session, test_user, fake_redis):
        strategy = make_strategy(db_session, test_user)
        batch = make_batch(db_session, strategy)
        adapter, seen = _apollo(fake_redis)
        with sg.batch_scope(db_session, batch.id) as guard:
            assert guard is None
            adapter.search({"titles": ["Owner"]}, 3)
        assert len(seen) == 1
        assert db_session.query(m.EnrichmentAttempt).count() == 0


# --------------------------------------------------------------------------
# Forecast model
# --------------------------------------------------------------------------


class TestForecast:
    def test_strategy_run_is_budget_plans_own_numbers(self, db_session):
        prices = usage_meter.price_table(db_session)
        f = sg.forecast_strategy_run(db_session, flow_type=m.FlowType.WITH_CLIENTS,
                                     steps_per_phase=None)
        reserve = int(round(settings.pipeline_verification_reserve_usd * 1_000_000))
        assert f.ai_micros == 72 * budget_plan.estimate_step_micros(prices) + reserve
        assert f.ai_input_tokens == 72 * settings.pipeline_est_step_input_tokens
        assert f.ai_output_tokens == 72 * settings.pipeline_est_step_output_tokens
        assert f.ai_purposes == ("pipeline", "verification")
        # competitor_intel_enabled defaults on: one Apollo organization search.
        assert f.data_units == {"apollo": 1}
        two = sg.forecast_strategy_run(db_session, flow_type=m.FlowType.NO_CLIENTS,
                                       steps_per_phase=None)
        assert two.ai_input_tokens == 2 * f.ai_input_tokens

    def test_a_smaller_plan_forecasts_fewer_steps(self, db_session):
        f = sg.forecast_strategy_run(db_session, flow_type=m.FlowType.WITH_CLIENTS,
                                     steps_per_phase=3)
        assert f.detail["steps_to_run"] == len(budget_plan.planned_step_nos(3)) == 24

    def test_resume_counts_only_undone_steps(self, db_session):
        f = sg.forecast_strategy_run(db_session, flow_type=m.FlowType.WITH_CLIENTS,
                                     steps_per_phase=None, done_steps=70,
                                     market_intel_pending=False)
        assert f.detail["steps_to_run"] == 2
        assert f.data_units == {}

    def test_reverify_is_the_verification_reserve_only(self, db_session):
        f = sg.forecast_strategy_run(db_session, flow_type=m.FlowType.WITH_CLIENTS,
                                     steps_per_phase=None, include_pipeline=False)
        assert f.ai_micros == int(settings.pipeline_verification_reserve_usd * 1_000_000)
        assert f.ai_purposes == ("verification",)
        assert f.data_units == {}

    def test_enrichment_defaults_and_lead_scoring(self, db_session, test_user):
        f = sg.forecast_enrichment_run(db_session, test_user.id, 50)
        assert f.data_units == {"apollo": 100, "hunter": 75}
        calls = math.ceil(50 / 15)
        assert f.ai_input_tokens == calls * sg.LEAD_SCORING_DEFAULT_TOKENS[0]
        assert f.ai_output_tokens == calls * sg.LEAD_SCORING_DEFAULT_TOKENS[1]
        price_in, price_out = usage_meter.price_table(db_session)["anthropic"]
        assert f.ai_micros == int(round(f.ai_input_tokens * price_in
                                        + f.ai_output_tokens * price_out))
        assert f.data_micros == 0                     # prices default to 0
        out = f.to_dict()
        assert out["sends"]["count"] == 0 and "sends nothing" in out["sends"]["note"]

    def test_lead_scoring_off_means_no_ai_line(self, db_session, test_user):
        system_settings.set(db_session, "lead_scoring_enabled", False)
        f = sg.forecast_enrichment_run(db_session, test_user.id, 50)
        assert f.ai_micros == 0 and f.ai_input_tokens == 0

    def test_prices_turn_credits_into_dollars(self, db_session, test_user):
        system_settings.set(db_session, "spend_guard_apollo_usd_per_credit", 0.02)
        system_settings.set(db_session, "spend_guard_hunter_usd_per_credit", 0.05)
        f = sg.forecast_enrichment_run(db_session, test_user.id, 10)
        assert f.data_micros == 20 * 20_000 + 15 * 50_000

    def test_credits_per_lead_calibrate_from_own_finished_batches(self, db_session,
                                                                   test_user):
        strategy = make_strategy(db_session, test_user)
        other = make_user(db_session)
        foreign = make_batch(db_session, make_strategy(db_session, other),
                             stage=m.BatchStage.FINALIZED)
        attempt_row(db_session, other, "apollo", 9999, batch=foreign)
        for _ in range(3):
            batch = make_batch(db_session, strategy, requested=10,
                               stage=m.BatchStage.FINALIZED)
            decision = sg.evaluate(db_session, test_user.id,
                                   sg.forecast_enrichment_run(db_session, test_user.id, 10))
            sg.record(db_session, test_user.id, decision, strategy_id=strategy.id,
                      batch_id=batch.id)
            attempt_row(db_session, test_user, "apollo", 12, batch=batch)
            attempt_row(db_session, test_user, "hunter", 6, batch=batch, step="hunter",
                        operation="verify", outcome="valid")
        rates, basis = sg.calibrate_credits(db_session, test_user.id)
        assert rates == {"apollo": 1.2, "hunter": 0.6}
        assert "calibrated" in basis
        f = sg.forecast_enrichment_run(db_session, test_user.id, 20)
        assert f.data_units == {"apollo": 24, "hunter": 12}

    def test_lead_scoring_tokens_calibrate_from_own_usage(self, db_session, test_user):
        usage_row(db_session, test_user, 10, purpose="lead_scoring", calls=4, tin=8000,
                  tout=2000, ts=datetime.now(timezone.utc) - timedelta(days=1))
        (tin, tout), basis = sg.lead_scoring_tokens(db_session, test_user.id)
        assert (tin, tout) == (2000, 500) and "average" in basis

    def test_quickstart_adds_pipeline_sourcing_and_its_own_budget(self, db_session,
                                                                   test_user):
        f = sg.forecast_quickstart(db_session, test_user.id, steps_per_phase=None,
                                   requested_leads=25, budget_micros=1_000_000)
        pipe = sg.forecast_strategy_run(db_session, flow_type=m.FlowType.NO_CLIENTS,
                                        steps_per_phase=None)
        src = sg.forecast_enrichment_run(db_session, test_user.id, 25)
        assert f.ai_micros == pipe.ai_micros + src.ai_micros + 1_000_000
        assert f.data_units == {"apollo": 1 + 50, "hunter": 38}
        assert set(f.ai_purposes) == {"pipeline", "verification", "lead_scoring",
                                      "quickstart"}


# --------------------------------------------------------------------------
# Pre-run refusal through the API
# --------------------------------------------------------------------------


@pytest.fixture()
def product(db_session, test_user):
    product = m.Product(user=test_user, name="P", description="d",
                        type=m.ProductType.PRODUCT)
    db_session.add(product)
    db_session.commit()
    return product


class TestPreCheck:
    def test_new_strategy_refused_over_the_cap_creates_nothing(self, client, on, test_user,
                                                               product, enqueued):
        cap(on, test_user, 5.0)
        usage_row(on, test_user, 4_500_000)
        r = client.post(f"/products/{product.id}/strategies", json={"flow_type": "no_clients"})
        assert r.status_code == 429, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "monthly_spend_cap_reached"
        assert detail["remaining_usd"] == 0.5 and detail["cap_usd"] == 5.0
        assert "Nothing was started" in detail["message"]
        assert on.query(m.Strategy).count() == 0
        assert enqueued == []
        rows = on.query(m.SpendForecast).all()
        assert [r.decision for r in rows] == ["refused"]

    def test_new_strategy_under_the_cap_is_allowed_and_linked(self, client, on, test_user,
                                                              product, enqueued):
        cap(on, test_user, 1000.0)
        r = client.post(f"/products/{product.id}/strategies", json={"flow_type": "no_clients"})
        assert r.status_code == 202, r.text
        row = on.query(m.SpendForecast).one()
        assert row.decision == "allowed" and row.kind == "strategy_run"
        assert str(row.strategy_id) == r.json()["id"]
        assert len(enqueued) == 1

    def test_no_cap_still_forecasts_and_allows(self, client, on, product):
        r = client.post(f"/products/{product.id}/strategies", json={"flow_type": "no_clients"})
        assert r.status_code == 202
        assert on.query(m.SpendForecast).one().cap_micros is None

    def test_a_failed_request_leaves_no_allowed_forecast(self, client, on, product):
        r = client.post(f"/products/{product.id}/strategies",
                        json={"flow_type": "no_clients", "max_budget_usd": 0.6})
        assert r.status_code == 422          # budget too small for any plan
        assert on.query(m.SpendForecast).count() == 0

    def test_lead_sourcing_refused_creates_no_batch(self, leads_client, on, test_user,
                                                    verified_strategy, chain_calls):
        cap(on, test_user, 2.0)
        system_settings.set(on, "spend_guard_apollo_usd_per_credit", 0.01)
        usage_row(on, test_user, 1_900_000)
        r = leads_client.post(f"/strategies/{verified_strategy.id}/leads/source",
                              json={"max_leads": 50, "icp_criteria": {"titles": ["Owner"]}})
        assert r.status_code == 429, r.text
        assert on.query(m.LeadBatch).count() == 0
        assert chain_calls == []
        row = on.query(m.SpendForecast).one()
        assert row.kind == "enrichment_run" and row.decision == "refused"
        assert row.requested_leads == 50

    def test_lead_sourcing_allowed_links_the_batch(self, leads_client, on, test_user,
                                                   verified_strategy, chain_calls):
        cap(on, test_user, 100.0)
        r = leads_client.post(f"/strategies/{verified_strategy.id}/leads/source",
                              json={"max_leads": 20, "icp_criteria": {"titles": ["Owner"]}})
        assert r.status_code == 202, r.text
        row = on.query(m.SpendForecast).one()
        assert str(row.batch_id) == r.json()["id"]
        assert row.strategy_id == verified_strategy.id
        assert len(chain_calls) == 1

    def test_refused_resume_changes_nothing(self, client, on, test_user, enqueued,
                                            product_with_strategy):
        _, strategy = product_with_strategy(m.FlowType.WITH_CLIENTS)
        strategy.status = m.StrategyStatus.BUDGET_EXHAUSTED
        strategy.budget_micros = 5_000_000
        on.commit()
        cap(on, test_user, 3.0)
        usage_row(on, test_user, 2_990_000)
        r = client.post(f"/strategies/{strategy.id}/resume", json={"max_budget_usd": 20})
        assert r.status_code == 429, r.text
        on.refresh(strategy)
        assert strategy.budget_micros == 5_000_000
        assert strategy.status is m.StrategyStatus.BUDGET_EXHAUSTED
        assert enqueued == []

    def test_refused_reverify_changes_nothing(self, client, on, test_user,
                                              product_with_strategy):
        _, strategy = product_with_strategy(m.FlowType.WITH_CLIENTS)
        strategy.status = m.StrategyStatus.NEEDS_HUMAN_REVIEW
        on.commit()
        cap(on, test_user, 1.0)
        usage_row(on, test_user, 900_000)
        r = client.post(f"/strategies/{strategy.id}/reverify")
        assert r.status_code == 429
        on.refresh(strategy)
        assert strategy.status is m.StrategyStatus.NEEDS_HUMAN_REVIEW

    def test_quickstart_refused_creates_no_rows(self, client, on, test_user, monkeypatch):
        system_settings.set(on, "quickstart_enabled", True)
        queued = []
        monkeypatch.setattr("app.workers.quickstart_tasks.enqueue",
                            lambda run_id: queued.append(run_id) or True)
        cap(on, test_user, 2.0)
        usage_row(on, test_user, 1_500_000)
        r = client.post("/quickstart", json={
            "website_url": "https://acme.example/",
            "offer": "Automated NFPA inspection reports for fire contractors"})
        assert r.status_code == 429, r.text
        assert on.query(m.QuickstartRun).count() == 0
        assert on.query(m.Product).count() == 0
        assert queued == []
        assert on.query(m.SpendForecast).one().kind == "quickstart"

    def test_another_accounts_spend_never_counts(self, client, on, test_user, product):
        cap(on, test_user, 20.0)
        other = make_user(on)
        usage_row(on, other, 500_000_000)
        attempt_row(on, other, "apollo", 100_000)
        r = client.post(f"/products/{product.id}/strategies", json={"flow_type": "no_clients"})
        assert r.status_code == 202, r.text


# --------------------------------------------------------------------------
# Per-call AI gate (usage_meter.check_budget)
# --------------------------------------------------------------------------


class TestModelGate:
    def test_refused_before_the_call_near_the_cap(self, on, test_user):
        cap(on, test_user, 1.0)
        usage_row(on, test_user, 990_000)
        with usage_meter.scope(on, user_id=test_user.id, purpose="pipeline") as active:
            assert active.monthly_guard is not None
            with pytest.raises(sg.MonthlySpendCapReached) as exc:
                usage_meter.check_budget(input_chars=30_000, max_output_tokens=4_000)
        assert isinstance(exc.value, usage_meter.BudgetExceededError)
        assert sg.STOP_PREFIX in str(exc.value)

    def test_spend_inside_the_scope_counts(self, on, test_user):
        cap(on, test_user, 1.0)
        with usage_meter.scope(on, user_id=test_user.id, purpose="pipeline"):
            usage_meter.check_budget(input_chars=3_000, max_output_tokens=1_000)
            usage_meter.note("anthropic", "fake", 300_000, 10_000)   # ~$1.05
            with pytest.raises(sg.MonthlySpendCapReached):
                usage_meter.check_budget(input_chars=300, max_output_tokens=10)

    def test_under_the_cap_passes(self, on, test_user):
        cap(on, test_user, 100.0)
        with usage_meter.scope(on, user_id=test_user.id, purpose="verification"):
            usage_meter.check_budget(input_chars=30_000, max_output_tokens=4_000)

    def test_sends_are_counted_not_stopped(self, on, test_user):
        cap(on, test_user, 1.0)
        usage_row(on, test_user, 5_000_000)
        with usage_meter.scope(on, user_id=test_user.id, purpose="send") as active:
            assert active.monthly_guard is None
            usage_meter.check_budget(input_chars=30_000, max_output_tokens=4_000)

    def test_the_plans_daily_cap_still_applies_under_a_higher_monthly_cap(self, on,
                                                                          test_user):
        cap(on, test_user, 10_000.0)
        usage_row(on, test_user, 9_999_000, ts=datetime.now(timezone.utc))  # free: $10/day
        with usage_meter.scope(on, user_id=test_user.id, purpose="pipeline"):
            with pytest.raises(usage_meter.AccountBudgetExceededError) as exc:
                usage_meter.check_budget(input_chars=30_000, max_output_tokens=4_000)
        assert not isinstance(exc.value, sg.MonthlySpendCapReached)

    def test_a_stopped_pipeline_becomes_budget_exhausted(self, on, test_user):
        from app.workers import tasks as worker_tasks

        strategy = make_strategy(on, test_user, status=m.StrategyStatus.RESEARCHING)
        exc = sg.MonthlySpendCapReached("monthly spend cap reached: x", budget_micros=1,
                                        spent_micros=1)
        worker_tasks._stop_for_budget(on, str(strategy.id), exc)
        on.refresh(strategy)
        assert strategy.status is m.StrategyStatus.BUDGET_EXHAUSTED

    def test_another_accounts_spend_is_not_in_the_guard(self, on, test_user):
        cap(on, test_user, 1.0)
        usage_row(on, make_user(on), 50_000_000)
        with usage_meter.scope(on, user_id=test_user.id, purpose="pipeline") as active:
            assert active.monthly_guard.spent(active) == 0
            usage_meter.check_budget(input_chars=3_000, max_output_tokens=1_000)


# --------------------------------------------------------------------------
# Per-call data gate (plumbing.BaseHttpAdapter.call)
# --------------------------------------------------------------------------


def _apollo(fake_redis):
    from app.integrations.apollo import ApolloAdapter

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={
            "people": [{"id": f"p{i}", "name": f"P {i}", "organization": {}} for i in range(3)],
            "pagination": {"total_pages": 1}})

    adapter = ApolloAdapter(api_key="k", redis=fake_redis,
                            http=httpx.Client(base_url="https://apollo.test",
                                              transport=httpx.MockTransport(handler)))
    return adapter, seen


def _hunter(fake_redis):
    from app.integrations.hunter import HunterAdapter

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": {"result": "deliverable", "score": 90}})

    adapter = HunterAdapter(api_key="k", redis=fake_redis,
                            http=httpx.Client(base_url="https://hunter.test",
                                              transport=httpx.MockTransport(handler)))
    return adapter, seen


class TestDataGate:
    def test_a_paid_call_is_metered_into_the_f01_ledger(self, on, test_user, fake_redis):
        strategy = make_strategy(on, test_user)
        batch = make_batch(on, strategy)
        adapter, seen = _apollo(fake_redis)
        with sg.batch_scope(on, batch.id) as guard:
            assert guard is not None and guard.user_id == test_user.id
            adapter.search({"titles": ["Owner"]}, 3)
        rows = on.query(m.EnrichmentAttempt).all()
        assert len(seen) == 1 and len(rows) == 1
        row = rows[0]
        assert (row.step, row.provider, row.operation, row.cost_units) == \
            ("spend_guard", "apollo", "api_search", 3)
        assert row.batch_id == batch.id and row.user_id == test_user.id
        assert row.lead_id is None and row.email_domain is None

    def test_a_cache_hit_costs_nothing_and_is_not_metered(self, on, test_user, fake_redis):
        batch = make_batch(on, make_strategy(on, test_user))
        adapter, seen = _apollo(fake_redis)
        with sg.batch_scope(on, batch.id):
            adapter.search({"titles": ["Owner"]}, 3)
            adapter.search({"titles": ["Owner"]}, 3)
        assert len(seen) == 1
        assert on.query(m.EnrichmentAttempt).count() == 1

    def test_refused_before_the_request_at_the_cap(self, on, test_user, fake_redis):
        system_settings.set(on, "spend_guard_apollo_usd_per_credit", 0.10)
        cap(on, test_user, 1.0)
        usage_row(on, test_user, 900_000)
        batch = make_batch(on, make_strategy(on, test_user))
        adapter, seen = _apollo(fake_redis)
        with pytest.raises(sg.MonthlySpendCapReached):
            with sg.batch_scope(on, batch.id):
                adapter.search({"titles": ["Owner"]}, 3)      # 3 credits = $0.30
        assert seen == []
        assert on.query(m.EnrichmentAttempt).count() == 0

    def test_committed_spend_is_reread_before_each_data_call(self, on, test_user, fake_redis):
        """Another run's spend that lands mid-stage counts at the next call."""
        system_settings.set(on, "spend_guard_hunter_usd_per_credit", 0.01)
        cap(on, test_user, 1.0)
        batch = make_batch(on, make_strategy(on, test_user))
        adapter, seen = _hunter(fake_redis)
        with sg.batch_scope(on, batch.id):
            adapter.verify("a@x.test")
            usage_row(on, test_user, 995_000)       # elsewhere: now $0.995 + $0.01
            with pytest.raises(sg.MonthlySpendCapReached):
                adapter.verify("b@x.test")
        assert len(seen) == 1

    def test_unpriced_credits_still_stop_once_the_cap_is_reached(self, on, test_user,
                                                                 fake_redis):
        cap(on, test_user, 1.0)
        usage_row(on, test_user, 1_000_000)
        batch = make_batch(on, make_strategy(on, test_user))
        adapter, seen = _apollo(fake_redis)
        with pytest.raises(sg.MonthlySpendCapReached):
            with sg.batch_scope(on, batch.id):
                adapter.search({"titles": ["Owner"]}, 3)
        assert seen == []

    def test_the_waterfalls_own_provider_is_not_metered_twice(self, on, test_user,
                                                              fake_redis):
        system_settings.set(on, "waterfall_enrichment_enabled", True)
        batch = make_batch(on, make_strategy(on, test_user), verifier="hunter")
        adapter, seen = _hunter(fake_redis)
        with sg.batch_scope(on, batch.id) as guard:
            assert guard.self_ledgered == frozenset({"hunter"})
            adapter.verify("a@x.test")
        assert len(seen) == 1
        assert on.query(m.EnrichmentAttempt).count() == 0      # F01 writes its own row

    def test_legacy_hunter_calls_are_metered_when_the_waterfall_is_off(self, on, test_user,
                                                                       fake_redis):
        batch = make_batch(on, make_strategy(on, test_user), verifier="hunter")
        adapter, _ = _hunter(fake_redis)
        with sg.batch_scope(on, batch.id):
            adapter.verify("a@x.test")
        row = on.query(m.EnrichmentAttempt).one()
        assert (row.provider, row.operation) == ("hunter", "email-verifier")

    def test_outside_a_guarded_run_nothing_happens(self, on, test_user, fake_redis):
        cap(on, test_user, 1.0)
        usage_row(on, test_user, 5_000_000)
        adapter, seen = _apollo(fake_redis)
        adapter.search({"titles": ["Owner"]}, 3)
        assert len(seen) == 1
        assert on.query(m.EnrichmentAttempt).count() == 0

    def test_non_data_adapters_are_never_metered(self, on, test_user, fake_redis):
        from app.integrations.plumbing import BaseHttpAdapter

        class Calendar(BaseHttpAdapter):
            provider = "google_calendar"

        cap(on, test_user, 1.0)
        usage_row(on, test_user, 5_000_000)
        adapter = Calendar(redis=fake_redis, http=httpx.Client(
            base_url="https://cal.test",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))))
        batch = make_batch(on, make_strategy(on, test_user))
        with sg.batch_scope(on, batch.id):
            adapter.call("GET", "/events")
        assert on.query(m.EnrichmentAttempt).count() == 0

    def test_market_intel_inside_a_pipeline_scope_is_metered(self, on, test_user,
                                                             fake_redis):
        strategy = make_strategy(on, test_user)
        adapter, _ = _apollo(fake_redis)
        with usage_meter.scope(on, strategy_id=strategy.id, user_id=test_user.id,
                               purpose="pipeline"):
            adapter.call("POST", "/mixed_companies/search", json_body={"q": 1}, cost_units=1)
        row = on.query(m.EnrichmentAttempt).one()
        assert row.strategy_id == strategy.id and row.batch_id is None
        assert row.operation == "search"


class TestLeadStageStop:
    @pytest.fixture()
    def stage_session(self, db_session, monkeypatch):
        from app.workers import lead_tasks

        monkeypatch.setattr(lead_tasks, "SessionLocal", lambda: db_session)
        return db_session

    class _Task:
        def retry(self, exc=None, countdown=None):
            raise AssertionError(f"a spend-cap stop must not be retried: {exc}")

    def test_the_cap_stops_the_batch_and_later_stages_skip(self, on, stage_session,
                                                           test_user):
        from app.workers import lead_tasks

        batch = make_batch(on, make_strategy(on, test_user))
        batch_id = batch.id

        def capped(session, bid):
            raise sg.MonthlySpendCapReached("monthly spend cap reached: $1.00",
                                            budget_micros=1, spent_micros=1)

        out = lead_tasks._run_stage(capped, str(batch_id), self._Task())
        assert out == {"stopped": sg.STOP_PREFIX}
        batch = on.get(m.LeadBatch, batch_id)
        assert batch.stage is m.BatchStage.FAILED
        assert batch.error.startswith(sg.STOP_PREFIX)

        called = []
        out = lead_tasks._run_stage(lambda s, b: called.append(b), str(batch_id), self._Task())
        assert out == {"skipped": sg.STOP_PREFIX} and called == []

    def test_flag_off_stages_run_unchanged(self, db_session, stage_session, test_user):
        from app.workers import lead_tasks

        batch = make_batch(db_session, make_strategy(db_session, test_user))
        assert lead_tasks._run_stage(lambda s, b: 7, str(batch.id), self._Task()) == 7


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------


class TestAlerts:
    def test_each_threshold_fires_once_per_month(self, on, test_user, mailbox):
        cap(on, test_user, 10.0)
        usage_row(on, test_user, 5_500_000)
        assert [a.threshold for a in sg.evaluate_alerts(on, test_user.id)] == [50]
        assert sg.evaluate_alerts(on, test_user.id) == []           # idempotent
        usage_row(on, test_user, 3_000_000)
        assert [a.threshold for a in sg.evaluate_alerts(on, test_user.id)] == [80]
        usage_row(on, test_user, 1_500_000)
        assert [a.threshold for a in sg.evaluate_alerts(on, test_user.id)] == [100]
        assert sg.evaluate_alerts(on, test_user.id) == []
        assert on.query(m.SpendAlert).count() == 3
        assert len(mailbox) == 3
        assert all(msg["to"] == test_user.email for msg in mailbox)
        assert "cap is stopping work" in mailbox[-1]["subject"]

    def test_a_jump_fires_every_threshold_with_one_email(self, on, test_user, mailbox):
        cap(on, test_user, 10.0)
        usage_row(on, test_user, 12_000_000)
        fired = sg.evaluate_alerts(on, test_user.id)
        assert sorted(a.threshold for a in fired) == [50, 80, 100]
        assert len(mailbox) == 1
        statuses = {a.threshold: a.email_status for a in on.query(m.SpendAlert)}
        assert statuses == {50: "merged", 80: "merged", 100: "sent"}

    def test_a_racing_duplicate_is_absorbed_by_the_unique_constraint(self, on, test_user,
                                                                     mailbox, monkeypatch):
        cap(on, test_user, 10.0)
        usage_row(on, test_user, 8_500_000)
        real = sg.month_spend

        def racing(db, uid, now=None, per_unit=None):
            # Another worker fires 50% between our "already fired?" read and
            # our insert.
            db.add(m.SpendAlert(user_id=uid, period=sg.period_label(now), threshold=50,
                                cap_micros=10_000_000, spent_micros=5_000_000,
                                email_status="sent"))
            db.commit()
            return real(db, uid, now, per_unit)

        monkeypatch.setattr(sg, "month_spend", racing)
        fired = sg.evaluate_alerts(on, test_user.id)
        assert [a.threshold for a in fired] == [80]
        assert on.query(m.SpendAlert).filter_by(threshold=50).count() == 1
        assert len(mailbox) == 1

    def test_the_schema_refuses_a_second_row_for_one_threshold(self, on, test_user):
        from sqlalchemy.exc import IntegrityError

        for _ in range(2):
            on.add(m.SpendAlert(user_id=test_user.id, period="2026-10", threshold=80,
                                cap_micros=1, spent_micros=1, email_status="sent"))
        with pytest.raises(IntegrityError):
            on.commit()
        on.rollback()

    def test_email_preference_off_is_in_app_only(self, on, test_user, mailbox):
        cap(on, test_user, 10.0, email_alerts=False)
        usage_row(on, test_user, 6_000_000)
        fired = sg.evaluate_alerts(on, test_user.id)
        assert fired[0].email_status == "skipped"
        assert mailbox == []
        assert sg.alerts_for(on, test_user.id)[0]["threshold"] == 50

    def test_a_new_month_fires_again(self, on, test_user, mailbox):
        cap(on, test_user, 10.0)
        sept = datetime(2026, 9, 15, tzinfo=timezone.utc)
        octo = datetime(2026, 10, 15, tzinfo=timezone.utc)
        usage_row(on, test_user, 6_000_000, ts=sept)
        usage_row(on, test_user, 6_000_000, ts=octo)
        assert [a.threshold for a in sg.evaluate_alerts(on, test_user.id, now=sept)] == [50]
        assert [a.threshold for a in sg.evaluate_alerts(on, test_user.id, now=octo)] == [50]
        assert {a.period for a in on.query(m.SpendAlert)} == {"2026-09", "2026-10"}

    def test_alerts_never_use_the_outreach_path(self, on, test_user, mailbox):
        cap(on, test_user, 1.0)
        usage_row(on, test_user, 2_000_000)
        sg.evaluate_alerts(on, test_user.id)
        assert on.query(m.Message).count() == 0
        assert len(mailbox) == 1
        source = open(sg.__file__, encoding="utf-8").read()
        assert "send_message_impl" not in source and "outreach_tasks" not in source

    def test_sweep_covers_capped_accounts_only(self, on, test_user, mailbox):
        other = make_user(on)
        cap(on, test_user, 1.0)
        usage_row(on, test_user, 2_000_000)
        usage_row(on, other, 50_000_000)              # no cap: nothing to alert on
        assert sg.sweep_alerts(on) == 3
        assert {a.user_id for a in on.query(m.SpendAlert)} == {test_user.id}

    def test_beat_entry_is_scheduled_and_routed(self):
        from app.workers.celery_app import celery_app

        entry = celery_app.conf.beat_schedule["sweep-spend-alerts"]
        assert entry["task"] == "app.workers.spend_guard_tasks.sweep_spend_alerts"
        assert sorted(entry["schedule"].minute) == [4, 34]  # a 30-minute burst, +4


# --------------------------------------------------------------------------
# Reconciliation (the +/-25% metric)
# --------------------------------------------------------------------------


def _forecast_row(db, user, *, kind="enrichment_run", strategy=None, batch=None,
                  ai_micros=0, units=None, purposes=("lead_scoring",), at=T0,
                  decision="allowed", requested=50):
    row = m.SpendForecast(
        user_id=user.id, kind=kind, decision=decision,
        strategy_id=getattr(strategy, "id", None), batch_id=getattr(batch, "id", None),
        requested_leads=requested, ai_purposes=list(purposes), ai_input_tokens=0,
        ai_output_tokens=0, ai_micros=ai_micros, data_units_json=units or {},
        data_micros=0, total_micros=ai_micros, month_spent_micros=0, cap_micros=None,
        detail_json={}, created_at=at, updated_at=at)
    db.add(row)
    db.commit()
    return row


class TestReconcile:
    def test_enrichment_forecast_reconciles_to_fixture_rows(self, db_session, test_user):
        system_settings.set(db_session, "spend_guard_apollo_usd_per_credit", 0.01)
        system_settings.set(db_session, "spend_guard_hunter_usd_per_credit", 0.02)
        strategy = make_strategy(db_session, test_user)
        batch = make_batch(db_session, strategy, stage=m.BatchStage.FINALIZED)
        other_batch = make_batch(db_session, strategy)
        row = _forecast_row(db_session, test_user, strategy=strategy, batch=batch,
                            ai_micros=400_000, units={"apollo": 100, "hunter": 75})
        later = T0 + timedelta(hours=1)
        attempt_row(db_session, test_user, "apollo", 90, batch=batch, ts=later)
        attempt_row(db_session, test_user, "hunter", 80, batch=batch, ts=later,
                    step="hunter", operation="verify", outcome="valid")
        attempt_row(db_session, test_user, "apollo", 500, batch=other_batch, ts=later)
        attempt_row(db_session, test_user, "apollo", 7, batch=batch,
                    ts=T0 - timedelta(hours=1))                      # before the run
        other = make_user(db_session)
        attempt_row(db_session, other, "apollo", 999, batch=batch, ts=later)
        usage_row(db_session, test_user, 380_000, purpose="lead_scoring", strategy=strategy,
                  ts=later, calls=4, tin=10_000, tout=4_000)
        usage_row(db_session, test_user, 9_000_000, purpose="send", strategy=strategy,
                  ts=later)
        usage_row(db_session, other, 9_000_000, purpose="lead_scoring", strategy=strategy,
                  ts=later)

        rec = sg.reconcile(db_session, row)
        assert rec["complete"] is True
        assert rec["data"]["credits"]["apollo"] == {"forecast": 100, "actual": 90,
                                                    "error_pct": round(10 / 90, 4),
                                                    "within_tolerance": True}
        assert rec["data"]["credits"]["hunter"]["actual"] == 80
        assert rec["ai"]["actual_usd"] == 0.38 and rec["ai"]["actual_tokens"] == [10_000, 4_000]
        forecast_total = 400_000 + 100 * 10_000 + 75 * 20_000      # 2.9
        actual_total = 380_000 + 90 * 10_000 + 80 * 20_000         # 2.88
        assert rec["forecast_usd"] == forecast_total / 1e6
        assert rec["actual_usd"] == actual_total / 1e6
        assert rec["error_pct"] == round((forecast_total - actual_total) / actual_total, 4)
        assert rec["within_tolerance"] is True

    def test_outside_tolerance_is_reported(self, db_session, test_user):
        strategy = make_strategy(db_session, test_user, status=m.StrategyStatus.VERIFIED)
        row = _forecast_row(db_session, test_user, kind="strategy_run", strategy=strategy,
                            ai_micros=1_000_000, purposes=("pipeline", "verification"))
        usage_row(db_session, test_user, 2_000_000, strategy=strategy,
                  ts=T0 + timedelta(minutes=5))
        rec = sg.reconcile(db_session, row)
        assert rec["error_pct"] == -0.5 and rec["within_tolerance"] is False
        assert rec["complete"] is True

    def test_the_window_ends_at_the_next_forecast_for_that_strategy(self, db_session,
                                                                    test_user):
        strategy = make_strategy(db_session, test_user)
        first = _forecast_row(db_session, test_user, kind="strategy_run", strategy=strategy,
                              ai_micros=1_000_000, purposes=("pipeline",))
        _forecast_row(db_session, test_user, kind="strategy_run", strategy=strategy,
                      ai_micros=1_000_000, purposes=("pipeline",), at=T0 + timedelta(hours=2))
        usage_row(db_session, test_user, 1_000_000, strategy=strategy,
                  ts=T0 + timedelta(hours=1))
        usage_row(db_session, test_user, 7_000_000, strategy=strategy,
                  ts=T0 + timedelta(hours=3))           # the resume's spend
        rec = sg.reconcile(db_session, first)
        assert rec["actual_usd"] == 1.0 and rec["error_pct"] == 0.0

    def test_a_later_sourcing_batch_is_not_the_strategy_runs_data(self, db_session,
                                                                 test_user):
        strategy = make_strategy(db_session, test_user)
        batch = make_batch(db_session, strategy)
        row = _forecast_row(db_session, test_user, kind="strategy_run", strategy=strategy,
                            ai_micros=1_000_000, units={"apollo": 1},
                            purposes=("pipeline", "verification"))
        later = T0 + timedelta(hours=5)
        attempt_row(db_session, test_user, "apollo", 1, strategy=strategy,
                    ts=T0 + timedelta(minutes=1))                       # market intel
        attempt_row(db_session, test_user, "apollo", 40, strategy=strategy, batch=batch,
                    ts=later)                                           # sourcing
        usage_row(db_session, test_user, 2_000_000, purpose="lead_scoring",
                  strategy=strategy, ts=later)
        rec = sg.reconcile(db_session, row)
        assert rec["data"]["credits"]["apollo"]["actual"] == 1
        assert rec["ai"]["actual_usd"] == 0.0

    def test_an_enrichment_batch_is_measured_whole_even_after_the_next_forecast(
            self, db_session, test_user):
        strategy = make_strategy(db_session, test_user)
        first = make_batch(db_session, strategy, stage=m.BatchStage.FINALIZED)
        second = make_batch(db_session, strategy)
        row = _forecast_row(db_session, test_user, strategy=strategy, batch=first,
                            units={"apollo": 20})
        _forecast_row(db_session, test_user, strategy=strategy, batch=second,
                      units={"apollo": 20}, at=T0 + timedelta(minutes=10))
        attempt_row(db_session, test_user, "apollo", 20, batch=first,
                    ts=T0 + timedelta(minutes=30))     # first batch still running
        assert sg.reconcile(db_session, row)["data"]["credits"]["apollo"]["actual"] == 20

    def test_refused_and_unfinished_runs_are_not_measured(self, db_session, test_user):
        strategy = make_strategy(db_session, test_user, status=m.StrategyStatus.RESEARCHING)
        refused = _forecast_row(db_session, test_user, kind="strategy_run",
                                strategy=strategy, decision="refused")
        assert sg.reconcile(db_session, refused)["reconcilable"] is False
        running = _forecast_row(db_session, test_user, kind="strategy_run",
                                strategy=strategy, ai_micros=1_000_000,
                                purposes=("pipeline",), at=T0 + timedelta(hours=1))
        usage_row(db_session, test_user, 1_000_000, strategy=strategy,
                  ts=T0 + timedelta(hours=2))
        assert sg.reconcile(db_session, running)["complete"] is False
        assert sg.accuracy(db_session, test_user.id)["measured_runs"] == 0
        strategy.status = m.StrategyStatus.VERIFIED
        db_session.commit()
        acc = sg.accuracy(db_session, test_user.id)
        assert acc == {"measured_runs": 1, "within_tolerance": 1,
                       "share_within_tolerance": 1.0, "tolerance": 0.25}


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


class TestApi:
    def test_usage_shape_and_tenancy(self, client, on, test_user):
        system_settings.set(on, "spend_guard_apollo_usd_per_credit", 0.01)
        cap(on, test_user, 10.0)
        strategy = make_strategy(on, test_user)
        make_batch(on, strategy, stage=m.BatchStage.FINALIZED,
                   summary={"counts": {"verified": 8, "dropped": 2}, "total": 10})
        usage_row(on, test_user, 2_000_000)
        attempt_row(on, test_user, "apollo", 100)
        attempt_row(on, test_user, "hunter", 0, step="hunter", operation="verify",
                    outcome="invalid")
        other = make_user(on)
        other_strategy = make_strategy(on, other)
        make_batch(on, other_strategy, stage=m.BatchStage.FINALIZED,
                   summary={"counts": {"verified": 500}})
        usage_row(on, other, 50_000_000)
        attempt_row(on, other, "apollo", 5_000)
        cap(on, other, 1.0)
        sg.evaluate_alerts(on, other.id)
        _forecast_row(on, other, strategy=other_strategy)

        body = client.get("/spend-guard/usage").json()
        assert body["cap"]["monthly_cap_usd"] == 10.0
        assert body["spend"]["ai_usd"] == 2.0 and body["spend"]["data_usd"] == 1.0
        assert body["spend"]["total_usd"] == 3.0 and body["spend"]["percent_of_cap"] == 30.0
        assert body["spend"]["data_credits"] == {"apollo": 100}
        assert body["billable"]["verified_results"] == 8
        assert body["billable"]["data_attempts"] == 2
        assert body["billable"]["paid_data_attempts"] == 1
        assert body["billable"]["cost_per_verified_result_usd"] == 0.125
        assert body["alerts"] == [] and body["forecasts"] == []
        priced = {p["provider"]: p["priced"] for p in body["pricing"]["providers"]}
        assert priced == {"apollo": True, "hunter": False}
        assert body["daily"]["plan"] == "free"

    def test_cap_set_clear_and_validate(self, client, on, test_user):
        r = client.put("/spend-guard/cap", json={"monthly_cap_usd": 25, "email_alerts": False})
        assert r.status_code == 200, r.text
        assert r.json()["cap"] == {"monthly_cap_usd": 25.0, "email_alerts": False,
                                   "thresholds": [50, 80, 100]}
        assert client.put("/spend-guard/cap",
                          json={"monthly_cap_usd": 0.5}).status_code == 422
        r = client.put("/spend-guard/cap", json={"monthly_cap_usd": None})
        assert r.json()["cap"]["monthly_cap_usd"] is None
        assert sg.cap_micros(on, test_user.id) is None

    def test_setting_a_cap_below_spend_fires_alerts_at_once(self, client, on, test_user,
                                                            mailbox):
        usage_row(on, test_user, 9_000_000)
        body = client.put("/spend-guard/cap", json={"monthly_cap_usd": 10}).json()
        assert [a["threshold"] for a in body["alerts"]] == [50, 80]
        assert len(mailbox) == 1

    def test_an_sdr_cannot_change_the_cap(self, client, on, test_user):
        from app.services import workspaces

        ws = workspaces.personal_workspace(on, test_user)
        sdr = m.User(email="sdr@spend.dev", email_verified=True)
        on.add(sdr)
        on.flush()
        on.add(m.WorkspaceMember(workspace_id=ws.id, user_id=sdr.id, role="sdr"))
        on.commit()
        headers = {**auth_headers(sdr), "X-Workspace-Id": str(ws.id)}
        r = client.put("/spend-guard/cap", json={"monthly_cap_usd": 50}, headers=headers)
        assert r.status_code == 403
        assert sg.cap_row(on, test_user.id) is None

    def test_forecast_preview_stores_nothing(self, client, on, test_user, verified_strategy):
        cap(on, test_user, 0 + 1.0)
        usage_row(on, test_user, 999_000)
        r = client.post("/spend-guard/forecast",
                        json={"kind": "enrichment_run", "strategy_id": str(verified_strategy.id),
                              "max_leads": 30})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["forecast"]["requested_leads"] == 30
        assert body["forecast"]["data"]["credits"] == {"apollo": 60, "hunter": 45}
        assert body["would_allow"] is False
        r = client.post("/spend-guard/forecast",
                        json={"kind": "strategy_run", "flow_type": "with_clients"})
        assert r.status_code == 200 and r.json()["forecast"]["kind"] == "strategy_run"
        assert client.post("/spend-guard/forecast",
                           json={"kind": "strategy_run"}).status_code == 422
        assert on.query(m.SpendForecast).count() == 0

    def test_forecast_preview_of_another_accounts_strategy_is_404(self, client, on):
        foreign = make_strategy(on, make_user(on))
        r = client.post("/spend-guard/forecast",
                        json={"kind": "enrichment_run", "strategy_id": str(foreign.id)})
        assert r.status_code == 404

    def test_one_forecast_is_owner_only(self, client, on, test_user):
        mine = _forecast_row(on, test_user, strategy=make_strategy(on, test_user))
        theirs = _forecast_row(on, make_user(on))
        assert client.get(f"/spend-guard/forecasts/{mine.id}").json()["forecast_id"] == \
            str(mine.id)
        assert client.get(f"/spend-guard/forecasts/{theirs.id}").status_code == 404
        assert client.get(f"/spend-guard/forecasts/{uuid.uuid4()}").status_code == 404


# --------------------------------------------------------------------------
# F01 compatibility + data model
# --------------------------------------------------------------------------


class TestCompatibility:
    def test_f01_metrics_ignore_spend_guard_rows(self, db_session, test_user):
        from app.services import bounce_shield

        attempt_row(db_session, test_user, "apollo", 50,
                    ts=datetime.now(timezone.utc) - timedelta(hours=1))
        attempt_row(db_session, test_user, "hunter", 1, step="hunter", operation="verify",
                    outcome="valid", ts=datetime.now(timezone.utc) - timedelta(hours=1))
        ledger = bounce_shield.metrics(db_session, test_user.id, days=30)["ledger"]
        assert set(ledger["by_provider"]) == {"hunter"}

    def test_tables_are_in_the_account_export(self):
        from app.services import account_export

        for table in ("spend_caps", "spend_alerts", "spend_forecasts"):
            assert account_export.OWNERSHIP[table] == ["user_id"]

    def test_nothing_is_written_with_personal_data(self):
        for model in (m.SpendCap, m.SpendAlert, m.SpendForecast):
            columns = set(model.__table__.columns.keys())
            assert not columns & {"email", "full_name", "name", "phone", "address"}


# --------------------------------------------------------------------------
# Review fixes (reviewer probe tests/…/f07/test_f07_probe.py, ported)
# --------------------------------------------------------------------------


def _count_statements(db):
    """A list that collects every SQL statement the session's engine runs."""
    from sqlalchemy import event

    seen: list[str] = []
    engine = db.get_bind()

    def _before(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", _before)
    return seen, lambda: event.remove(engine, "before_cursor_execute", _before)


class TestFreshSpend:
    """M1: other runs' committed spend is seen at every check."""

    def test_spend_committed_by_another_run_mid_scope_is_seen(self, on, test_user):
        cap(on, test_user, 1.0)
        with usage_meter.scope(on, user_id=test_user.id, purpose="pipeline"):
            usage_row(on, test_user, 900_000)                  # another run commits $0.90
            usage_meter.note("anthropic", "fake", 100_000, 0)  # this run: $0.30
            with pytest.raises(sg.MonthlySpendCapReached):
                usage_meter.check_budget(input_chars=300, max_output_tokens=10)

    def test_own_flushed_calls_are_not_counted_twice(self, on, test_user):
        cap(on, test_user, 1.0)
        strategy = make_strategy(on, test_user)
        with usage_meter.scope(on, strategy_id=strategy.id, user_id=test_user.id,
                               purpose="pipeline") as active:
            usage_meter.note("anthropic", "fake", 200_000, 0)  # $0.60, pending
            assert active.monthly_guard.spent(active) == 600_000
            usage_meter.flush_active(on)                       # now committed
            assert active.monthly_guard.spent(active) == 600_000
            usage_meter.check_budget(input_chars=300, max_output_tokens=10)

    def test_data_calls_inside_a_model_scope_use_the_fresh_figure(self, on, test_user,
                                                                  fake_redis):
        system_settings.set(on, "spend_guard_apollo_usd_per_credit", 0.10)
        cap(on, test_user, 1.0)
        adapter, seen = _apollo(fake_redis)
        with usage_meter.scope(on, user_id=test_user.id, purpose="pipeline"):
            usage_row(on, test_user, 950_000)                  # another run commits
            with pytest.raises(sg.MonthlySpendCapReached):
                adapter.call("POST", "/mixed_companies/search", json_body={"q": 1},
                             cost_units=1)
        assert seen == []

    def test_committed_month_spend_is_one_statement(self, on, test_user):
        system_settings.set(on, "spend_guard_apollo_usd_per_credit", 0.01)
        usage_row(on, test_user, 500_000)
        attempt_row(on, test_user, "apollo", 10)
        attempt_row(on, test_user, "hunter", 99)               # unpriced
        per_unit = sg.unit_micros(on)
        seen, stop = _count_statements(on)
        try:
            total = sg.committed_month_micros(on, test_user.id, per_unit)
        finally:
            stop()
        assert total == 500_000 + 10 * 10_000
        assert len(seen) == 1


class TestCapReachedAlert:
    """M2: when the cap stops work, the 100% alert fires (once a month)."""

    def test_a_refused_model_call_fires_the_100_alert(self, on, test_user, mailbox):
        cap(on, test_user, 1.0)
        usage_row(on, test_user, 990_000)
        with usage_meter.scope(on, user_id=test_user.id, purpose="pipeline"):
            with pytest.raises(sg.MonthlySpendCapReached):
                usage_meter.check_budget(input_chars=30_000, max_output_tokens=4_000)
        thresholds = sorted(a.threshold for a in on.query(m.SpendAlert))
        assert thresholds == [50, 80, 100]
        assert len(mailbox) == 1 and "stopping work" in mailbox[0]["subject"]
        # Idempotent: a second refusal this month fires nothing new.
        with usage_meter.scope(on, user_id=test_user.id, purpose="pipeline"):
            with pytest.raises(sg.MonthlySpendCapReached):
                usage_meter.check_budget(input_chars=30_000, max_output_tokens=4_000)
        assert on.query(m.SpendAlert).count() == 3 and len(mailbox) == 1

    def test_a_refusal_caught_inside_the_scope_still_fires(self, on, test_user):
        """Lead scoring catches the refusal and falls back to the heuristic."""
        cap(on, test_user, 1.0)
        usage_row(on, test_user, 990_000)
        with usage_meter.scope(on, user_id=test_user.id, purpose="lead_scoring"):
            try:
                usage_meter.check_budget(input_chars=30_000, max_output_tokens=4_000)
            except sg.MonthlySpendCapReached:
                pass
        assert on.query(m.SpendAlert).filter_by(threshold=100).count() == 1

    def test_a_refused_data_call_fires_the_100_alert(self, on, test_user, fake_redis):
        system_settings.set(on, "spend_guard_apollo_usd_per_credit", 0.10)
        cap(on, test_user, 1.0)
        usage_row(on, test_user, 900_000)
        batch = make_batch(on, make_strategy(on, test_user))
        adapter, _ = _apollo(fake_redis)
        with pytest.raises(sg.MonthlySpendCapReached):
            with sg.batch_scope(on, batch.id):
                adapter.search({"titles": ["Owner"]}, 3)
        assert on.query(m.SpendAlert).filter_by(threshold=100).count() == 1

    def test_a_refused_run_fires_the_100_alert(self, client, on, test_user, product):
        cap(on, test_user, 5.0)
        usage_row(on, test_user, 1_000_000)          # 20%: no threshold crossed by spend
        r = client.post(f"/products/{product.id}/strategies", json={"flow_type": "no_clients"})
        assert r.status_code == 429
        assert [a.threshold for a in on.query(m.SpendAlert)] == [100]

    def test_no_refusal_means_no_100_alert(self, on, test_user):
        cap(on, test_user, 100.0)
        with usage_meter.scope(on, user_id=test_user.id, purpose="pipeline"):
            usage_meter.check_budget(input_chars=300, max_output_tokens=10)
        assert on.query(m.SpendAlert).count() == 0


class TestSinglePaidEndpoints:
    """L3: the two paid calls outside any run honour the cap."""

    def test_market_signal_refresh_is_checked_and_metered(self, on, test_user, fake_redis,
                                                          monkeypatch):
        from app.services import market_intel
        from app.workers import intelligence_tasks

        adapter, seen = _apollo(fake_redis)
        monkeypatch.setattr(market_intel, "fetch_google_news", lambda q, now=None: [])
        monkeypatch.setattr(market_intel, "_apollo_org_search", lambda keywords: adapter.call(
            "POST", "/mixed_companies/search", json_body={"q": keywords}, cost_units=1
        ).get("organizations", []))
        monkeypatch.setattr(market_intel, "build_queries", lambda s, st: ["fire safety"])
        monkeypatch.setattr(intelligence_tasks, "SessionLocal", lambda: on)
        strategy = make_strategy(on, test_user)
        sid = strategy.id

        intelligence_tasks.refresh_market_signals(str(sid))
        assert len(seen) == 1
        row = on.query(m.EnrichmentAttempt).one()
        assert (row.provider, row.strategy_id, row.step) == ("apollo", sid, "spend_guard")

        cap(on, test_user, 1.0)
        usage_row(on, test_user, 1_000_000)
        intelligence_tasks.refresh_market_signals(str(sid))
        assert len(seen) == 1                                   # refused before the call
        assert on.query(m.SpendAlert).filter_by(threshold=100).count() == 1

    def test_single_lead_reverify_honours_the_cap(self, client, on, test_user, fake_redis,
                                                  monkeypatch, verified_strategy):
        from app.api import bounce_shield as bounce_api

        system_settings.set(on, "waterfall_enrichment_enabled", True)
        adapter, seen = _hunter(fake_redis)
        monkeypatch.setattr(bounce_api, "_get_verifier", lambda: adapter)
        lead = m.Lead(strategy_id=verified_strategy.id, full_name="A B", source="apollo",
                      external_id="x1", email="a@x.test", status=m.LeadStatus.VERIFIED)
        on.add(lead)
        on.commit()
        cap(on, test_user, 1.0)
        usage_row(on, test_user, 1_000_000)
        r = client.post(f"/leads/{lead.id}/email-verdict/refresh")
        assert r.status_code == 429, r.text
        assert r.json()["detail"]["error"] == "monthly_spend_cap_reached"
        assert seen == []
        assert on.query(m.EnrichmentAttempt).count() == 0      # no "error" attempt
        cap(on, test_user, 100.0)
        r = client.post(f"/leads/{lead.id}/email-verdict/refresh")
        assert r.status_code == 200, r.text
        rows = on.query(m.EnrichmentAttempt).all()
        assert [(r.step, r.provider) for r in rows] == [("reverify", "hunter")]  # F01 only


class TestWaterfallRefusal:
    """L4: a cap refusal inside the REAL F01 waterfall is not an attempt."""

    @pytest.mark.parametrize("has_email", [True, False],
                             ids=["verify-refused", "finder-refused"])
    def test_no_error_attempt_and_clean_f01_metrics(self, on, test_user, fake_redis,
                                                    monkeypatch, has_email):
        from app.services import bounce_shield
        from app.workers import lead_tasks

        system_settings.set(on, "waterfall_enrichment_enabled", True)
        strategy = make_strategy(on, test_user)
        batch = make_batch(on, strategy, verifier="hunter", stage=m.BatchStage.ENRICHING)
        on.add(m.Lead(strategy_id=strategy.id, batch_id=batch.id, full_name="Pat Lee",
                      source="apollo", external_id="e1",
                      email="pat@x.test" if has_email else None,
                      enrichment_json={"company_domain": "x.test"},
                      status=m.LeadStatus.ENRICHED))
        on.commit()
        cap(on, test_user, 1.0)
        usage_row(on, test_user, 1_000_000)
        adapter, seen = _hunter(fake_redis)
        monkeypatch.setattr(lead_tasks, "_get_verifier", lambda b: adapter)
        monkeypatch.setattr(lead_tasks, "SessionLocal", lambda: on)
        batch_id = batch.id

        out = lead_tasks._run_stage(lead_tasks.find_missing_emails_impl, str(batch_id),
                                    TestLeadStageStop._Task())
        assert out == {"stopped": sg.STOP_PREFIX}
        assert seen == []
        rows = {(r.provider, r.operation, r.outcome) for r in on.query(m.EnrichmentAttempt)}
        # Only the free "Apollo already had an address" candidate may be there;
        # the refused Hunter call is not an attempt, so no "error" row.
        assert rows == ({("apollo", "find_email", "candidate")} if has_email else set())
        ledger = bounce_shield.metrics(on, test_user.id, days=30)["ledger"]
        assert "hunter" not in ledger["by_provider"] and ledger["verdicts"] == {}
        assert on.get(m.LeadBatch, batch_id).stage is m.BatchStage.FAILED

    def test_a_real_provider_error_is_still_ledgered(self, on, test_user):
        """The F01 behaviour the fix must keep: provider failures are attempts."""
        from app.services import waterfall_enrichment as wf

        class Broken:
            provider = "hunter"

            def verify(self, email):
                raise RuntimeError("provider down")

        strategy = make_strategy(on, test_user)
        lead = m.Lead(strategy_id=strategy.id, full_name="A B", source="apollo",
                      external_id="x1", email="a@x.test", status=m.LeadStatus.EMAIL_FOUND)
        on.add(lead)
        on.commit()
        with pytest.raises(RuntimeError):
            wf.verify_address(on, lead, lead.email, Broken(), wf.context_for_lead(on, lead),
                              step="hunter")
        assert on.query(m.EnrichmentAttempt).one().outcome == "error"


class TestFlagOffQueries:
    """L5: with the flag off the hooks cost one flag read, no more."""

    def test_a_lead_stage_costs_one_query(self, db_session, test_user, monkeypatch):
        from app.workers import lead_tasks

        monkeypatch.setattr(lead_tasks, "SessionLocal", lambda: db_session)
        batch = make_batch(db_session, make_strategy(db_session, test_user))
        batch_id = str(batch.id)
        db_session.expunge_all()            # no identity-map shortcut
        seen, stop = _count_statements(db_session)
        try:
            assert lead_tasks._run_stage(lambda s, b: 7, batch_id,
                                         TestLeadStageStop._Task()) == 7
        finally:
            stop()
        assert len(seen) == 1 and "system_settings" in seen[0]

    def test_an_outermost_capped_scope_costs_one_query(self, db_session, test_user,
                                                       monkeypatch):
        def statements(arm):
            monkeypatch.setattr(sg, "arm", arm)
            seen, stop = _count_statements(db_session)
            try:
                with usage_meter.scope(db_session, user_id=test_user.id, purpose="pipeline"):
                    pass
            finally:
                stop()
            return seen

        from collections import Counter

        real = sg.arm
        without = statements(lambda session, active: None)
        with_hook = statements(real)
        extra = list((Counter(with_hook) - Counter(without)).elements())
        assert len(with_hook) - len(without) == 1
        assert "system_settings" in extra[0]

    def test_a_non_capped_scope_costs_nothing(self, db_session, test_user):
        seen, stop = _count_statements(db_session)
        try:
            with usage_meter.scope(db_session, user_id=test_user.id, purpose="send"):
                pass
        finally:
            stop()
        assert seen == []


class TestSavepoints:
    """L6/L7: a failing F07 read or write never aborts the caller's transaction."""

    @staticmethod
    def _savepoint_rollbacks(db):
        from sqlalchemy import event

        rolled: list[str] = []
        engine = db.get_bind()

        def _on(conn, name, context):
            rolled.append(name)

        event.listen(engine, "rollback_savepoint", _on)
        return rolled, lambda: event.remove(engine, "rollback_savepoint", _on)

    def test_flag_on_before_the_migration_rolls_back_only_a_savepoint(self, on, test_user):
        """spend_caps missing (flag switched on before 0077 ran)."""
        m.SpendCap.__table__.drop(on.get_bind())
        pending = m.Product(user=test_user, name="pending", description="d",
                            type=m.ProductType.PRODUCT)
        on.add(pending)
        on.flush()
        rolled, stop = self._savepoint_rollbacks(on)
        try:
            with usage_meter.scope(on, user_id=test_user.id, purpose="pipeline") as active:
                assert active.monthly_guard is None
            with sg.batch_scope(on, uuid.uuid4()) as guard:
                assert guard is None
        finally:
            stop()
        assert len(rolled) == 1          # arm's savepoint (the batch does not exist)
        on.commit()                      # the caller's transaction is intact
        assert on.query(m.Product).filter_by(name="pending").count() == 1

    def test_a_failing_meter_write_keeps_the_callers_pending_work(self, on, test_user,
                                                                  fake_redis):
        batch = make_batch(on, make_strategy(on, test_user))
        guard_batch_id = batch.id
        m.EnrichmentAttempt.__table__.drop(on.get_bind())
        adapter, seen = _apollo(fake_redis)
        with sg.batch_scope(on, guard_batch_id):
            pending = m.Product(user=test_user, name="pending", description="d",
                                type=m.ProductType.PRODUCT)
            on.add(pending)
            on.flush()
            adapter.search({"titles": ["Owner"]}, 3)          # paid; meter insert fails
        assert len(seen) == 1
        on.commit()
        assert on.query(m.Product).filter_by(name="pending").count() == 1
