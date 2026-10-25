---
id: linkedin
title: LinkedIn outreach and your LinkedIn network
audience: customer
last_verified: 2026-10-12
owner: help-center
sources:
  - app/services/plan_holds.py
  - app/core/plans.py
  - app/api/sequences.py
  - app/integrations/linkedin_channel.py
  - app/integrations/unipile_linkedin.py
  - app/integrations/linkedin_posts.py
  - app/services/linkedin_outreach.py
  - app/services/linkedin_limits.py
  - app/services/system_settings.py
  - app/workers/outreach_tasks.py
  - app/api/linkedin.py
  - app/api/linkedin_network.py
  - app/services/linkedin_network.py
  - app/services/network_messages.py
  - frontend/src/components/settings/LinkedInAccountsCard.tsx
  - frontend/src/app/(app)/settings/page.tsx
  - frontend/src/app/(app)/network/page.tsx
  - frontend/src/components/shell/nav.ts
  - docs/features/linkedin-channel.md
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

Victorae has two separate LinkedIn features. LinkedIn steps in a sequence send connection requests, messages and InMail to prospects through a LinkedIn account you connect. The Network page lets you upload your own LinkedIn connections, see which of them may fit your products, and write messages to them. This article explains what each one does and does not do.

## Which plans include LinkedIn?

There are two different plan checks:

- **LinkedIn steps in sequences** are part of the plan's channel list. The Growth, Scale, Pay per meeting, Pro and Enterprise plans include the LinkedIn channel. Free and Starter do not. If your plan does not include it, creating a sequence with a LinkedIn step is refused with an upgrade message. The check runs again when a sequence is launched, so a sequence built on a higher plan cannot be launched after a downgrade. For sequences enrolled from 12 October 2026 on, LinkedIn steps scheduled before a downgrade are held when they come due, not sent, and show the reason on the lead and on the Campaigns page; they are released automatically within about 30 minutes once your plan includes LinkedIn again.
- **The Network page** (your private LinkedIn network) is included with Growth, Scale, Pro and Enterprise. It is **not** included with Pay per meeting, even though that plan includes the LinkedIn channel. On other plans the Network page still opens and shows "Upgrade in Settings".

## Has LinkedIn sending been verified against the live service?

No.

Victorae talks to LinkedIn through a third-party service called Unipile. The code that calls Unipile is marked in several places as not yet checked against Unipile's current documentation. That includes looking up profiles, sending connection requests, starting chats, sending messages, the account-connection page, reading InMail credits, and the incoming webhook for replies and accepted connection requests. Until that check is done, treat LinkedIn sending as not yet verified against the live service.

Your Victorae administrator must also set up Unipile (Admin > Integrations > Unipile) before any LinkedIn sending can work. If it is not set up, LinkedIn steps fail with the reason "LinkedIn is not configured".

## How do I connect my LinkedIn account?

Go to **Settings**, open the **Integrations** tab, and find the **LinkedIn** card. Click **Connect an account**. You are sent to Unipile's hosted sign-in page and sign in to LinkedIn there. Victorae does not see your LinkedIn password; it stores only an account id. There is also an option to "Link an existing Unipile account id instead" if you already have one.

You can connect more than one LinkedIn account. Each connected account shows a **Premium** badge if it has LinkedIn Premium, a **Paused** badge if it is inactive, and two usage bars for today: **Connection requests** and **Messages + InMail**. You can refresh an account's details or remove it from the same card.

## What does a LinkedIn step actually send?

LinkedIn steps are sent automatically by Victorae from your connected account, in the same way as email and WhatsApp steps: within your send window, your daily limits, and the send-review queue if you use it. Each step has an action. The default, **auto**, decides at send time based on the prospect:

- Already connected to you: a **message**.
- A connection request is still pending: the step is **skipped** and the sequence continues.
- The prospect has a Premium profile: an **InMail**. If none of your accounts can send InMail (no Premium, or no InMail credits left), it falls back to a **connection request**.
- Anyone else: a **connection request**.

