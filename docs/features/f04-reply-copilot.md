# F04 — Reply Copilot

**Flags** `reply_copilot_enabled` (system setting, **default off**) and
`reply_copilot_auto_send_allowed` (system setting, **default off**) plus a
**per-account** opt-in `auto_send_meeting_time` (default off) ·
**Migration** `0077_reply_copilot` · **Service** `app/services/reply_copilot.py` ·
**Worker** `app/workers/reply_tasks.py` (draft hook + `send_reply_draft`) ·
**Send path** `app/workers/outreach_tasks.py::send_message_impl` (the existing
chokepoint; three narrow additions, see "Sending") ·
**API** `app/api/reply_copilot.py` ·
**UI** `frontend/src/components/inbox/ReplyCopilotPanel.tsx` (in the unified
inbox), `frontend/src/lib/replyCopilot.ts`, `frontend/src/lib/api/replyCopilot.ts`

Status: built in the same PR as this spec (founder decision 2026-10-10:
"build straight away"; the founders review spec and code together at merge).

## Problem

Competitor complaint (STAR A.6, F04): **11x's most-cited weakness is reply
handling.** An outbound tool that books meetings on paper but answers a
"sounds interesting — how does it work?" with silence, a canned line, or a
hallucinated case study loses exactly the replies it worked hardest to get.

Vectorae already *reads* every reply three ways (routing class, Feature 2
category + an ungrounded draft, Feature 1 positive-intent label) and files it
in the unified inbox. What it could not do was **answer**: the Feature 2 draft
is not grounded in the campaign's objection research, the approved-claims
library or a booking link, is never claim-verified, and "approve" only writes a
CRM note — there is no path from an approved answer to a compliant send.

## Recon — what already exists (this is "extend", not a parallel engine)

| Building block | Where | Used how |
|---|---|---|
| Reply-processing trigger, dispatched for every stored reply on every channel | `app/workers/reply_tasks.py::process_inbound_reply_impl` | The draft hook runs here, **only when `reply_copilot_enabled` is on**; off = one settings read and an identical return value. |
| Routing class (`interested / question / objection / …`) | `app/services/reply_classification.py`, stored on `inbound_replies.classification` | Eligibility: only `interested`, `question`, `objection` are drafted. |
| Positive-intent label (Feature 1) | `app/services/reply_intent.py`, `inbound_replies.intent_label` | `unsubscribe` blocks a draft; used as the fallback eligibility signal when the routing class is not stored yet (the LinkedIn webhook enqueues before it routes). |
| Feature 2 reply intelligence (category + ungrounded draft, `approved_draft` = CRM note) | `app/services/reply_intelligence.py`, `PATCH /crm/replies/{id}/intelligence` | Reused helpers: `_original_message` (what the reply answers), `find_banned_words` / `BANNED_WORDS`. Feature 2 itself is **unchanged** (its draft stays an internal CRM suggestion with no send path). |
| Unified inbox + per-reply "handled" marker (Feature 6) | `app/services/inbox.py`, `app/api/inbox.py`, `/inbox?lead=` page | Drafts are queued and decided inside the inbox thread view; approving (or copying) marks the reply handled through `inbox.mark_handled`. |
| Strategy objection research | `research_steps` phase 7 of the STRATEGY pipeline ("Objection mapping, proof assets, case-study angles"): step 56 *Rebuttal scripts*, 62 *Objection-handling FAQ*, 63 *Phase synthesis: objection playbook* (`app/pipeline/registry.py`) | Read (truncated) into the prompt as the only allowed source of objection answers. |
| Approved claims + forbidden phrases (F05) | `app/services/guardrails.py` (`approved_claims`, `forbidden_phrases`, `forbidden_hits`, `check_copy`, `exclusion_for_lead`) | Approved claims are the only claims about us the prompt allows; forbidden phrases are listed in the prompt, checked on the draft, checked again at approval and (existing) at send. |
| Claim verification (Feature A1 + F05 sender claims) | `app/services/claim_verification.py::enforce` | Run on every draft at generation (unsupported claims stripped, recorded on the draft), again at approval (any unsupported claim **blocks** approval), and again at send by the chokepoint's renderer. |
| Booking link | `sequences.booking_url` (tagged with the owner by `calendly.tag_booking_url`, exactly as the send path does), else the owner's first active `/book` page (`calendar_booking_pages`, public at `{frontend_url}/book/?slug=<slug>`) | Given to the prompt; the only URL a draft may contain. |
| The send chokepoint | `app/workers/outreach_tasks.py::send_message_impl` | **Every** Reply Copilot email goes through it as an ordinary `messages` row (`origin = "reply_copilot"`). |
| Usage meter | `app/services/usage_meter.py` | Draft generation and approval-time verification run inside `usage_meter.scope(..., purpose="reply_copilot")`. |

