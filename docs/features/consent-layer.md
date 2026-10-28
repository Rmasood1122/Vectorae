# Compliance & Consent Layer (Part 1, Feature 9)

**Migration** `0061_consent_ledger` · **Service** `app/services/consent.py` ·
**API** `app/api/consent.py` ·
**UI** `frontend/src/lib/consent.ts`, `components/leads/ConsentPanel.tsx`

> None of this is legal advice. It is a record of what was checked and what was
> done, which is a different and more useful thing.

## What was already there, and is unchanged

- `compliance_region.py` — which regime a recipient falls under, from their
  country and failing that their timezone.
- `compliance_audit.py` + `compliance_audit_log` — one row **per send** saying
  what was checked and on what legal basis.
- `sequence_engine.compliance_footer` — sender identity and unsubscribe link in
  every email.
- `lead_tasks.is_suppressed` — email, phone **and** LinkedIn checked on every
  send.

## What this feature adds, and why each piece exists

### 1. One suppression act that covers every channel — including WhatsApp

`unsubscribe_lead` suppressed email, phone and LinkedIn but **left
`whatsapp_opted_in` alone**. Someone who unsubscribed by email could still be
messaged on WhatsApp, because the flag the adapter checks was never revoked.

That is now `consent.suppress_everywhere()`: **"stop contacting me" is a single
instruction about a person, not four per-channel preferences.** It is called
from the email unsubscribe, from a WhatsApp STOP (so a STOP now suppresses email
too), and from the API.

It is idempotent — suppressing twice adds no duplicate rows — and it reports
per channel whether it suppressed, found it already suppressed, or had no
identifier to suppress. *Those are three different facts.*

### 2. A consent ledger

`compliance_audit_log` answers "on what basis did you **send** this?". Nothing
answered the question a regulator — and a prospect writing an angry second email
— actually asks: **"when did they tell you to stop, and what did you do about
it?"**

`consent_events` is append-only and records `granted`, `withdrawn`,
`suppressed` and `erased`, each with the region, the regime, the source of the
instruction, the identifier it covers and who acted.

**Both halves are recorded.** A WhatsApp opt-in writes a `granted` event. A
ledger that only holds withdrawals cannot show that the contact was lawful to
begin with, which is the half a dispute usually turns on.

**`lead_id` is `ON DELETE SET NULL`, never CASCADE.** The point of the erasure
event is that it *survives the erasure*. `record_erasure()` is called **before**
the delete, with the identifier and owner denormalized onto the row, so the
proof that the request was honoured outlives the data it concerned. Every other
foreign key on the table is `SET NULL` for the same reason.

The `identifier` column is indexed on purpose: when someone writes "I asked you
to stop three months ago", the address is the only thing you have.

### 3. Requirements per region and channel, stated once

What each regime demands was previously spread between a footer builder, a
region module and a comment. `requirements_for(region, channel)` puts it in one
place that the send path, the UI and the tests all read, so they cannot drift.

| Region | Regime | Notes the layer records |
|--------|--------|------------------------|
| US | CAN-SPAM | Opt-out regime: no prior consent, but the opt-out must work |
| CA | CASL | B2B relies on **implied** consent, which cannot be verified here — recorded as assumed |
| EU / UK | GDPR / ePrivacy, UK GDPR / PECR | Legitimate interest; right to object stated; **tracking pixels not used** |
| SG | PDPA | Phone and SMS also need the national DNC check, which Vectorae does **not** perform |
| AU / NZ | Spam Act 2003 / UEMA 2007 | Inferred consent + identification + unsubscribe |
| *unknown* | strictest baseline | Identification and a working opt-out |

Two channel rules override region:

- **WhatsApp requires prior opt-in everywhere** — not because of a statute, but
  because Meta's Business Policy says so, and losing the number is a harder
  problem than a regulator's letter.
- **A US phone needs prior express consent** for an AI voice (FCC, February
  2024: an AI-generated voice is an "artificial voice" under the TCPA).

## API

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/compliance/requirements?region=` | What each regime demands, per channel |
| `GET` | `/compliance/consent?lead_id=&identifier=&kind=` | The ledger, newest first, searchable by address |
| `GET` | `/leads/{id}/consent` | Per channel: contactable, and if not, **why not** |
| `POST` | `/leads/{id}/consent/withdraw` | Every channel at once; `source` recorded verbatim |

`/compliance/audit` (FG9) is untouched.

`source` is recorded verbatim because "they clicked unsubscribe" and "a person
asked on the phone" are different facts and a regulator may care which.

## UI

`ConsentPanel` on the lead page leads with the channels that are **open**
(the question a person has is "how can I reach them", not "how can't I"), and
gives the **reason** for each closed one — because "no address on file" and
"they asked us to stop" look identical in a product that only shows a red dot,
and only one of them is a problem you can fix.

## Tests

`tests/test_consent.py` (31) — the WhatsApp gap being closed, one event per
channel plus one for the instruction, idempotency, a WhatsApp STOP suppressing
email, an opt-in recorded as a grant, the erasure record surviving a real
`DELETE /leads/{id}`, per-channel reasons, and the API including the verbatim
source and the cross-account 404. `tests/test_consent_migration.py` (7) —
including that `lead_id` is SET NULL. `frontend/src/tests/consent.test.ts` (18).
