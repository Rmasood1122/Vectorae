---
id: linkedin
title: LinkedIn outreach and your LinkedIn network
audience: customer
last_verified: 2026-11-02
owner: help-center
sources:
  - app/services/plan_holds.py
  - app/core/plans.py
  - app/api/sequences.py
  - app/integrations/linkedin_channel.py
  - app/services/linkedin_tasks.py
  - app/api/linkedin_tasks.py
  - app/services/linkedin_consent.py
  - app/services/linkedin_profile.py
  - app/services/linkedin_outreach.py
  - app/services/system_settings.py
  - app/workers/outreach_tasks.py
  - app/api/linkedin.py
  - app/api/linkedin_network.py
  - app/services/linkedin_network.py
  - app/services/network_messages.py
  - frontend/src/components/linkedin/LinkedInTasksPanel.tsx
  - frontend/src/app/(app)/network/page.tsx
  - frontend/src/components/shell/nav.ts
  - docs/features/linkedin-assisted.md
  - app/services/daily_send_approval.py
  - docs/features/daily-send-approval.md
  - app/workers/network_tasks.py
  - app/api/email_network.py
  - app/services/email_network.py
  - app/services/email_network_filter.py
  - app/services/email_network_parse.py
  - app/services/email_network_gmail.py
  - app/services/email_network_messages.py
  - frontend/src/components/network/EmailNetworkPanel.tsx
  - docs/features/email-network.md
---
# LinkedIn outreach and your LinkedIn network

**Vectorae drafts; you send on LinkedIn.** Vectorae never sends, connects, comments or reads anything on LinkedIn for you. It has no access to your LinkedIn account and asks for no LinkedIn login. LinkedIn's rules do not allow automation tools to act for you, so every LinkedIn message is one you send yourself.

There are two LinkedIn features. LinkedIn steps in a sequence turn into **LinkedIn tasks**: Vectorae writes the text and you send it. The Network page lets you upload your own LinkedIn connections, see which of them may fit your products, and write messages to them, which also become tasks.

## Which plans include LinkedIn?

There are two different plan checks:

- **LinkedIn steps in sequences** are part of the plan's channel list. The Growth, Scale, Pay per meeting, Pro and Enterprise plans include the LinkedIn channel. Free and Starter do not. If your plan does not include it, creating a sequence with a LinkedIn step is refused with an upgrade message. The check runs again when a sequence is launched, so a sequence built on a higher plan cannot be launched after a downgrade. For sequences enrolled from 12 October 2026 on, LinkedIn steps scheduled before a downgrade are held when they come due and show the reason on the lead and on the Campaigns page; they are released automatically within about 30 minutes once your plan includes LinkedIn again.
- **The Network page** (your private LinkedIn network) is included with Growth, Scale, Pro and Enterprise. It is **not** included with Pay per meeting, even though that plan includes the LinkedIn channel. On other plans the Network page still opens and shows "Upgrade in Settings".

## Do I need to connect my LinkedIn account?

No. There is nothing to connect. A LinkedIn task contains a plain link to the person's public LinkedIn profile and the text to send. You open the profile in your own browser, signed in as yourself, and send the text there.

## What happens when a LinkedIn step comes due?

When a LinkedIn step is due (inside your send window, and after your daily approval if you use it), Vectorae writes the text and adds a task to **Your LinkedIn tasks** on the Network page. Each step has an action. The default, **auto**, decides based on what you have reported on earlier tasks:

- You marked an earlier task with this person as **They replied**: a **message**.
- You sent a connection note and they have not replied yet: the step is **skipped** and the sequence continues.
- The prospect has a Premium profile: a **message** you can send as an InMail if your own account allows it.
- Anyone else: a **connection note**.

A "message" step to someone you have not reported as connected is skipped. A step for a lead without a link to a LinkedIn member profile fails with "the lead has no LinkedIn profile link".

The text is written by AI from the step brief and your messaging playbook, and it passes through the same claim check as other channels. Length limits in the code: a connection note is cut to at most 300 characters, a message to at most 1,500 characters. The writer is told to keep connection notes to 280 characters with no pitch, messages under 120 words, and InMail bodies under 150 words with a short subject.

## How do I work through my LinkedIn tasks?

Each task goes through the same steps, one task at a time:

