"""Feature Group 3 — analytics & revenue intelligence.

What must hold:
  * open tracking: the pixel token cannot be forged; loads right after the
    send (scanners) are ignored; the first real open writes ONE outcome on
    the lead's local clock, later loads only count; EU/UK leads and an admin
    "off" get no pixel; the plain-text part stays first in the MIME;
  * the usage meter prices model calls into the campaign that caused them,
    never double-counts nested scopes and still records a failed run;
  * smart send time: windows only after the open threshold, only inside the
    send window; scheduling moves forward to a window, never past the cap;
  * the funnel attributes replies/bookings to the right step and separates
    drop-offs from leads still waiting;
  * sentiment excludes non-human replies and alerts on a spike exactly once;
  * revenue: currency discipline, shared-cost allocation by sends, cost per
    meeting / deal and ROI; every endpoint is owner-scoped (404).
"""

from __future__ import annotations

import base64
import email
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.db import models as m
from app.integrations.gmail import GmailChannel
from app.integrations.outreach_base import OutboundMessage
from app.services import (
    compliance_region,
    funnel,
    open_tracking,
    reply_sentiment,
    revenue_analytics,
    send_windows,
    system_settings,
    usage_meter,
)
from app.workers import analytics_tasks, outreach_tasks

from .conftest import auth_headers

UTC = timezone.utc
MONDAY = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)   # a Monday, inside 9-17


def _lead(db, strategy, n, **kw):
    lead = m.Lead(strategy_id=strategy.id, source="apollo", external_id=f"R{n}",
                  full_name=f"Rev {n}", email=f"rev{n}@x{n}.com",
                  status=m.LeadStatus.VERIFIED, **kw)
    db.add(lead)
    db.commit()
    return lead


def _sent_message(db, sequence, lead, step_no=1, sent_at=MONDAY, **kw):
    msg = m.Message(sequence_id=sequence.id, lead_id=lead.id, channel=m.ChannelType.EMAIL,
                    step_no=step_no, template="brief", body="hello", status=m.MessageStatus.SENT,
                    sent_at=sent_at, **kw)
    db.add(msg)
    db.commit()
    return msg


def _other_user_strategy(db, product_with_strategy):
    other = m.User(email="other@leadpilot.dev", email_verified=True)
    db.add(other)
    db.commit()
    product = m.Product(user_id=other.id, name="Other", description="x",
                        type=m.ProductType.SKILL)
    db.add(product)
    db.flush()
    strategy = m.Strategy(product_id=product.id, flow_type=m.FlowType.WITH_CLIENTS)
    db.add(strategy)
    db.commit()
    return other, strategy


# --------------------------------------------------------------------------
# Open tracking
# --------------------------------------------------------------------------