There was no existing path that sends an answer to an inbound message
(`route_inbound_impl`: "Never auto-replies to humans"; Feature 2's approval is a
handoff note). Half of the feature did not exist, so the new code is: the
grounded draft, the draft queue (approve / edit / discard / copy), the send
hand-off into the chokepoint, the opt-in auto-send, and the metrics.

## Behavior

### 1. Which replies get a draft

A draft is generated in `process_inbound_reply_impl` when **all** hold:

1. `reply_copilot_enabled` is on;
2. the reply is matched to a lead (`lead_id` not null) and has no draft yet
   (`reply_drafts.reply_id` is UNIQUE — redeliveries and `force=True` never
   create a second one or overwrite a draft a person may be editing);
3. the channel is `email` (sendable) or `linkedin` (copy-only). WhatsApp and
   phone are non-goals;
4. it is from a person (`inbox.is_from_a_person`: not bounce / out-of-office /
   automated / bot);
5. the routing class is `interested` (→ intent `positive`), `question` or
   `objection`. When the routing class is not stored yet, the Feature 1 label
   decides: `interested` → `positive`, `objection` → `objection`; anything else
   → no draft;
6. **opt-out guard (SAFETY):** no draft when the routing class is
   `unsubscribe_request` / `not_interested`, when the intent label is
   `unsubscribe`, or when the prospect's **new text** contains opt-out
   language (deterministic, `reply_copilot.opt_out_language`: "unsubscribe",
   "opt out", "remove me/us", "take me off", "do not / don't contact|email|
   message", "no more emails", "leave me alone", "delete my data"; and "stop"
   only as the whole message or a whole line, "please stop", or "stop
   emailing|messaging|contacting|sending|writing|calling…" — "stop by" is not an
   opt-out). The classifier degrades unparseable answers to `question`, so this
   check is deliberately independent of the model and errs toward *no draft*.
   **New text** (`reply_copilot.new_text`) is everything above the quoted
   thread: it cuts at Gmail/Apple "On … wrote:" (also when wrapped over two
   lines), Outlook "-----Original Message-----", a `From:` line followed within
   four lines by `Sent:/Date:/To:/Subject:`, an underscore separator line, and
   forwarded-message headers; drops `>`-quoted lines; and removes our own
   compliance footer / region notice / unsubscribe-link lines. It only ever
   removes quoted material, so an opt-out written above the quote always
   blocks. The same new text is used for the meeting-time check, for the reply
   given to the model, and for the later-reply checks at send time;
7. **contact guard (SAFETY):** the lead's status is answerable (see
   `ANSWERABLE_LEAD_STATUSES` below) and it is not archived by the conversion
   gate; the lead is not suppressed (`is_suppressed` on email, phone and
   LinkedIn profile) and not excluded by the account's F05 lists (when
   `guardrails_enabled`).

**Answerable lead statuses** (`reply_copilot.ANSWERABLE_LEAD_STATUSES`):
`replied`, `contacted`, `verified`, `flagged` — and `engagement_state` not
`archived`. Every other status means the conversation has moved on or the
contact is gone: `meeting_booked`, `proposal_sent`, `opportunity`,
`closed_won`, `closed_lost`, `disqualified`, `dropped` (bounced, GDPR-erased,
undeliverable) and the pre-contact sourcing states. These leads get no draft,
cannot have a draft approved (409), and a queued answer is cancelled at send.

A newer draft for the same lead marks that lead's older **pending** drafts
`superseded` (an answer to a stale message is not worth sending). This is a
conditional `UPDATE … WHERE status = 'pending'`, so a draft being approved at
that moment is never flipped back; its queued send is then cancelled by the
newer-reply rule (§4).

### 2. The draft

One model call (`reply_copilot._SYSTEM`) with, as context:

- the reply — **untrusted**, inside `prompt_safety.block()` under
  `prompt_safety.NOTICE` (F-AI-02); the lead's name, company and title via
  `prompt_safety.inline()`; the message we sent (our own text);
