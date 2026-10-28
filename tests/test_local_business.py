"""Local business mode, Part A -- business type, service area, and the local
context in research, sourcing and messages (docs/features/local-business-mode.md).

Offline: SQLite, FakeClaude, no network.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db import models as m
from app.integrations.apollo import icp_to_apollo_params
from app.services import local_business as lb
from app.services import system_settings
from tests.conftest import auth_headers

AREA = {"city": "Astoria, NY", "country": "US", "radius": 3, "unit": "mi",
        "timezone": "America/New_York"}


def _local(db, strategy, business_type="restaurant", area=AREA,
           offer="Catering packages for team lunches, 10-200 people"):
    product = db.get(m.Product, strategy.product_id)
    product.business_type = business_type
    strategy.service_area_json = dict(lb.ServiceArea(**area).model_dump()) if area else None
    strategy.local_offer = offer
    db.commit()
    return strategy


def _product(db, user, business_type=None):
    product = m.Product(user_id=user.id, name="Bella Cafe", description="Neighbourhood cafe",
                        type=m.ProductType.PRODUCT, business_type=business_type)
    db.add(product)
    db.commit()
    return product


class TestFlags:
    def test_defaults(self):
        assert system_settings.DEFAULTS[lb.FLAG][0] is True
        assert system_settings.DEFAULTS[lb.WINBACK_FLAG][0] is False


class TestBusinessTypeOnProduct:
    def test_create_with_business_type(self, client, db_session):
        r = client.post("/products", json={"name": "Bella Cafe", "description": "Cafe",
                                           "type": "product", "business_type": "restaurant"})
        assert r.status_code == 201, r.text
        assert r.json()["business_type"] == "restaurant"
        assert db_session.get(m.Product, uuid.UUID(r.json()["id"])).business_type == "restaurant"

    def test_default_is_not_local(self, client, db_session):
        r = client.post("/products", json={"name": "X", "description": "Y", "type": "skill"})
        assert r.status_code == 201
        assert r.json()["business_type"] is None

    def test_unknown_type_is_422(self, client, db_session):
        r = client.post("/products", json={"name": "X", "description": "Y", "type": "product",
                                           "business_type": "casino"})
        assert r.status_code == 422
        assert db_session.execute(select(m.Product)).scalars().all() == []

    def test_flag_off_refuses(self, client, db_session):
        system_settings.set(db_session, lb.FLAG, False)
        r = client.post("/products", json={"name": "X", "description": "Y", "type": "product",
                                           "business_type": "retail"})
        assert r.status_code == 422
        assert "switched off" in r.json()["detail"]

    def test_put_sets_and_clears(self, client, db_session, test_user):
        product = _product(db_session, test_user)
        r = client.put(f"/products/{product.id}/local-business",
                       json={"business_type": "healthcare"})
        assert r.status_code == 200 and r.json()["business_type"] == "healthcare"
        r = client.put(f"/products/{product.id}/local-business", json={"business_type": None})
        assert r.status_code == 200 and r.json()["business_type"] is None

    def test_put_on_another_accounts_product_is_404(self, client, db_session):
        other = m.User(email="other@x.dev", email_verified=True)
        db_session.add(other)
        db_session.commit()
        product = _product(db_session, other)
        r = client.put(f"/products/{product.id}/local-business",
                       json={"business_type": "retail"})
        assert r.status_code == 404
        db_session.refresh(product)
        assert product.business_type is None


class TestServiceArea:
    def test_strategy_created_with_service_area(self, client, db_session, test_user):
        product = _product(db_session, test_user, "restaurant")
        r = client.post(f"/products/{product.id}/strategies",
                        json={"service_area": {**AREA, "country": "us"},
                              "local_offer": "  Team lunch catering  "})
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["business_type"] == "restaurant"
        assert body["service_area"]["country"] == "US"
        assert body["service_area"]["radius"] == 3
        assert body["local_offer"] == "Team lunch catering"
        strategy = db_session.get(m.Strategy, uuid.UUID(body["id"]))
        assert strategy.service_area_json["city"] == "Astoria, NY"
        assert strategy.business_type == "restaurant"     # read-only property

    @pytest.mark.parametrize("area", [
        {"city": "Astoria", "radius": 3},                               # no country
        {"country": "US", "radius": 3},                                 # no city/postcode
        {"city": "Astoria", "country": "USA", "radius": 3},             # 3-letter
        {"city": "Astoria", "country": "US", "radius": 51, "unit": "mi"},
        {"city": "Astoria", "country": "US", "radius": 81, "unit": "km"},
        {"city": "Astoria", "country": "US", "radius": 0.2, "unit": "mi"},
        {"city": "Astoria", "country": "US", "radius": 3, "unit": "miles"},
        {"city": "Astoria", "country": "US", "radius": 3, "timezone": "Mars/Base"},
        {"postal_code": "!!", "country": "US", "radius": 3},
    ])
    def test_invalid_service_area_is_422_and_creates_nothing(self, client, db_session,
                                                            test_user, area):
        product = _product(db_session, test_user, "restaurant")
        r = client.post(f"/products/{product.id}/strategies", json={"service_area": area})
        assert r.status_code == 422, r.text
        assert db_session.execute(select(m.Strategy)).scalars().all() == []

    def test_radius_in_km_up_to_80(self):
        area = lb.ServiceArea(postal_code="10115", country="DE", radius=80, unit="km")
        assert lb.location_string(area.model_dump()) == "10115, DE"

    def test_service_area_needs_a_local_product(self, client, db_session, test_user):
        product = _product(db_session, test_user, None)
        r = client.post(f"/products/{product.id}/strategies", json={"service_area": AREA})
        assert r.status_code == 422
        assert "local business" in r.json()["detail"]
        assert db_session.execute(select(m.Strategy)).scalars().all() == []

    def test_flag_off_refuses_service_area(self, client, db_session, test_user):
        product = _product(db_session, test_user, "retail")
        system_settings.set(db_session, lb.FLAG, False)
        r = client.post(f"/products/{product.id}/strategies", json={"service_area": AREA})
        assert r.status_code == 422

    def test_put_updates_and_clears(self, client, db_session, verified_strategy):
        db_session.get(m.Product, verified_strategy.product_id).business_type = "retail"
        db_session.commit()
        r = client.put(f"/strategies/{verified_strategy.id}/local-business",
                       json={"service_area": {"postal_code": "11103", "country": "US",
                                              "radius": 2}, "local_offer": "Gift boxes"})
        assert r.status_code == 200, r.text
        assert r.json()["service_area"]["postal_code"] == "11103"
        assert r.json()["local_offer"] == "Gift boxes"
        r = client.put(f"/strategies/{verified_strategy.id}/local-business", json={})
        assert r.status_code == 200
        db_session.refresh(verified_strategy)
        assert verified_strategy.service_area_json is None
        assert verified_strategy.local_offer is None

    def test_put_on_another_accounts_strategy_is_404(self, client, db_session,
                                                      verified_strategy):
        other = m.User(email="o2@x.dev", email_verified=True)
        db_session.add(other)
        db_session.commit()
        r = client.put(f"/strategies/{verified_strategy.id}/local-business",
                       json={"local_offer": "x"}, headers=auth_headers(other))
        assert r.status_code == 404


class TestResearchPrompts:
    def _capture(self, fake_claude, monkeypatch):
        prompts: list[str] = []
        original = fake_claude.complete

        def complete(system, prompt, **kw):
            prompts.append((kw.get("cache_prefix") or "") + prompt)  # what the model reads
            return original(system, prompt, **kw)

        monkeypatch.setattr(fake_claude, "complete", complete)
        return prompts

    def test_pipeline_step_sees_the_local_block(self, db_session, verified_strategy,
                                                fake_claude, monkeypatch):
        from app.pipeline import engine

        _local(db_session, verified_strategy)
        prompts = self._capture(fake_claude, monkeypatch)
        engine.run_step(db_session, verified_strategy, engine.next_step(db_session,
                                                                        verified_strategy))
        assert "LOCAL BUSINESS CONTEXT" in prompts[0]
        assert "within 3 mi of Astoria, NY, US" in prompts[0]
        assert "Catering packages for team lunches" in prompts[0]
        assert "Business buyers only" in prompts[0]

    def test_non_local_product_block_is_unchanged(self, db_session, verified_strategy,
                                                  fake_claude, monkeypatch):
        from app.pipeline import engine

        product = db_session.get(m.Product, verified_strategy.product_id)
        expected = (f"Name: {product.name}\nType: {product.type.value}\n"
                    f"Description: {product.description}")
        assert engine._product_block(product) == expected
        prompts = self._capture(fake_claude, monkeypatch)
        engine.run_step(db_session, verified_strategy, engine.next_step(db_session,
                                                                        verified_strategy))
        assert "LOCAL BUSINESS CONTEXT" not in prompts[0]

    def test_flag_off_removes_the_block(self, db_session, verified_strategy):
        _local(db_session, verified_strategy)
        assert lb.block_for_strategy(db_session, verified_strategy)
        system_settings.set(db_session, lb.FLAG, False)
        assert lb.block_for_strategy(db_session, verified_strategy) == ""

    def test_healthcare_block_carries_the_hipaa_limit(self, db_session, verified_strategy):
        _local(db_session, verified_strategy, business_type="healthcare", offer=None)
        block = lb.block_for_strategy(db_session, verified_strategy)
        assert "Employers in the service area" in block
        assert "patient information" in block and "HIPAA" in block

    def test_distance_rule_is_in_every_block(self, db_session, verified_strategy):
        for key in lb.BUSINESS_TYPE_KEYS:
            _local(db_session, verified_strategy, business_type=key)
            assert "Never state a distance or travel time" in lb.block_for_strategy(
                db_session, verified_strategy)


class TestPersonalization:
    def test_email_prompt_carries_local_context(self, db_session, verified_strategy,
                                                verified_leads, email_sequence, fake_claude):
        from app.services.message_personalization import render_message

        _local(db_session, verified_strategy)
        render_message(db_session, verified_strategy, verified_leads[0],
                       email_sequence.steps[0])
        _, prompt = fake_claude.personalization_calls[-1]
        assert "LOCAL BUSINESS CONTEXT" in prompt and "Astoria" in prompt

    def test_non_local_email_prompt_unchanged(self, db_session, verified_strategy,
                                              verified_leads, email_sequence, fake_claude):
        from app.services.message_personalization import render_message

        render_message(db_session, verified_strategy, verified_leads[0],
                       email_sequence.steps[0])
        _, prompt = fake_claude.personalization_calls[-1]
        assert "LOCAL BUSINESS CONTEXT" not in prompt

    def test_claim_engine_treats_the_area_as_sender_vocabulary(self, db_session,
                                                               verified_strategy):
        from app.services.claim_verification import _sender_vocabulary

        before = _sender_vocabulary(db_session, verified_strategy)
        assert "astoria" not in before
        _local(db_session, verified_strategy)
        after = _sender_vocabulary(db_session, verified_strategy)
        assert "astoria" in after and after.startswith(before)


class TestSourcing:
    def test_extracted_criteria_are_narrowed_to_the_service_area(
            self, db_session, verified_strategy, fake_claude, monkeypatch):
        from app.services.icp_extraction import extract_icp_criteria

        prompts: list[str] = []
        original = fake_claude.complete_json

        def complete_json(system, prompt, **kw):
            prompts.append(prompt)
            return original(system, prompt, **kw)

        monkeypatch.setattr(fake_claude, "complete_json", complete_json)
        _local(db_session, verified_strategy)
        criteria = extract_icp_criteria(db_session, verified_strategy)
        assert criteria["locations"] == ["Astoria, NY, US"]          # not Texas/Georgia
        assert criteria["titles"] == ["Owner", "Operations Manager"]  # the model's, kept
        assert criteria["local_business"]["radius_sent_to_provider"] is False
        assert criteria["local_business"]["service_area"]["radius"] == 3
        assert "LOCAL BUSINESS CONTEXT" in prompts[-1]

    def test_empty_titles_and_industries_come_from_the_type(self, db_session,
                                                            verified_strategy):
        _local(db_session, verified_strategy, business_type="healthcare")
        out = lb.apply_to_criteria(db_session, verified_strategy,
                                   {"titles": [], "industries": [], "locations": ["US"]})
        assert "HR Director" in out["titles"] and "Benefits Manager" in out["titles"]
        assert "insurance" in out["industries"]
        assert out["locations"] == ["Astoria, NY, US"]

    def test_explicit_locations_are_kept(self, db_session, verified_strategy):
        _local(db_session, verified_strategy)
        out = lb.apply_to_criteria(db_session, verified_strategy,
                                   {"titles": ["CTO"], "locations": ["Queens, US"]},
                                   explicit=True)
        assert out["locations"] == ["Queens, US"] and out["titles"] == ["CTO"]

    def test_no_local_context_returns_the_same_dict(self, db_session, verified_strategy):
        criteria = {"titles": ["CTO"], "locations": ["Berlin, DE"]}
        assert lb.apply_to_criteria(db_session, verified_strategy, criteria) is criteria

    def test_apollo_gets_the_location_and_no_radius(self, db_session, verified_strategy):
        _local(db_session, verified_strategy)
        out = lb.apply_to_criteria(db_session, verified_strategy, {"titles": ["CTO"]})
        params = icp_to_apollo_params(out)
        assert params["person_locations[]"] == ["Astoria, NY, US"]
        assert not any("radius" in key or "distance" in key for key in params)

    def test_source_api_stores_local_criteria_on_the_batch(self, leads_client, db_session,
                                                           verified_strategy, chain_calls):
        _local(db_session, verified_strategy)
        r = leads_client.post(f"/strategies/{verified_strategy.id}/leads/source", json={})
        assert r.status_code == 202, r.text
        criteria = r.json()["icp_criteria_json"]
        assert criteria["locations"] == ["Astoria, NY, US"]
        assert criteria["local_business"]["business_type"] == "restaurant"

    def test_source_api_explicit_criteria_fill_missing_location(self, leads_client,
                                                                 db_session, verified_strategy):
        _local(db_session, verified_strategy)
        r = leads_client.post(f"/strategies/{verified_strategy.id}/leads/source",
                              json={"icp_criteria": {"titles": ["Office Manager"]}})
        assert r.status_code == 202, r.text
        assert r.json()["icp_criteria_json"]["locations"] == ["Astoria, NY, US"]


class TestSendTimeMetadata:
    def test_every_type_has_a_profile(self):
        for key in lb.BUSINESS_TYPE_KEYS:
            profile = lb.send_time_profile(key)
            assert profile["business_type"] == key
            for start, end in profile["avoid_local_hours"] + profile["prefer_local_hours"]:
                assert 0 <= start < end <= 24

    def test_restaurant_avoids_service_hours(self):
        profile = lb.send_time_profile("restaurant")
        assert profile["avoid_local_hours"] == [[11, 14], [17, 21]]

    def test_unknown_type_has_none(self):
        assert lb.send_time_profile(None) is None
        assert lb.send_time_profile("casino") is None

    def test_strategy_exposes_business_type(self, db_session, verified_strategy):
        assert verified_strategy.business_type is None
        _local(db_session, verified_strategy, business_type="retail")
        assert verified_strategy.business_type == "retail"

    def test_types_endpoint(self, client):
        r = client.get("/local-business/types")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True and body["winback_enabled"] is False
        keys = [t["key"] for t in body["types"]]
        assert keys == list(lb.BUSINESS_TYPE_KEYS)
        healthcare = next(t for t in body["types"] if t["key"] == "healthcare")
        assert healthcare["winback_allowed"] is False
        assert "not sent to the lead provider" in body["radius_note"]