def test_pixel_token_round_trips_and_resists_tampering():
    import uuid

    mid = uuid.uuid4()
    token = open_tracking.make_token(mid)
    assert open_tracking.parse_token(token) == mid
    raw = bytearray(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    raw[0] ^= 0xFF   # flip the message id, keep the old MAC
    forged = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")
    assert open_tracking.parse_token(forged) is None
    assert open_tracking.parse_token("not-a-token") is None
    assert open_tracking.parse_token("") is None


def test_html_body_escapes_links_and_carries_pixel():
    html = open_tracking.html_body(
        "Hi <Sam>,\nsee https://cal.com/x?a=1&b=2.\n\nThanks", "https://api/t/o/tok.gif")
    assert "&lt;Sam&gt;" in html
    assert '<a href="https://cal.com/x?a=1&amp;b=2">' in html
    assert "</a>." in html                      # trailing full stop not in the link
    assert html.count("<p>") == 2 and "<br>" in html
    assert 'src="https://api/t/o/tok.gif"' in html


def test_record_open_ignores_prefetch_then_counts_once(db_session, verified_strategy,
                                                        email_sequence, queued_jobs):
    lead = _lead(db_session, verified_strategy, 1, enrichment_json={"timezone": "America/New_York"})
    msg = _sent_message(db_session, email_sequence, lead)

    assert open_tracking.record_open(db_session, msg.id, now=MONDAY + timedelta(seconds=5)) == "prefetch"
    assert msg.open_count == 0

    first = MONDAY + timedelta(hours=3)
    assert open_tracking.record_open(db_session, msg.id, user_agent="Mail", now=first) == "recorded"
    assert open_tracking.record_open(db_session, msg.id, now=first + timedelta(hours=1)) == "repeat"
    db_session.refresh(msg)
    assert msg.open_count == 2
    outcomes = db_session.query(m.Outcome).filter_by(event=m.OutcomeEvent.OPENED).all()
    assert len(outcomes) == 1
    # 13:00 UTC Monday = 09:00 in New York: the lead's clock, not the server's.
    assert outcomes[0].meta_json["local_dow"] == 0
    assert outcomes[0].meta_json["local_hour"] == 9
    assert outcomes[0].strategy_id == verified_strategy.id


def test_record_open_unknown_and_unsent(db_session, verified_strategy, email_sequence):
    import uuid

    lead = _lead(db_session, verified_strategy, 2)
    msg = _sent_message(db_session, email_sequence, lead, sent_at=None)
    msg.status = m.MessageStatus.SCHEDULED
    db_session.commit()
    assert open_tracking.record_open(db_session, uuid.uuid4()) == "unknown"
    assert open_tracking.record_open(db_session, msg.id) == "not_sent"


def test_crossing_open_threshold_queues_window_computation(db_session, verified_strategy,
                                                           email_sequence, queued_jobs):
    system_settings.set(db_session, "send_time_min_opens", 2)
    lead = _lead(db_session, verified_strategy, 3)
    a = _sent_message(db_session, email_sequence, lead, step_no=1)
    b = _sent_message(db_session, email_sequence, lead, step_no=2)
    open_tracking.record_open(db_session, a.id, now=MONDAY + timedelta(hours=1))
    assert queued_jobs["analytics"] == []
    open_tracking.record_open(db_session, b.id, now=MONDAY + timedelta(hours=1))
    assert queued_jobs["analytics"] == [str(verified_strategy.id)]


def test_pixel_endpoint_always_returns_the_gif(client, db_session, verified_strategy,
                                               email_sequence):
    lead = _lead(db_session, verified_strategy, 4)
    msg = _sent_message(db_session, email_sequence, lead,
                        sent_at=datetime.now(UTC) - timedelta(hours=2))
    client.headers.pop("Authorization", None)

    bad = client.get("/t/o/garbage.gif")
    assert bad.status_code == 200 and bad.headers["content-type"] == "image/gif"
    assert bad.content == open_tracking.PIXEL_GIF

    good = client.get(f"/t/o/{open_tracking.make_token(msg.id)}.gif")
    assert good.status_code == 200 and good.content == open_tracking.PIXEL_GIF
    assert "no-store" in good.headers["cache-control"]
    db_session.refresh(msg)
    assert msg.opened_at is not None and msg.open_count == 1


def test_region_rules():
    us = SimpleNamespace(enrichment_json={"person": {"country": "United States"}})
    de = SimpleNamespace(enrichment_json={"raw": {"person": {"country": "Germany"}}})
    uk_tz = SimpleNamespace(enrichment_json={"timezone": "Europe/London"})
    unknown = SimpleNamespace(enrichment_json=None)
    assert compliance_region.lead_region(us) == "us"
    assert compliance_region.lead_region(de) == "eu"
    assert compliance_region.lead_region(uk_tz) == "uk"
    assert compliance_region.lead_region(unknown) is None
    assert compliance_region.open_tracking_allowed(us)
    assert not compliance_region.open_tracking_allowed(de)
    assert not compliance_region.open_tracking_allowed(uk_tz)
    assert compliance_region.open_tracking_allowed(unknown)


def _first_message(db, sequence, lead):
    return db.query(m.Message).filter_by(sequence_id=sequence.id, lead_id=lead.id).first()


def test_email_render_adds_pixel_except_eu_or_disabled(db_session, enrolled, verified_leads,
                                                       verified_strategy):
    lead = verified_leads[0]
    msg = _first_message(db_session, enrolled, lead)
    out = outreach_tasks._render_email_outbound(db_session, verified_strategy, lead, enrolled, msg)
    assert open_tracking.make_token(msg.id) in out.metadata["html_body"]
    assert "<img" not in out.body and "/t/o/" not in out.body   # plain text untouched

    eu = verified_leads[1]
    eu.enrichment_json = {"person": {"country": "France"}}
    db_session.commit()
    out = outreach_tasks._render_email_outbound(db_session, verified_strategy, eu, enrolled,
                                                _first_message(db_session, enrolled, eu))
    assert "html_body" not in out.metadata

    system_settings.set(db_session, "open_tracking_enabled", False)
    out = outreach_tasks._render_email_outbound(db_session, verified_strategy, lead, enrolled, msg)
    assert "html_body" not in out.metadata


def test_gmail_send_keeps_plain_text_first():
    channel = GmailChannel.__new__(GmailChannel)
    channel.account = SimpleNamespace(email_address="me@x.com")
    sent = {}

    def fake_call(method, path, json_body=None, **kw):
        sent["payload"] = json_body
        return {"id": "g1", "threadId": "t1"}

    channel.call = fake_call
    result = channel.send(OutboundMessage(
        message_id="m1", lead_id="l1", to_address="lead@x.com", subject="Hi", body="Plain body",
        metadata={"html_body": "<html><body><p>Plain body</p><img src='p'></body></html>"}))
    assert result.ok
    mime = email.message_from_bytes(base64.urlsafe_b64decode(sent["payload"]["raw"]))
    assert mime.get_content_type() == "multipart/alternative"
    parts = [p.get_content_type() for p in mime.walk() if not p.is_multipart()]
    assert parts == ["text/plain", "text/html"]


# --------------------------------------------------------------------------
# Usage meter
# --------------------------------------------------------------------------


def test_usage_meter_prices_and_attributes(db_session, verified_strategy, test_user):
    usage_meter.note("anthropic", "claude", 1000, 1000)   # outside any scope: dropped
    with usage_meter.scope(db_session, strategy_id=verified_strategy.id, user_id=test_user.id,
                           purpose="pipeline"):
        usage_meter.note("anthropic", "claude", 1_000_000, 100_000)
        with usage_meter.scope(db_session, strategy_id=None, purpose="inner"):
            usage_meter.note("anthropic", "claude", 0, 100_000)   # collapses into outer
        usage_meter.note("openai", "gpt-4o", 1_000_000, 0)
    rows = {r.provider: r for r in db_session.query(m.ApiUsage).all()}
    assert set(rows) == {"anthropic", "openai"}
    claude = rows["anthropic"]
    assert (claude.calls, claude.input_tokens, claude.output_tokens) == (2, 1_000_000, 200_000)
    # $3/Mtok in + $15/Mtok out = 3_000_000 + 3_000_000 micro-dollars
    assert claude.cost_micros == 6_000_000
    assert claude.strategy_id == verified_strategy.id and claude.purpose == "pipeline"
    assert rows["openai"].cost_micros == 2_500_000


def test_usage_meter_records_a_failed_run(db_session, verified_strategy):
    with pytest.raises(RuntimeError):
        with usage_meter.scope(db_session, strategy_id=verified_strategy.id, purpose="pipeline"):
            usage_meter.note("anthropic", "claude", 10, 10)
            raise RuntimeError("step failed")
    assert db_session.query(m.ApiUsage).count() == 1


# --------------------------------------------------------------------------
# Smart send time
# --------------------------------------------------------------------------


def _open_at(db, lead, dow, hour, n=1):
    for _ in range(n):
        db.add(m.Outcome(lead_id=lead.id, strategy_id=lead.strategy_id,
                         event=m.OutcomeEvent.OPENED, channel="email",
                         meta_json={"local_dow": dow, "local_hour": hour}, ts=MONDAY))
    db.commit()


def test_windows_need_threshold_and_stay_inside_send_window(db_session, verified_strategy):
    system_settings.set(db_session, "send_time_min_opens", 10)
    lead = _lead(db_session, verified_strategy, 10)
    _open_at(db_session, lead, 1, 10, 3)            # Tue 10
    assert send_windows.compute(db_session, verified_strategy)["status"] == "insufficient_data"
    assert verified_strategy.send_windows_json is None

    _open_at(db_session, lead, 5, 11, 20)           # Saturday: never schedulable
    _open_at(db_session, lead, 2, 21, 20)           # 21:00: outside 9-17
    _open_at(db_session, lead, 3, 9, 2)             # Thu 9
    db_session.add(m.Outcome(lead_id=lead.id, event=m.OutcomeEvent.REPLIED, channel="email",
                             meta_json={"local_dow": 3, "local_hour": 9}, ts=MONDAY))
    _open_at(db_session, lead, 0, 14, 1)            # Mon 14
    result = send_windows.compute(db_session, verified_strategy)
    assert result["status"] == "computed"
    slots = [(w["dow"], w["hour"]) for w in verified_strategy.send_windows_json["windows"]]
    # Thu 9 = 2 opens + 1 reply x3 = 5 beats Tue 10 = 3; weekend/evening excluded.
    assert slots == [(3, 9), (1, 10), (0, 14)]
    assert verified_strategy.send_windows_computed_at is not None


def _windows(strategy, *slots, on=True):
    strategy.smart_send_time = on
    strategy.send_windows_json = {"windows": [{"dow": d, "hour": h, "score": 1, "share": 1,
                                               "opens": 1, "replies": 0} for d, h in slots]}


def test_next_smart_slot(db_session, verified_strategy):
    _windows(verified_strategy, (1, 10), on=False)
    assert send_windows.next_smart_slot(db_session, verified_strategy, MONDAY, "UTC") == MONDAY

    _windows(verified_strategy, (1, 10))
    slot = send_windows.next_smart_slot(db_session, verified_strategy, MONDAY, "UTC",
                                        spread_key="lead-1")
    assert slot.date() == date(2026, 9, 15) and slot.hour == 10 and slot.minute < 45

    _windows(verified_strategy, (0, 10))            # already inside the window
    assert send_windows.next_smart_slot(db_session, verified_strategy, MONDAY, "UTC") == MONDAY

    _windows(verified_strategy, (4, 10))            # Friday = 96h away > 72h cap
    assert send_windows.next_smart_slot(db_session, verified_strategy, MONDAY, "UTC") == MONDAY


def test_enrolment_and_reslot_follow_windows(db_session, verified_strategy, email_sequence,
                                             verified_leads):
    from app.services.sequence_engine import enroll_leads

    _windows(verified_strategy, (1, 10))
    db_session.commit()
    enroll_leads(db_session, email_sequence, now=MONDAY)
    scheduled = db_session.query(m.Message).filter_by(sequence_id=email_sequence.id).all()
    assert scheduled and all(
        (s.scheduled_at.replace(tzinfo=UTC) if s.scheduled_at.tzinfo is None else s.scheduled_at)
        .date() == date(2026, 9, 15) for s in scheduled)

    lead = verified_leads[0]
    soon = m.Message(sequence_id=email_sequence.id, lead_id=lead.id, channel=m.ChannelType.EMAIL,
                     step_no=2, template="x", status=m.MessageStatus.SCHEDULED,
                     scheduled_at=MONDAY + timedelta(minutes=2))
    later = m.Message(sequence_id=email_sequence.id, lead_id=lead.id,
                      channel=m.ChannelType.EMAIL, step_no=3, template="x",
                      status=m.MessageStatus.SCHEDULED, scheduled_at=MONDAY + timedelta(hours=4))
    db_session.add_all([soon, later])
    db_session.commit()
    moved = send_windows.reslot_scheduled(db_session, verified_strategy, now=MONDAY)
    assert moved >= 1
    db_session.refresh(soon)
    db_session.refresh(later)
    assert soon.scheduled_at.replace(tzinfo=UTC) == MONDAY + timedelta(minutes=2)
    assert later.scheduled_at.replace(tzinfo=UTC).date() == date(2026, 9, 15)


def test_refresh_sweep_computes_eligible_campaigns(db_session, verified_strategy):
    system_settings.set(db_session, "send_time_min_opens", 3)
    lead = _lead(db_session, verified_strategy, 11)
    _open_at(db_session, lead, 1, 10, 3)
    assert analytics_tasks.refresh_send_windows_impl(db_session) == {"computed": 1, "failed": 0}
    db_session.refresh(verified_strategy)
    assert verified_strategy.send_windows_json["windows"][0]["hour"] == 10


# --------------------------------------------------------------------------
# Funnel
# --------------------------------------------------------------------------


def test_funnel_attribution_and_drop_off(db_session, verified_strategy, email_sequence,
                                         verified_leads):
    a, b, c = verified_leads
    t = MONDAY
    a1 = _sent_message(db_session, email_sequence, a, 1, t, opened_at=t + timedelta(hours=1))
    _sent_message(db_session, email_sequence, b, 1, t)
    _sent_message(db_session, email_sequence, b, 2, t + timedelta(days=3))
    _sent_message(db_session, email_sequence, c, 1, t)
    db_session.add_all([
        m.Outcome(lead_id=a.id, message_id=a1.id, event=m.OutcomeEvent.REPLIED, channel="email",
                  ts=t + timedelta(hours=2)),
        # No message id: attributed to the last step A received before it.
        m.Outcome(lead_id=a.id, event=m.OutcomeEvent.BOOKED, channel="email",
                  ts=t + timedelta(days=1)),
        m.SequenceEnrollment(sequence_id=email_sequence.id, lead_id=a.id,
                             status=m.EnrollmentStatus.STOPPED),
        m.SequenceEnrollment(sequence_id=email_sequence.id, lead_id=b.id,
                             status=m.EnrollmentStatus.COMPLETED),
        m.SequenceEnrollment(sequence_id=email_sequence.id, lead_id=c.id,
                             status=m.EnrollmentStatus.ACTIVE),
    ])
    db_session.commit()

    report = funnel.step_funnel(db_session, verified_strategy.id)
    steps = {s["step_no"]: s for s in report["sequences"][0]["steps"]}
    s1, s2, s3 = steps[1], steps[2], steps[3]
    assert (s1["sent"], s1["opened"], s1["replied"], s1["booked"]) == (3, 1, 1, 1)
    assert (s1["dropped"], s1["in_progress"]) == (0, 1)
    assert s1["open_rate"] == round(1 / 3, 4) and s1["reply_rate"] == round(1 / 3, 4)
    assert (s2["sent"], s2["dropped"], s2["drop_off_rate"]) == (1, 1, 1.0)
    assert s3["sent"] == 0 and s3["reply_rate"] is None
    assert report["best_step"] is None          # nothing reached the 20-send minimum


# --------------------------------------------------------------------------
# Reply sentiment
# --------------------------------------------------------------------------

WED = datetime(2026, 9, 16, 12, tzinfo=UTC)     # current week starts Mon 14 Sep


def _replies(db, lead, when, **counts):
    for classification, n in counts.items():
        for _ in range(n):
            db.add(m.InboundReply(lead_id=lead.id, channel="email", from_address=lead.email,
                                  body="...", classification=classification,
                                  received_at=when, created_at=when))
    db.commit()


def test_weekly_breakdown_counts_humans_only(db_session, verified_strategy):
    lead = _lead(db_session, verified_strategy, 20)
    _replies(db_session, lead, datetime(2026, 9, 8, 9, tzinfo=UTC),
             interested=2, objection=1, out_of_office=5, bounce=1, unsubscribe_request=1)
    weeks = reply_sentiment.weekly_breakdown(db_session, verified_strategy.id, weeks=3, now=WED)
    assert [w["week_start"] for w in weeks] == [date(2026, 8, 31), date(2026, 9, 7), date(2026, 9, 14)]
    mid = weeks[1]
    assert (mid["total"], mid["interested"], mid["objection"], mid["unsubscribe"]) == (4, 2, 1, 1)
    assert mid["objection_rate"] == 0.25
    assert weeks[0]["objection_rate"] is None     # no replies = a gap, not 0%


def test_spike_rule():
    def w(total, objection):
        return {"total": total, "objection_rate": objection / total if total else None}
    rule = dict(threshold=0.20, min_replies=10)
    assert reply_sentiment.is_spike(w(20, 4), w(20, 8), **rule)          # 20% -> 40%
    # Exactly +5 points and +25%: alerts. (0.25 - 0.20 is 0.0499... in
    # floating point -- this pins the epsilon in is_spike.)
    assert reply_sentiment.is_spike(w(20, 4), w(20, 5), **rule)
    assert not reply_sentiment.is_spike(w(40, 8), w(40, 9), **rule)      # +2.5pts: under the floor
    assert not reply_sentiment.is_spike(w(20, 10), w(20, 11), **rule)    # +5pts but only +10%
    assert not reply_sentiment.is_spike(w(100, 4), w(100, 5), **rule)    # +1pt: under the floor
    assert not reply_sentiment.is_spike(w(5, 0), w(20, 10), **rule)      # too few last week
    assert reply_sentiment.is_spike(w(20, 0), w(20, 2), **rule)          # 0% -> 10%


def test_objection_spike_alerts_once(db_session, verified_strategy, queued_jobs):
    lead = _lead(db_session, verified_strategy, 21)
    _replies(db_session, lead, datetime(2026, 9, 1, 9, tzinfo=UTC), interested=8, objection=2)
    _replies(db_session, lead, datetime(2026, 9, 8, 9, tzinfo=UTC), interested=4, objection=6)

    assert reply_sentiment.aggregate_and_alert(db_session, WED) == {"strategies": 1, "alerts": 1}
    events = [e for e in queued_jobs["events"] if e[1] == "objection_spike"]
    assert len(events) == 1
    assert "20% to 60%" in events[0][2]["body"]
    assert events[0][2]["deep_link"].endswith("tab=sentiment")
    rows = db_session.query(m.ReplySentimentWeek).order_by(m.ReplySentimentWeek.week_start).all()
    assert [r.total for r in rows] == [10, 10] and rows[1].alerted_at is not None

    assert reply_sentiment.aggregate_and_alert(db_session, WED)["alerts"] == 0
    trend = reply_sentiment.trend(db_session, verified_strategy.id, weeks=3, now=WED)
    assert [w["alerted"] for w in trend["weeks"]] == [False, True, False]
    assert trend["weeks"][-1]["partial"] is True


# --------------------------------------------------------------------------
# Revenue
# --------------------------------------------------------------------------


@pytest.fixture()
def revenue_data(db_session, verified_strategy, test_user):
    a = verified_strategy
    b = m.Strategy(product_id=a.product_id, flow_type=m.FlowType.WITH_CLIENTS)
    db_session.add(b)
    db_session.commit()
    la, lb = _lead(db_session, a, 30), _lead(db_session, b, 31)
    t = datetime(2026, 9, 3, 12, tzinfo=UTC)
    for _ in range(3):
        db_session.add(m.Outcome(lead_id=la.id, event=m.OutcomeEvent.SENT, channel="email", ts=t))
    db_session.add(m.Outcome(lead_id=lb.id, event=m.OutcomeEvent.SENT, channel="email", ts=t))
    db_session.add(m.Outcome(lead_id=la.id, event=m.OutcomeEvent.BOOKED, channel="email", ts=t))
    db_session.add(m.Outcome(lead_id=la.id, event=m.OutcomeEvent.BOOKED, channel="email", ts=t))
    uid = test_user.id
    db_session.add_all([
        m.Deal(user_id=uid, strategy_id=a.id, name="Won", value_cents=500_000,
               stage=m.DealStage.WON, close_date=date(2026, 9, 5)),
        m.Deal(user_id=uid, strategy_id=a.id, name="Old", value_cents=999,
               stage=m.DealStage.WON, close_date=date(2026, 7, 1)),
        m.Deal(user_id=uid, strategy_id=a.id, name="EUR", value_cents=100, currency="EUR",
               stage=m.DealStage.WON, close_date=date(2026, 9, 6)),
        m.Deal(user_id=uid, strategy_id=b.id, name="Open", value_cents=100_000,
               stage=m.DealStage.OPEN),
        m.CampaignCost(user_id=uid, strategy_id=a.id, category="data", amount_cents=10_000,
                       incurred_on=date(2026, 9, 1)),
        m.CampaignCost(user_id=uid, strategy_id=None, category="tools", amount_cents=4_000,
                       incurred_on=date(2026, 9, 2)),
        m.CampaignCost(user_id=uid, strategy_id=a.id, category="ads", amount_cents=50,
                       currency="EUR", incurred_on=date(2026, 9, 2)),
        m.ApiUsage(user_id=uid, strategy_id=a.id, provider="anthropic", model="c",
                   purpose="pipeline", calls=1, input_tokens=1, output_tokens=1,
                   cost_micros=2_000_000, ts=t),
        m.ApiUsage(user_id=uid, strategy_id=None, provider="anthropic", model="c",
                   purpose="other", calls=1, input_tokens=1, output_tokens=1,
                   cost_micros=1_000_000, ts=t),
        m.Call(lead_id=la.id, strategy_id=a.id, provider="vapi", to_number="+15550100",
               duration_seconds=120, created_at=t),
    ])
    db_session.commit()
    return a, b


def test_revenue_report(db_session, test_user, revenue_data):
    a, b = revenue_data
    report = revenue_analytics.report(db_session, test_user.id, date(2026, 8, 1), date(2026, 9, 30))
    assert report["currency"] == "USD"
    rows = {r["strategy_id"]: r for r in report["campaigns"]}
    ra, rb = rows[str(a.id)], rows[str(b.id)]
    # shared = $40 recorded + $1 unattributed AI, split 3:1 by sends
    assert (ra["allocated_cost_cents"], rb["allocated_cost_cents"]) == (3075, 1025)
    assert (ra["direct_cost_cents"], ra["api_cost_cents"], ra["voice_cost_cents"]) == (10_000, 200, 30)
    assert ra["total_cost_cents"] == 13_305
    assert (ra["meetings"], ra["deals_won"], ra["revenue_cents"]) == (1, 1, 500_000)
    assert ra["cost_per_meeting_cents"] == 13_305 and ra["cost_per_deal_cents"] == 13_305
    assert ra["roi"] == round((500_000 - 13_305) / 13_305, 4)
    assert rb["cost_per_meeting_cents"] is None and rb["roi"] == -1.0

    totals = report["totals"]
    assert (totals["revenue_cents"], totals["cost_cents"]) == (500_000, 14_330)
    assert totals["open_pipeline_cents"] == 100_000 and totals["unallocated_cost_cents"] == 0
    assert report["notes"]["excluded_deals"] == 1 and report["notes"]["excluded_costs"] == 1
    months = {mth["month"]: mth for mth in report["monthly"]}
    assert months["2026-08"] == {"month": "2026-08", "revenue_cents": 0, "cost_cents": 0}
    assert months["2026-09"]["cost_cents"] == 14_330
    assert {u["purpose"] for u in report["api_usage"]} == {"pipeline", "other"}


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_revenue_endpoint(client, revenue_data):
    resp = client.get("/analytics/revenue?date_from=2026-08-01&date_to=2026-09-30")
    assert resp.status_code == 200 and resp.json()["totals"]["revenue_cents"] == 500_000
    assert client.get("/analytics/revenue?date_from=2026-09-30&date_to=2026-08-01").status_code == 422
    assert client.get("/analytics/revenue").status_code == 200


def test_costs_crud_and_ownership(client, db_session, verified_strategy, product_with_strategy):
    made = client.post("/costs", json={"strategy_id": str(verified_strategy.id),
                                        "category": "data", "amount": 49.99})
    assert made.status_code == 201 and made.json()["amount_cents"] == 4999
    timed = client.post("/costs", json={"category": "time", "hours": 2.5, "hourly_rate": 60})
    assert timed.status_code == 201 and timed.json()["amount_cents"] == 15_000
    assert timed.json()["strategy_id"] is None and timed.json()["hours"] == 2.5
    assert client.post("/costs", json={"category": "data"}).status_code == 422

    listed = client.get(f"/costs?strategy_id={verified_strategy.id}").json()
    assert listed["total"] == 1
    patched = client.patch(f"/costs/{made.json()['id']}", json={"amount": 10})
    assert patched.json()["amount_cents"] == 1000

    other, other_strategy = _other_user_strategy(db_session, product_with_strategy)
    assert client.post("/costs", json={"strategy_id": str(other_strategy.id),
                                        "amount": 1}).status_code == 404
    foreign = m.CampaignCost(user_id=other.id, category="data", amount_cents=1,
                             incurred_on=date(2026, 9, 1))
    db_session.add(foreign)
    db_session.commit()
    assert client.delete(f"/costs/{foreign.id}").status_code == 404
    assert client.delete(f"/costs/{made.json()['id']}").status_code == 204


def test_campaign_insight_endpoints(client, db_session, verified_strategy, email_sequence,
                                    product_with_strategy):
    sid = verified_strategy.id
    assert client.get(f"/strategies/{sid}/funnel").json()["sequences"][0]["name"] == "Intro sequence"
    st = client.get(f"/strategies/{sid}/send-time").json()
    assert st["smart_send_time"] is False and st["min_opens"] == 50

    on = client.put(f"/strategies/{sid}/send-time", json={"smart_send_time": True})
    assert on.status_code == 200 and on.json()["smart_send_time"] is True
    db_session.refresh(verified_strategy)
    assert verified_strategy.smart_send_time is True
    assert client.post(f"/strategies/{sid}/send-time/recompute").json()["result"] == "insufficient_data"
    assert len(client.get(f"/strategies/{sid}/sentiment?weeks=4").json()["weeks"]) == 4

    _, foreign = _other_user_strategy(db_session, product_with_strategy)
    for path in ("funnel", "send-time", "sentiment"):
        assert client.get(f"/strategies/{foreign.id}/{path}").status_code == 404
    assert client.put(f"/strategies/{foreign.id}/send-time",
                      json={"smart_send_time": True}).status_code == 404


# --------------------------------------------------------------------------
# The repaired M8 optimizer
# --------------------------------------------------------------------------


def test_legacy_optimizer_runs_on_orm(db_session, verified_strategy, email_sequence):
    from app.core.redis_client import get_sync_redis
    from app.services.send_time_optimizer import (
        get_send_time_recommendation,
        update_send_time_scores,
    )

    lead = _lead(db_session, verified_strategy, 40)
    msg = _sent_message(db_session, email_sequence, lead)
    db_session.add_all([
        m.Outcome(lead_id=lead.id, message_id=msg.id, event=m.OutcomeEvent.SENT,
                  channel="email", ts=MONDAY),
        m.Outcome(lead_id=lead.id, message_id=msg.id, event=m.OutcomeEvent.REPLIED,
                  channel="email", ts=MONDAY + timedelta(hours=5)),
    ])
    db_session.commit()
    assert update_send_time_scores(db_session) == 1
    owner_id = db_session.get(m.Product, verified_strategy.product_id).user_id
    cached = json.loads(get_sync_redis().get(f"send_time:slots:{owner_id}:email:any:any"))
    slot = cached["slots"][0]
    # Monday 10:00 in the Sunday=0 convention; the reply counts in its send slot.
    assert (slot["day_of_week"], slot["hour_utc"], slot["expected_reply_rate"]) == (1, 10, 1.0)
    assert get_send_time_recommendation("gmail", None, None,
                                        owner_id=owner_id).fallback_used is False