- the intent (positive / objection / question) and what to do for it;
- the campaign's objection playbook (phase-7 research, ≤ 6,000 chars);
- the product's approved claims (F05, ≤ 20) — "the only statements about us,
  our results or our customers you may make";
- the booking link, or an explicit "there is no booking link — do not invent
  one";
- the forbidden phrases (F05) and Feature 2's banned words.

The model returns `{"body", "meeting_time_request"}`. The draft is rejected and
regenerated **once** (naming the problem) when it is empty, contains a banned
word or forbidden phrase, a placeholder (`[Name]`, `{first_name}`, `{{x}}`,
`<name>`), or **any link other than the booking link or the sender's own
domain** (a reply cannot smuggle a link into our answer). Links are found with
or without a scheme: `https://…`, `www.…`, and bare hosts such as
`acme-pay.io/verify` whose TLD is in a curated list (`reply_copilot._TLDS`) so
that "e.g.", "i.e.", "3.5x", "Node.js" and e-mail addresses are not read as
links. The booking link must match exactly (with or without its scheme — any
other path on the same booking host is foreign); the sender's own domain is
the domain of the account's connected sending mailbox(es), minus shared
consumer providers (gmail.com etc.), and any path or subdomain of it is
allowed. A second
failure → no draft (a person answers by hand); nothing degrades to a canned
text.

The surviving text then goes through `claim_verification.enforce`
(`fields={"body": ...}`, `message=None`): unsupported claims about the prospect
and unbacked proof claims about us are stripped; every non-verified decision is
stored on the draft (`claim_flags_json`) and shown in the UI as "removed by
claim verification". Email drafts get the subject `Re: <their subject>`
(falls back to the subject we sent).

`meeting_time_request` is true only when the model says so **and** the reply
text itself matches the deterministic "send me a time" language
(`reply_copilot.meeting_time_language`: "send me a time/times/slot/invite",
"what times work", "when are you free/available", "find a time", "book a call",
"send your calendar/booking link" …).

### 3. The queue: Approve / Edit / Discard (and Copy for LinkedIn)

Drafts appear in the unified inbox thread for that prospect.

- **Approve (email)** — optionally with an edited body/subject ("Edit" is
  approve-with-changes; there is no separate save). Before anything is queued,
  the final words are checked: (a) the contact guard again (suppressed or
  excluded → **409**, nothing queued); (b) forbidden phrases → **422** listing
  them; (c) claim verification over subject + body — **any** claim that would
  be stripped or rewritten **blocks approval (422)** with the claims listed. We
  chose *block* over *flag*: an approved text must be the text that is sent,
  and a silently stripped sentence would make "sent unedited" a lie. Then a
  `messages` row is created (`origin="reply_copilot"`, the enrollment's
  sequence, `scheduled_at = now + 5 min` dispatch guard — the same guard the
  auto-follow-up uses) and `send_reply_draft` is enqueued, which calls
  `send_message_impl`. If the broker is down, the beat dispatcher picks the
  row up after the guard. A blank or whitespace subject falls back to the
  drafted `Re: <their subject>` (never sent subjectless; not counted as an
  edit). The reply is marked handled at approval, and the marker then follows
  the send (§4.4).
- **Copy (LinkedIn)** — the same final-words checks; the draft becomes
  `copied` and the reply is marked handled. **Nothing is sent by Vectorae**:
  the UI copies the text to the clipboard. `approve` on a LinkedIn draft is
  **409** ("copy-only").
