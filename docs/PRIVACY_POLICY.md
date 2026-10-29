# Vectorae Privacy Policy

Effective date: October 29, 2026

<!-- The published text is frontend/src/app/privacy/page.tsx; this file holds the
same policy word for word, followed by a reviewer section that is not published.
tests/test_privacy_docs_truth.py checks both files against the code. -->

## 1. Who is responsible

Vectorae (calendarharvest.com) is operated by Rehan Masood, an individual based in New York, USA. Co-founder Ahmad Shahid helps run the service. No company has been registered for Vectorae yet. This policy covers visits to calendarharvest.com, use of the Vectorae dashboard and mobile app, and outreach run for our clients, including data about the prospects they contact. For prospect data, our client decides who is contacted and why, and we process that data on the client's instructions. Contact: rehanmasood@calendarharvest.com.

## 2. Data we collect

- **Account data:** name, email, company, and details about your business and ideal clients.
- **Technical and security data:** IP address, browser and device type, sign-in and security logs, country and VPN checks, AI usage, tutorial progress, and a push-notification token in the mobile app.
- **Prospect data:** public LinkedIn profile information and public posts, professional emails, job titles and company details, collected on our clients' instructions.
- **Payment data:** invoices and payment records. We never see or store full card numbers.
- **Communication data:** emails sent and received through the service, and your correspondence with us.

We do not use Google Analytics or any other analytics or advertising trackers to record which pages you view.

## 3. Google user data (Gmail)

When you connect Gmail, Vectorae asks Google for these permissions:

- `https://www.googleapis.com/auth/gmail.send`: sends the emails you approved, from your Gmail address.
- `https://www.googleapis.com/auth/gmail.readonly`: reads new messages in the email threads Vectorae started for you, and new emails from prospects Vectorae has emailed for you, to find replies, bounces and out-of-office messages.
- `https://www.googleapis.com/auth/gmail.compose`: saves the follow-up email after a meeting as a draft in your Gmail, for you to review and send.
- `https://www.googleapis.com/auth/calendar.events`: creates the calendar event and Google Meet link for a meeting booked with a prospect.

We use them only to:

- send the emails you approved, from your own Gmail address;
- detect replies, bounces and out-of-office messages to your Vectorae emails, so a reply stops the sequence;
- classify those replies (interested, question, objection, not interested, unsubscribe, out of office, bounce, automated) and show them to you.

We do not read, store or use other email in your mailbox for any other purpose. Reply text from Gmail is sent to one AI provider, Anthropic, and only to classify the reply, suggest a reply draft for you to review, prepare your brief before a booked meeting, note when a prospect asks to be contacted again later, and let you practise a sales call against the objections the prospect raised.

- We do not sell Google user data or use it for advertising.
- We do not use Google user data to develop, improve or train generalized AI or machine-learning models.
- No person reads your Gmail data unless you ask us to for support, it is needed for security or to comply with the law, or the data has been made anonymous.
- You can disconnect Vectorae at any time at myaccount.google.com/permissions. You can also ask us to delete the Gmail data we hold (see section 8).

Vectorae's use and transfer to any other app of information received from Google APIs will adhere to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.

## 4. How we use data

We use data to provide the service, report results to you, keep the service secure, and improve how it performs. We do not sell your data or your prospects' data. We do not use your data to train AI models without your consent.

## 5. Service providers

We share data only with providers that help run Vectorae, and only for that purpose.

- **Always used:** Neon (database), Upstash, Render, Vercel (hosting), Anthropic (AI), Resend (transactional email).
- **Used when a feature needs it:** Google (Gmail API, Calendar, Meet, Postmaster Tools, Firebase Cloud Messaging), Unipile, Meta, Apollo, Hunter, NewsAPI, RapidAPI, Mailreach, OpenAI, Vapi, ElevenLabs, Twilio, Recall.ai, Zoom, Calendly, HubSpot, Salesforce, Slack, Stripe, and IP-check services.

Our primary database is managed by Neon in the EU and US. Transfers of UK or EU data rely on safeguards such as Standard Contractual Clauses.

## 6. Security

Data is stored on encrypted servers and sent over encrypted connections. Passwords are stored only as one-way hashes (PBKDF2-HMAC-SHA256).

## 7. How long we keep data

