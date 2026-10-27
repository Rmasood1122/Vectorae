---
id: whatsapp-outreach
title: WhatsApp outreach
audience: customer
last_verified: 2026-10-18
owner: help-center
sources:
  - frontend/src/lib/conversation.ts
  - frontend/src/components/strategy/AutopilotCard.tsx
  - app/services/autopilot.py
  - frontend/src/content/product-facts.json
  - docs/program/ai-visibility/REPORT.md
  - app/integrations/whatsapp.py
  - app/api/whatsapp_templates.py
  - app/services/whatsapp_templates.py
  - app/api/whatsapp_optin.py
  - app/services/whatsapp_optin.py
  - app/services/whatsapp_window.py
  - app/api/webhooks_whatsapp.py
  - app/api/sequences.py
  - app/services/sequence_engine.py
  - app/services/message_personalization.py
  - app/services/consent.py
  - app/workers/outreach_tasks.py
  - app/core/plans.py
  - app/config.py
  - app/db/models.py
  - app/api/ui_support.py
  - docs/program/BASELINE.md
  - docs/program/DECISIONS.md
  - docs/VERIFICATION_REPORT.md
  - docs/program/help-center-discoveries.md
  - docs/program/fixes/product-defects.md
  - frontend/src/app/(app)/campaigns/page.tsx
  - frontend/src/components/campaigns/TemplateEditor.tsx
  - frontend/src/lib/whatsappTemplate.ts
  - frontend/src/app/(app)/settings/page.tsx
  - frontend/src/app/(app)/pipeline/page.tsx
  - frontend/src/components/strategy/OutreachPanel.tsx
  - app/api/auth.py
---

# WhatsApp outreach

Victorae can send outreach on WhatsApp through Meta's WhatsApp Business Cloud API. WhatsApp has stricter rules than email, and Victorae enforces them at the moment of sending: a contact needs a recorded opt-in, a first message must use a Meta-approved template, and free-form messages can only go out inside the 24-hour window the contact opens by messaging you. This article explains each rule, how STOP works, and whose number your messages come from.

## What do I need to use WhatsApp?

- **A plan that includes WhatsApp.** The Free plan is email only. Starter, Growth, Pay per meeting, Scale, Pro and Enterprise include WhatsApp.
- **A WhatsApp Business number configured on your deployment** by your administrator, in the server settings, not in the app. Sending needs `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID`. Submitting and syncing templates also needs `WHATSAPP_BUSINESS_ACCOUNT_ID`. Replies (which open the 24-hour window), delivery reports and STOP messages arrive through Meta's webhook, which needs `WHATSAPP_VERIFY_TOKEN` and `WHATSAPP_APP_SECRET`; without the app secret, every delivery is rejected.
- In **Settings** → **Integrations**, the **WhatsApp Business** card shows the phone number ID when sending is set up, or "Not configured — set WHATSAPP_* env vars on the server". **Test** checks the connection.
- **Approved templates** and **recorded opt-ins**, described below.

## Whose number are my WhatsApp messages sent from?

There is **one WhatsApp Business number per Victorae deployment**, and every user in the deployment sends from it. A deployment is meant for one organization, but sign-up is open to any email address by default; unless your administrator closes it or limits it to your email domains, other accounts share the number too. As a result:

- **Agencies:** every client's prospects see the same number; unlike email, there is no per-client number.
- **Templates are shared, so an admin manages them:** only an administrator can create, edit, generate, submit and sync templates. Everyone signed in sees the approved ones; drafts, submitted and rejected templates are visible to administrators only.
- **The daily cap is shared.** By default the number sends at most 10 messages on the day of its first WhatsApp send, 10 more each day after that, up to 100 per day, across all users. Messages over the cap wait for the next day's send window; they are not dropped.

## How do I create and approve a template?

Go to **Campaigns**, choose a strategy, and open the **Templates** tab. The buttons below appear only for an administrator; anyone else sees the approved templates and a note that an admin creates and submits them.