- **Discard** — `discarded`; nothing else changes (the reply stays in "needs
  reply": discarding a draft is not answering the prospect).

Only `pending` drafts can be decided (409 otherwise). Every decision —
approve (including auto-send), copy, discard — moves the draft out of
`pending` with one conditional `UPDATE reply_drafts SET status=… WHERE id=:id
AND user_id=:uid AND status='pending'`; a rowcount other than 1 rolls back and
answers 409. The approve claim is in the same transaction as the message it
creates, so two tabs, a double click or a person racing auto-send can never
queue two messages.

### 4. Sending — through the chokepoint, never around it

`send_message_impl` treats a `reply_copilot` row like any other message: campaign
state, **suppression on email/phone/LinkedIn**, **F05 exclusions (fails
closed)**, **F01 Bounce Shield**, conversion gate, **compliance rules (consent,
send window)**, **daily cap + mailbox health + F03 ramp**, client-domain pool,
render, **F03 one-click unsubscribe headers**, **send-review queue (Part 1
F5)**, **F05 forbidden phrases over the final words**, then the channel. Three
narrow additions, all for `origin == "reply_copilot"` only:

1. **Send-time cancel rules** (`reply_copilot.send_time_hold`, before every
   other gate; the message is CANCELLED with the reason in `messages.error`),
   in order:

   | Condition | Result |
   |---|---|
   | `reply_copilot_enabled` switched off since approval | `cancelled_reply_copilot_off` |
   | the draft or its reply is gone (erased) | `cancelled_draft_missing` |
   | lead status not in `ANSWERABLE_LEAD_STATUSES`, or `engagement_state = archived` | `cancelled_lead_status` |
   | a later reply (by `received_at`) declines or opts out: routing `unsubscribe_request` / `not_interested`, intent `unsubscribe`, or opt-out wording in its new text | `cancelled_prospect_declined` |
   | any later reply from a person | `cancelled_newer_reply` |

   Why the lead's own state is checked: every later stop site (Calendly
   `meeting_booked`, CRM sync, phone-call outcome, meeting follow-up,
   conversion-gate archive) stops only enrollments that are not already
   stopped, and the reply has already stopped this one — so the stop reason
   never changes after the reply. Suppression and F05 exclusions are checked
   right after by the chokepoint itself (`cancelled_suppressed` /
   `cancelled_excluded`), which also catches an opt-out received after
   approval.