You can also set a step to **connect**, **message** or **inmail** explicitly. A "connect" step to someone already connected becomes a message. A "message" step to someone who is not connected is skipped.

Before each send, Victorae re-reads the prospect's profile (connected, pending, Premium) from LinkedIn through Unipile. When someone accepts your connection request, the incoming webhook marks them as connected.

The text is written by AI from the step brief and your messaging playbook, and it passes through the same claim check as other channels. Length limits in the code: a connection note is cut to at most 300 characters, a message to at most 1,500 characters. The writer is told to keep connection notes to 280 characters with no pitch, messages under 120 words, and InMail bodies under 150 words with a short subject.

## Will Victorae warn me that LinkedIn can restrict my account?

Yes. LinkedIn's User Agreement does not allow automated tools to send messages or connection requests, and LinkedIn may restrict or suspend LinkedIn accounts that use automation, including Victorae's automatic LinkedIn sending through your connected account. Because of that, automatic LinkedIn sending needs a separate approval every day. On the daily approval page the LinkedIn box is never ticked for you; ticking it shows this warning and asks "Do you want to proceed with automatic LinkedIn sending today?", and you must confirm before LinkedIn can be approved. If you approve only email, LinkedIn steps and bulk-sent network messages keep waiting and are not sent. See [The send-review queue and approvals](12-send-review-queue.md#do-i-approve-automatic-sending-every-day) for how the daily approval works.

## How many LinkedIn actions are sent per day?

Each connected LinkedIn account has two daily ceilings, counted per UTC day:

- **Connection requests:** default 20 per account per day.
- **Messages (including InMail):** default 50 per account per day. An InMail counts against the message ceiling.

Both numbers are set by your Victorae administrator in System Settings, so your deployment may use different values. If a send fails, the slot it reserved is given back.

When you have several accounts, a prospect's first touch goes from the active account with the fewest sends of that kind today. **Every later touch to that prospect comes from the same account**, so the conversation stays with one person. If that account is disconnected, the step fails with a message asking you to reconnect it.

When every eligible account has reached its ceiling, the message is not dropped. It is moved to the next day's send window.

## How do people opt out on LinkedIn?

LinkedIn messages do not carry an unsubscribe footer, because a link in a connection note can get the account restricted. Instead, a reply on LinkedIn goes through the same reply classifier and stop rules as email and WhatsApp. A reply classified as an unsubscribe request suppresses that person's LinkedIn profile, email address and phone number.

An unsubscribe on any channel also suppresses the LinkedIn profile. Victorae checks the suppression list when a prospect is enrolled, when a message is sent, and once more immediately before the LinkedIn send call. A suppressed prospect is never sent a LinkedIn message.

## What is the Network page?

**Network** in the main menu opens "Your LinkedIn network". LinkedIn offers no way for Victorae to read your connections, so you upload them yourself:

1. On LinkedIn: Settings & Privacy, then Data privacy, then Get a copy of your data.
2. Choose **Connections** and request the archive.
3. Upload the `Connections.csv` file from it on the Network page.

The file can be up to 5 MB and up to 20,000 rows. Only name, job title, company, profile link and connection date are stored. Email addresses and phone numbers in the file are discarded when it is read.

Victorae then scores each connection against each of your products with fixed rules. This scoring does not use AI and does not send your connections to any outside service. Connections scoring 30 or more out of 100 appear as matches, grouped into segments, with the reasons for each match. Stored connections are re-scored weekly. New connections only appear when you upload a fresh export; the page reminds you when your last upload is more than 7 days old.

Your uploaded connections are private to your account. They are never shown to any other account.

## Can Victorae message my own connections?

Yes, on eligible plans, and only after you approve the text. For a matched connection:

- **Draft it in my voice** asks AI to write a short message. A draft is never sent.
- Edit it if you want (up to 1,500 characters), then click **Save**. Only saved text can be sent.
- Then choose how to send it:
  - **Copy and open LinkedIn** copies the text and opens their profile; you send it yourself, then click **I sent it** to record it.
  - **Send now via LinkedIn** sends it through your connected LinkedIn account after you confirm.
  - **Bulk send** sends up to 25 saved messages per batch, one at a time, about 90 seconds apart. Because the batch then sends on its own, it counts as automatic LinkedIn sending: queued messages wait until today's LinkedIn sending is approved on the daily approval page, and go out after that. Sending one message with **Send** is your own action and is not held.

Every Victorae send to a connection checks again at that moment that your plan still includes the feature, the person is not on the do-not-contact list, your daily LinkedIn message limit has not been reached, and the person is still a 1st-degree connection. If they are no longer connected, nothing is sent and the message is marked failed.

## How do I delete my uploaded connections?

On the Network page, click **Delete my uploaded connections** and confirm. This deletes everything you uploaded. Deleting is not tied to your plan: after a downgrade the page shows the upgrade notice, and the delete button is still there for as long as you have connections stored. It can also be done through the Victorae API (`DELETE /linkedin-network`).

## Can I find warm leads in my own email contacts?

Yes, on the same plans as the LinkedIn network (Growth, Scale, Pro and Enterprise), once the email network is switched on for your Victorae server (it is off by default). The **Network** page has two tabs: **LinkedIn** and **Email contacts**. On **Email contacts**, first tick the box confirming that these are your own business contacts, then bring your contacts in one of these ways:

- a Google Contacts CSV export, an Outlook contacts CSV export, or a vCard (`.vcf`) file from Apple Contacts or a phone (up to 20 MB; at most 10 addresses are read from one cell);
- a mailbox export such as a Google Takeout `.mbox` file (up to 300 MB and 50,000 messages; for a message sent to more than 50 people only the first 50 count);
- **Scan my connected Gmail**, which reads the last 12 months of your connected Gmail account, up to 5,000 messages, in the background. Only the account owner can start it, not a workspace manager. If your Gmail connection is too old to allow this, the page asks you to reconnect Gmail in Settings. A new scan can be started an hour after the last one started, whether it finished, failed or needed a reconnect.

One upload keeps at most 50,000 people and an account holds at most 100,000 email contacts. When a limit is reached, the summary says what was left out. Everyone in the workspace who can open the Network page sees the contacts list and matches, as with the LinkedIn network.

Victorae then scores each contact against each of your products with the same fixed rules as the LinkedIn network (job title and company against your target customer), plus how strong the email relationship is: whether you have written to each other, how recently, and how often. People who have never emailed back and forth with you are ranked below people who have. Each match shows why, for example "You emailed each other 14 times, last in Aug 2026". Scores are refreshed weekly. This scoring does not use AI.

## What does Victorae read from my mailbox, and who is left out?

A mailbox file is read through on Victorae's server to find the From, To, Cc and Date lines of each email, plus the markers that show an email was a newsletter or sent automatically; everything else is discarded as it is read. A Gmail scan asks Google for those lines only. Subjects, the text of your emails, attachments and signatures are never stored. For each person Victorae keeps their name, email address, company, job title (only if your contacts file has one), how many emails went each way, the first and last date, and whether the exchange was two-way. A company that is not in a contacts file is guessed from the email domain and marked as a guess.

These people are left out and never stored, and the import summary counts them by reason:

- personal addresses such as Gmail, Yahoo, Outlook.com, Hotmail, iCloud, AOL and Proton, including their country versions (the feature is for business contacts only);
- role inboxes such as info@, support@, sales@, hello@, admin@ and billing@, and variants such as customer-service@ or sales-team@;
- newsletters, no-reply and other automated senders;
- you and your colleagues (your own addresses and your company's domain and its subdomains; you can add other addresses of yours on the page);
- anyone on the suppression list (unsubscribed, bounced or erased on request) or on your exclusion lists.

Someone who is suppressed later is hidden from your matches at once. An unsubscribe or an erasure request removes them from your email contacts straight away; other suppressions (for example a bounce) are removed at the weekly refresh.

Your email contacts are private to your account and never shown to any other account.

## How do I contact people from my email contacts?

Victorae does not send one-off emails from this page. There are two ways to reach a match, and both go through the normal campaign send checks:

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