- **+ New draft** opens an editor. Enter a name (lowercase letters, digits and underscores, for example `intro_offer`) and a body. Templates created here use language `en_US` and category "marketing". For each placeholder you type, such as `{{1}}`, a "What goes in {{1}}?" field appears; describe what fills it (for example "first name").
- **Generate with AI** (only for a strategy that is your own) asks the AI for 2 or 3 candidates from the strategy's research (any that fail the checks are dropped, so you may get only one), each with its placeholders described. Every valid candidate is saved as a draft straight away. Choose one and press **Edit & save this draft**; you can change the placeholders as long as each one is described and they still follow the template rules below. Drafts are never submitted automatically.
- **Submit** sends a draft or rejected template to Meta for review, which "takes minutes to days". Victorae sends the name, language, category and body. Meta requires an example value for each placeholder, and Victorae sends each placeholder's description as its example, so write descriptions a reviewer can read as a sample (for example "Sara" or "first name").
- **Sync status** fetches Meta's latest decision. A rejected template shows Meta's reason, and an administrator can edit and resubmit it.

Victorae checks a template before saving and again before submitting: the body cannot be empty, must be at most 1,024 characters by default, and its placeholders must run `{{1}}`, `{{2}}`, … in order with no gaps or repeats. A **marketing** template must also tell the contact how to opt out: an instruction with the word STOP in capitals, such as "Reply STOP to opt out.", "Text STOP anytime" or "STOP to unsubscribe". The word alone does not count ("NON-STOP deals" or a link ending in /STOP is refused), and only English is recognised, so other languages need an English opt-out line too. Utility templates are exempt. **Generate with AI** adds "Reply STOP to opt out." to a marketing draft that lacks it; in the editor you add it yourself. Editing an approved template creates a new draft version.

