"""Feature Group 8 — workspaces, roles, manager approval, white label.

What must hold:
  * a member acting in a workspace (X-Workspace-Id) works on the OWNER's
    data; a workspace you are not in is a 404;
  * roles are enforced at the API: viewers read-only; SDRs cannot touch
    integrations/costs or delete; personal routes ignore the header; admin
    rights come from the person signed in, never the workspace owner;
  * an SDR's launch waits for a manager; nothing is enrolled until approval;
  * invitations are single-use, expiring and bound to the invited address;
  * who may change or remove whom follows the role ladder;
  * white label: owner-only, validated, domain verified by DNS, served to
    the matching host only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db import models as m
from app.services import rbac, system_settings, workspaces

from .conftest import auth_headers


def _user(db, email):
    user = m.User(email=email, email_verified=True)
    db.add(user)
    db.commit()
    return user


@pytest.fixture()
def team(db_session, test_user):
    ws = workspaces.personal_workspace(db_session, test_user)
    people = {}
    for role in ("manager", "sdr", "viewer"):
        user = _user(db_session, f"{role}@team.dev")
        db_session.add(m.WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role))
        people[role] = user
    db_session.commit()
    return ws, people


def _as(user, ws=None):
    headers = auth_headers(user)
    if ws is not None:
        headers["X-Workspace-Id"] = str(ws.id)
    return headers


def test_personal_workspace_is_created_once(client, db_session, test_user):
    listed = client.get("/workspaces").json()
    assert len(listed) == 1 and listed[0]["role"] == "owner" and listed[0]["is_personal"]
    assert client.get("/workspaces").json()[0]["id"] == listed[0]["id"]
    assert db_session.query(m.Workspace).count() == 1


def test_members_act_on_the_owners_data(client, team, verified_strategy):
    ws, people = team
    path = f"/strategies/{verified_strategy.id}/funnel"
    assert client.get(path, headers=_as(people["sdr"], ws)).status_code == 200
    assert client.get(path, headers=_as(people["sdr"])).status_code == 404   # own workspace
    outsider = people["viewer"]
    assert client.get("/workspaces", headers=_as(outsider)).status_code == 200


def test_unknown_or_foreign_workspace(client, db_session, team):
    ws, people = team
    stranger = _user(db_session, "stranger@x.dev")
    assert client.get("/deals", headers=_as(stranger, ws)).status_code == 404
    bad = {**auth_headers(people["sdr"]), "X-Workspace-Id": "not-a-uuid"}
    assert client.get("/deals", headers=bad).status_code == 400


def test_roles_are_enforced(client, db_session, team, verified_strategy, test_user):
    ws, people = team
    viewer, sdr, manager = people["viewer"], people["sdr"], people["manager"]
    assert client.get("/deals", headers=_as(viewer, ws)).status_code == 200
    assert client.post("/deals", json={"value": 1}, headers=_as(viewer, ws)).status_code == 403

    made = client.post("/deals", json={"value": 10}, headers=_as(sdr, ws))
    assert made.status_code == 201
    deal = db_session.get(m.Deal, __import__("uuid").UUID(made.json()["id"]))
    assert deal.user_id == test_user.id                 # the OWNER's deal
    assert client.delete(f"/deals/{deal.id}", headers=_as(sdr, ws)).status_code == 403
    assert client.post("/costs", json={"amount": 5}, headers=_as(sdr, ws)).status_code == 403
    assert client.post("/costs", json={"amount": 5}, headers=_as(manager, ws)).status_code == 201
    assert client.delete(f"/deals/{deal.id}", headers=_as(manager, ws)).status_code == 204


def test_personal_routes_and_admin_use_the_signed_in_user(client, db_session, team,
                                                          test_user):
    ws, people = team
    me = client.get("/auth/me", headers=_as(people["sdr"], ws)).json()
    assert me["email"] == "sdr@team.dev"

    test_user.is_admin = True
    db_session.commit()
    assert client.get("/admin/integrations").status_code == 200
    assert client.get("/admin/integrations",
                      headers=_as(people["manager"], ws)).status_code == 403


def _launchable(db, verified_strategy):
    seq = m.Sequence(strategy_id=verified_strategy.id, channel=m.ChannelType.EMAIL,
                     name="Launch me", status=m.SequenceStatus.DRAFT)
    db.add(seq)
    db.flush()
    db.add(m.SequenceStep(sequence_id=seq.id, step_no=1, template="hi", delay_days=0))
    db.commit()
    return seq


def test_sdr_launch_waits_for_a_manager(client, db_session, team, verified_strategy,
                                        verified_leads, gmail_account, fake_claude,
                                        queued_jobs, test_user):
    ws, people = team
    seq = _launchable(db_session, verified_strategy)
    held = client.post(f"/sequences/{seq.id}/enroll", json={}, headers=_as(people["sdr"], ws))
    assert held.status_code == 202 and held.json() == {"enrolled": 0,
                                                       "status": "pending_approval"}
    db_session.refresh(seq)
    assert seq.status is m.SequenceStatus.PENDING_APPROVAL
    assert db_session.query(m.Message).filter_by(sequence_id=seq.id).count() == 0
    notified = {uid for uid, event, _ in queued_jobs["events"] if event == "approval_requested"}
    assert notified == {str(test_user.id), str(people["manager"].id)}

    assert client.post(f"/sequences/{seq.id}/approve",
                       headers=_as(people["sdr"], ws)).status_code == 403
    pending = client.get("/approvals", headers=_as(people["sdr"], ws)).json()
    assert pending[0]["state"] == "pending" and pending[0]["requested_by"] == "sdr@team.dev"

    approved = client.post(f"/sequences/{seq.id}/approve", headers=_as(people["manager"], ws))
    assert approved.json() == {"status": "active", "enrolled": 3}
    assert db_session.query(m.Message).filter_by(sequence_id=seq.id).count() == 3
    decided = [(uid, kw) for uid, event, kw in queued_jobs["events"]
               if event == "approval_decided"]
    assert decided[0][0] == str(people["sdr"].id) and "approved" in decided[0][1]["title"]

    again = client.post(f"/sequences/{seq.id}/enroll", json={}, headers=_as(people["sdr"], ws))
    assert "status" not in again.json()      # approved once; no second approval


def test_reject_and_approval_optional(client, db_session, team, verified_strategy,
                                      verified_leads, gmail_account, fake_claude):
    ws, people = team
    seq = _launchable(db_session, verified_strategy)
    client.post(f"/sequences/{seq.id}/enroll", json={}, headers=_as(people["sdr"], ws))
    rejected = client.post(f"/sequences/{seq.id}/reject", json={"note": "Tone it down"})
    assert rejected.json() == {"status": "draft"}
    declined = client.get("/approvals", headers=_as(people["viewer"], ws)).json()
    assert declined[0]["state"] == "declined" and declined[0]["note"] == "Tone it down"

    ws.approval_required = False
    db_session.commit()
    direct = client.post(f"/sequences/{seq.id}/enroll", json={}, headers=_as(people["sdr"], ws))
    assert direct.json() == {"enrolled": 3}


def test_invitations(client, db_session, team, test_user):
    ws, people = team
    made = client.post("/workspaces/current/invitations",
                       json={"email": "New@Agency.dev", "role": "sdr"})
    assert made.status_code == 201 and made.json()["email"] == "new@agency.dev"
    token = made.json()["link"].split("token=", 1)[1]
    listed = client.get("/workspaces/current/invitations").json()
    assert [i["email"] for i in listed] == ["new@agency.dev"]

    wrong = _user(db_session, "someone@else.dev")
    assert client.post("/workspaces/invitations/accept", json={"token": token},
                       headers=_as(wrong)).status_code == 403
    invitee = _user(db_session, "new@agency.dev")
    ok = client.post("/workspaces/invitations/accept", json={"token": token},
                     headers=_as(invitee))
    assert ok.json() == {"workspace_id": str(ws.id), "name": ws.name, "role": "sdr"}
    assert client.post("/workspaces/invitations/accept", json={"token": token},
                       headers=_as(invitee)).status_code == 409
    roles = {m_["email"]: m_["role"] for m_ in client.get("/workspaces/current/members").json()}
    assert roles["new@agency.dev"] == "sdr"
    ids = {w["id"] for w in client.get("/workspaces", headers=_as(invitee)).json()}
    assert str(ws.id) in ids

    manager = _as(people["manager"], ws)
    assert client.post("/workspaces/current/invitations",
                       json={"email": "boss@x.dev", "role": "manager"},
                       headers=manager).status_code == 403
    assert client.post("/workspaces/current/invitations",
                       json={"email": "rep@x.dev", "role": "sdr"},
                       headers=manager).status_code == 201
    assert client.post("/workspaces/current/invitations",
                       json={"email": "v@x.dev", "role": "viewer"},
                       headers=_as(people["viewer"], ws)).status_code == 403
    assert client.post("/workspaces/current/invitations",
                       json={"email": "sdr@team.dev", "role": "viewer"}).status_code == 409

    late = client.post("/workspaces/current/invitations", json={"email": "late@x.dev",
                                                                "role": "viewer"}).json()
    row = db_session.get(m.WorkspaceInvitation, __import__("uuid").UUID(late["id"]))
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    late_user = _user(db_session, "late@x.dev")
    assert client.post("/workspaces/invitations/accept",
                       json={"token": late["link"].split("token=", 1)[1]},
                       headers=_as(late_user)).status_code == 410


def test_role_ladder(client, team, test_user):
    ws, people = team
    manager = _as(people["manager"], ws)
    sdr_id, viewer_id = people["sdr"].id, people["viewer"].id
    assert client.patch(f"/workspaces/current/members/{sdr_id}", json={"role": "manager"},
                        headers=manager).status_code == 403
    assert client.patch(f"/workspaces/current/members/{sdr_id}", json={"role": "viewer"},
                        headers=manager).status_code == 200
    assert client.patch(f"/workspaces/current/members/{test_user.id}", json={"role": "viewer"},
                        headers=manager).status_code == 403
    assert client.patch(f"/workspaces/current/members/{sdr_id}",
                        json={"role": "manager"}).status_code == 200      # owner may
    assert client.delete(f"/workspaces/current/members/{viewer_id}",
                         headers=manager).status_code == 204
    assert client.delete(f"/workspaces/current/members/{test_user.id}").status_code == 403
    assert client.delete(f"/workspaces/current/members/{people['manager'].id}",
                         headers=manager).status_code == 204                # leaving


def test_white_label(client, db_session, team, monkeypatch):
    ws, people = team
    assert client.put("/workspaces/current/branding", json={"brand_name": "X"},
                      headers=_as(people["manager"], ws)).status_code == 403
    assert client.put("/workspaces/current/branding",
                      json={"primary_color": "blue"}).status_code == 422
    assert client.put("/workspaces/current/branding",
                      json={"custom_domain": "https://bad"}).status_code == 422

    saved = client.put("/workspaces/current/branding", json={
        "white_label_enabled": True, "brand_name": "Acme Growth", "primary_color": "#0f766e",
        "support_email": "help@acme.dev", "custom_domain": "App.Acme.dev"}).json()
    assert saved["custom_domain"] == "app.acme.dev" and not saved["domain_verified"]
    record = saved["verification_record"]
    assert record["name"] == "_leadpilot-verify.app.acme.dev"

    public = client.get("/branding?host=app.acme.dev").json()
    assert public["brand_name"] == "Vectorae"          # not verified yet
    monkeypatch.setattr(workspaces, "_txt_records", lambda name: ["other", record["value"]])
    assert client.post("/workspaces/current/domain/verify").json()["verified"] is True
    public = client.get("/branding?host=app.acme.dev:443").json()
    assert (public["brand_name"], public["primary_color"]) == ("Acme Growth", "#0f766e")
    assert "custom_domain" not in public

    monkeypatch.setattr(settings, "white_label_base_domain", "leadpilot.app")
    assert client.get(f"/branding?host={ws.slug}.leadpilot.app").json()["white_label"] is True
    assert client.get("/branding?host=unknown.leadpilot.app").json()["white_label"] is False

    system_settings.set(db_session, "white_label_allowed", False)
    assert client.put("/workspaces/current/branding",
                      json={"white_label_enabled": True}).status_code == 403


def test_logo_upload_checks_the_bytes(client, tmp_path, monkeypatch, test_user):
    monkeypatch.setattr(settings, "media_dir", str(tmp_path))
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    ok = client.post("/workspaces/current/branding/logo",
                     files={"file": ("logo.png", png, "image/png")})
    assert ok.status_code == 201 and ok.json()["logo_url"].endswith(".png")
    assert list((tmp_path / "branding").iterdir())
    fake = client.post("/workspaces/current/branding/logo",
                       files={"file": ("logo.png", b"<svg onload=alert(1)>", "image/png")})
    assert fake.status_code == 415
    svg = client.post("/workspaces/current/branding/logo",
                      files={"file": ("logo.svg", b"<svg/>", "image/svg+xml")})
    assert svg.status_code == 415


def test_rbac_rules():
    assert rbac.is_personal("/auth/me") and rbac.is_personal("/me/api-keys")
    assert not rbac.is_personal("/meetings") and not rbac.is_personal("/deals")
    rbac.enforce("viewer", "GET", "/deals")
    rbac.enforce("sdr", "POST", "/sequences/x/enroll")
    for role, method, path in (("viewer", "POST", "/deals"), ("sdr", "DELETE", "/deals/1"),
                               ("sdr", "PUT", "/integrations/hubspot/settings"),
                               ("sdr", "POST", "/webhooks/outbound/register")):
        with pytest.raises(Exception) as exc:
            rbac.enforce(role, method, path)
        assert exc.value.status_code == 403
