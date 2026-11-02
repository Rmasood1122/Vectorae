# LinkedIn, user-permission-based (L0 + L3)

Program: `evals/vectorae/` (Phase 5B). Founder decision 2026-11-02: delete the
Unipile and RapidAPI integrations; LinkedIn becomes **"Vectorae drafts, you
send on LinkedIn."** This file describes what is built.

## Principle
Vectorae never acts on LinkedIn by itself. It either:
- (a) calls an official LinkedIn API the user authorized, for an action the user approved (L1/L2, **not built**); or
- (b) prepares a task the user performs on LinkedIn themselves (L0).

Nothing is scraped. No browser automation, no cookie, no extension.

## L3: your own connections export (Growth and up)
- **Where:** `POST /linkedin-network/import` (`app/api/linkedin_network.py`).
- **Upload:** the user uploads LinkedIn's `Connections.csv`, downloaded from their own account.
- **Attestation first:**
  - The upload is refused (422, nothing read or stored) unless it carries the current consent-text version from `GET /linkedin-network/status` → `consent.version`, plus a lawful basis (`legitimate_interests` or `consent`).
  - The attestation is stored in `linkedin_consents`: scope, purpose, text version, basis, time, and an HMAC of the IP, never the IP itself.
  - A new text version requires re-consent.
- **Stored data:** names, company, title, profile link and connection date. Emails and phones in the file are discarded at parse time.
- **No email sourcing:** imported people never become email leads.
- **Suppression:** suppressed people are not recommended, and no draft is made for them (checked before any model call).
- **Deletion:** deleting the network withdraws the attestation.
- **Tests:** `tests/test_linkedin_l3_attestation.py`, `tests/test_linkedin_network.py`.

## L0: assisted tasks
- **Service and routes:** `app/services/linkedin_tasks.py`; routes `/linkedin-tasks/*`.
- **Task contents:**
  - text Vectorae drafted, already claim-checked by the caller;
  - the prospect's public profile link, `https://www.linkedin.com/in/<slug>/` only.
- **Flow:**
  1. The user approves ONE task. There is no bulk approve. The approval expires after 24 hours.
  2. "Open profile on LinkedIn" re-checks suppression, business hours and today's cap, then copies the text and opens the plain profile link.
  3. The user sends it on LinkedIn and marks the task done, replied or skipped.
- **Caps:**
  - Per user per local day (account timezone): 20 connection notes, and 50 messages plus comments.
  - Admin settings can lower these, never raise them.
  - They count on "open".
- **Suppression:**
  - Checked at create, approve and open.
  - `consent.suppress_everywhere` (unsubscribe, "stop" reply, GDPR) cancels open tasks for the person in every account.
- **Approval scope:** LinkedIn approval is per task and separate from the email daily approval.
- **Audit:** each state change writes a `security_audit` event: `linkedin_task.created/approved/opened/completed/cancelled/expired`. It holds no message text.
- **Sources:**
  - Today: messages to your own connections (`POST /linkedin-tasks/from-network-message`).
  - Sequence LinkedIn steps become tasks when Unipile is removed (gate PR `claude/gate-linkedin-unipile-removal`).
- **Tests:** `tests/test_linkedin_tasks.py`, `frontend/src/tests/linkedinTasks.test.ts`.

## Not built (stop gates)
- **L1** Sign In with LinkedIn (OIDC: `openid profile email`).
- **L2** Share on LinkedIn (`w_member_social`).

Both need a LinkedIn developer app that Rehan creates. **L4** (messaging, invitations, Sales Navigator) needs LinkedIn partner approval and is not built.
