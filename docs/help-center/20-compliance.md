---
id: compliance
title: Compliance (CAN-SPAM, GDPR, CASL, PDPA)
audience: customer
last_verified: 2026-10-12
owner: help-center
sources:
  - app/services/sequence_engine.py
  - app/api/unsubscribe.py
  - app/workers/lead_tasks.py
  - app/workers/outreach_tasks.py
  - app/services/compliance_region.py
  - app/services/compliance_audit.py
  - app/services/compliance_rules.py
  - app/services/consent.py
  - app/api/consent.py
  - app/api/leads.py
  - app/config.py
  - app/core/production_guard.py
  - app/services/adversarial_review.py
  - app/services/phone_calls.py
  - app/services/voice_twin.py
  - app/api/voice_twin.py
  - frontend/src/components/settings/VoiceTwinCard.tsx
  - docs/features/voice-twin.md
  - app/services/system_settings.py
  - frontend/src/components/leads/ConsentPanel.tsx
  - frontend/src/components/settings/DeliverabilityCard.tsx
  - frontend/src/app/(app)/settings/page.tsx
  - docs/features/consent-layer.md
  - docs/features/compliance-rules.md
  - docs/features/trust-deliverability.md
  - docs/features/revenue-analytics.md
---

# Compliance (CAN-SPAM, GDPR, CASL, PDPA)

Vectorae builds several compliance safeguards into how it sends: an unsubscribe route in every email, a suppression list checked right before every send, rules that depend on where the recipient is, and a record of what was checked. This article describes what the software does. It is not legal advice, and it does not decide what the law requires for your business. You remain responsible for your outreach.

## What does every outreach email include?

Every email Vectorae sends includes:

- a footer with the **sender identity** (your business name and postal address, set by whoever runs your deployment) and an unsubscribe link: "Don't want these emails? Unsubscribe instantly";
- `List-Unsubscribe` headers that support one-click unsubscribe in mail apps that offer it.

A production deployment refuses to start if the sender identity is missing or still the placeholder address, or if the public address used to build unsubscribe links is not set.

Depending on the recipient's region, an extra notice is added above the footer:

- **EU and UK:** a statement that the email is sent because their professional role matches your offer (legitimate interest), and that they can object at any time by replying "stop" or using the link.
- **Canada:** "Sent by [your sender identity]", plus how to unsubscribe by link or by replying "stop".

## What happens when someone unsubscribes?

Clicking the unsubscribe link takes effect **immediately**; no login is needed. The person sees "You're unsubscribed." Vectorae then:

- adds them to the suppression list on **every** channel at once: email, phone, LinkedIn, and WhatsApp (their WhatsApp opt-in is revoked);
- stops every sequence they are in;
- records the instruction in the consent ledger.

A reply asking to stop ("remove me", "stop emailing me", "take me off your list") is treated exactly like clicking the link. A WhatsApp STOP also suppresses email. You can do the same yourself on a lead's page: in the **Can we contact them?** panel, choose **They asked us to stop**, then **Confirm — suppress everywhere**.

## When is the suppression list checked?

A suppressed person is never contacted again from any campaign on your deployment. Vectorae checks the list:

- when leads are sourced, so suppressed people are not imported;
- at the moment each message is sent, for email address, phone number and LinkedIn profile together;
- again inside the LinkedIn and WhatsApp senders, right before the provider is called.

If a match is found at send time, the message is cancelled and the person's sequence is stopped. Viewing and adding entries to the full suppression list in **Settings** is limited to administrators.

## How does Vectorae decide which region's rules apply?

It uses the country on the lead's enrichment record. If there is none, it uses the lead's time zone. If neither is known, the region is "unknown" and a baseline of sender identification and a working unsubscribe applies.

| Region | Regime | What Vectorae records as the basis |
|---|---|---|
| US | CAN-SPAM | opt-out regime |
| Canada | CASL | implied consent **assumed** – Vectorae cannot verify it |
| EU/EEA | GDPR | legitimate interest (B2B); right to object stated |
| UK | UK GDPR / PECR | legitimate interest (B2B); corporate subscriber |
| Singapore | PDPA | business contact information; Do-Not-Call registry **not** checked |
| Australia / New Zealand | Spam Act 2003 / UEMA 2007 | inferred consent |

Vectorae does not check any national Do-Not-Call registry and cannot verify implied consent. The records say so.

## Are opens tracked?

Outreach emails can carry a tracking pixel to count opens, but **never for EU, EEA or UK leads**, because a tracking pixel needs consent under ePrivacy and PECR rules. Your administrator can turn open tracking off entirely. Opens are approximate; replies are the reliable signal.

