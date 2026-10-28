"""Tool integrations: AegisAudit, PostIQ, SIGNALFORGE.

The wire formats asserted here were verified against each tool's source
(AegisAudit routes/audits.ts + shared schemas, PostIQ apps-script/Code.js +
core-logic.js, SIGNALFORGE app/control/*). Every external call is mocked —
respx for the HTTP clients, monkeypatch for the API and tasks.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os

import httpx
import pytest
import respx
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db import models as m
from app.db.base import Base
from app.integrations import aegisaudit, postiq, signalforge
from app.services import tool_integrations as svc
from app.workers import tool_integration_tasks as tasks

WEBAPP = "https://script.google.com/macros/s/AKfyc123/exec"
AEGIS = "http://aegis.test:8787"
SF = "http://sf.test:8000"


def _sse(*events: dict) -> str:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


def _ok(data: dict) -> dict:
    return {"success": True, "command": "x", "data": data, "warnings": [], "errors": []}


def _fail(code: str) -> dict:
    return {"success": False, "command": "x", "data": None, "warnings": [],
            "errors": [{"code": code, "message": "refused", "details": {}}]}


# ── AegisAudit client ──────────────────────────────────────────────────────

@respx.mock
def test_aegisaudit_starts_then_follows_event_stream():
    result = {"decision": {"decision": "review"}, "scores": {"overall": 72}, "findings": []}
    start = respx.post(f"{AEGIS}/api/audits").mock(
        return_value=httpx.Response(202, json={"auditId": "a1", "status": "running"}))
    respx.get(f"{AEGIS}/api/audits/a1/events").mock(return_value=httpx.Response(
        200, text=": heartbeat\n\n" + _sse({"type": "progress", "progress": {}},
                                           {"type": "completed", "result": result})))
    assert aegisaudit.audit_response(base_url=AEGIS, prompt="p", answer="a") == result
    sent = json.loads(start.calls.last.request.content)
    assert sent["mode"] == "deep" and sent["providers"] == ["deterministic"]
    assert sent["consentToSendData"] is False


@respx.mock
def test_aegisaudit_failed_event_raises():
    respx.post(f"{AEGIS}/api/audits").mock(return_value=httpx.Response(202, json={"auditId": "a1"}))
    respx.get(f"{AEGIS}/api/audits/a1/events").mock(return_value=httpx.Response(
        200, text=_sse({"type": "failed", "error": "boom", "progress": {}})))
    with pytest.raises(aegisaudit.AegisAuditError, match="boom"):
        aegisaudit.audit_response(base_url=AEGIS, prompt="p", answer="a")


@respx.mock
def test_aegisaudit_consent_refusal_is_permanent():
    respx.post(f"{AEGIS}/api/audits").mock(return_value=httpx.Response(
        403, json={"error": "consent_required", "message": "Confirm first"}))
    with pytest.raises(aegisaudit.AegisAuditError) as exc:
        aegisaudit.audit_response(base_url=AEGIS, prompt="p", answer="a", providers=["gemini"])
    assert exc.value.permanent and "Confirm first" in str(exc.value)


@pytest.mark.parametrize("kwargs", [{"mode": "maximum_strictness"}, {"providers": ["gpt"]}])
def test_aegisaudit_rejects_values_aegisaudit_does_not_know(kwargs):
    with pytest.raises(ValueError):
        aegisaudit.audit_response(base_url=AEGIS, prompt="p", answer="a", **kwargs)


# ── PostIQ client ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://script.google.com/macros/s/x/exec",
    "https://script.google.com.evil.test/macros/s/x/exec",
    "https://evil.test/macros/s/x/exec",
    "https://user@script.google.com/macros/s/x/exec",
    "https://script.google.com/a/other",
    "http://169.254.169.254/latest/meta-data",
])
def test_postiq_url_must_be_apps_script(url):
    with pytest.raises(ValueError):
        postiq.validate_webapp_url(url)


@respx.mock
def test_postiq_health_follows_apps_script_redirect():
    respx.get(WEBAPP).mock(return_value=httpx.Response(
        302, headers={"location": "https://script.googleusercontent.com/echo?x=1"}))
    respx.get("https://script.googleusercontent.com/echo").mock(
        return_value=httpx.Response(200, json={"ok": True, "service": "postiq", "phase": 4}))
    assert postiq.health_check(webapp_url=WEBAPP) is True


@respx.mock
def test_postiq_token_travels_in_body_and_saves_nothing():
    route = respx.post(WEBAPP).mock(return_value=httpx.Response(200, json={
        "ok": False, "error_code": "VALIDATION_FAILED", "message": "Capture failed validation."}))
    postiq.verify_token(webapp_url=WEBAPP, token="tok-12345")  # accepted -> no raise
    sent = json.loads(route.calls.last.request.content)
    assert sent["auth"] == {"client_token": "tok-12345"}
    assert "capture" not in sent  # no capture => PostIQ can never save a row
    assert "x-postiq-token" not in {k.lower() for k in route.calls.last.request.headers}


@respx.mock
@pytest.mark.parametrize("code", ["UNAUTHORIZED", "SERVER_NOT_CONFIGURED", "RATE_LIMITED"])
def test_postiq_token_rejections(code):
    respx.post(WEBAPP).mock(return_value=httpx.Response(200, json={"ok": False, "error_code": code}))
    with pytest.raises(postiq.PostIQError) as exc:
        postiq.verify_token(webapp_url=WEBAPP, token="tok-12345")
    assert exc.value.error_code == code


@respx.mock
def test_postiq_sign_in_page_is_a_clean_error():
    respx.get(WEBAPP).mock(return_value=httpx.Response(200, text="<html>Sign in</html>"))
    respx.post(WEBAPP).mock(return_value=httpx.Response(200, text="<html>Sign in</html>"))
    assert postiq.health_check(webapp_url=WEBAPP) is False
    with pytest.raises(postiq.PostIQError):
        postiq.verify_token(webapp_url=WEBAPP, token="tok-12345")


# ── SIGNALFORGE client ─────────────────────────────────────────────────────

@respx.mock
def test_signalforge_reads_data_from_envelope_and_sends_key():
    route = respx.post(f"{SF}/control/commands/get_icp_score").mock(
        return_value=httpx.Response(200, json=_ok({"overall_score": 81.5, "breakdown": {}})))
    out = signalforge.get_icp_score(base_url=SF, operator_key="k" * 8, prospect_id=7)
    assert out["overall_score"] == 81.5
    assert route.calls.last.request.headers[signalforge.CONTROL_KEY_HEADER] == "k" * 8
    assert json.loads(route.calls.last.request.content) == {"prospect_id": 7}


@respx.mock
@pytest.mark.parametrize("code,permanent", [
    ("PERMISSION_DENIED", True), ("UNAUTHENTICATED", True), ("NOT_FOUND", True),
    ("RATE_LIMITED", False), ("UPSTREAM_ERROR", False), ("INTERNAL_ERROR", False),
])
def test_signalforge_refusals_arrive_as_200_envelopes(code, permanent):
    respx.post(f"{SF}/control/commands/start_research").mock(
        return_value=httpx.Response(200, json=_fail(code)))
    with pytest.raises(signalforge.SignalForgeError) as exc:
        signalforge.start_research(base_url=SF, operator_key="k" * 8, prospect_id=1)
    assert exc.value.error_code == code and exc.value.permanent is permanent


@respx.mock
def test_signalforge_http_failure_is_transient():
    respx.post(f"{SF}/control/commands/start_research").mock(
        return_value=httpx.Response(502, text="bad gateway"))
    with pytest.raises(signalforge.SignalForgeError) as exc:
        signalforge.start_research(base_url=SF, operator_key="k" * 8, prospect_id=1)
    assert exc.value.permanent is False


def test_signalforge_tone_uses_signalforge_enum():
    with pytest.raises(ValueError):
        signalforge.start_research(base_url=SF, operator_key="k" * 8, prospect_id=1,
                                   tone="professional")


@respx.mock
def test_signalforge_health_checks_the_key_not_just_reachability():
    respx.get(f"{SF}/control/health").mock(
        return_value=httpx.Response(200, json=_fail("UNAUTHENTICATED")))
    assert signalforge.health_check(base_url=SF, operator_key="wrong-key") is False
    respx.get(f"{SF}/control/health").mock(return_value=httpx.Response(200, json=_ok({})))
    assert signalforge.health_check(base_url=SF, operator_key="right-key") is True


# ── Service: storage + encryption ──────────────────────────────────────────

def test_secrets_are_encrypted_at_rest(db_session, test_user):
    svc.save_postiq_config(db_session, test_user.id, webapp_url=WEBAPP, token="tok-secret")
    svc.save_signalforge_config(db_session, base_url=SF, operator_key="op-secret-key")
    rows = {r.config_key: r for r in db_session.query(m.ToolIntegrationSettings)}
    assert rows["postiq_token"].is_encrypted and "tok-secret" not in rows["postiq_token"].config_value
    assert rows["signalforge_operator_key"].is_encrypted
    assert not rows["webapp_url"].is_encrypted
    assert svc.get_postiq_config(db_session, test_user.id)["token"] == "tok-secret"
    assert svc.get_signalforge_config(db_session)["operator_key"] == "op-secret-key"


def test_workspace_settings_update_in_place(db_session):
    svc.save_signalforge_config(db_session, base_url=SF, operator_key="first-key-1")
    svc.save_signalforge_config(db_session, base_url=SF, operator_key="second-key-2")
    assert db_session.query(m.ToolIntegrationSettings).filter_by(
        config_key="signalforge_operator_key").count() == 1
    assert svc.get_signalforge_config(db_session)["operator_key"] == "second-key-2"
    svc.delete_signalforge_config(db_session)
    assert svc.get_signalforge_config(db_session) is None


def test_postiq_config_is_per_user(db_session, test_user):
    other = m.User(email="other@example.com")
    db_session.add(other)
    db_session.commit()
    svc.save_postiq_config(db_session, test_user.id, webapp_url=WEBAPP, token="tok-12345")
    assert svc.get_postiq_config(db_session, other.id) is None
    svc.delete_postiq_config(db_session, other.id)
    assert svc.get_postiq_config(db_session, test_user.id) is not None


# ── API ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("get", "/integrations/tools/aegisaudit"),
    ("put", "/integrations/tools/aegisaudit"),
    ("get", "/integrations/tools/signalforge"),
    ("put", "/integrations/tools/signalforge"),
    ("delete", "/integrations/tools/signalforge"),
])
def test_workspace_tools_require_admin(client, method, path):
    kwargs = {"json": {"operator_key": "x" * 8}} if method == "put" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 403


def test_signalforge_admin_flow_never_returns_key(client, db_session, test_user, monkeypatch):
    test_user.is_admin = True
    db_session.commit()
    assert client.get("/integrations/tools/signalforge").json() == {"configured": False}
    assert client.put("/integrations/tools/signalforge",
                      json={"base_url": "ftp://x", "operator_key": "op-key-123"}).status_code == 422
    assert client.put("/integrations/tools/signalforge",
                      json={"base_url": SF + "/", "operator_key": "op-key-123"}).json() == {
        "saved": True}
    status = client.get("/integrations/tools/signalforge").json()
    assert status == {"configured": True, "base_url": SF, "key_preview": "op-k••••"}

    monkeypatch.setattr(signalforge, "health_check", lambda **kw: False)
    assert client.post("/integrations/tools/signalforge/test").status_code == 503
    monkeypatch.setattr(signalforge, "health_check", lambda **kw: True)
    assert client.post("/integrations/tools/signalforge/test").json()["connected"] is True
    assert client.delete("/integrations/tools/signalforge").status_code == 204


def test_aegisaudit_admin_config(client, db_session, test_user):
    test_user.is_admin = True
    db_session.commit()
    assert client.get("/integrations/tools/aegisaudit").json()["enabled"] is False
    assert client.put("/integrations/tools/aegisaudit",
                      json={"port": "99999"}).status_code == 422
    assert client.put("/integrations/tools/aegisaudit",
                      json={"default_mode": "maximum_strictness"}).status_code == 422
    assert client.put("/integrations/tools/aegisaudit",
                      json={"host": "aegis.internal", "default_mode": "maximum"}).status_code == 200
    cfg = client.get("/integrations/tools/aegisaudit").json()
    assert cfg["base_url"] == "http://aegis.internal:8787" and cfg["default_mode"] == "maximum"


def test_postiq_user_flow(client, monkeypatch):
    assert client.get("/integrations/tools/postiq").json() == {"connected": False}
    assert client.put("/integrations/tools/postiq",
                      json={"webapp_url": "http://127.0.0.1:8787/api", "token": "tok-12345"}
                      ).status_code == 422
    assert client.put("/integrations/tools/postiq",
                      json={"webapp_url": WEBAPP, "token": "tok-12345"}).status_code == 200
    status = client.get("/integrations/tools/postiq").json()
    assert status == {"connected": True, "webapp_url": WEBAPP, "token_configured": True}

    monkeypatch.setattr(postiq, "health_check", lambda **kw: True)
    monkeypatch.setattr(postiq, "verify_token", lambda **kw: None)
    assert client.post("/integrations/tools/postiq/test").json() == {
        "connected": True, "token_valid": True}

    def rejected(**kw):
        raise postiq.PostIQError("PostIQ rejected the token.", error_code="UNAUTHORIZED")
    monkeypatch.setattr(postiq, "verify_token", rejected)
    bad = client.post("/integrations/tools/postiq/test")
    assert bad.status_code == 502 and "rejected" in bad.json()["detail"]

    assert client.get("/integrations/tools/postiq/drafts").status_code == 501
    assert client.delete("/integrations/tools/postiq").status_code == 204
    assert client.get("/integrations/tools/postiq").json() == {"connected": False}


# ── Celery tasks ───────────────────────────────────────────────────────────

@pytest.fixture()
def task_db(db_session, monkeypatch):
    monkeypatch.setattr(tasks, "SessionLocal", lambda: contextlib.nullcontext(db_session))
    return db_session


def test_audit_is_skipped_when_disabled(task_db):
    out = tasks.audit_draft_with_aegisaudit.run(prompt="p", answer="a")
    assert out["decision"] == "pass" and out["gate"] == "skipped_disabled"


@pytest.mark.parametrize("gate,decision", [
    ("not_ready", "block"), ("human_review", "flag"), ("review", "flag"),
    ("ready_for_human_review", "pass"), ("something_new", "flag"),
])
def test_audit_decision_maps_to_send_action(task_db, monkeypatch, gate, decision):
    svc.save_aegisaudit_config(task_db, enabled=True)
    monkeypatch.setattr(aegisaudit, "audit_response", lambda **kw: {
        "decision": {"decision": gate}, "scores": {"overall": 64}, "findings": [{"x": 1}]})
    out = tasks.audit_draft_with_aegisaudit.run(prompt="p", answer="a")
    assert out == {"decision": decision, "gate": gate, "score": 64, "findings": [{"x": 1}]}


def test_audit_fails_open(task_db, monkeypatch):
    svc.save_aegisaudit_config(task_db, enabled=True)

    def down(**kw):
        raise aegisaudit.AegisAuditError("unreachable")
    monkeypatch.setattr(aegisaudit, "audit_response", down)
    out = tasks.audit_draft_with_aegisaudit.run(prompt="p", answer="a")
    assert out["decision"] == "pass" and out["gate"] == "skipped_error"


def _sf_ok(monkeypatch, **overrides):
    calls = []

    def rec(name, value):
        def fn(**kw):
            calls.append((name, kw))
            if isinstance(value, Exception):
                raise value
            return value
        return fn
    responses = {
        "start_research": {"research_job_id": 3, "final_status": "AWAITING_APPROVAL",
                           "draft_id": 12, "icp_score": 70.0, "warnings": ["thin"]},
        "get_icp_score": {"overall_score": 78.0, "breakdown": {"fit": 1}},
        "get_prospect": {"latest_draft": {"id": 12, "subject": "S", "body": "B",
                                          "status": "REVIEW_REQUIRED"}},
    }
    responses.update(overrides)
    for name, value in responses.items():
        monkeypatch.setattr(signalforge, name, rec(name, value))
    return calls


def test_signalforge_client_never_creates_prospects():
    """Vectorae's key holds READ/RESEARCH/AI_GENERATION only; creating needs WRITE."""
    assert not hasattr(signalforge, "create_prospect")


