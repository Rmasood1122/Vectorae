---
id: privacy-data-retention-deletion
title: Privacy, data retention and deletion
audience: customer
last_verified: 2026-10-30
owner: help-center
sources:
  - app/db/models.py
  - app/api/account_data.py
  - app/services/account_export.py
  - app/services/account_erasure.py
  - app/api/unsubscribe.py
  - app/workers/outreach_tasks.py
  - app/services/billing.py
  - frontend/src/lib/api/profile.ts
  - docs/features/gdpr-erasure.md
  - app/api/leads.py
  - app/services/consent.py
  - app/api/strategies.py
  - app/api/linkedin_network.py
  - app/workers/support_tasks.py
  - app/workers/celery_app.py
  - app/api/support.py
  - app/config.py
  - app/services/rbac.py
  - app/services/auth.py
  - frontend/src/app/(app)/profile/page.tsx
  - frontend/src/lib/profile.ts
  - app/api/admin.py
  - app/services/admin_service.py
  - app/services/linkedin_network.py
  - frontend/src/app/(app)/pipeline/page.tsx
  - frontend/src/components/strategy/DeleteStrategyDialog.tsx
  - frontend/src/app/(app)/network/page.tsx
  - frontend/src/app/(app)/settings/page.tsx
  - frontend/src/app/privacy/page.tsx
  - docs/program/BASELINE.md
---

# Privacy, data retention and deletion

This article explains what Vectorae stores about you and about the prospects you contact, how long it keeps it, and what you can download or delete yourself. It describes how the product behaves today, including what is not built yet. It is not legal advice.

## What does Vectorae store about me?

For your own account, Vectorae stores:

- your email address, your name if you add one, and a one-way hash of your password (never the password itself);
- your plan and billing status;
- what you enter during identity onboarding: the country you are in, whether you sign up as an individual or a company, the company name and country, and your verified phone number;
- the IP address you signed up from and, once it has been looked up, the country that address appears to be in. Your deployment's administrators can see both, and they are normally notified when an account is created;
- your theme and writing-voice settings;
- connections to Gmail, LinkedIn and other services. Their access tokens are stored encrypted.

Vectorae also keeps security and activity records about your account, such as sign-ins, refused attempts, data exports and deleted strategies.

## What does Vectorae store about my prospects?

For each prospect (lead) found for a strategy, Vectorae stores what it sourced and enriched: name, job title, company, email address, phone number, LinkedIn profile, and research notes such as recent posts or company news.

It also stores the outreach itself: the subject and body of every message, when it was sent, and the replies prospects send back, including the reply text. Bookings, meetings, meeting notes and transcripts are stored too.

For compliance, Vectorae keeps a consent ledger (opt-ins, opt-outs and erasures) and a do-not-contact (suppression) list of email addresses, phone numbers and LinkedIn profiles.

## How long is my data kept?

Apart from support chat history, Vectorae does not delete your data automatically after a set time. Your products, strategies, leads, messages, replies and meetings stay until you delete them, or until you delete your account.

**Support chat history** is the exception. A conversation is deleted 30 days after its last message, by a job that runs every night at 03:20 UTC. The number of days is a deployment setting; 30 is the default. Support tickets are not deleted by this job, even if the conversation they came from is.

The suppression list is kept without a time limit. You cannot remove an entry yourself; only a platform administrator can remove one, so a person who asked not to be contacted stays protected.

After you delete your account, a few records are kept (see "Can I delete my whole account?" below): billing records for 7 years, the consent ledger and the suppression list without a time limit, and a security record that the account was deleted. Nothing deletes the billing records automatically after the 7 years yet.

## How do I download a copy of my data?

1. Open **Profile**.
2. In the **Download your data** card, type your password in **Confirm with your password**.
3. Press **Download my data**.

You get one JSON file, named like `vectorae-account-export-2026-10-10.json`. It contains everything your account owns: profile, products, strategies, leads, messages, meetings, settings and the records listed above.

Things to know:

- Passwords and connection secrets (such as encrypted tokens and API key hashes) are left out on purpose.
- Shared, deployment-wide lists with no owner, such as the suppression list, are not included.
- You must sign in to export. A personal API key cannot download the whole account.
- Every export, and every attempt refused for a wrong password, is recorded in the account's security log.
- If you type the wrong password you see "That password is not right." Too many attempts in a short time show "Too many attempts. Wait a while and try again."
- In a workspace, the export always covers the account of the person who is signed in, not the workspace owner's account.

## How do I erase a prospect's data?

Open the lead from **Pipeline**, press **Delete (GDPR erasure)**, then press **Delete** in the "Permanently delete lead?" confirmation. In a workspace, SDRs and viewers cannot do this; it needs the owner or a manager.

When you confirm, Vectorae:

1. records the erasure in the consent ledger first, so there is proof the request was honoured. That record keeps the email address or phone number it was about;
2. adds the person's email, phone number and LinkedIn profile to the suppression list, so no strategy will contact or source them again;
3. clears the lead's name, job title, email, phone, company, LinkedIn details, research notes, its scores (booking likelihood and F-P-T-A scores) with their explanations, and marks the lead as dropped. An empty record without personal details is kept so the same person is not re-added to that strategy;
4. erases the correspondence: the subject and text of every message written to the person, and the sender address, subject, text and AI draft answer of every reply they sent. Any message to them that was still scheduled, waiting for review, held, or being sent at that moment is cancelled and not sent;
5. erases everything else recorded about them: call transcripts, phone number, recording links and voicemail audio files; booking details (name, email, phone, answers); meeting titles, notes, transcripts, summaries and the person's participant entry; meeting briefs and follow-up drafts; CRM notes, custom field values and deal notes; competitor alerts; the review, attribution and fact-check copies of their messages; their WhatsApp opt-in number (the opt-in is marked revoked); and any planned re-engagement is cancelled;
6. keeps where each field came from (for example "Apollo"), but no longer the value it had;
7. forgets which record in your connected CRM (HubSpot or Salesforce) the lead was linked to.

What the reply and send counts need (when a message was sent, opened or replied, how a reply was classified, call and meeting times and statuses, deal amounts) is kept, so your campaign numbers do not change.

What this does **not** do today:

- Anything already copied into your CRM stays there. Erasing it in the CRM is up to you.
- If the person clicks the unsubscribe link in an email they received earlier, it still works: their address goes on the suppression list again and the click is recorded in the consent ledger.

## Can I delete a strategy or my uploaded LinkedIn network?

Yes.

- **Strategy:** open the strategy and press **Delete**. In the **Delete strategy** window, confirm with your password. This permanently deletes the strategy with its research, leads, sequences, messages and campaign history. A campaign that is still sending must be paused first. The deletion is recorded in the security log, and the tamper-evident activity trail keeps its entries.
- **LinkedIn network:** on the **Network** page, **Delete my uploaded connections** removes every connection you uploaded, after you confirm "Delete all uploaded connections?". The deletion is never limited by plan: the button shows on every plan while you have connections stored, including after a downgrade.
- **Email contacts:** on the **Network** page, **Email contacts** tab, **Delete my email contacts** removes every imported contact, score, saved first email and import record, after you confirm. It is never limited by plan either. Email contacts store names, addresses, companies, titles, counts and dates only, never the subject or text of any email.

## Can I delete my whole account?

Yes. It is immediate and cannot be undone, so download your data first if you want a copy.

1. Open **Profile**.
2. In the **Delete account** card, type your password in **Confirm with your password**.
3. Type `DELETE` (in capital letters) in **Type DELETE to confirm**.
4. Press **Delete my account permanently**.

When you confirm, Vectorae:

1. cancels any paid plan at once, so nothing more is charged. A monthly or annual plan ends immediately; the unused rest of the current month or year is not refunded automatically (contact support about a refund before deleting). Pay-per-meeting meetings that were not yet charged are waived. If the payment provider does not confirm the cancellation, or payments are not set up on the server your account runs on, nothing is deleted and you see a message asking you to try again or contact support;
2. disconnects your LinkedIn accounts at Unipile, so the stored LinkedIn session is dropped there too;
3. cancels every scheduled message and stops every sequence;
4. erases your profile, products, strategies, prospects, messages, replies, meetings, CRM records, settings, connected accounts (Gmail, LinkedIn, CRM and other integrations, with their stored tokens), API keys, every sign-in session on every device, uploaded backgrounds and logos, and voicemail audio;
5. signs you out.

## What is kept after I delete my account?

- **Billing records** (amounts, dates and payment-provider reference numbers) for 7 years, for tax and accounting. Vectorae's copy has no name or email address. Stripe, the payment provider, keeps its own customer record of you (name, email and payment details); Vectorae does not ask Stripe to delete it.
- **The consent ledger and the suppression list**: every recorded opt-in, opt-out, suppression and erasure for your prospects, with the email address or phone number it concerns, so a prospect who opted out or asked to be erased is never contacted again.
- **A security record** that the account was deleted, without your IP address, device or email address. Refused sign-in attempts recorded under your email address are cleared the same way.

Outside Vectorae's database:

- Answers from the contact-finding providers are cached for up to 30 days (search results for 1 day) and expire on their own.
- The hosting provider's request logs record IP addresses and are kept for the period the hosting provider sets.

## What else should I know before deleting my account?

- If you own a workspace, it is deleted and its members lose access to it. Their own accounts stay.
- If you are a member of someone else's workspace, only your own account goes; the workspace and its data stay.
- In a workspace, this always deletes the account of the person who is signed in, never the workspace owner's.
- A personal API key cannot delete an account, and an administrator account cannot be deleted from the app.
- You can sign up again later with the same email address; it starts as a new, empty account.
- Gmail, HubSpot, Salesforce and Slack connections are removed from Vectorae, but Vectorae does not revoke its access inside those services. You can remove it there yourself (for Google: your Google Account's third-party access page). A sign-in link to one of them that was still open when you deleted the account no longer works.
- If you type the wrong password you see "That password is not right."

## Where is the privacy policy?

The public **Privacy** page on the Vectorae website is the policy. It lists the email address for privacy requests. If anything in this article and that page seem to disagree, ask support.

## Related
- [Compliance](20-compliance.md)
- [Account security](23-account-security.md)
- [Pausing and stopping outreach](25-pause-stop-kill-switch.md)
- [CRM and integrations](16-crm-and-integrations.md)
- [Known limitations](29-known-limitations.md)
- [Contact support and status](30-contact-support-and-status.md)