## What other rules apply to sending?

- **Send window:** by default messages go out between 09:00 and 17:00 in the lead's time zone, weekdays only.
- **WhatsApp:** cold messages need an approved Meta template and a recorded opt-in, in every country. See [WhatsApp outreach](10-whatsapp-outreach.md).
- **AI phone calls** are off by default (an administrator setting). When they are on, by default a lead is only called if their phone consent has been recorded; otherwise the call step is skipped and the sequence continues. AI-voice calls are regulated in the US under the TCPA. Every AI call, and every voicemail it leaves, starts by saying it is an AI assistant, and every call says it is recorded.
- **Pre-launch review:** a sequence is checked before launch for compliance problems, such as briefs that remove the unsubscribe footer, misleading "Re:" subject lines, or a placeholder postal address. Blocking findings stop the launch unless an owner or manager overrides them with a written reason.

Your administrator can set rules per workspace, region and channel (Admin › Compliance Rules): a different send window anywhere between 07:00 and 20:00, weekend sending on or off, lower daily caps, a lower bounce-pause threshold, or requiring recorded consent. Daily caps and the bounce-pause threshold can only be lowered, never raised past the deployment defaults. The send window can be set anywhere between 07:00 and 20:00, which can be wider than the default 09:00–17:00. If a matching rule is invalid, Vectorae uses the strictest combination of the defaults and the valid rules. If the rules cannot be read at all, it falls back to the deployment defaults. Because Vectorae records no consent for email or LinkedIn, a "consent required" rule on those channels stops those sends.

## Can the AI call people in my own voice?

Yes, if your administrator has turned on AI calling and voice cloning (both are off by default). In **Settings**, **Voice**, under **Your voice on calls**, you sign a written consent that the recordings are of your own voice, then upload recordings (or record them in the browser). They are sent straight to ElevenLabs, which creates a Professional Voice Clone; Vectorae does not keep the recordings. ElevenLabs then asks you to read a short text aloud to prove the voice is yours, and trains the voice, which can take hours. When it is ready you can hear the opening line and turn on **Use my voice for AI calls**.

Your voice sounds like you, but the assistant never pretends to be you or a human. Every call opens with "Hi, this is <your name>'s AI assistant, using <your name>'s voice. This call is recorded." If someone asks whether it is a real person, it says it is an AI assistant and offers to put them through to you. Notes or call briefs that tell it to hide that it is an AI are refused when you save them. After every call, Vectorae checks what the assistant said; if it ever claimed to be human, all your AI calls are paused until you read the call and resume them. You can revoke your consent at any time: the voice stops being used at once and is deleted at ElevenLabs. Deleting your account deletes it too, and an unused clone is deleted after 3 years.

## Can my AI assistant answer my calls, and can I take over a live call?

Yes. **Answer my calls with my AI assistant** (off by default; the account owner or a manager can turn it on) lets the assistant answer people who call your own AI-calling number. It says it is an AI assistant and that the call is recorded, and it can book a meeting on your booking page, take a message, or put the caller through to your phone. If a caller asks not to be called again, their number is added to the suppression list.

When any AI call is live you get a notification and a banner in the app. Press **Take over** to have the call put through to the phone number you saved under **Settings**, **Voice**. You cannot listen in to a live call from the app.

## What is the consent ledger and the compliance audit log?

- The **compliance audit log** has one entry per send, and per send refused on compliance grounds: region, regime, decision, legal basis and the checks made. You can see it under **Settings**, **Deliverability**, in the **Compliance audit log** table.
- The **consent ledger** is an append-only record of consent granted (for example a WhatsApp opt-in), withdrawn, suppressed and erased, with the source and who acted. It answers "when did they tell us to stop, and what did we do?". Entries survive even if the lead is later deleted.

## How do I handle a request to erase someone's data?

Delete the lead. Vectorae erases the lead's personal data, puts their email, phone and LinkedIn profile on the suppression list so they are never contacted or re-imported, and records the erasure in the consent ledger first. See [Privacy, data retention and deletion](21-privacy-data-retention-deletion.md) for details.

## Related
- [Email sending through Gmail](09-email-sending-gmail.md)
- [WhatsApp outreach](10-whatsapp-outreach.md)
- [LinkedIn outreach and your LinkedIn network](11-linkedin.md)
- [Deliverability and mailbox health](19-deliverability-and-mailbox-health.md)
- [Privacy, data retention and deletion](21-privacy-data-retention-deletion.md)
- [Pause, stop and the kill switch](25-pause-stop-kill-switch.md)