@pytest.mark.parametrize("prospect_id", [None, ""])
def test_signalforge_task_requires_existing_prospect(task_db, monkeypatch, prospect_id):
    svc.save_signalforge_config(task_db, base_url=SF, operator_key="op-key-123")
    calls = _sf_ok(monkeypatch)
    out = tasks.signalforge_research_lead.run(lead_id="L1", prospect_id=prospect_id)
    assert out["skipped"] is True and out["reason"] == "no_prospect_id_provided"
    assert "Create the prospect in SIGNALFORGE first" in out["message"]
    assert calls == []


def test_signalforge_research_task_reads_existing_draft(task_db, monkeypatch):
    kw = {"lead_id": "L1", "prospect_id": "9"}  # JSON-serialised args may arrive as str
    assert tasks.signalforge_research_lead.run(**kw)["reason"] == "signalforge_not_configured"
    svc.save_signalforge_config(task_db, base_url=SF, operator_key="op-key-123")
    calls = _sf_ok(monkeypatch)
    assert tasks.signalforge_research_lead.run(**kw) == {
        "lead_id": "L1", "prospect_id": 9, "research_job_id": 3,
        "final_status": "AWAITING_APPROVAL", "icp_score": 78.0, "icp_breakdown": {"fit": 1},
        "draft_id": 12, "draft_subject": "S", "draft_body": "B",
        "draft_status": "REVIEW_REQUIRED", "warnings": ["thin"]}
    assert [c[0] for c in calls] == ["start_research", "get_icp_score", "get_prospect"]
    assert calls[0][1]["prospect_id"] == 9