2. **The enrollment-stopped gate** (`reply_copilot.answers_the_stop`, logic in
   `stop_explained_by`). The send
   path cancels anything on a stopped enrollment. A Reply Copilot message may
   pass it **only** when the stop is attributable to the conversation it
   answers: `stop_reason == "replied_<class>"` of **this draft's reply**, or of
   an earlier reply of the same lead (the prospect wrote twice: the first
   reply stopped the sequence, the second is not re-stopped), **and**
   `stopped_at` is not before that reply was stored.
   **Per-account setting `answer_earlier_replies`** (founder decision
   2026-10-11, "User decide this"; `reply_copilot_settings`, default **true** =
   the rule just described). When the account sets it to **false (strict)**,
   only the stop caused by **this draft's own reply** is accepted: its class
   must match the stop reason, it must be stored at or before `stopped_at`,
   and no other reply of the lead stored at or before it may match the same
   stop (a reply-class stop is written once, by the first stopping reply, so
   an earlier match means the stop was that reply's; a tie fails closed).
   In practice, strict means an answer to a prospect's second reply is
   cancelled (`cancelled_stopped`) and the reply returns to "needs reply".
   Strict only ever cancels more; it never lets through anything the default
   would stop. Auto-send does not queue an answer the strict gate would
   cancel (`auto_send_decision` → `stop_not_from_this_reply`). A stop for any other
   reason (unsubscribed, bounced, excluded, manual, `crm_*`, `call_*`,
   `meeting_booked`, `kill_signal:*`), a reply-class stop no reply of this
   conversation explains, or a stop without `stopped_at` cancels it
   (`cancelled_stopped`). A newer reply is handled by rule 1.
3. **Rendering.** The approved words are sent; the sequence step is **not**
   re-rendered by the model. They go through `claim_verification.enforce`
   again and `build_compliant_email` (unsubscribe footer, region notice,
   `List-Unsubscribe` + `List-Unsubscribe-Post` headers) exactly like every
   email; the thread is the prospect's reply thread (`inbound_replies.thread_ref`,
   else the answered message's). The words are read from the draft, not the
   message row, so a retry after a transient failure never footers twice. No
   open pixel or click rewriting is added to an answer.
4. **After every send attempt** (`reply_copilot.after_send_attempt`, called
   by `send_reply_draft` and by the beat dispatcher's `leadpilot.outreach.send`
   task, so every path is covered) the result is recorded on the draft and the
   reply's inbox marker is kept true: message `sent` → reply handled; still
   `scheduled`/`sending` (deferred to the send window, cap, paused campaign) →
   unchanged; anything else (held for review, cancelled, blocked, failed) →
   the reply goes back to "needs reply". On `sent` the sequence is **not**
   advanced (`schedule_next_step` is skipped — a reply is not a sequence step)
   and the `SENT` outcome is tagged `source = "reply_copilot"`.

### 5. Optional auto-send — "send me a time", email only, opt-in

Default **off** at two levels. A draft is auto-approved (same checks, same
queue, `auto_sent = true`, `decided_by_user_id = NULL`) only when **all** hold:

- deployment switch `reply_copilot_auto_send_allowed` is on (admin);
- the account opted in (`PUT /reply-copilot/settings {"auto_send_meeting_time": true}`;
  refused with 409 while the deployment switch is off);
- channel is `email`; intent is `positive` or `question`;
- `meeting_time_request` is true (model **and** deterministic language);
- the draft contains the booking link and claim verification changed nothing;
- the routing has already stopped the sequence for this reply (otherwise the
  routing's own stop would cancel the queued message — the draft stays pending
  for a person instead);
- every approval check passes. Any failure leaves the draft **pending** for a
  person; auto-send never weakens a check.

LinkedIn is never auto-sent (copy-only).

## Non-goals

- No WhatsApp or phone drafts. No LinkedIn sending (copy-to-clipboard only).
- No change to Feature 2's draft, categories or `PATCH /crm/replies/{id}/intelligence`.
  Consolidating Feature 2's ungrounded draft and this draft into one model
  call is a possible follow-up (it would save one call per reply) but changes
  Feature 2's contract, so it is not done here.
- No automatic sending of anything except the narrow auto-send above.
- No plan gate: the feature's only marginal cost is AI spend, already capped
  per account per day (see "AI cost"). If the founders want it plan-gated, it
  is one `PlanGate` dependency on the router plus a key in every plan.
- No regeneration endpoint (discard + answer by hand, or wait for the next reply).

## Data model and migration (`0077_reply_copilot`, create-only)

`reply_drafts` — one per inbound reply:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK users CASCADE, indexed | owning account (lead → strategy → product → user) — the tenancy column |
| `reply_id` | UUID FK inbound_replies CASCADE, **UNIQUE** | idempotency |
| `lead_id` | UUID FK leads CASCADE, indexed | |
| `strategy_id` | UUID FK strategies SET NULL | |
| `channel` | String(20) | `email` / `linkedin` |
| `intent` | String(20) | `positive` / `objection` / `question` |
| `meeting_time_request` | Boolean | |
| `status` | String(20), indexed | `pending` / `approved` / `copied` / `discarded` / `superseded` |
| `draft_subject`, `draft_body` | String(500) / Text | as generated (after claim verification) |
| `final_subject`, `final_body` | String(500) / Text | what was approved / copied |
| `edited` | Boolean | final ≠ draft (whitespace-normalised) — the "sent unedited" metric |
| `claim_flags_json` | JSON | non-verified claim decisions at generation |
| `grounding_json` | JSON | playbook steps used, approved-claim ids, booking URL, forbidden-phrase count |
| `message_id` | UUID FK messages SET NULL | the queued send |
| `send_result` | String(60) | last `send_message_impl` result for it |
| `auto_sent` | Boolean | |
| `reply_received_at` | DateTime tz | copied from the reply — the metric's start |
| `decided_at` | DateTime tz | approve / copy / discard time |
| `decided_by_user_id` | UUID FK users SET NULL | NULL for auto-send |
| `created_at`, `updated_at` | | |

`reply_copilot_settings` — one per account: `user_id` (PK, FK users CASCADE),
`auto_send_meeting_time` (Boolean, server default false),
`answer_earlier_replies` (Boolean, server default **true**; added by migration
`0082_annual_billing`, so every existing account keeps the original
behaviour), `updated_by_user_id` (FK users SET NULL), timestamps.

JSON (not JSONB), `sa.Uuid`, boolean server defaults — works on SQLite and
PostgreSQL. **Downgrade** drops both tables: drafts and the opt-in are lost;
the messages a draft queued and their outcomes stay (they are ordinary
`messages` rows with `origin = "reply_copilot"`). `TARGET_REVISION` in
`scripts/activate_with_keys.py` moves to `0077_reply_copilot`.

Both tables are in `account_export.OWNERSHIP` (`user_id`), and the GDPR lead
delete erases the lead's drafts in the same transaction (the lead row is a
tombstone, so its FK cascade never fires).

