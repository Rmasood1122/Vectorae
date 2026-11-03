"""scripts/linkedin_unipile_offboard.py: the one-time session revoke + purge.

Offline: a SQLite file built from the models and an httpx MockTransport
standing in for Unipile. Pins: dry run changes nothing; --apply ends every
not-yet-revoked session (paused accounts too), marks it revoked, keeps a
failed one for a re-run, and purges prospect LinkedIn post data; nothing
identifying is printed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db import models as m
from app.db.base import Base

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location(
        "offboard", ROOT / "scripts" / "linkedin_unipile_offboard.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["offboard"] = mod          # dataclasses resolve annotations here
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def world(tmp_path):
    url = f"sqlite:///{tmp_path / 'off.db'}"
    engine = sa.create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = m.User(email="off@x.test", email_verified=True)
        db.add(user)
        db.flush()
        for ref, active in (("acc-ok", True), ("acc-paused", False), ("acc-fail", True)):
            db.add(m.LinkedInAccount(user_id=user.id, unipile_account_id=ref,
                                     display_name="Me", is_active=active))
        product = m.Product(user=user, name="P", type=m.ProductType.SKILL, description="d")
        db.add(product)
        db.flush()
        strategy = m.Strategy(product_id=product.id, flow_type=m.FlowType.WITH_CLIENTS)
        db.add(strategy)
        db.flush()
        db.add(m.Lead(strategy_id=strategy.id, source="apollo", external_id="L1",
                      full_name="Ana", linkedin_posts_json=[{"text": "SECRET POST"}],
                      enrichment_json={"linkedin_posts": [{"text": "x"}], "keep": 1}))
        db.add(m.Lead(strategy_id=strategy.id, source="apollo", external_id="L2",
                      full_name="Ben", enrichment_json={"keep": 2}))
        db.commit()
    engine.dispose()
    return url


def _http(calls, fail=("acc-fail",)):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("X-API-KEY")))
        if any(request.url.path.endswith(f) for f in fail):
            return httpx.Response(500, json={"type": "errors/unexpected"})
        return httpx.Response(200, json={})
    return httpx.Client(transport=httpx.MockTransport(handler))


def _rows(url):
    engine = sa.create_engine(url)
    with Session(engine) as db:
        accounts = {a.unipile_account_id: (a.is_active, a.status)
                    for a in db.query(m.LinkedInAccount)}
        leads = {l.external_id: (l.linkedin_posts_json, l.enrichment_json)
                 for l in db.query(m.Lead)}
    engine.dispose()
    return accounts, leads


def test_dry_run_changes_nothing_and_calls_nobody(world):
    calls = []
    report = _script().run(world, apply=False, dsn="api8.unipile.com:1", api_key="k",
                           http=_http(calls))
    assert calls == []
    assert report.accounts == 3 and report.leads_purged == 1
    accounts, leads = _rows(world)
    assert accounts["acc-ok"] == (True, "ok")
    assert leads["L1"][0] == [{"text": "SECRET POST"}]


def test_apply_ends_every_session_including_paused_and_purges_posts(world):
    calls = []
    report = _script().run(world, apply=True, dsn="https://api8.unipile.com:1/",
                           api_key="k", http=_http(calls))
    assert sorted(p for _, p, _ in calls) == [
        "/api/v1/accounts/acc-fail", "/api/v1/accounts/acc-ok", "/api/v1/accounts/acc-paused"]
    assert {mth for mth, _, _ in calls} == {"DELETE"} and {k for *_, k in calls} == {"k"}
    assert (report.ended, report.failed) == (2, 1)
    accounts, leads = _rows(world)
    assert accounts["acc-ok"] == (False, "revoked")
    assert accounts["acc-paused"] == (False, "revoked")
    assert accounts["acc-fail"] == (True, "ok")        # left for a re-run
    assert leads["L1"] == (None, {"keep": 1})
    assert leads["L2"] == (None, {"keep": 2})

    calls.clear()
    _script().run(world, apply=True, dsn="api8.unipile.com:1", api_key="k",
                  http=_http(calls, fail=()))
    assert [p for _, p, _ in calls] == ["/api/v1/accounts/acc-fail"]   # only the leftover


def test_apply_without_credentials_reports_failure(world):
    report = _script().run(world, apply=True, dsn=None, api_key=None, http=None)
    assert report.failed == 3 and "required" in report.errors[0]


def test_output_has_counts_only(world, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", world)
    monkeypatch.delenv("UNIPILE_DSN", raising=False)
    assert _script().main([]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "acc-" not in out and "SECRET" not in out and "Ana" not in out
