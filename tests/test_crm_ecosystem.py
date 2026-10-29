"""Feature Group 4 — CRM & ecosystem.

What must hold:
  * outbound webhooks: https + public only, secrets encrypted and shown once,
    deliveries signed (t=,v1=), retried with backoff, 410 unsubscribes, a
    host resolving to a private address is never called;
  * API keys authenticate like a bearer token, can be revoked, and never
    reach an admin route;
  * Slack / HubSpot / Salesforce OAuth states are purpose-bound; callbacks
    store credentials in TokenStore and redirect to the settings page;
  * CRM sync is idempotent (links unique both ways), inbound changes only
    move leads forward and never reopen a closed one, and both inbound
    webhooks fail closed on a bad signature;
  * the four spec events reach webhooks/Slack without a second push.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import date
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.db import models as m
from app.integrations import hubspot as hubspot_mod
from app.integrations import slack as slack_mod
from app.integrations.hubspot import HubSpotError
from app.integrations.token_store import TokenStore
from app.services import (
    api_keys,
    crm_sync,
    event_bus,
    oauth_state,
    webhook_delivery as wd,
)
from app.workers import crm_tasks, lead_tasks, outreach_tasks

from .conftest import auth_headers


# --------------------------------------------------------------------------
# Outbound webhooks
# --------------------------------------------------------------------------


@pytest.fixture()
def public_dns(monkeypatch):
    monkeypatch.setattr(wd, "_resolve", lambda host: ["93.184.216.34"])


class FakeHttp:
    def __init__(self, status=200):
        self.status = status
        self.posts = []

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, content=None, headers=None):
        self.posts.append({"url": url, "body": content, "headers": headers})
        if isinstance(self.status, Exception):
            raise self.status
        return httpx.Response(self.status)


def test_register_validates_and_returns_secret_once(client, db_session):
    resp = client.post("/webhooks/outbound/register", json={
        "url": "https://hooks.zapier.com/abc", "events": ["meeting_booked", "lead_replied"],
        "source": "zapier"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["secret"].startswith("whsec_")
    assert body["events"] == ["meeting_booked", "reply_received"]   # alias normalised
    target = db_session.get(m.WebhookTarget, uuid.UUID(body["id"]))
    assert body["secret"] not in target.secret_encrypted

    listed = client.get("/webhooks/outbound").json()
    assert listed[0]["id"] == body["id"] and "secret" not in listed[0]

    for url in ("http://hooks.example.com/x", "https://10.0.0.5/x", "https://127.0.0.1/x",
                "https://localhost/x", "https://user:pw@hooks.example.com/x"):
        assert client.post("/webhooks/outbound/register",
                           json={"url": url, "events": ["meeting_booked"]}).status_code == 422
    assert client.post("/webhooks/outbound/register", json={
        "url": "https://hooks.example.com/x", "events": ["nope"]}).status_code == 422


def test_legacy_targets_routes_still_work(client):
    made = client.post("/webhooks/targets", json={
        "url": "https://crm.example.com/hook", "secret": "s" * 20,
        "event_types": ["campaign_paused"]})
    assert made.status_code == 201 and made.json()["status"] == "created"
    rows = client.get("/webhooks/targets").json()
    assert rows[0]["event_types"] == ["campaign_paused"]
    assert client.delete(f"/webhooks/targets/{made.json()['id']}").json()["status"] == "deleted"


def _target(db, user, events=("reply_received",), secret="s" * 24):
    target, _ = wd.create_target(db, user.id, "https://hooks.example.com/in", list(events),
                                 secret=secret)
    return target


def test_fire_event_only_hits_subscribers(db_session, test_user, queued_jobs):
    subscribed = _target(db_session, test_user, ("reply_received",))
    _target(db_session, test_user, ("meeting_booked",))
    ids = wd.fire_event(db_session, test_user.id, "lead_replied", {"x": 1})
    assert len(ids) == 1 and queued_jobs["webhooks"] == [(ids[0], "0")]
    delivery = db_session.get(m.WebhookDelivery, uuid.UUID(ids[0]))
    assert delivery.target_id == subscribed.id
    assert delivery.payload_json["event"] == "reply_received"
    assert delivery.payload_json["data"] == {"x": 1}


def test_event_hub_fans_out_to_webhooks(db_session, test_user, queued_jobs, monkeypatch):
    monkeypatch.setattr(event_bus, "_push", lambda *a, **k: 0)
    _target(db_session, test_user, ("deal_won",))
    result = event_bus.emit(db_session, test_user.id, "deal_won", title="t", body="b",
                            slack=False, webhook_payload={"deal_id": "d1"})
    assert result["webhooks"] == 1


def test_delivery_is_signed_and_verifiable(db_session, test_user, public_dns, monkeypatch):
    http = FakeHttp(200)
    monkeypatch.setattr(wd, "_http", http)
    target = _target(db_session, test_user, secret="topsecret-signing-key")
    delivery = wd.create_delivery(db_session, target, "reply_received", {"a": 1}, enqueue=False)
    assert wd.deliver(db_session, delivery.id) == "delivered"
    sent = http.posts[0]
    assert wd.verify_signature("topsecret-signing-key", sent["body"],
                               sent["headers"]["X-LeadPilot-Signature"])
    assert not wd.verify_signature("wrong-secret", sent["body"],
                                   sent["headers"]["X-LeadPilot-Signature"])
    assert sent["headers"]["X-LeadPilot-Delivery"] == str(delivery.id)
    assert json.loads(sent["body"])["data"] == {"a": 1}
    db_session.refresh(delivery)
    assert delivery.status == "delivered" and delivery.attempts == 1


def test_delivery_retries_then_exhausts(db_session, test_user, public_dns, monkeypatch,
                                        queued_jobs):
    monkeypatch.setattr(wd, "_http", FakeHttp(500))
    exhausted = []
    monkeypatch.setattr(wd, "_record_exhaustion", lambda t, d, e: exhausted.append(e))
    target = _target(db_session, test_user)
    delivery = wd.create_delivery(db_session, target, "reply_received", {}, enqueue=False)
    # The first attempt + 5 retries (30s, 2m, 10m, 30m, 2h): 6 attempts. This
    # test used to pin 5 attempts, i.e. the off-by-one that meant the
    # documented 2-hour retry never happened (help-center discoveries #12).
    outcomes = [wd.deliver(db_session, delivery.id) for _ in range(6)]
    assert outcomes == ["retry"] * 5 + ["exhausted"]
    assert [c for _, c in queued_jobs["webhooks"]] == ["30", "120", "600", "1800", "7200"]
    assert exhausted == ["HTTP 500"]
    assert wd.deliver(db_session, delivery.id) == "skipped"


def test_gone_unsubscribes_and_private_hosts_are_blocked(db_session, test_user, monkeypatch):
    monkeypatch.setattr(wd, "_resolve", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(wd, "_http", FakeHttp(410))
    target = _target(db_session, test_user)
    d1 = wd.create_delivery(db_session, target, "reply_received", {}, enqueue=False)
    assert wd.deliver(db_session, d1.id) == "gone"
    db_session.refresh(target)
    assert target.active is False and "410" in target.disabled_reason

    monkeypatch.setattr(wd, "_resolve", lambda host: ["169.254.169.254"])
    http = FakeHttp(200)
    monkeypatch.setattr(wd, "_http", http)
    other = _target(db_session, test_user)
    d2 = wd.create_delivery(db_session, other, "reply_received", {}, enqueue=False)
    assert wd.deliver(db_session, d2.id) == "blocked"
    assert http.posts == []


def test_webhook_api_test_deliveries_and_ownership(client, db_session, test_user,
                                                   queued_jobs):
    made = client.post("/webhooks/outbound/register", json={
        "url": "https://hooks.example.com/x", "events": ["lead_sourced"]}).json()
    queued = client.post(f"/webhooks/outbound/{made['id']}/test")
    assert queued.status_code == 202 and queued.json()["event"] == "lead_sourced"
    assert len(queued_jobs["webhooks"]) == 1
    assert client.get("/webhooks/outbound/deliveries").json()[0]["event"] == "lead_sourced"
    events = {e["event"]: e for e in client.get("/webhooks/outbound/events").json()}
    assert {n for n, e in events.items() if e["zapier"]} == set(wd.ZAPIER_EVENTS)

    other = m.User(email="o@x.dev", email_verified=True)
    db_session.add(other)
    db_session.commit()
    foreign = _target(db_session, other)
    assert client.delete(f"/webhooks/outbound/{foreign.id}").status_code == 404
    assert client.delete(f"/webhooks/outbound/{made['id']}").status_code == 204


# --------------------------------------------------------------------------
# API keys
# --------------------------------------------------------------------------


def test_api_keys_authenticate_revoke_and_never_reach_admin(client, db_session, test_user):
    made = client.post("/me/api-keys", json={"name": "Zapier"})
    assert made.status_code == 201
    key = made.json()["key"]
    assert key.startswith("lpk_") and made.json()["prefix"] == key[:12]
    row = db_session.get(m.ApiKey, uuid.UUID(made.json()["id"]))
    assert row.key_hash != key and key not in row.key_hash

    listed = client.get("/me/api-keys", headers={"Authorization": f"Bearer {key}"})
    assert listed.status_code == 200 and "key" not in listed.json()[0]
    assert client.get("/me/api-keys", headers={"Authorization": "",
                                               "X-API-Key": key}).status_code == 200

    test_user.is_admin = True
    db_session.commit()
    assert client.get("/admin/integrations").status_code == 200
    assert client.get("/admin/integrations",
                      headers={"Authorization": f"Bearer {key}"}).status_code == 403

    assert client.delete(f"/me/api-keys/{made.json()['id']}").status_code == 204
    assert client.get("/me/api-keys",
                      headers={"Authorization": f"Bearer {key}"}).status_code == 401
    assert client.get("/me/api-keys",
                      headers={"Authorization": "Bearer lpk_forged"}).status_code == 401


def test_api_key_limit(db_session, test_user):
    for i in range(api_keys.MAX_ACTIVE):
        api_keys.create(db_session, test_user.id, f"k{i}")
    with pytest.raises(api_keys.ApiKeyLimit):
        api_keys.create(db_session, test_user.id, "one too many")


# --------------------------------------------------------------------------
# OAuth states and Slack
# --------------------------------------------------------------------------


def test_oauth_state_is_purpose_bound_and_expires(test_user):
    state = oauth_state.make(test_user.id, "slack")
    assert oauth_state.parse(state, "slack") == test_user.id
    with pytest.raises(oauth_state.InvalidState):
        oauth_state.parse(state, "hubspot")
    with pytest.raises(oauth_state.InvalidState):
        oauth_state.parse(oauth_state.make(test_user.id, "slack", ttl=-1), "slack")
    with pytest.raises(oauth_state.InvalidState):
        oauth_state.parse("garbage", "slack")


@pytest.fixture()
def slack_app(db_session):
    TokenStore.set(db_session, None, "slack_app", "client_id", "sl-id")
    TokenStore.set(db_session, None, "slack_app", "client_secret", "sl-secret")


def test_slack_connect_flow(client, db_session, test_user, slack_app, monkeypatch):
    auth = client.get("/integrations/slack/auth-url").json()["auth_url"]
    query = parse_qs(urlparse(auth).query)
    assert query["client_id"] == ["sl-id"] and "chat:write.public" in query["scope"][0]

    monkeypatch.setattr(slack_mod, "exchange_code", lambda *a: {
        "ok": True, "access_token": "xoxb-1", "team": {"name": "Acme"}})
    done = client.get(f"/integrations/slack/callback?code=c&state={query['state'][0]}",
                      follow_redirects=False)
    assert done.status_code == 303 and "slack=connected" in done.headers["location"]
    assert TokenStore.get(db_session, test_user.id, "slack", "bot_token") == "xoxb-1"

    wrong = oauth_state.make(test_user.id, "hubspot")
    bad = client.get(f"/integrations/slack/callback?code=c&state={wrong}",
                     follow_redirects=False)
    assert "slack=error" in bad.headers["location"]

    monkeypatch.setattr(slack_mod, "list_channels",
                        lambda token: [{"id": "C1", "name": "sales", "is_private": False}])
    assert client.put("/integrations/slack/channel",
                      json={"channel_id": "C9"}).status_code == 422
    chosen = client.put("/integrations/slack/channel", json={"channel_id": "C1"}).json()
    assert chosen["channel_name"] == "sales"
    posted = []
    monkeypatch.setattr(slack_mod, "post_message", lambda *a, **k: posted.append(a) or {})
    assert client.post("/integrations/slack/send-test").json() == {"ok": True}
    assert posted[0][1] == "C1"
    assert client.delete("/integrations/slack").status_code == 204
    assert client.get("/integrations/slack").json()["connected"] is False


def test_slack_needs_app_config(client):
    assert client.get("/integrations/slack/auth-url").status_code == 503


# --------------------------------------------------------------------------
# HubSpot
# --------------------------------------------------------------------------


class FakeHubSpot:
    def __init__(self):
        self.contacts, self.deals, self.assoc, self.calls = {}, {}, [], []
        self._n = 0

    def _id(self):
        self._n += 1
        return str(1000 + self._n)

    def find_contact_by_email(self, email):
        return next((c for c, p in self.contacts.items() if p.get("email") == email), None)

    def create_contact(self, props):
        cid = self._id()
        self.contacts[cid] = dict(props)
        self.calls.append(("create_contact", cid))
        return cid

    def update_contact(self, cid, props):
        if cid not in self.contacts:
            raise HubSpotError("not found", 404)
        self.contacts[cid].update(props)
        self.calls.append(("update_contact", cid))

    def create_deal(self, props):
        did = self._id()
        self.deals[did] = dict(props)
        return did

    def update_deal(self, did, props):
        self.deals[did].update(props)

    def associate_deal_contact(self, did, cid):
        self.assoc.append((did, cid))

    def get_deal(self, did):
        return {"id": did, "properties": self.deals[did], "associations": {"contacts": {
            "results": [{"id": c} for d, c in self.assoc if d == did]}}}

    def batch_read(self, object_type, ids, properties):
        store = self.contacts if object_type == "contacts" else self.deals
        return [{"id": i, "properties": {k: store[i].get(k) for k in properties}}
                for i in ids if i in store]


@pytest.fixture()
def hs(db_session, test_user, monkeypatch):
    fake = FakeHubSpot()
    monkeypatch.setattr(crm_sync, "hubspot_client", lambda db, conn: fake)
    TokenStore.set(db_session, None, "hubspot_app", "client_id", "hs-id")
    TokenStore.set(db_session, None, "hubspot_app", "client_secret", "hs-secret")
    conn = crm_sync.upsert_connection(db_session, test_user.id, "hubspot", account_id="777")
    return SimpleNamespace(fake=fake, conn=conn)


def _engaged(db, lead, status=m.LeadStatus.REPLIED):
    lead.status = status
    db.commit()
    return lead


def test_hubspot_callback_connects_and_queues_full_sync(client, db_session, test_user,
                                                        monkeypatch, queued_jobs):
    TokenStore.set(db_session, None, "hubspot_app", "client_id", "hs-id")
    TokenStore.set(db_session, None, "hubspot_app", "client_secret", "hs-secret")
    auth = client.get("/integrations/hubspot/auth-url").json()["auth_url"]
    state = parse_qs(urlparse(auth).query)["state"][0]
    monkeypatch.setattr(hubspot_mod, "exchange_code", lambda *a: {
        "access_token": "at", "refresh_token": "rt", "expires_in": 1800})
    monkeypatch.setattr(hubspot_mod, "token_info",
                        lambda token, *creds: {"hub_id": 777, "hub_domain": "acme.hubspot.com"})
    done = client.get(f"/integrations/hubspot/callback?code=c&state={state}",
                      follow_redirects=False)
    assert "hubspot=connected" in done.headers["location"]
    conn = crm_sync.get_connection(db_session, test_user.id, "hubspot")
    assert conn.account_id == "777" and conn.account_name == "acme.hubspot.com"
    assert TokenStore.get(db_session, test_user.id, "hubspot", "refresh_token") == "rt"
    assert queued_jobs["crm"] == [("sync", str(conn.id), True)]
    status = {s["provider"]: s for s in client.get("/integrations/crm").json()}
    assert status["hubspot"]["connected"] and not status["salesforce"]["connected"]


def test_push_lead_is_idempotent_and_owner_scoped(db_session, test_user, hs, verified_leads):
    lead = _engaged(db_session, verified_leads[0])
    first = crm_sync.push_lead(db_session, test_user.id, lead.id)["hubspot"]
    second = crm_sync.push_lead(db_session, test_user.id, lead.id)["hubspot"]
    assert first == second and len(hs.fake.contacts) == 1
    contact = hs.fake.contacts[first]
    assert contact["hs_lead_status"] == "CONNECTED" and contact["email"] == lead.email
    assert "lifecyclestage" not in contact

    lead.email = None
    db_session.commit()
    assert crm_sync.push_lead(db_session, test_user.id, lead.id) == {}
    assert crm_sync.push_lead(db_session, uuid.uuid4(), verified_leads[1].id) == {}


def test_push_deal_associates_contact(db_session, test_user, hs, verified_leads):
    lead = _engaged(db_session, verified_leads[0], m.LeadStatus.OPPORTUNITY)
    deal = m.Deal(user_id=test_user.id, lead_id=lead.id, strategy_id=lead.strategy_id,
                  name="Acme retainer", value_cents=250_000, stage=m.DealStage.WON,
                  close_date=date(2026, 9, 5))
    db_session.add(deal)
    db_session.commit()
    remote = crm_sync.push_deal(db_session, test_user.id, deal.id)["hubspot"]
    assert hs.fake.deals[remote]["dealstage"] == "closedwon"
    assert hs.fake.deals[remote]["amount"] == "2500.00"
    assert hs.fake.assoc == [(remote, crm_sync._link(db_session, test_user.id, "hubspot",
                                                      "lead", lead.id).remote_id)]


def _hs_headers(body: bytes, secret="hs-secret", uri="http://testserver/webhooks/hubspot"):
    ts = str(int(time.time() * 1000))
    sig = base64.b64encode(hmac.new(secret.encode(), b"POST" + uri.encode() + body + ts.encode(),
                                    hashlib.sha256).digest()).decode()
    return {"X-HubSpot-Signature-v3": sig, "X-HubSpot-Request-Timestamp": ts,
            "Content-Type": "application/json"}


def test_hubspot_webhook_applies_forward_changes_only(client, db_session, test_user, hs,
                                                      verified_leads, email_sequence):
    lead = _engaged(db_session, verified_leads[0])
    contact = crm_sync.push_lead(db_session, test_user.id, lead.id)["hubspot"]
    db_session.add(m.SequenceEnrollment(sequence_id=email_sequence.id, lead_id=lead.id,
                                        status=m.EnrollmentStatus.ACTIVE))
    db_session.commit()
    client.headers.pop("Authorization", None)

    backwards = json.dumps([{"portalId": 777, "subscriptionType": "contact.propertyChange",
                             "objectId": int(contact), "propertyName": "lifecyclestage",
                             "propertyValue": "lead"}]).encode()
    assert client.post("/webhooks/hubspot", content=backwards,
                       headers=_hs_headers(backwards)).json()["applied"] == 0

    body = json.dumps([{"portalId": 777, "subscriptionType": "contact.propertyChange",
                        "objectId": int(contact), "propertyName": "hs_lead_status",
                        "propertyValue": "UNQUALIFIED"}]).encode()
    assert client.post("/webhooks/hubspot", content=body,
                       headers=_hs_headers(body, secret="wrong")).status_code == 401
    ok = client.post("/webhooks/hubspot", content=body, headers=_hs_headers(body))
    assert ok.status_code == 200 and ok.json()["applied"] == 1
    db_session.refresh(lead)
    assert lead.status is m.LeadStatus.DISQUALIFIED
    enrollment = db_session.query(m.SequenceEnrollment).filter_by(lead_id=lead.id).one()
    assert enrollment.status is m.EnrollmentStatus.STOPPED

    # A closed lead is never reopened from the CRM.
    reopen = json.dumps([{"portalId": 777, "subscriptionType": "contact.propertyChange",
                          "objectId": int(contact), "propertyName": "lifecyclestage",
                          "propertyValue": "opportunity"}]).encode()
    client.post("/webhooks/hubspot", content=reopen, headers=_hs_headers(reopen))
    db_session.refresh(lead)
    assert lead.status is m.LeadStatus.DISQUALIFIED


def test_hubspot_deal_webhook_and_import(client, db_session, test_user, hs, verified_leads,
                                         queued_jobs):
    lead = _engaged(db_session, verified_leads[0], m.LeadStatus.OPPORTUNITY)
    deal = m.Deal(user_id=test_user.id, lead_id=lead.id, strategy_id=lead.strategy_id,
                  name="D", value_cents=100, stage=m.DealStage.OPEN)
    db_session.add(deal)
    db_session.commit()
    remote = crm_sync.push_deal(db_session, test_user.id, deal.id)["hubspot"]
    client.headers.pop("Authorization", None)
    body = json.dumps([
        {"portalId": 777, "subscriptionType": "deal.propertyChange", "objectId": int(remote),
         "propertyName": "amount", "propertyValue": "900.50"},
        {"portalId": 777, "subscriptionType": "deal.propertyChange", "objectId": int(remote),
         "propertyName": "dealstage", "propertyValue": "closedwon"},
        {"portalId": 777, "subscriptionType": "deal.creation", "objectId": 5555},
        {"portalId": 999, "subscriptionType": "deal.creation", "objectId": 6666},
    ]).encode()
    result = client.post("/webhooks/hubspot", content=body, headers=_hs_headers(body)).json()
    assert result["applied"] == 2 and result["imports_queued"] == 1
    db_session.refresh(deal)
    db_session.refresh(lead)
    assert deal.stage is m.DealStage.WON and deal.value_cents == 90_050
    assert lead.status is m.LeadStatus.CLOSED_WON
    assert queued_jobs["crm"] == [("import", str(test_user.id), "5555")]

    contact = crm_sync._link(db_session, test_user.id, "hubspot", "lead", lead.id).remote_id
    hs.fake.deals["5555"] = {"dealname": "Upsell", "amount": "1200", "dealstage": "qualifiedtobuy"}
    hs.fake.assoc.append(("5555", contact))
    assert crm_sync.import_hubspot_deal(db_session, test_user.id, "5555") == "imported"
    imported = db_session.query(m.Deal).filter_by(external_ref="5555").one()
    assert (imported.source, imported.value_cents, imported.stage) == ("hubspot", 120_000,
                                                                       m.DealStage.OPEN)
    assert crm_sync.import_hubspot_deal(db_session, test_user.id, "5555") == "exists"


def test_pull_and_sweep(db_session, test_user, hs, verified_leads):
    engaged = _engaged(db_session, verified_leads[0], m.LeadStatus.MEETING_BOOKED)
    fresh = verified_leads[1]   # still "verified": not pushed by default
    result = crm_sync.sync_connection(db_session, hs.conn.id, full=True)
    assert result["status"] == "ok" and result["pushed"] == 1
    assert crm_sync._link(db_session, test_user.id, "hubspot", "lead", fresh.id) is None

    contact = crm_sync._link(db_session, test_user.id, "hubspot", "lead", engaged.id).remote_id
    hs.fake.contacts[contact]["lifecyclestage"] = "customer"
    assert crm_sync.pull(db_session, hs.conn)["applied"] == 1
    db_session.refresh(engaged)
    assert engaged.status is m.LeadStatus.CLOSED_WON

    hs.conn.settings_json = {"sync_new_leads": True}
    db_session.commit()
    crm_sync.sync_connection(db_session, hs.conn.id, full=True)
    assert crm_sync._link(db_session, test_user.id, "hubspot", "lead", fresh.id) is not None


def test_hubspot_token_refresh(db_session, test_user, monkeypatch):
    TokenStore.set(db_session, None, "hubspot_app", "client_id", "hs-id")
    TokenStore.set(db_session, None, "hubspot_app", "client_secret", "hs-secret")
    TokenStore.set(db_session, test_user.id, "hubspot", "refresh_token", "rt")
    conn = crm_sync.upsert_connection(db_session, test_user.id, "hubspot", account_id="1")
    monkeypatch.setattr(hubspot_mod, "refresh",
                        lambda cid, secret, rt: {"access_token": "fresh", "expires_in": 1800})
    assert crm_sync._hubspot_token(db_session, conn) == "fresh"
    assert TokenStore.get(db_session, test_user.id, "hubspot", "access_token") == "fresh"


def test_revoked_grant_marks_connection_error(db_session, test_user, hs, verified_leads,
                                              monkeypatch):
    def boom(db, conn):
        raise HubSpotError("unauthorized", 401)

    monkeypatch.setattr(crm_sync, "hubspot_client", boom)
    lead = _engaged(db_session, verified_leads[0])
    assert crm_sync.push_lead(db_session, test_user.id, lead.id) == {"hubspot": None}
    db_session.refresh(hs.conn)
    assert hs.conn.status == "error" and "unauthorized" in hs.conn.last_error


# --------------------------------------------------------------------------
# Salesforce
# --------------------------------------------------------------------------


class FakeSalesforce:
    def __init__(self):
        self.records = {"Lead": {}, "Opportunity": {}}
        self._n = 0

    def find_lead_by_email(self, email):
        return next((i for i, r in self.records["Lead"].items() if r.get("Email") == email), None)

    def create(self, sobject, fields):
        self._n += 1
        rid = f"{'00Q' if sobject == 'Lead' else '006'}{self._n:015d}"
        self.records[sobject][rid] = dict(fields)
        return rid

    def update(self, sobject, rid, fields):
        self.records[sobject][rid].update(fields)

    def get_many(self, sobject, ids, fields):
        return [{"Id": i, **self.records[sobject][i]} for i in ids if i in self.records[sobject]]


@pytest.fixture()
def sf(db_session, test_user, monkeypatch):
    fake = FakeSalesforce()
    monkeypatch.setattr(crm_sync, "salesforce_client", lambda db, conn: fake)
    TokenStore.set(db_session, None, "salesforce_app", "webhook_secret", "relay-secret")
    conn = crm_sync.upsert_connection(db_session, test_user.id, "salesforce",
                                      account_id="00D000000000001AAA",
                                      instance_url="https://acme.my.salesforce.com")
    return SimpleNamespace(fake=fake, conn=conn)


def _sf_headers(body: bytes, secret="relay-secret"):
    ts = str(int(time.time()))
    sig = "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + body,
                               hashlib.sha256).hexdigest()
    return {"X-LeadPilot-Timestamp": ts, "X-LeadPilot-Signature": sig,
            "Content-Type": "application/json"}


def test_salesforce_push_and_relay(client, db_session, test_user, sf, verified_leads):
    lead = _engaged(db_session, verified_leads[0])
    lead.full_name = "Madonna"
    lead.company = None
    db_session.commit()
    rid = crm_sync.push_lead(db_session, test_user.id, lead.id)["salesforce"]
    record = sf.fake.records["Lead"][rid]
    assert (record["LastName"], record["Company"], record["LeadSource"]) == (
        "Madonna", "Unknown", "Vectorae")
    assert record["Status"] == "Working - Contacted"

    client.headers.pop("Authorization", None)
    body = json.dumps({"org_id": "00D000000000001", "records": [
        {"sobject": "Lead", "id": rid, "fields": {"Status": "Closed - Not Converted"}}]}).encode()
    assert client.post("/webhooks/salesforce", content=body,
                       headers=_sf_headers(body, "nope")).status_code == 401
    stale = {**_sf_headers(body), "X-LeadPilot-Timestamp": str(int(time.time()) - 3600)}
    assert client.post("/webhooks/salesforce", content=body, headers=stale).status_code == 401
    ok = client.post("/webhooks/salesforce", content=body, headers=_sf_headers(body))
    assert ok.json()["applied"] == 1
    db_session.refresh(lead)
    assert lead.status is m.LeadStatus.DISQUALIFIED


def test_salesforce_opportunity_pull(db_session, test_user, sf, verified_leads):
    lead = _engaged(db_session, verified_leads[0], m.LeadStatus.OPPORTUNITY)
    deal = m.Deal(user_id=test_user.id, lead_id=lead.id, strategy_id=lead.strategy_id,
                  name="Opp", value_cents=5000, stage=m.DealStage.OPEN)
    db_session.add(deal)
    db_session.commit()
    rid = crm_sync.push_deal(db_session, test_user.id, deal.id)["salesforce"]
    assert sf.fake.records["Opportunity"][rid]["StageName"] == "Prospecting"
    sf.fake.records["Opportunity"][rid].update({"IsWon": True, "IsClosed": True,
                                                "Amount": 75.0, "CloseDate": "2026-09-20"})
    assert crm_sync.pull(db_session, sf.conn)["applied"] == 1
    db_session.refresh(deal)
    assert (deal.stage, deal.value_cents, deal.close_date) == (m.DealStage.WON, 7500,
                                                               date(2026, 9, 20))


# --------------------------------------------------------------------------
# Event wiring
# --------------------------------------------------------------------------


def _events(queued_jobs, name):
    return [kw for _, event, kw in queued_jobs["events"] if event == name]


def test_replies_reach_webhooks_and_slack_without_a_second_push(db_session, verified_leads,
                                                                queued_jobs):
    lead = verified_leads[0]
    outreach_tasks._notify_new_reply(db_session, lead, "interested", "linkedin")
    received = _events(queued_jobs, "reply_received")[0]
    interested = _events(queued_jobs, "reply_interested")[0]
    assert received["push"] is False and received["slack"] is False
    assert received["webhook_payload"]["channel"] == "linkedin"
    assert received["webhook_payload"]["lead"]["email"] == lead.email
    assert interested["push"] is False and "slack" not in interested

    outreach_tasks._notify_new_reply(db_session, verified_leads[1], "question", "email")
    assert len(_events(queued_jobs, "reply_interested")) == 1


def test_manual_pause_and_lead_sourced_events(client, db_session, verified_strategy,
                                              lead_batch, queued_jobs):
    assert client.post(f"/strategies/{verified_strategy.id}/campaign/pause").status_code == 200
    paused = _events(queued_jobs, "campaign_paused")[0]
    assert paused["webhook_payload"]["trigger"] == "manual" and paused["slack"] is False

    for i in range(2):
        db_session.add(m.Lead(strategy_id=verified_strategy.id, batch_id=lead_batch.id,
                              source="apollo", external_id=f"B{i}", email=f"b{i}@x.com",
                              status=m.LeadStatus.VERIFIED))
    db_session.commit()
    lead_tasks.finalize_lead_batch_impl(db_session, lead_batch.id)
    sourced = _events(queued_jobs, "lead_sourced")[0]
    assert len(sourced["webhook_payload"]["leads"]) == 2 and sourced["push"] is False


def test_crm_hook_and_deal_edits_queue_pushes(client, db_session, test_user, hs,
                                              verified_leads, queued_jobs, monkeypatch):
    monkeypatch.setattr(event_bus, "_push", lambda *a, **k: 0)
    lead = verified_leads[0]
    event_bus.emit(db_session, test_user.id, "meeting_booked", title="t", body="b",
                   slack=False, data={"leadId": str(lead.id)})
    assert ("push", "lead", str(test_user.id), str(lead.id)) in queued_jobs["crm"]

    made = client.post("/deals", json={"value": 100, "lead_id": str(lead.id)})
    assert made.status_code == 201
    assert ("push", "deal", str(test_user.id), made.json()["id"]) in queued_jobs["crm"]


def test_gdpr_delete_forgets_crm_links(client, db_session, test_user, hs, verified_leads):
    lead = _engaged(db_session, verified_leads[0])
    crm_sync.push_lead(db_session, test_user.id, lead.id)
    assert client.delete(f"/leads/{lead.id}").status_code in (200, 204)
    assert crm_sync._link(db_session, test_user.id, "hubspot", "lead", lead.id) is None


def test_disconnect_removes_tokens_and_links(client, db_session, test_user, hs,
                                             verified_leads):
    lead = _engaged(db_session, verified_leads[0])
    crm_sync.push_lead(db_session, test_user.id, lead.id)
    TokenStore.set(db_session, test_user.id, "hubspot", "refresh_token", "rt")
    assert client.delete("/integrations/hubspot").status_code == 204
    assert crm_sync.get_connection(db_session, test_user.id, "hubspot") is None
    assert TokenStore.get(db_session, test_user.id, "hubspot", "refresh_token") is None
    assert db_session.query(m.CrmSyncLink).count() == 0
    assert client.delete("/integrations/hubspot").status_code == 404
    assert client.post("/integrations/salesforce/sync").status_code == 404


def test_crm_settings_validation(client, hs):
    bad = client.put("/integrations/hubspot/settings",
                     json={"lead_status_map": {"not_a_status": "X"}})
    assert bad.status_code == 422
    ok = client.put("/integrations/hubspot/settings",
                    json={"sync_new_leads": True, "lead_status_map": {"replied": "OPEN"}})
    assert ok.json()["settings"]["sync_new_leads"] is True


def test_sweep_task_survives_a_failing_connection(db_session, hs, monkeypatch):
    monkeypatch.setattr(crm_sync, "sync_connection",
                        lambda db, cid, full=False: (_ for _ in ()).throw(RuntimeError("x")))
    assert crm_tasks.sync_all_impl(db_session) == {"connections": 1, "ok": 0, "failed": 1}