## API (all under the flag: **404** while `reply_copilot_enabled` is off)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/reply-copilot/drafts?status=pending\|approved\|copied\|discarded\|superseded\|all&lead_id=&limit=&offset=` | the account's drafts, oldest pending first |
| `GET` | `/reply-copilot/drafts/{id}` | one draft, with its delivery state (from the queued message) |
| `POST` | `/reply-copilot/drafts/{id}/approve` | `{subject?, body?}` — email only; 409 not pending / LinkedIn / contact blocked / no campaign to send through; 422 forbidden phrase or unsupported claim |
| `POST` | `/reply-copilot/drafts/{id}/copy` | `{body?}` — LinkedIn only; same checks; sends nothing |
| `POST` | `/reply-copilot/drafts/{id}/discard` | |
| `GET` / `PUT` | `/reply-copilot/settings` | `{auto_send_meeting_time?, answer_earlier_replies?}` (either alone; neither = 422) + whether the deployment allows auto-send. `answer_earlier_replies` needs no deployment switch. |
| `GET` | `/reply-copilot/metrics?days=30` | the success metric (below) |

Every route is authenticated; every query filters `reply_drafts.user_id =
current_user.id`; another account's draft is **404** (never 403). Decision
routes are rate limited (`crm_write` bucket, as the other reply write paths).
Approval-time claim verification (which may make one claim-extraction model
call) runs inside `usage_meter.scope(db, user_id=..., strategy_id=...,
purpose="reply_copilot")` — `app/api/metering.py` (PR #61) is not on this
branch, so the scope is opened directly, as the other handlers on this branch do.

## UI

The inbox thread view (`/inbox?lead=<id>`) shows a **Reply Copilot** card above
the conversation when the flag is on (the API's 404 renders nothing): each
pending draft with its intent, channel, "removed by claim verification" notes,
the booking link used, and an editable text area. Email: **Approve & send** (or
**Send edited** once changed) and **Discard**. LinkedIn: **Copy** (writes the
clipboard, then records the copy) and **Discard**. Decided drafts show their
delivery state (queued / sent / held for review / blocked: reason). The card
also holds the account's auto-send opt-in toggle, disabled with an explanation
when the deployment does not allow it, and the "When a prospect wrote twice,
still send an approved answer to the later reply" toggle
(`answer_earlier_replies`, on by default).

## Tenancy

- `reply_drafts.user_id` is resolved from the lead (strategy → product →
  user) at creation; every read and decision filters on it; 404 for others.
- `reply_copilot_settings` is keyed by `user_id`.
- The prompt only ever contains the owner's own library entries
  (`guardrails.approved_claims(session, owner, product)`), the owner's own
  booking page and the lead's own strategy research.
- Tests: another account cannot list, read, approve, copy or discard a draft,
  nor see it in metrics.

## Compliance review (CAN-SPAM / GDPR / CASL / PDPA — zero tolerance)

- **Opt-outs.** An opt-out reply never gets a draft (routing class, intent
  label *and* a deterministic phrase check). A suppressed or excluded contact
  never gets a draft, cannot have one approved (409), and a queued send is
  cancelled at send time (`cancelled_suppressed` / `cancelled_excluded`) —
  including an opt-out that arrives after approval.
- **CAN-SPAM / CASL identification + unsubscribe.** Reply emails go through
  `build_compliant_email`: physical-address footer, unsubscribe link, region
  notice (GDPR/UK/CASL), RFC 8058 one-click headers; with F03 on, a send
  without both headers never transmits.
- **Consent / send windows / caps.** Unchanged chokepoint rules apply
  (`compliance_rules.resolve`, consent-required regions, send window, daily cap,
  mailbox health, ramp). A reply may therefore wait for the prospect's send
  window — deliberately: answering inside the rules beats answering fast.
- **Truthfulness (FTC / CAN-SPAM deceptive content).** Claims about the
  prospect must be backed by stored data; claims about us must be backed by an
  approved claim (F05); otherwise approval is blocked. The draft may not
  contain any link but the booking link.
- **Human in the loop.** Default is draft-only. Auto-send is off at two levels,
  narrow (meeting-time requests on email), and logged (`auto_sent`, outcome
  `source`, compliance audit row).
- **GDPR minimisation / erasure.** Drafts quote the person; they are in the
  account export and erased with the lead.
- **Prompt injection.** Reply text is delimited and labelled untrusted; the
  output is checked for foreign URLs, placeholders, banned/forbidden phrases
  and unsupported claims, and a person approves it by default.

**Legal/safety guards added** (for the orchestrator's sealing decision): the
opt-out no-draft guard (on the prospect's new text); the contact guard
(lead status, suppression, exclusions) at draft and approval; the send-time
cancel rules and stop attribution in `send_message_impl`; atomic decisions;
the link/placeholder guard; approval blocked on unsupported claims /
forbidden phrases; auto-send conditions. Tests: `tests/test_reply_copilot.py`
(`TestOptOutGuard`, `TestContactGuard`, `TestSendPath`, `TestAutoSend`) and
`tests/test_reply_copilot_review_fixes.py`.

## AI cost and budget impact

- One draft call per eligible reply (`max_tokens` 700; prompt ≈ 2–4k tokens:
  reply ≤ 4,000 chars, playbook ≤ 6,000 chars, ≤ 20 approved claims). At the
  default price settings ($3 / $15 per Mtok) ≈ **$0.02 per draft**, at most
  2× on a repair attempt.
- `claim_verification.enforce` makes one claim-extraction call when
  `claim_model_extraction_enabled` (default on): at generation, at approval
  (metered `reply_copilot`) and at send (metered `outreach`, as every send).
- Purpose `reply_copilot` is **new** — no reply-related purpose existed on this
  branch (purposes in use: pipeline, verification, lead_scoring, outreach,
  meeting_prep, call_analysis, quickstart). It is added to
  `usage_meter.ACCOUNT_CAPPED_PURPOSES` (one-word additive change), so drafts
  stop cleanly at the plan's daily AI cap: the reply simply gets no draft. It
  does **not** count against the strategy's pipeline budget (that budget is
  "what it costs to run the research").
- Feature 2's call per reply is unchanged, so with the flag on an eligible
  reply costs one extra call.

## Test plan (offline: SQLite, FakeClaude, fake channel, no network)

`tests/test_reply_copilot.py`:
- flag off → no draft, no extra model call, identical task result; API 404;
- eligibility: positive / objection / question drafted; not_interested,
  out-of-office, bounce, automated, unmatched, WhatsApp skipped; idempotent;
  supersede older pending drafts;
- review fixes (`tests/test_reply_copilot_review_fixes.py`): later status
  changes / archive / newer reply cancel a queued answer; approval refuses
  moved-on leads; stop attribution; stale second approve / copy / discard and
  auto-send racing a person → 409 and one message; bare-domain links and
  `{x}`/`<x>` placeholders; quoted Gmail (quoted and unquoted, wrapped
  header) and Outlook (separator and "Original Message") replies with our
  footer get a draft, "stop by" gets a draft, opt-outs above the quote block;
  blank subject fallback; inbox marker after blocked / held / deferred / sent
  sends, on both send paths;
- **opt-out guard**: "stop", "unsubscribe me", "please remove me" with a
  model that mislabels them → no draft, no model call;
- **contact guard**: suppressed / excluded lead → no draft; suppressed after
  drafting → approve 409;
- prompt: reply delimited as untrusted under the notice; playbook, approved
  claims, booking link (tagged), forbidden phrases present; usage row with
  purpose `reply_copilot`;
- output checks: foreign URL / forbidden phrase → repaired once, then no draft;
  unsupported claim stripped and flagged;
- API: list / get / approve / edit / discard / copy; tenancy (another account
  404 on every route, not in metrics); 409 on decided drafts and LinkedIn
  approve; 422 forbidden phrase and unsupported claim in an edit;
- edit tracking: unedited vs edited → `edited`; metrics median and share;
- **send path**: approved draft → `send_message_impl` → sent once with footer,
  one-click headers, reply thread, outcome `source=reply_copilot`, sequence not
  advanced; suppressed after approval → `cancelled_suppressed`; stopped for a
  non-reply reason → cancelled; flag off after approval → cancelled;
  forbidden phrase added to the library after approval → blocked at send;
- **auto-send**: only with deployment switch AND account opt-in AND
  meeting-time intent AND email; LinkedIn never; missing booking link or
  claim changes → stays pending; settings PUT refused while not allowed.
- migration matches the models (`tests/test_reply_copilot_migration.py`;
  `answer_earlier_replies` is proven by `tests/test_annual_billing_migration.py`).
- **`answer_earlier_replies`** (`tests/test_reply_copilot_strictness.py`):
  default on (no row, row created for auto-send) answers the second reply;
  strict cancels it (different class and same class), still answers the reply
  that caused the stop, ignores an earlier out-of-office reply; switching back
  restores the default; strict auto-send does not queue; API round trip
  without the deployment switch, empty PUT 422, refused auto-send does not
  half-apply, per-account, flag off 404.
- `frontend/src/tests/replyCopilot.test.ts` — labels, edit detection,
  delivery state, metric formatting.

## Success metric (measured, never promised)

From `GET /reply-copilot/metrics`:
- **median reply-to-approved-response time** — `decided_at − reply_received_at`
  over drafts a person approved or copied (auto-sends reported separately, not
  mixed in);
- **share of drafts sent unedited** — of human-approved drafts whose message
  reached `sent`, the share with `edited = false` (null when none).

Neither number is measured yet — see Unverified.

## Unverified

- Draft quality against real replies and the real model (only FakeClaude has
  produced drafts). The repair/reject rules are tested; tone is not.
- Gmail threading: the reply is sent with `threadId` = the stored thread ref;
  the Gmail adapter itself marks `threadId` "TODO: verify against current Gmail
  API docs". Since 2026-10-11 (gaps-sweep-2 #6, migration 0086) the answer
  also carries `In-Reply-To` / `References` built from the reply's own
  Message-ID and References (RFC 5322 3.6.4, `app/services/email_threading.py`),
  so other clients can thread it. Replies stored before 0086 have no stored
  Message-ID and their answers still thread by `threadId` only. Not yet
  verified against live Gmail / Outlook / Apple Mail.
- The success metric — no pilot data.
- The cost estimate (prices are admin estimates; token counts are estimated).
- Race on the LinkedIn webhook: fixed 2026-10-11 (gaps-sweep-2 #7) -- the
  reply job is now published after routing has set the classification (same
  for WhatsApp); verified offline only.
- Whether the send-review queue's `prior_objection` trigger (if an admin turns
  it on) holding objection answers a second time is the desired UX.
- With F01 Bounce Shield on, an answer to a lead whose address has no fresh
  "verified valid" verdict is blocked like any email, even though the
  prospect just wrote from it. Correct by the shield's rules; whether a reply
  should count as proof of deliverability is a product decision, not made here.
- No `scripts/gate/e2e_journeys.py` exists on this branch, so no journey was
  added; J5 (reply -> inbox) is the natural place for "-> draft -> approve ->
  sent with headers" once it lands.
- A reply answer counts as a `sent` message in campaign send totals (like the
  re-engagement touch); it is distinguishable by `messages.origin` and the
  outcome's `source`.

## Help-center article to write

**"Reply Copilot: answer replies with a checked draft"**
- Off by default; an admin turns on `reply_copilot_enabled` (`app/services/system_settings.py:294`).
- Drafts are made only for interested, question and objection replies, on email and LinkedIn (`app/services/reply_copilot.py:89`).
- Opt-out replies never get a draft, whatever the classifier said; suppressed and excluded contacts never get one either (`app/services/reply_copilot.py:259`, `app/services/reply_copilot.py:367`).
- Drafts use the campaign's objection research, your approved claims and your booking link, and may contain no other link except your own domain (`app/services/reply_copilot.py:490`, `app/services/reply_copilot.py:566`).
- An edit with a claim Vectorae cannot back, or a forbidden phrase, cannot be approved (`app/services/reply_copilot.py:860`; API returns 422, `app/api/reply_copilot.py:74`).
- Approve sends through the normal send path with the unsubscribe footer and one-click headers, so it can wait for the prospect's send window or the daily cap (`app/workers/outreach_tasks.py:487`, `app/workers/outreach_tasks.py:754`).
- LinkedIn drafts are copy-only; Vectorae never sends them (`app/services/reply_copilot.py:1014`).
- A queued answer is cancelled if the lead books a meeting, is disqualified or closed, or writes again before it goes out (`app/services/reply_copilot.py`, `send_time_hold`).
- When a prospect wrote twice, an approved answer to the later reply is still sent by default; turn off "When a prospect wrote twice, still send an approved answer to the later reply" to send only answers to the reply that stopped the sequence (`app/services/reply_copilot.py`, `stop_explained_by`; setting `answer_earlier_replies`).
- Auto-send covers only "send me a time" email replies and needs an admin switch plus your own opt-in, both off by default (`app/services/reply_copilot.py:806`, `app/services/system_settings.py:299`).