1. **Approve this one.** You read the text and approve that single task. There is no "approve all". An approval lasts 24 hours; after that the task asks to be approved again. Editing the text withdraws the approval.
2. **Open profile on LinkedIn.** Vectorae copies the text and opens the person's profile. Before it does, it checks again that the person is not on the do-not-contact list, that it is business hours, and that you are within today's limits.
3. Send the text yourself on LinkedIn.
4. Mark the task **I sent it**, **They replied** or **Skip**.

**They replied** stops that person's sequences, the same as a reply on any other channel.

LinkedIn approval is separate from the daily email approval: approving a task approves only that task.

## How many LinkedIn tasks can I open per day?

To protect your LinkedIn account from looking like automation, at most **20 connection notes** and at most **50 messages** can be opened per day, counted in your account's timezone, during business hours. Your Vectorae administrator can lower these limits in System Settings but cannot raise them.

When you already have a full day's worth of tasks waiting, a LinkedIn step that comes due is not dropped. It is moved to the next day's send window.

## How do people opt out on LinkedIn?

LinkedIn messages do not carry an unsubscribe footer, because a link in a connection note looks like spam on LinkedIn. If someone asks you on LinkedIn to stop, mark the task **They replied** and add them to the do-not-contact list.

An unsubscribe on any channel suppresses that person's LinkedIn profile too. Vectorae checks the suppression list when a prospect is enrolled, when a task is created, when it is approved and again when you open it. When someone is suppressed, their open LinkedIn tasks are cancelled at once.

## What is the Network page?

**Network** in the main menu opens "Your LinkedIn network". LinkedIn offers no way for Vectorae to read your connections, so you upload them yourself:

1. On LinkedIn: Settings & Privacy, then Data privacy, then Get a copy of your data.
2. Choose **Connections** and request the archive.
3. On the Network page, read the notice, choose the lawful basis you rely on (usually legitimate interests), tick the confirmation box, and upload the `Connections.csv` file. The file picker stays locked until you have done both, and nothing is pre-selected.

Vectorae records your confirmation (the version of the notice you read, the lawful basis, and the time). If the notice changes, you are asked again on your next upload. Deleting your uploaded connections also withdraws it.

The file can be up to 5 MB and up to 20,000 rows. Only name, job title, company, profile link and connection date are stored. Email addresses and phone numbers in the file are discarded when it is read, and your connections are never added to email campaigns. A connection already stored is skipped, and the import summary says how many duplicates there were.

Vectorae then scores each connection against each of your products with fixed rules. This scoring does not use AI and does not send your connections to any outside service. Connections scoring 30 or more out of 100 appear as matches, grouped into segments, with the reasons for each match. People on the do-not-contact list are never shown. Stored connections are re-scored weekly. New connections only appear when you upload a fresh export; the page reminds you when your last upload is more than 7 days old.

Your uploaded connections are private to your account. They are never shown to any other account.

## Can Vectorae message my own connections?

No. You message them yourself, with Vectorae's help, on eligible plans. For a matched connection:

- **Draft it in my voice** asks AI to write a short message. No draft is made for someone on the do-not-contact list.
- Edit it if you want (up to 1,500 characters), then click **Save**.
- Click **Add to my LinkedIn tasks**. The message then goes through the task steps above: approve, open the profile, send it yourself, mark it.

## How do I delete my uploaded connections?

On the Network page, click **Delete my uploaded connections** and confirm. This deletes everything you uploaded. Deleting is not tied to your plan: after a downgrade the page shows the upgrade notice, and the delete button is still there for as long as you have connections stored. It can also be done through the Vectorae API (`DELETE /linkedin-network`).

## Can I find warm leads in my own email contacts?

Yes, on the same plans as the LinkedIn network (Growth, Scale, Pro and Enterprise), once the email network is switched on for your Vectorae server (it is off by default). The **Network** page has two tabs: **LinkedIn** and **Email contacts**. On **Email contacts**, first tick the box confirming that these are your own business contacts, then bring your contacts in one of these ways:

- a Google Contacts CSV export, an Outlook contacts CSV export, or a vCard (`.vcf`) file from Apple Contacts or a phone (up to 20 MB; at most 10 addresses are read from one cell);
- a mailbox export such as a Google Takeout `.mbox` file (up to 300 MB and 50,000 messages; for a message sent to more than 50 people only the first 50 count);
- **Scan my connected Gmail**, which reads the last 12 months of your connected Gmail account, up to 5,000 messages, in the background. Only the account owner can start it, not a workspace manager. If your Gmail connection is too old to allow this, the page asks you to reconnect Gmail in Settings. A new scan can be started an hour after the last one started, whether it finished, failed or needed a reconnect.