def test_signalforge_unknown_prospect_is_explained(task_db, monkeypatch):
    svc.save_signalforge_config(task_db, base_url=SF, operator_key="op-key-123")
    _sf_ok(monkeypatch, start_research=signalforge.SignalForgeError(
        "no", error_code="NOT_FOUND"))
    out = tasks.signalforge_research_lead.run(lead_id="L1", prospect_id=404)
    assert out["skipped"] is True and out["reason"] == "prospect_not_found"
    assert "Create the prospect in SIGNALFORGE first" in out["message"]


def test_signalforge_score_and_draft_are_best_effort(task_db, monkeypatch):
    svc.save_signalforge_config(task_db, base_url=SF, operator_key="op-key-123")
    err = signalforge.SignalForgeError("busy", error_code="RATE_LIMITED")
    _sf_ok(monkeypatch, get_icp_score=err, get_prospect=err)
    out = tasks.signalforge_research_lead.run(lead_id="L1", prospect_id=9)
    assert out["icp_score"] == 70.0 and out["draft_id"] == 12 and out["draft_body"] is None


def test_signalforge_task_outlives_the_research_timeout():
    task = tasks.signalforge_research_lead
    assert task.soft_time_limit > signalforge.RESEARCH_TIMEOUT_SECONDS


# ── Migration 0032 matches the model ───────────────────────────────────────

