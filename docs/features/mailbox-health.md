# Deliverability Health Score per Mailbox (Part 1, Feature 4)

**Migration** `0056_mailbox_health` · **Service** `app/services/mailbox_health.py` ·
**Task** `app.workers.deliverability_tasks.refresh_mailbox_health` (every 4h) ·
**UI** `frontend/src/lib/mailboxHealth.ts`,
`components/settings/MailboxHealthCard.tsx`

## Why this exists beside `deliverability.py`

FG9's `deliverability.py` scores a sending **domain**: SPF/DKIM/DMARC,
blocklists, the account's overall bounce rate. That is the right thing to
monitor and it is unchanged.

But the domain is the wrong grain to *act* on. Two mailboxes on one domain can
have very different reputations — one warmed for a year, one connected last
week and already collecting complaints — and slowing the whole domain punishes
the good one. This feature scores the **mailbox** and throttles exactly the
mailbox that is in trouble.

`mailbox_ref` is **exactly** the value the send path writes to
`messages.sender_ref` (a Gmail account id, `linkedin:<id>`,
`whatsapp:<phone id>`). That is what makes volume, bounces and complaints
countable per mailbox, and it is why the column is a string rather than a
foreign key: not every sending identity lives in one table.

## The four inputs

| Input | Source |
|-------|--------|
| **auth** | SPF / DKIM / DMARC via `deliverability.dns_auth` — reused verbatim, so there is one implementation of the DNS reading, not two that can disagree |
| **complaints** | a documented **proxy** — see below |
| **bounces** | bounce outcomes joined to messages sent **from this mailbox** |
| **volume** | sends today vs a 7-day baseline that *excludes today*, so today is compared against what it is supposed to be a spike above |

### Honesty about the complaint rate

Vectorae has **no feedback-loop (FBL) or Postmaster Tools integration**, so a
true "marked as spam" signal is not available. `complaint_rate` is a
**proxy**: unsubscribes plus replies the classifier read as a request to stop,
over sends in the window.

Every payload carries `complaint_source: "proxy"`, `status()` carries a
`complaint_note` explaining it, and the UI prints "(proxy)" next to the number.
A number presented as something it is not would be worse than no number. When
an FBL is connected, only `complaint_rate` and that label change — the score,
the thresholds and the send gate do not.

**F03 Deliverability Autopilot** (flag off by default) is that change: with a
fresh Google Postmaster Tools reading for the mailbox's domain, the real Gmail
spam rate replaces the proxy and `complaint_source` is `"postmaster"` — stored
on the row (`mailbox_health.complaint_source`, migration 0072; NULL reads as
`"proxy"`). It also adds explicit brakes on top of the score — ≥ 0.10%
throttles, ≥ 0.30% pauses — and the stepped new-mailbox ramp in the daily
allowance. The Postmaster importer is built but disabled and unverified
against a live account; see `f03-deliverability-autopilot.md`.

## The score and what it does

```
100
 -25  no SPF            -20  no DMARC (-10 for p=none)     -10  no DKIM
 -35  complaints ≥ 0.1%      -65  complaints ≥ 0.3%
 -35  bounces    ≥ 2%        -65  bounces    ≥ 5%
 -10  volume spike (≥3× the 7-day baseline, min 20 sends)
```

| Score | State | Effect |
|-------|-------|--------|
| ≥ 70 | `healthy` | normal cap |
| 40–69 | `throttled` | daily cap cut to 25% of the ramped allowance, minimum 2 |
| < 40 | `paused` | no sends; messages **deferred**, never cancelled |

The deductions are sized deliberately:

- A mailbox with **no SPF, DMARC or DKIM** lands on **45 — throttled, not
  paused**. Those are DNS records fixable in an afternoon; stopping outreach
  outright would cost more than slowing it.
- A **complaint or bounce rate past the danger line takes 65 points on its
  own**, which pauses from full marks. A mailbox is only stopped by evidence of
  actual recipient harm.
- **Unknown rates are never punished.** A brand-new mailbox has no rates; that
  is not evidence of anything.
- A throttle **never becomes a silent pause**: `MIN_THROTTLE_CAP` is 2.

## The send gate

In `outreach_tasks.send_message_impl`, the email branch now asks
`mailbox_health.gate()`:

- **paused** → the message is rescheduled into tomorrow's send window, its
  `error` records `mailbox paused: <reason>` so the lead timeline can say why,
  and the task returns `deferred_mailbox_paused`. **Deferred, never dropped** —
  the same rule the daily cap has always followed.
- **throttled** → the throttle cap is combined with the compliance rule's cap
  by taking the **smaller** of the two. A throttle can never *raise* a cap.
- **no row** → healthy. Absence of a check is not a reason to block sending.

`gate()` never raises; a lookup failure degrades to healthy.

## Resuming is a human decision

A refresh that happens to score higher **never** resumes a paused mailbox — the
same rule as the bounce pause and the blacklist pause. `resume()` records
`resumed_at` and `resumed_by_user_id`, and if the mailbox is still under 70 it
comes back **throttled**, not free.

## Schedule

Every four hours at :05 (`crontab(hour="*/4", minute=5)`), offset from the
daily domain sweep so the two never share a minute on the `default` queue.
Four-hourly rather than daily because the two signals this exists to catch — a
complaint spike and a volume spike — do their damage inside a day.

## API

| Method | Path |
|--------|------|
| `GET` | `/deliverability/mailboxes` |
| `POST` | `/deliverability/mailboxes/refresh` |
| `POST` | `/deliverability/mailboxes/{ref}/resume` |

A connected mailbox with no row yet is listed as `band: "unchecked"` rather
than omitted — "we have not looked" is a state the settings page must show.

## Tests

`tests/test_mailbox_health.py` (50) — the score's shape and bounds, that
unauthenticated-but-quiet throttles rather than pauses, per-mailbox isolation
of bounces and complaints, the spike baseline, upsert-not-append, consumer
domains not scored on DNS they do not own, a DNS failure keeping the last known
auth, a refresh never resuming a pause, and the send gate deferring rather than
dropping. `tests/test_mailbox_health_migration.py` (5).
`frontend/src/tests/mailboxHealth.test.ts` (19).
