"""F08 — the meetings-per-dollar ledger (docs/features/f08-meetings-per-dollar.md).

What must hold (the STAR success metric is the first block):
  * the ledger RECONCILES: its cost equals the raw campaign_costs + api_usage
    + calls + billable_meetings rows, summed here in plain Python, and its
    booked meetings by attribution method equal the attribution_entries rows;
  * funnel rules: only interested replies (the attribution ledger's own rule)
    or bookings qualify; distinct leads; only completed meetings or logged
    outcomes are "held"; the period bounds everything;
  * a booking the attribution sweep has not reached is "pending", never
    guessed; a held meeting inherits its booking's attribution;
  * infrastructure is 0 and labelled unless an admin sets a rate;
  * another account's rows never appear; the flag off = 409 and nothing else
    changes; exports are private, CSV-injection safe and carry no names.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db import models as m
from app.services import (
    attribution,
    meetings_ledger,
    revenue_analytics,
    system_settings,
)

from .conftest import auth_headers

UTC = timezone.utc
D_FROM, D_TO = date(2026, 9, 1), date(2026, 9, 30)


def at(day: int, hour: int = 10, month: int = 9) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=UTC)


def _lead(db, strategy, n, company=None):
    lead = m.Lead(strategy_id=strategy.id, source="apollo", external_id=f"ML{n}",
                  full_name=f"Person {n}", company=company or f"Company {n}",
                  email=f"p{n}@co{n}.test", status=m.LeadStatus.CONTACTED)
    db.add(lead)
    db.commit()
    return lead


def _sequence(db, strategy):
    seq = m.Sequence(strategy_id=strategy.id, channel=m.ChannelType.EMAIL,
                     name="Ledger seq", status=m.SequenceStatus.ACTIVE)
    db.add(seq)
    db.commit()
    return seq


def _sent(db, seq, lead, when, step_no=1):
    msg = m.Message(sequence_id=seq.id, lead_id=lead.id, channel=m.ChannelType.EMAIL,
                    step_no=step_no, template="t", subject=f"Step {step_no}",
                    body="hello", status=m.MessageStatus.SENT, sent_at=when)
    db.add(msg)
    db.commit()
    return msg


def _reply(db, lead, when, label, message=None):
    reply = m.InboundReply(lead_id=lead.id, channel="email", from_address=lead.email,
                           body="reply", received_at=when, intent_label=label,
                           message_id=message.id if message else None)
    db.add(reply)
    db.commit()
    return reply


def _outcome(db, lead, event, when):
    row = m.Outcome(lead_id=lead.id, strategy_id=lead.strategy_id, event=event,
                    channel="email", ts=when)
    db.add(row)
    db.commit()
    return row


def _meeting(db, user, lead, when, status=m.MeetingStatus.COMPLETED, started=True):
    meeting = m.Meeting(host_user_id=user.id, lead_id=lead.id, start_at=when,
                        end_at=when + timedelta(minutes=30), status=status,
                        actual_start_at=when if started else None)
    db.add(meeting)
    db.commit()
    return meeting


def _other_account(db):
    other = m.User(email="other-ledger@leadpilot.dev", email_verified=True)
    db.add(other)
    db.commit()
    product = m.Product(user_id=other.id, name="Rival", description="x",
                        type=m.ProductType.SKILL)
    db.add(product)
    db.flush()
    strategy = m.Strategy(product_id=product.id, flow_type=m.FlowType.WITH_CLIENTS)
    db.add(strategy)
    db.commit()
    return other, strategy


@pytest.fixture()
def world(db_session, test_user, verified_strategy):
    """Two campaigns of ours, one of another account's, a September period.

    Campaign A
      l1  sent 2 Sep, interested reply LINKED to it 3 Sep, booked 4 Sep,
          meeting completed 10 Sep            -> qualified, booked, held; direct_reply
      l2  sent 5 Sep, booked 2h later, rebooked 6 Sep, meeting never started
                                              -> qualified, booked (once); last_touch
      l3  objection reply 7 Sep               -> nothing
      l4  interested reply 8 Sep, no booking  -> qualified (positive_reply, none)
      l5  booked 12 Sep AFTER the sweep ran, meeting outcome logged 14 Sep
                                              -> qualified, booked, held; pending
      l6  booked 20 Aug                       -> outside the period
    Campaign B
      b1  sent 1 Sep, booked 15 Sep           -> qualified, booked; last_touch
    """
    db = db_session
    a = verified_strategy
    b = m.Strategy(product_id=a.product_id, flow_type=m.FlowType.WITH_CLIENTS)
    db.add(b)
    db.commit()
    seq_a, seq_b = _sequence(db, a), _sequence(db, b)
    l1, l2, l3, l4, l5, l6 = (_lead(db, a, n) for n in range(1, 7))
    b1 = _lead(db, b, 7)

    m1 = _sent(db, seq_a, l1, at(2))
    _reply(db, l1, at(3), "interested", message=m1)
    _outcome(db, l1, m.OutcomeEvent.REPLIED, at(3))
    _outcome(db, l1, m.OutcomeEvent.BOOKED, at(4))
    _meeting(db, test_user, l1, at(10))

    _sent(db, seq_a, l2, at(5))
    _outcome(db, l2, m.OutcomeEvent.BOOKED, at(5, 12))
    _outcome(db, l2, m.OutcomeEvent.BOOKED, at(6))
    _meeting(db, test_user, l2, at(11), status=m.MeetingStatus.CANCELLED, started=False)

    _reply(db, l3, at(7), "objection")
    _outcome(db, l3, m.OutcomeEvent.REPLIED, at(7))

    _reply(db, l4, at(8), "interested")
    _outcome(db, l4, m.OutcomeEvent.REPLIED, at(8))

    _outcome(db, l6, m.OutcomeEvent.BOOKED, at(20, month=8))

    _sent(db, seq_b, b1, at(1))
    _outcome(db, b1, m.OutcomeEvent.BOOKED, at(15))

    # Sends: 3 for A, 1 for B -> shared costs split 3:1.
    for lead in (l1, l1, l2):
        _outcome(db, lead, m.OutcomeEvent.SENT, at(2))
    _outcome(db, b1, m.OutcomeEvent.SENT, at(1))

    uid = test_user.id
    db.add_all([
        m.CampaignCost(user_id=uid, strategy_id=a.id, category="data", amount_cents=10_000,
                       incurred_on=date(2026, 9, 1)),
        m.CampaignCost(user_id=uid, strategy_id=a.id, category="tools", amount_cents=2_000,
                       incurred_on=date(2026, 9, 2)),
        m.CampaignCost(user_id=uid, strategy_id=a.id, category="ai", amount_cents=500,
                       incurred_on=date(2026, 9, 3)),
        m.CampaignCost(user_id=uid, strategy_id=None, category="tools", amount_cents=4_000,
                       incurred_on=date(2026, 9, 2)),
        m.CampaignCost(user_id=uid, strategy_id=a.id, category="ads", amount_cents=50,
                       currency="EUR", incurred_on=date(2026, 9, 2)),
        m.CampaignCost(user_id=uid, strategy_id=a.id, category="data", amount_cents=777,
                       incurred_on=date(2026, 8, 15)),
        m.ApiUsage(user_id=uid, strategy_id=a.id, provider="anthropic", model="c",
                   purpose="pipeline", calls=1, input_tokens=1, output_tokens=1,
                   cost_micros=2_000_000, ts=at(3)),
        m.ApiUsage(user_id=uid, strategy_id=None, provider="anthropic", model="c",
                   purpose="other", calls=1, input_tokens=1, output_tokens=1,
                   cost_micros=1_000_000, ts=at(3)),
        m.Call(lead_id=l1.id, strategy_id=a.id, provider="vapi", to_number="+15550100",
               duration_seconds=120, created_at=at(4)),
        m.BillableMeeting(user_id=uid, lead_id=l1.id, strategy_id=a.id,
                          dedupe_key=str(l1.id), source="calendly", amount_cents=5_000,
                          status="pending", occurred_at=at(4), charge_after=at(6)),
        m.Deal(user_id=uid, strategy_id=a.id, name="Won", value_cents=900_000,
               stage=m.DealStage.WON, close_date=date(2026, 9, 20)),
        m.Deal(user_id=uid, strategy_id=a.id, name="Open", value_cents=50_000,
               stage=m.DealStage.OPEN),
    ])
    db.commit()

    # Another account with the same shapes: none of it may surface.
    other, os_ = _other_account(db)
    seq_o = _sequence(db, os_)
    o1 = _lead(db, os_, 99, company="Rival Co")
    _sent(db, seq_o, o1, at(2))
    _outcome(db, o1, m.OutcomeEvent.SENT, at(2))
    _outcome(db, o1, m.OutcomeEvent.BOOKED, at(4))
    _meeting(db, other, o1, at(9))
    db.add_all([
        m.CampaignCost(user_id=other.id, strategy_id=os_.id, category="data",
                       amount_cents=123_456, incurred_on=date(2026, 9, 3)),
        m.ApiUsage(user_id=other.id, strategy_id=os_.id, provider="anthropic", model="c",
                   purpose="pipeline", calls=1, input_tokens=1, output_tokens=1,
                   cost_micros=9_990_000, ts=at(3)),
    ])
    db.commit()

    # The real attribution sweep writes the ledger entries...
    attribution.run_sweep(db, now=at(30))
    # ...and l5's booking arrives after it, so it is still pending.
    _outcome(db, l5, m.OutcomeEvent.BOOKED, at(12))
    db.add(m.MeetingOutcome(lead_id=l5.id, user_id=uid,
                            outcome=m.MeetingOutcomeKind.INTERESTED, created_at=at(14)))
    db.commit()
    return {"a": a, "b": b, "leads": (l1, l2, l3, l4, l5, l6), "b1": b1,
            "other": other, "other_strategy": os_, "other_lead": o1}


def _ledger(db, user, **kw):
    return meetings_ledger.build_ledger(db, user.id, D_FROM, D_TO,
                                        now=at(30, 12), **kw)


def _rows(ledger):
    return {c["strategy_id"]: c for c in ledger["campaigns"]}


# --------------------------------------------------------------------------
# Reconciliation — the success metric
# --------------------------------------------------------------------------


def _raw_cost_cents(db, user_id, owned_ids):
    """Every raw cost row, summed in plain Python -- no ledger code involved."""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = datetime(2026, 10, 1, tzinfo=UTC)

    def _in(ts):
        ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        return start <= ts < end

    recorded = sum(c.amount_cents for c in db.query(m.CampaignCost).all()
                   if c.user_id == user_id and c.currency == "USD"
                   and D_FROM <= c.incurred_on <= D_TO)
    micros = sum(u.cost_micros for u in db.query(m.ApiUsage).all()
                 if (u.strategy_id in owned_ids
                     or (u.strategy_id is None and u.user_id == user_id)) and _in(u.ts))
    seconds = sum(c.duration_seconds or 0 for c in db.query(m.Call).all()
                  if c.strategy_id in owned_ids and _in(c.created_at))
    fees = sum(f.amount_cents for f in db.query(m.BillableMeeting).all()
               if f.user_id == user_id and f.status in ("pending", "charged")
               and _in(f.occurred_at))
    rate = system_settings.get(db, "voice_cost_per_minute")
    return recorded + round(micros / 10_000) + round(seconds / 60 * rate * 100) + fees


def test_ledger_cost_reconciles_to_the_raw_cost_rows(db_session, test_user, world):
    ledger = _ledger(db_session, test_user)
    owned = {world["a"].id, world["b"].id}
    raw = _raw_cost_cents(db_session, test_user.id, owned)
    # $100 data + $20 tools + $5 AI + $40 shared + $3 metered AI + $0.30 calls + $50 fee
    assert raw == 21_830
    totals = ledger["totals"]
    assert totals["infrastructure_cost_cents"] == 0
    assert totals["total_cost_cents"] == raw
    assert sum(c["total_cost_cents"] for c in ledger["campaigns"]) \
        + totals["unallocated_cost_cents"] == raw

    rec = ledger["reconciliation"]
    assert rec["raw_cost_cents"] == raw and rec["ledger_cost_cents"] == raw
    assert rec["difference_cents"] == 0 and rec["balanced"] is True
    assert rec["decomposition_balanced"] is True


def test_cost_decomposition_per_campaign(db_session, test_user, world):
    rows = _rows(_ledger(db_session, test_user))
    a, b = rows[str(world["a"].id)], rows[str(world["b"].id)]
    # shared = $40 recorded + $1 unattributed AI, split 3:1 by sends
    assert (a["shared_cost_cents"], b["shared_cost_cents"]) == (3_075, 1_025)
    assert a["ai_cost_cents"] == 200 + 30 + 500           # metered + calls + recorded AI
    assert a["data_cost_cents"] == 10_000                  # August's $7.77 is outside
    assert a["other_recorded_cost_cents"] == 2_000         # EUR ads excluded
    assert a["platform_fee_cents"] == 5_000
    assert a["total_cost_cents"] == 730 + 10_000 + 2_000 + 3_075 + 5_000
    for row in (a, b):
        parts = (row["ai_cost_cents"] + row["data_cost_cents"]
                 + row["other_recorded_cost_cents"] + row["shared_cost_cents"]
                 + row["platform_fee_cents"] + row["infrastructure_cost_cents"])
        assert parts == row["total_cost_cents"]


def test_agrees_with_revenue_analytics_when_infrastructure_is_zero(
        db_session, test_user, world):
    ledger = _ledger(db_session, test_user)
    report = revenue_analytics.report(db_session, test_user.id, D_FROM, D_TO)
    by_id = {c["strategy_id"]: c for c in report["campaigns"]}
    for row in ledger["campaigns"]:
        base = by_id[row["strategy_id"]]
        assert row["total_cost_cents"] == base["total_cost_cents"]
        assert row["meetings_booked"] == base["meetings"]
        assert row["cost_per_meeting_booked_cents"] == base["cost_per_meeting_cents"]
        assert row["revenue_cents"] == base["revenue_cents"]
    assert ledger["totals"]["total_cost_cents"] == report["totals"]["cost_cents"]


def test_booked_meetings_reconcile_to_attribution_rows(db_session, test_user, world):
    ledger = _ledger(db_session, test_user)
    start, end = at(1, 0), datetime(2026, 10, 1, tzinfo=UTC)
    booked = [o for o in db_session.query(m.Outcome).filter_by(event=m.OutcomeEvent.BOOKED)
              if start <= o.ts.replace(tzinfo=UTC) < end
              and o.lead_id in {lead.id for lead in world["leads"]} | {world["b1"].id}]
    # first booking per lead in the period
    first: dict = {}
    for o in sorted(booked, key=lambda o: o.ts):
        first.setdefault(o.lead_id, o)
    entries = {e.outcome_id: e for e in db_session.query(m.AttributionEntry).filter_by(
        user_id=test_user.id, outcome_kind=attribution.MEETING_BOOKED)}
    expected = {name: 0 for name in meetings_ledger.METHODS}
    for o in first.values():
        entry = entries.get(o.id)
        expected[entry.method if entry else meetings_ledger.PENDING] += 1

    totals = ledger["totals"]
    assert totals["booked_by_method"] == expected
    assert expected == {"direct_reply": 1, "thread_match": 0, "last_touch": 2,
                        "none": 0, "pending": 1}
    assert totals["booked_certain"] == 1 and totals["booked_attributed"] == 3

    rec = ledger["reconciliation"]
    assert rec["booked_outcome_leads"] == len(first) == rec["ledger_meetings_booked"] == 4
    assert rec["revenue_analytics_meetings"] == 4
    assert rec["attributed"] + rec["pending"] == 4 and rec["pending"] == 1


def test_every_event_carries_the_attribution_entry_of_its_outcome(
        db_session, test_user, world):
    ledger = _ledger(db_session, test_user)
    entries = {str(e.outcome_id): e for e in db_session.query(m.AttributionEntry)}
    for event in ledger["events"]:
        if event["kind"] == meetings_ledger.HELD:
            continue
        entry = entries.get(event["outcome_id"])
        if entry is None:
            assert event["method"] == "pending" and event["confidence"] is None
        else:
            assert event["method"] == entry.method
            assert event["confidence"] == entry.confidence
            assert event["attribution_id"] == str(entry.id)


# --------------------------------------------------------------------------
# Funnel rules
# --------------------------------------------------------------------------


def test_funnel_counts_distinct_leads_by_the_documented_rules(db_session, test_user, world):
    ledger = _ledger(db_session, test_user)
    rows = _rows(ledger)
    a, b = rows[str(world["a"].id)], rows[str(world["b"].id)]
    assert (a["qualified_conversations"], a["meetings_booked"], a["meetings_held"]) == (4, 3, 2)
    assert (b["qualified_conversations"], b["meetings_booked"], b["meetings_held"]) == (1, 1, 0)

    l1, l2, l3, l4, l5, l6 = world["leads"]
    events = ledger["events"]
    kinds = {(e["lead_id"], e["kind"]): e for e in events}
    # l1 qualified by its interested reply (earlier than the booking)
    assert kinds[(str(l1.id), "qualified_conversation")]["source"] == "positive_reply"
    assert kinds[(str(l1.id), "qualified_conversation")]["method"] == "direct_reply"
    # l2 qualified by its booking; rebooked once, counted once
    assert kinds[(str(l2.id), "qualified_conversation")]["source"] == "booked_outcome"
    assert sum(1 for e in events if e["lead_id"] == str(l2.id)
               and e["kind"] == "meeting_booked") == 1
    # l3 objected: not a qualified conversation; l6 booked in August: out
    assert not any(e["lead_id"] in (str(l3.id), str(l6.id)) for e in events)
    # l4 interested but never booked
    assert kinds[(str(l4.id), "qualified_conversation")]["method"] == "none"
    assert (str(l4.id), "meeting_booked") not in kinds
    # held: l1 by a completed meeting (inheriting its booking's attribution),
    # l5 by a logged outcome (its booking not yet attributed); l2's cancelled
    # meeting does not count
    held1 = kinds[(str(l1.id), "meeting_held")]
    assert held1["source"] == "meeting_completed" and held1["method"] == "direct_reply"
    held5 = kinds[(str(l5.id), "meeting_held")]
    assert held5["source"] == "meeting_outcome_logged" and held5["method"] == "pending"
    assert (str(l2.id), "meeting_held") not in kinds


def test_ratios(db_session, test_user, world):
    rows = _rows(_ledger(db_session, test_user))
    a = rows[str(world["a"].id)]
    cost = a["total_cost_cents"]
    assert a["cost_per_qualified_conversation_cents"] == round(cost / 4)
    assert a["cost_per_meeting_booked_cents"] == round(cost / 3)
    assert a["cost_per_meeting_held_cents"] == round(cost / 2)
    assert a["meetings_booked_per_1k_spend"] == round(3 * 100_000 / cost, 2)
    assert a["open_pipeline_cents"] == 50_000 and a["revenue_cents"] == 900_000
    assert a["booked_mean_confidence"] == round((1.0 + 0.7) / 2, 3)


def test_zero_denominators_are_null_not_zero(db_session, test_user, verified_strategy):
    ledger = _ledger(db_session, test_user)
    row = ledger["campaigns"][0]
    assert row["meetings_booked"] == 0 and row["total_cost_cents"] == 0
    assert row["cost_per_meeting_booked_cents"] is None
    assert row["cost_per_meeting_held_cents"] is None
    assert row["meetings_booked_per_1k_spend"] is None
    assert ledger["reconciliation"]["balanced"] is True


def test_a_meeting_outside_the_period_is_not_held(db_session, test_user, world):
    l4 = world["leads"][3]
    _meeting(db_session, test_user, l4, at(2, month=10))
    rows = _rows(_ledger(db_session, test_user))
    assert rows[str(world["a"].id)]["meetings_held"] == 2


# --------------------------------------------------------------------------
# Infrastructure allocation
# --------------------------------------------------------------------------


def test_infrastructure_is_zero_and_labelled_by_default(db_session, test_user, world):
    ledger = _ledger(db_session, test_user)
    assert system_settings.get(db_session, meetings_ledger.INFRA_SETTING) == 0
    infra = ledger["infrastructure"]
    assert infra["cents_per_1k_sends"] == 0 and infra["included"] is False
    assert "Not included" in infra["note"]
    assert all(c["infrastructure_cost_cents"] == 0 for c in ledger["campaigns"])


def test_infrastructure_rate_is_sends_times_rate(db_session, test_user, world):
    system_settings.set(db_session, meetings_ledger.INFRA_SETTING, 2_500)
    ledger = _ledger(db_session, test_user)
    rows = _rows(ledger)
    a, b = rows[str(world["a"].id)], rows[str(world["b"].id)]
    assert a["infrastructure_cost_cents"] == round(3 * 2_500 / 1000)   # 8
    assert b["infrastructure_cost_cents"] == round(1 * 2_500 / 1000)   # 2
    raw = _raw_cost_cents(db_session, test_user.id, {world["a"].id, world["b"].id})
    assert ledger["totals"]["total_cost_cents"] == raw + 10
    # Reconciliation compares like with like: infrastructure is not a raw row.
    assert ledger["reconciliation"]["ledger_cost_cents"] == raw
    assert ledger["infrastructure"]["included"] is True


def test_infrastructure_rate_rejects_bad_values(db_session):
    from app.services.system_settings import SettingTypeError

    with pytest.raises(SettingTypeError):
        system_settings.set(db_session, meetings_ledger.INFRA_SETTING, -1)
    with pytest.raises(SettingTypeError):
        system_settings.set(db_session, meetings_ledger.INFRA_SETTING, "100")


def test_non_usd_report_excludes_infrastructure_and_says_so(
        db_session, test_user, verified_strategy):
    system_settings.set(db_session, meetings_ledger.INFRA_SETTING, 1_000)
    for n in range(2):
        db_session.add(m.Deal(user_id=test_user.id, strategy_id=verified_strategy.id,
                              name=f"EUR {n}", value_cents=100, currency="EUR",
                              stage=m.DealStage.OPEN))
    lead = _lead(db_session, verified_strategy, 50)
    for _ in range(4):
        _outcome(db_session, lead, m.OutcomeEvent.SENT, at(5))
    ledger = _ledger(db_session, test_user)
    assert ledger["currency"] == "EUR"
    assert ledger["infrastructure"]["included"] is False
    assert ledger["infrastructure"]["excluded_non_usd_cents"] == 4
    assert ledger["totals"]["infrastructure_cost_cents"] == 0


# --------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------


def test_another_accounts_rows_never_appear(db_session, test_user, world):
    ledger = _ledger(db_session, test_user)
    ids = {c["strategy_id"] for c in ledger["campaigns"]}
    assert ids == {str(world["a"].id), str(world["b"].id)}
    assert all(e["lead_id"] != str(world["other_lead"].id) for e in ledger["events"])
    assert ledger["totals"]["total_cost_cents"] == 21_830     # not 123_456 / 9.99
    csv_text = meetings_ledger.to_csv(ledger, view="events")
    assert "Rival" not in csv_text

    # ...and the other account sees only its own.
    theirs = meetings_ledger.build_ledger(db_session, world["other"].id, D_FROM, D_TO)
    assert [c["strategy_id"] for c in theirs["campaigns"]] == [str(world["other_strategy"].id)]
    assert theirs["totals"]["meetings_booked"] == 1
    assert theirs["totals"]["meetings_held"] == 1
    assert theirs["totals"]["total_cost_cents"] == 123_456 + 999


def test_foreign_strategy_ids_are_dropped(db_session, test_user, world):
    ledger = _ledger(db_session, test_user, strategy_ids=[world["other_strategy"].id])
    assert ledger["campaigns"] == [] and ledger["events"] == []
    assert ledger["totals"]["total_cost_cents"] == 0


def test_strategy_filter_narrows_rows_but_reconciles_account_wide(
        db_session, test_user, world):
    ledger = _ledger(db_session, test_user, strategy_ids=[world["b"].id])
    assert [c["strategy_id"] for c in ledger["campaigns"]] == [str(world["b"].id)]
    assert {e["strategy_id"] for e in ledger["events"]} == {str(world["b"].id)}
    assert ledger["totals"]["meetings_booked"] == 1
    assert ledger["totals"]["total_cost_cents"] == 1_025
    assert ledger["reconciliation"]["raw_cost_cents"] == 21_830
    assert ledger["reconciliation"]["balanced"] is True


# --------------------------------------------------------------------------
# Exports
# --------------------------------------------------------------------------


def test_campaign_csv_has_one_row_per_campaign_and_a_total(db_session, test_user, world):
    ledger = _ledger(db_session, test_user)
    rows = list(csv.DictReader(io.StringIO(meetings_ledger.to_csv(ledger))))
    assert [r["campaign"] for r in rows][-1] == "TOTAL"
    assert len(rows) == 3
    total = rows[-1]
    assert total["total_cost"] == "218.30" and total["meetings_booked"] == "4"
    assert total["booked_pending"] == "1" and total["booked_direct_reply"] == "1"
    a = next(r for r in rows if r["strategy_id"] == str(world["a"].id))
    assert a["qualified_conversations"] == "4" and a["meetings_held"] == "2"
    assert a["infrastructure_cost"] == "0.00" and a["currency"] == "USD"


def test_events_csv_carries_no_names_or_emails(db_session, test_user, world):
    text = meetings_ledger.to_csv(_ledger(db_session, test_user), view="events")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == (4 + 3 + 2) + (1 + 1)  # A qualified/booked/held + B
    assert "Person " not in text and "@" not in text
    assert {r["attribution_method"] for r in rows} >= {"direct_reply", "pending"}


def test_csv_neutralises_spreadsheet_formulas(db_session, test_user, verified_strategy):
    product = db_session.get(m.Product, verified_strategy.product_id)
    product.name = '=HYPERLINK("http://evil.test","x")'
    lead = _lead(db_session, verified_strategy, 60, company="+cmd|' /C calc'!A0")
    _outcome(db_session, lead, m.OutcomeEvent.BOOKED, at(5))
    ledger = _ledger(db_session, test_user)
    campaigns = meetings_ledger.to_csv(ledger)
    events = meetings_ledger.to_csv(ledger, view="events")
    assert "'=HYPERLINK" in campaigns
    assert "'+cmd" in events
    for text in (campaigns, events):
        for row in csv.reader(io.StringIO(text)):
            assert not any(cell.startswith(("=", "+", "@")) for cell in row)


def test_unknown_csv_view_is_refused(db_session, test_user):
    with pytest.raises(ValueError):
        meetings_ledger.to_csv(_ledger(db_session, test_user), view="raw")


def test_pdf_renders(db_session, test_user, world):
    product = db_session.get(m.Product, world["a"].product_id)
    product.name = "Café Outreach — Ünïcode ✓"
    db_session.commit()
    pdf = meetings_ledger.to_pdf(_ledger(db_session, test_user))
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000


def test_audit_trail_pdf_still_renders_through_the_shared_renderer():
    from app.services import audit_trail

    report = {
        "account": {"user_id": "u"}, "generated_at": "2026-09-30T00:00:00",
        "scope": {"lead_id": None, "date_from": None, "date_to": None},
        "chain": {"valid": True, "sealed_records": 0, "head_seq_no": None, "head_hash": ""},
        "summary": {}, "signature": {"algorithm": "ed25519", "key_id": "k", "value": "v" * 200},
        "records": [{"seq_no": i, "occurred_at": "2026-09-01", "event": "sent",
                     "channel": "email", "lead_ref": "l", "chain_hash": "h"}
                    for i in range(1, 80)],
    }
    pdf = audit_trail.render_pdf(report)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000


# --------------------------------------------------------------------------
# Flag + API
# --------------------------------------------------------------------------


def _enable(db):
    system_settings.set(db, meetings_ledger.FLAG, True)


def test_flag_defaults_off(db_session):
    assert system_settings.DEFAULTS[meetings_ledger.FLAG][0] is False
    assert system_settings.get(db_session, meetings_ledger.FLAG) is False
    keys = {s["key"] for s in system_settings.all_settings(db_session)}
    assert {meetings_ledger.FLAG, meetings_ledger.INFRA_SETTING} <= keys


def test_flag_off_is_409_on_every_route(client, world):
    for path in ("/analytics/meetings-ledger",
                 "/analytics/meetings-ledger/export?format=csv",
                 "/analytics/meetings-ledger/export?format=pdf"):
        resp = client.get(path)
        assert resp.status_code == 409, path
        assert "disabled" in resp.json()["detail"]


def test_flag_does_not_change_the_revenue_report(client, db_session, world):
    url = "/analytics/revenue?date_from=2026-09-01&date_to=2026-09-30"
    before = client.get(url).json()
    _enable(db_session)
    assert client.get(url).json() == before


def test_ledger_endpoint(client, db_session, world):
    _enable(db_session)
    resp = client.get("/analytics/meetings-ledger?date_from=2026-09-01&date_to=2026-09-30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["meetings_booked"] == 4
    assert body["reconciliation"]["balanced"] is True
    assert body["version"] == "leadpilot.meetings-ledger.v1"

    one = client.get(f"/analytics/meetings-ledger?date_from=2026-09-01&date_to=2026-09-30"
                     f"&strategy_id={world['a'].id}").json()
    assert [c["strategy_id"] for c in one["campaigns"]] == [str(world["a"].id)]

    assert client.get("/analytics/meetings-ledger").status_code == 200   # default period
    assert client.get("/analytics/meetings-ledger?date_from=2026-09-30"
                      "&date_to=2026-09-01").status_code == 422
    assert client.get("/analytics/meetings-ledger?date_from=2020-01-01"
                      "&date_to=2026-09-01").status_code == 422


def test_foreign_strategy_is_404(client, db_session, world):
    _enable(db_session)
    resp = client.get(f"/analytics/meetings-ledger?strategy_id={world['other_strategy'].id}")
    assert resp.status_code == 404
    resp = client.get(f"/analytics/meetings-ledger/export?strategy_id={uuid.uuid4()}")
    assert resp.status_code == 404


def test_other_account_sees_only_its_own_through_the_api(client, db_session, world):
    _enable(db_session)
    resp = client.get("/analytics/meetings-ledger?date_from=2026-09-01&date_to=2026-09-30",
                      headers=auth_headers(world["other"]))
    assert resp.status_code == 200
    body = resp.json()
    assert [c["strategy_id"] for c in body["campaigns"]] == [str(world["other_strategy"].id)]
    assert body["totals"]["meetings_booked"] == 1


def test_exports(client, db_session, world):
    _enable(db_session)
    q = "date_from=2026-09-01&date_to=2026-09-30"
    resp = client.get(f"/analytics/meetings-ledger/export?format=csv&{q}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["cache-control"] == "private, no-store"
    assert 'filename="vectorae-meetings-ledger-2026-09-01-2026-09-30.csv"' \
        in resp.headers["content-disposition"]
    assert resp.text.splitlines()[0].startswith("campaign,strategy_id,currency")

    events = client.get(f"/analytics/meetings-ledger/export?format=csv&view=events&{q}")
    assert events.status_code == 200 and "-events.csv" in events.headers["content-disposition"]
    assert events.text.splitlines()[0].startswith("campaign,strategy_id,kind")

    pdf = client.get(f"/analytics/meetings-ledger/export?format=pdf&{q}")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")

    assert client.get(f"/analytics/meetings-ledger/export?format=xlsx&{q}").status_code == 422
    assert client.get(f"/analytics/meetings-ledger/export?view=all&{q}").status_code == 422


def test_unauthenticated_is_refused(anon_client, db_session):
    _enable(db_session)
    assert anon_client.get("/analytics/meetings-ledger").status_code == 401