Only **approved** templates are sent, and approval and the opt-out instruction are checked again at the moment of sending. A template that fails either (for example one approved before the opt-out rule) is not sent; the message is not sent and stops with the reason recorded (the timeline shows "WhatsApp not sent", and the campaign's WhatsApp card counts it under "need template").

## What happens if Meta pauses an approved template?

If Meta later pauses, disables, archives or deletes an approved template, Victorae marks it rejected with Meta's status in the reason (for example "Meta status PAUSED: this template cannot be sent"), and it is not sent. When Meta reinstates or unarchives it, it is approved again. Victorae learns this from Meta's webhook or when an administrator presses **Sync status**.

## Why won't a template with placeholders save?

In the editor, **Save draft** stays disabled until:

- every placeholder has a description in its "What goes in {{n}}?" field;
- the body is not empty, and a new template has a name;
- a marketing body contains an opt-out instruction. If it does not, the editor says "A marketing message must tell the recipient how to opt out…".

If saving still fails, a message gives the server's reason, for example "variable placeholders must be sequential starting at {{1}}" (you used `{{2}}` without `{{1}}`, or skipped a number), "duplicate variable placeholders (each {{n}} once)", or a name that is not lowercase letters, digits and underscores.

An earlier version of the editor could not save any template with placeholders, because it never asked for their descriptions. That is fixed: it now asks for one per placeholder, and edits start from the saved descriptions.

## How do I add WhatsApp steps to a sequence?

The launch form on the strategy page creates email sequences only. WhatsApp steps come from Autopilot (where you pick the template on the strategy page) or through the Victorae API. A WhatsApp step is one of two kinds:

- **Template step:** names any template that has not been rejected (draft, submitted or approved; only administrators can see drafts and submitted ones) and says how each variable is filled. The template must be approved by the time the step is due; if it is not, the message is not sent then. If an earlier message in the sequence went out and the lead has not replied, Victorae retries that step automatically once the previous step's follow-up delay (72 hours by default) has passed since that message went out, and sends it if the template has been approved by then. A WhatsApp template that is the first step is not retried. Each variable comes from the lead's record (full name, first name, title, company or email) or from a short brief the AI fills from the lead's data without inventing facts. A message is never sent with an empty slot.
- **Free-form text step:** only sends inside an open 24-hour window, and can never be the first step of a sequence.

A sequence whose first step is on WhatsApp enrolls verified leads that have a phone number, including leads with no email address. The number must be stored in international format, starting with + and the country code (for example +14155550123; 00 in place of + also works). Meta adds your business number's own country code to a number without one, which can reach the wrong person, so Victorae does not send to such a number. The message is not sent: the lead's timeline shows "WhatsApp not sent", and the campaign's WhatsApp card counts it under "need template" even though the template is not the problem. The reason recorded on the message says the number "is not in international format". Correct the lead's phone number to include the country code. If a later step is on a channel the lead has no address for, that message is cancelled and the sequence stops with the reason "no address for the step's channel".

## Do contacts need to opt in?

Yes. Victorae never sends a WhatsApp message to a contact without a recorded opt-in. A WhatsApp step for such a contact is **skipped**, and the sequence carries on with its other steps; if every step was skipped, the sequence is flagged for your attention.

An opt-in can be recorded when:

- **the contact messages your number first.** If their status was unknown, this is recorded as an opt-in, with the message ID as evidence;
- **you record it through the API**, one contact at a time. Evidence of where, when and how they agreed is recommended, and required for a manual import;
- **you import a CSV through the API.** Every row needs evidence; rows without it are rejected with a reason.

Victorae also contains a hosted opt-in page, but the app gives you no way to get a contact's link to it.

Opt-ins and opt-outs are kept as an add-only history. The current status shows as **WhatsApp opt-in** when you open a lead on the **Pipeline** page. A number on the suppression list cannot be opted in again through Victorae, and the suppression cannot be lifted from the app.

## What is the 24-hour window?

When a contact sends you a WhatsApp message, a 24-hour window opens, and each new message from them restarts it. Template messages you send do not open it. Inside the window, free-form text steps can send; outside it, only templates can.

So you can contact someone who has never written to you, but only with an approved template, and only once their opt-in is recorded. A free-form message can only go out while their window is open.

The window is checked at the moment of sending: a free-form message due after the window closed is not sent and does not wait; it is counted under "need template". On **Campaigns**, the **Overview** tab's **WhatsApp** card shows how many windows are open and how many messages need a template. The 24-hour length follows Meta's policy as understood when the feature was built; it has not been re-checked since.

## What happens when someone replies STOP?

Victorae treats these messages as an opt-out, immediately and without waiting for any AI step: "stop", "unsubscribe", "opt out", "optout", "cancel", "remove me", "stop messaging me", "no more messages", and any message that starts with "stop" or "unsubscribe" (capital letters do not matter). An opt-out:

- records the contact as opted out;
- suppresses their phone number, email address and LinkedIn profile;
- stops all of their sequences on every channel.

If the AI later reads another reply as a request to stop, it has the same effect. A STOP from a number Victorae cannot match to one lead still suppresses that number. An opted-out contact who messages you again is not opted back in automatically.

## What happens with other replies?

Replies are classified like email replies. A real reply stops all of the contact's sequences, including email steps; an out-of-office reply pauses them. Victorae never replies automatically. A reply is matched to a lead by phone number: to the lead messaged most recently on WhatsApp if any was, otherwise to the only lead with that number. If several leads share the number and none was messaged, the reply is not attached to a lead. A message Meta reports as undelivered is handled like an email bounce.

## Has WhatsApp been verified against Meta's live service?

The founders confirmed WhatsApp steps as working end to end in production on 2026-10-13. The automated tests still run against simulated responses. On 2026-10-27 every request, webhook and status detail the code relies on was checked against Meta's public documentation (not against a live account), and the differences found were fixed. If something fails, contact support.

## Related
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [Personalization and claim verification](07-personalization-and-claim-verification.md)
- [Replies and the unified inbox](13-replies-and-unified-inbox.md)
- [Workspaces and agency mode](24-workspaces-and-agency-mode.md)
- [Compliance](20-compliance.md)
- [Plans, limits and billing](22-plans-limits-billing.md)