- Account and prospect data stays until you delete it.
- Support chat is deleted 30 days after its last message.
- Billing records are kept for 7 years.
- The do-not-contact list and consent records are kept without a time limit, so opted-out people stay opted out.
- Cached provider responses are kept for up to 30 days (search results for 1 day).

## 8. Your rights

You can access, correct, export or delete your data, and withdraw consent. You can erase a single prospect or delete your whole account yourself from your profile page. People in the UK or EEA have GDPR rights; California residents have CCPA rights. A prospect who asks to stop hearing from our clients is suppressed across all channels, and can ask us to delete their data. Email rehanmasood@calendarharvest.com. We may verify your identity and will answer within 30 days.

## 9. Cookies

We use only essential cookies, for sign-in, security and the dashboard. We use no advertising cookies.

## 10. Children

Vectorae is for businesses. It is not meant for anyone under 18, and we do not knowingly collect their data.

## 11. Changes

We will email active clients before a material change takes effect. The effective date at the top shows the current version.

## 12. Contact

Rehan Masood, operator of Vectorae · rehanmasood@calendarharvest.com

---

## Reviewer detail (not published)

What the code does behind the policy's short statements, checked 2026-10-29.
tests/test_privacy_docs_truth.py reads this section.

**Passwords (§6).** PBKDF2-HMAC-SHA256 with 600,000 iterations and a random salt per user (`app/services/auth.py`). Gmail and other connection tokens are stored encrypted.

**Communication data (§2).** The system stores the subject and body of every message it sends for you, and the replies prospects send back, including the reply text.

**Export and deletion (§8).** Export: open **Profile** and confirm your password in **Download your data** (one JSON file). Account deletion: open **Profile**, then **Delete account**, confirm your password and type DELETE. Any paid plan is cancelled first; if that cannot be confirmed, nothing is deleted.

**Retention (§7).** Support chat conversations are deleted by a nightly job 30 days after their last message. Billing records (amounts, dates, payment-provider references; no name or email in our copy) are kept 7 years. Stripe keeps its own customer record of you under its own privacy policy; Vectorae does not ask Stripe to delete it. The consent ledger keeps opt-ins (with a short note on how consent was given), opt-outs, suppressions and erasures without a time limit, with the do-not-contact list. Contact-finding and verification responses are cached for up to 30 days (search results for 1 day) and expire on their own.

**Gmail inbox (§3).** The reply poll lists inbox message ids and Gmail thread ids, and downloads only (a) messages in a thread that holds an email Vectorae sent for the account's owner and (b) new emails from prospects Vectorae has emailed for that owner, found by a Gmail `from:` search so other senders' mail is never listed (`app/integrations/gmail.py`, `fetch_replies`; `tests/test_gmail_inbox_scope.py`). The "scan my connected Gmail" contacts feature, which read the From/To/Cc lines of other mail, is locked off in every deployment (`app/services/email_network_gmail.py`, `scan_locked`); no setting turns it back on.

**Providers named in §5, by integration.** Gmail API, Calendar / Meet and Postmaster Tools (Google); Firebase Cloud Messaging; Unipile (LinkedIn); RapidAPI (LinkedIn posts fallback: a prospect's public LinkedIn profile address); WhatsApp Business Cloud API (Meta); Apollo; Hunter; NewsAPI (a prospect's company name); Mailreach (your sending mailbox address); OpenAI (strategy synthesis only, when an administrator enables it; never Gmail data); Vapi; ElevenLabs; Twilio; Recall.ai; Zoom; Calendly; HubSpot; Salesforce; Slack; Stripe. "IP-check services" are ipapi.co and ipinfo.io (country at the identity step) and IPQualityScore and proxycheck.io (VPN check at sign-up and sign-in).

**Not in §5, and why.** AegisAudit, PostIQ and SIGNALFORGE: separately run tools an administrator can connect. Today Vectorae only checks their health and, for PostIQ and SIGNALFORGE, the key; no draft or personal data is sent (`app/api/tool_integrations.py`; the tasks in `app/workers/tool_integration_tasks.py` are not called). If that changes, they must be added to §5.

**Earlier contact.** The privacy contact used to be ahmadshahid@calendarharvest.com; it is now rehanmasood@calendarharvest.com.