def _load_migration():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "m0032", os.path.join(root, "alembic", "versions", "0032_tool_integrations.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _describe(inspector):
    t = "tool_integration_settings"
    return {
        "columns": {c["name"]: (str(c["type"]), bool(c["nullable"]))
                    for c in inspector.get_columns(t)},
        "indexes": {i["name"]: (tuple(i["column_names"]), bool(i["unique"]))
                    for i in inspector.get_indexes(t)},
        "uniques": {u["name"]: tuple(u["column_names"])
                    for u in inspector.get_unique_constraints(t)},
        "fks": {(tuple(f["constrained_columns"]), f["referred_table"])
                for f in inspector.get_foreign_keys(t)},
    }


def test_migration_matches_model(tmp_path):
    models_engine = sa.create_engine(f"sqlite:///{tmp_path / 'models.db'}")
    Base.metadata.create_all(models_engine)

    mig_engine = sa.create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    meta = sa.MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.sorted_tables:
        table.to_metadata(meta)
    meta.create_all(mig_engine, tables=[meta.tables["users"]])
    module = _load_migration()
    assert module.down_revision == "0031_trust_deliverability"
    with mig_engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        module.upgrade()

    assert _describe(sa.inspect(mig_engine)) == _describe(sa.inspect(models_engine))

    with mig_engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        module.downgrade()
    assert "tool_integration_settings" not in sa.inspect(mig_engine).get_table_names()