One upload keeps at most 50,000 people and an account holds at most 100,000 email contacts. When a limit is reached, the summary says what was left out. Everyone in the workspace who can open the Network page sees the contacts list and matches, as with the LinkedIn network.

Vectorae then scores each contact against each of your products with the same fixed rules as the LinkedIn network (job title and company against your target customer), plus how strong the email relationship is: whether you have written to each other, how recently, and how often. People who have never emailed back and forth with you are ranked below people who have. Each match shows why, for example "You emailed each other 14 times, last in Aug 2026". Scores are refreshed weekly. This scoring does not use AI.

## What does Vectorae read from my mailbox, and who is left out?

A mailbox file is read through on Vectorae's server to find the From, To, Cc and Date lines of each email, plus the markers that show an email was a newsletter or sent automatically; everything else is discarded as it is read. A Gmail scan asks Google for those lines only. Subjects, the text of your emails, attachments and signatures are never stored. For each person Vectorae keeps their name, email address, company, job title (only if your contacts file has one), how many emails went each way, the first and last date, and whether the exchange was two-way. A company that is not in a contacts file is guessed from the email domain and marked as a guess.

These people are left out and never stored, and the import summary counts them by reason:

- personal addresses such as Gmail, Yahoo, Outlook.com, Hotmail, iCloud, AOL and Proton, including their country versions (the feature is for business contacts only);
- role inboxes such as info@, support@, sales@, hello@, admin@ and billing@, and variants such as customer-service@ or sales-team@;
- newsletters, no-reply and other automated senders;
- you and your colleagues (your own addresses and your company's domain and its subdomains; you can add other addresses of yours on the page);
- anyone on the suppression list (unsubscribed, bounced or erased on request) or on your exclusion lists.

Someone who is suppressed later is hidden from your matches at once. An unsubscribe or an erasure request removes them from your email contacts straight away; other suppressions (for example a bounce) are removed at the weekly refresh.

Your email contacts are private to your account and never shown to any other account.

## How do I contact people from my email contacts?

Vectorae does not send one-off emails from this page. There are two ways to reach a match, and both go through the normal campaign send checks:

- **Add to campaign.** Tick the people and choose one of your campaigns. They become ordinary leads in that campaign, and their email addresses are verified like any other lead. Only verified leads are enrolled, when you launch or enroll the campaign as usual.
- **Write a first email.** **Draft it in my voice** asks AI to write a short first email; edit it and click **Save**. A draft is never sent. Common phrases that claim a meeting, a call or a conversation that your email history does not show (for example "great speaking last week" or "thanks for taking my call" when you have never emailed back and forth) are refused when you save, and checked again on the final words when the email is sent. Then **Use as their first email** in a campaign: the person is added to that campaign, and when you launch it, their first campaign email is your saved text instead of a written one. If that email is rejected in review or fails, you can detach it and write a new one.

Either way, every email goes out through the campaign's normal sending, with the unsubscribe link and your postal address added, after your daily send approval, and only if the person is not suppressed; your exclusion lists are also checked at send time when they are switched on for your server (people on them are always kept out of your email contacts). People from your email contacts get the GDPR "right to object" notice and no open-tracking pixel unless their address shows a country with lighter rules (for example a .us address).

Canadian (.ca) addresses with no two-way email in the last two years cannot be added to a campaign, and are skipped at send time once the last two-way email is more than two years old, because under Canada's anti-spam law implied consent from a business relationship lasts two years. Two limits: only .ca addresses are checked, so Canadians on other domains are not detected, and a two-way email exchange is only a stand-in for a business relationship.

## How do I delete my email contacts?

On the **Email contacts** tab, click **Delete my email contacts** and confirm. This deletes every contact, score, saved first email and import record for your account. To delete one person, click **Delete this contact** next to them. Like the LinkedIn network, deleting is never tied to your plan. People you already added to a campaign stay there as leads, and you remove them like any other lead. If you delete a saved first email that was already attached to a campaign, that person's first campaign email is held in the review queue for you to approve, edit or reject, instead of being written by AI and sent. Erasing a lead on request also removes that person from your email contacts.

## Related
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [The send-review queue](12-send-review-queue.md)
- [Replies and the unified inbox](13-replies-and-unified-inbox.md)
- [Compliance](20-compliance.md)
- [Plans, limits and billing](22-plans-limits-billing.md)
- [Known limitations](29-known-limitations.md)
