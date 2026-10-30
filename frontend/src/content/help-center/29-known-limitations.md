---
id: known-limitations
title: Known limitations
audience: customer
last_verified: 2026-10-28
owner: help-center
sources:
  - app/services/score_decay.py
  - app/workers/learning_tasks.py
  - app/services/playbook_service.py
  - alembic/versions/0093_playbook_scores_owner.py
  - render.yaml
  - FEATURES.md
  - docs/program/STATUS_PAGE.md
  - docs/program/BASELINE.md
  - docs/PRODUCTION_READINESS.md
  - docs/features/ai-support-chat.md
  - docs/features/agency-mode.md
  - docs/README_M7_COMPLETE.md
  - app/api/leads.py
  - app/services/account_erasure.py
  - app/api/auth.py
  - app/api/billing.py
  - app/services/billing.py
  - app/integrations/stripe_billing.py
  - app/integrations/gmail.py
  - app/integrations/whatsapp.py
  - app/integrations/apollo.py
  - app/integrations/hunter.py
  - app/integrations/calendly.py
  - app/integrations/unipile_linkedin.py
  - app/integrations/zoom.py
  - app/integrations/recall.py
  - app/integrations/voice_providers.py
  - app/services/voice_twin.py
  - app/integrations/hubspot.py
  - app/core/plans.py
  - app/api/strategies_advanced.py
  - app/db/models.py
  - app/config.py
  - app/api/crm.py
  - app/api/support.py
  - app/api/admin.py
  - app/services/admin_service.py
  - app/workers/outreach_tasks.py
  - frontend/src/lib/api/billing.ts
  - frontend/src/components/billing/PlanCard.tsx
  - frontend/src/lib/api/clients.ts
  - frontend/capacitor.config.ts
  - docs/program/STAR_PROMPT.md
---

# Known limitations

This is an honest list of what Vectorae does not do, does not do yet, or has not yet proven against the real outside services it connects to. It is kept with the rest of the help center and reflects the product on the date at the top. If something here matters for your decision, ask support before relying on it.

## What can't I do with my account and data yet?

- **Undo an account deletion.** **Profile › Delete account** erases the account at once; there is no grace period and no way back. Download your data first if you want a copy.
- **Have Vectorae revoke its access inside Google, HubSpot, Salesforce or Slack when you delete your account.** The connections and their tokens are erased from Vectorae, but the access you granted is not revoked on the provider's side; remove it there yourself. (LinkedIn accounts are disconnected at Unipile.)
- **Have your Stripe customer record deleted when you delete your account.** Stripe keeps it under its own policy.
- **Use two-factor sign-in.** It is not available.
- **See or end individual sessions.** There is no list of signed-in devices. Changing or resetting your password signs out everywhere and revokes your API keys.
- **Erase a prospect from your CRM.** Erasing a lead erases what Vectorae holds about the person, but data already synced to HubSpot or Salesforce stays there.

## What are the billing limitations?

- The web app has no screen for disputing a pay-per-meeting charge. Disputes exist in the Vectorae API only; otherwise contact support. (Cancelling a plan is in **Profile → Plan**.)
- A paid annual plan cannot be changed to another plan online before its year ends, because there is no partial refund yet. Contact support.
- The payment connection to Stripe is still marked in the code as waiting for live keys, so it has not been proven against the live payment service. A production deployment without Stripe set up cannot change plans.
- The analytics history length per plan limits only the day-by-day chart on the Analytics page; totals and the A/B variant table are not limited (see the plans article).
- The daily AI limit amounts per plan may still change.

## Which integrations have not been verified against the live services?

Vectorae's automated tests run against simulated versions of outside services. Gmail, WhatsApp Business, Apollo, Hunter, Calendly, LinkedIn (through Unipile) and Zoom were checked against each provider's public documentation on 2026-10-27, and the differences found were fixed, but none of them has been checked against the live service from these tests. HubSpot, Recall.ai, Vapi and ElevenLabs were checked on 2026-10-28 from search excerpts of their documentation (their documentation sites could not be opened from the checking session), and the differences found were fixed. Some Recall.ai, Vapi and ElevenLabs fields and calls are still marked in the code as not confirmed (for Recall.ai this includes the request that sends the note-taker into a meeting), and none of these four has been checked against the live service.

In particular:

- **Google Meet and Zoom** meeting creation has not been verified against the live services. Creating a meeting with your own custom link is the only verified path. If Meet or Zoom fails, the meeting is still created, without the video link.
- **Recall.ai** (the bot that records and transcribes a call) has been built and tested with simulations only and has never been called live.
- **Zoom meetings** are all hosted by one Zoom user, which your administrator sets as the host user under Admin > Integrations > Zoom. Until it is set, Vectorae does not create Zoom links.
- **AI calls in your own voice** (ElevenLabs voice cloning, AI answering of your calls and Take over through Vapi) have been built and tested with simulations only. Some of the settings that make the assistant sound natural are switched off until they are checked against the live service.
- **AI calls through ElevenLabs** need the ElevenLabs agent to allow Vectorae to set the opening line, the script and, for your own voice, the voice (the agent's **Security** tab; all are off by default). Vectorae checks this before every ElevenLabs call and places no call until they are on. The check reads the agent's settings as ElevenLabs documents them; it has not been run against the live service, and the setting that carries your own voice into an ElevenLabs call is itself not yet confirmed against ElevenLabs' documentation.

## What are the sending limitations?

- **One Gmail mailbox per account.** You cannot connect several mailboxes to one account.
- **One WhatsApp number per deployment.** Every account and every agency client on a deployment sends WhatsApp from the same business number.
- **No cold WhatsApp without consent.** A cold WhatsApp message needs an approved Meta template and a recorded opt-in. There is no free-form cold path.
- **Workspace compliance rules apply at the moment of sending.** Scheduling still uses the standard send window, and a message a rule forbids is rescheduled when it comes due. Rule caps apply to Gmail and WhatsApp; LinkedIn and phone keep their own limits. The campaign page, the CRM campaigns dashboard and the email-verification (Bounce Shield) report all show the lowered bounce threshold.
- **Region detection** depends on the country or time zone found for a lead. Leads with neither are treated as "unknown".
- **Re-engagement** after a finished sequence is email only, is off until an owner or manager turns it on for a campaign, and is capped.
- **Assigning a lead to a teammate** does not change who sends: outreach still goes from the workspace owner's accounts. Assignees are not notified.
- **No single "stop this contact" button**, and no account-wide or platform-wide pause switch for customers. Pause campaigns one by one.

## What does Vectorae deliberately not do?

- **It does not reply to people automatically.** Replies are classified and routed. Approving an AI reply draft saves it as a note for a person to send; it does not send it.
- **It does not collect consent to record meetings.** Sending a recording bot records everyone on the call. Some US states, and GDPR for EU and UK participants, require every party's consent. The meeting room reminds the host to announce it; nothing enforces that.
- **It does not publish benchmark promises.** Reply, booking and deliverability results depend on your offer, list and sender. In-app benchmarks show only this deployment's accounts, and nothing is shown for a group with fewer than 10 accounts (never fewer than 5, whatever the setting).

## What are the meeting and booking limitations?

- **Booking links** of the form `/book/<name>` need a rule on the web host. Where it is missing, links use the form `/book/?slug=<name>` instead.
- Google accounts connected before calendar access was added must reconnect to create Meet links.

## What are the support, status and mobile limitations?

- **Support tickets** are stored and reviewed by the team in an admin queue. Nothing alerts the team automatically when a ticket arrives, and the app does not show you your tickets or replies to them.
- **Support chat** is limited to 30 messages per person per day on Vectorae's hosted service (a deployment setting; the built-in default is 20). Tickets are not limited.
- **Status page:** the status page at www.calendarharvest.com/status shows the current state and posted incidents only. It has no uptime history and no email or text alerts, and it checks from your own browser, so a problem with your connection can look like an outage.
- **Tutorials** on the Learn page appear only after the team publishes them, so the page can be empty.
- **Mobile:** there is an Android app but no iPhone app. Parts of the app's push-notification setup are marked as not yet verified against the current push service.

## What else should I know about reporting and testing?

- **Learning is shared within an account, not per client.** The playbook scores that guide new strategies are kept per account, so other accounts' outcomes never feed yours. In agency mode, different clients of the same agency account feed the same scores, even though each client's reports are kept separate.
- **Benchmarks in small groups** can reflect one account's rounded rate, and industry comes from the strategy's ICP. A strategy aimed at several industries is grouped as "multiple".
- **Database testing.** Key safety behaviours (separating accounts, GDPR lead deletion, suppression at send, bounce pause, STOP handling, sending exactly once) are proven by the automated test suite on a lightweight test database. The tests that run them on the production database type (PostgreSQL) were not working when this article was last verified, so those behaviours are not yet proven on PostgreSQL.

## Related
- [Privacy, data retention and deletion](21-privacy-data-retention-deletion.md)
- [Plans, limits and billing](22-plans-limits-billing.md)
- [CRM and integrations](16-crm-and-integrations.md)
- [Meetings, prep room and transcripts](15-meetings-prep-room-transcripts.md)
- [WhatsApp outreach](10-whatsapp-outreach.md)
- [Pausing and stopping outreach](25-pause-stop-kill-switch.md)
- [Contact support and status](30-contact-support-and-status.md)
