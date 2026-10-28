# Privacy Policy — ClientHunter Enterprise (Vectorae)

**Last updated:** October 2026

> ⚠️ **LEGAL NOTE:** This is a functional stub produced to satisfy Play Store submission requirements. It covers the material facts about data collection and processing accurately. **Have a qualified lawyer review it before publishing if you are targeting EU/UK users, handling personal data at scale, or operating in a regulated industry.** The author of this stub is not a lawyer and this document does not constitute legal advice.
>
> **NEEDS FOUNDER/LEGAL REVIEW BEFORE MERGE (2026-10-11).** Sections 1, 2, 6, 7, 8 and 11 were rewritten to match the code (founder decision D1: the code is the truth). The retention periods in section 6 (billing records kept 7 years; consent and suppression records kept without a time limit) are defaults awaiting founder/legal confirmation — see `docs/features/gdpr-erasure.md`, decisions E1–E3. The public website policy is `frontend/src/app/privacy/page.tsx`; keep the two consistent.

---

## 1. Who we are

ClientHunter Enterprise, also sold as Vectorae ("we," "us," "our"), is a software service for B2B client acquisition automation. The service is operated by:

**Rehan Masood**, an individual based in New York (no company has been registered yet)
[Postal address] — TODO (founders): fill in before publishing
ahmadshahid@calendarharvest.com — the privacy contact used on the public website (`frontend/src/app/privacy/page.tsx`). Founders: confirm this is the right address for privacy requests.

---

## 2. What data we collect

We collect the following categories of personal data when you use ClientHunter Enterprise:

### Data you provide directly
- **Account information:** Email address, an optional display name, and your password (stored only as a one-way hash, never the password itself).
- **Identity and risk information:** The country you are in, whether you sign up as an individual or a company, the company name and country, your verified phone number, and the IP address you signed up from with the country it appears to be in (used as a fraud and risk signal).
- **Product or service description:** The product or skill you want to find clients for.
- **Past client details:** Information you voluntarily enter about your previous clients to help the system identify patterns.

### Data the system sources on your behalf
When you run a campaign, the system sources information about prospective clients from third-party data providers (see Section 5). This data includes:
- Business email addresses and phone numbers
- Names and job titles
- Company names, sizes, and industries
- LinkedIn profile URLs, and public posts and company news used to personalise outreach

This data is used solely to run the outreach campaigns you configure. It is not used for any purpose unrelated to your campaign.

### Outreach and replies
The system stores the subject and body of every message it sends for you, and the replies prospects send back, including the reply text. Bookings, meetings, meeting notes and transcripts are stored too.

### Device and usage data
- **Device token:** A Firebase Cloud Messaging token tied to your device, used to send push notifications about your campaigns.
- **Security records:** Sign-ins, refused attempts, data exports and deletions are recorded in your account's security log.
- **Sessions:** For each signed-in session, the IP address and the browser/device description (user agent) your browser sends.
- **Network check:** The country and VPN check made on the IP address you sign up or sign in from.
- **AI usage:** How much AI processing your account uses, so plan limits can be applied. Your progress through the in-app tutorials.
- **Request logs:** The server's request logs record the time, IP address and address requested for each request, for operation and security.
- **No page tracking:** No analytics or tracking tool records which pages you view or how you move through the dashboard. (Corrected 2026-10-11, gaps-sweep-2 #13 -- NEEDS FOUNDER/LEGAL REVIEW, together with the website policy's section 2.)

---

## 3. How we use your data

We use the data we collect to:

- Operate the ClientHunter service on your behalf (research, outreach, meeting booking)
- Send you push notifications about campaign events (replies, meetings booked, strategy ready)
- Improve the product and the accuracy of AI-generated strategies
- Comply with legal obligations

We do not:
- Sell your data to third parties
- Use your data to build profiles for advertising
- Share your data with parties not listed in Section 5 (the complete list of services the code sends data to)

---

## 4. Legal basis for processing (GDPR)

If you are in the European Economic Area, the United Kingdom, or Switzerland, our legal basis for processing your personal data is:

- **Contract performance** (Article 6(1)(b)): processing necessary to provide the service you subscribed to.
- **Legitimate interests** (Article 6(1)(f)): improving the product, fraud prevention, security.
- **Consent** (Article 6(1)(a)): push notifications (you can withdraw by deregistering notifications in your device settings).

---

## 5. Third-party services

We send data to the following services, only as far as each needs it. The list was checked against the code (`app/integrations/`, `app/services/`, `render.yaml`) on 2026-10-11. Each has its own privacy policy (URLs to be verified by the founders before publishing).

**Always in use**

| Service | What it receives |
|---|---|
| **Neon** (PostgreSQL) | Everything stored in the database |
| **Upstash** (Redis) | Job queue payloads, rate-limit counters, cached provider responses |
| **Render** | Hosts the application servers; sees every request, including IP addresses |
| **Vercel** | Hosts the website and web app |
| **Anthropic** | Prospect research, strategy inputs and message drafting prompts |
| **Resend** | Account emails (for example sign-up verification): your email address |
| **Stripe** | Payments: your email address and payment details |

**Used when the feature is switched on or the connection is made**

| Service | What it receives |
|---|---|
| **Google** — Gmail API | Outreach email sent from the mailbox you connect, and replies read from it |
| **Google** — Calendar / Meet | Meeting events and invitees |
| **Google** — Postmaster Tools | Your sending domain (domain reputation) |
| **Google** — Firebase Cloud Messaging | Device tokens and notification text |
| **Unipile** | LinkedIn messages and profiles of prospects; your connected LinkedIn session |
| **RapidAPI** (LinkedIn posts fallback) | A prospect's public LinkedIn profile address |
| **Meta** — WhatsApp Business Cloud API | WhatsApp messages and phone numbers |
| **Apollo.io**, **Hunter.io** | Search criteria; prospect names, company domains and email addresses to verify |
| **NewsAPI** | A prospect's company name |
| **OpenAI** | The same prompts as Anthropic, when an administrator enables the second model |
| **Vapi**, **ElevenLabs** | AI phone calls (phone number, call script) and voicemail text-to-speech |
| **Twilio** | Your phone number, for SMS verification codes |
| **Recall.ai**, **Zoom** | Meeting recording bots and meeting links |
| **Calendly** | Bookings made through your Calendly link |
| **HubSpot**, **Salesforce** | Prospect and deal records synced to the CRM you connect |
| **Slack** | Notifications to the channel you choose |
| **Mailreach** | Your sending mailbox addresses (mailbox health) |
| **ipapi.co**, **ipinfo.io**, **IPQualityScore**, **proxycheck.io** | The IP address you sign up or sign in from (country and VPN check) |
| **AegisAudit**, **PostIQ**, **SIGNALFORGE** | The data you send to these tools when you connect them |

We do not use Google Analytics or any other analytics or advertising tracker.

---|---|---|
| **Anthropic** | AI research, strategy generation, message personalisation | https://www.anthropic.com/privacy — TODO: verify URL is current |
| **Apollo.io** | Lead sourcing and contact enrichment | https://www.apollo.io/privacy-policy — TODO: verify |
| **Hunter.io** | Email address finding and verification | https://hunter.io/privacy-policy — TODO: verify |
| **Google (Gmail API)** | Sending outreach emails on your behalf | https://policies.google.com/privacy — TODO: verify |
| **Google (Firebase Cloud Messaging)** | Push notifications | https://policies.google.com/privacy — TODO: verify |
| **Meta (WhatsApp Business Cloud API)** | WhatsApp outreach on your behalf | https://www.whatsapp.com/legal/privacy-policy — TODO: verify |
| **Calendly** | Meeting booking and scheduling | https://calendly.com/privacy — TODO: verify |

---

## 6. Data retention

There is no automatic deletion schedule for account, campaign or prospect data, with one exception (support chat). What exists today:

- **Account, campaign and prospect data:** Kept while your account exists. When you delete your account (Section 7), it is erased immediately, except for the records listed below.
- **Support chat conversations:** Deleted automatically 30 days after their last message, by a nightly job. Support tickets are kept.
- **Billing records** (subscription status, amounts, dates and payment-provider reference numbers; our copy holds no name or email address): kept after account deletion for 7 years for tax and accounting record-keeping. No job deletes them automatically yet. Stripe keeps its own customer record of you (name, email and payment details) under its own privacy policy; Vectorae does not ask Stripe to delete it (founder decision pending).
- **Consent ledger and do-not-contact records:** Every recorded consent event for your prospects — opt-ins (with a short note on how consent was given), opt-outs, suppressions and erasures — with the email address or phone number it concerns, its source and time, and the do-not-contact (suppression) list, are kept without a time limit, so that a person who asked not to be contacted is never contacted again — also after your account is deleted. An unsubscribe click that arrives after the prospect or your account was erased is still added to the do-not-contact list and the ledger.
- **Security records:** The record of what happened to an account (for example "data exported", "account deleted") is kept after deletion, with IP addresses, device details, email addresses and free-form details removed — including refused sign-in attempts recorded only under your email address.
- **Device tokens:** Deleted when you delete your account; a token Firebase invalidates is no longer used.
- **Cached provider responses:** Answers from the contact-finding and verification providers are cached for up to 30 days (search results for 1 day) and expire on their own; they are not removed when an account is deleted.
- **Server logs:** The hosting provider's request logs record the IP address of each request and are kept for the period the hosting provider sets (founders: state the period). Vectorae's own application log does not record IP addresses.
- **Files:** Uploaded backgrounds and logos and generated voicemail audio are deleted with the account; voicemail audio is also deleted when the prospect is erased.

---

## 7. Your rights

Depending on your location, you have the following rights:

- **Access and portability:** Download a copy of everything your account holds as one JSON file: open **Profile** and confirm your password in **Download your data**.
- **Rectification:** Correct inaccurate data.
- **Erasure ("right to be forgotten"):** Delete your account yourself: open **Profile**, then **Delete account**, confirm your password and type DELETE. Any paid plan is cancelled with the payment provider first; if that cannot be confirmed, nothing is deleted and you are asked to try again. Your account and its data are then erased immediately and connected LinkedIn accounts are disconnected at Unipile, except the billing, consent, do-not-contact and security records described in Section 6. Access you gave Vectorae inside Google, HubSpot, Salesforce or Slack is removed from Vectorae but not revoked on the provider's side. You can also erase an individual prospect's data from the dashboard: their contact details and research, the messages written to them and the replies they sent, call transcripts and voicemail audio, booking details, meeting notes, transcripts and briefs, and notes, custom fields and deal notes about them are erased; the dates, statuses and counts reports are built from are kept; their email address, phone number and LinkedIn profile go on the do-not-contact list. Data already copied into a CRM you connected stays there.
- **Objection:** Object to processing based on legitimate interests.
- **Withdrawal of consent:** Disable push notifications in your device settings at any time.

**To exercise these rights by email:** write to the privacy contact in Section 11. We will respond within 30 days.

---

## 8. Data security

All data is transmitted over HTTPS (TLS 1.2+). Passwords are stored as one-way hashes (PBKDF2-HMAC-SHA256 with 600,000 iterations and a random salt per user). Connection tokens for services such as Gmail are stored encrypted. Firebase Cloud Messaging tokens are stored in the database and used only to deliver notifications to the correct device. The content of the emails and messages sent through the system, and of the replies received, is stored so you can see your campaign history; it is erased when you erase the prospect or delete your account, as described in Section 7.

---

## 9. Children

ClientHunter Enterprise is a professional B2B tool intended for adults (18+). We do not knowingly collect data from anyone under 18. If you believe we have inadvertently collected data from a minor, contact us immediately.

---

## 10. Changes to this policy

We will notify you of material changes to this policy by email or via an in-app notification. The date at the top of this document reflects the most recent revision.

---

## 11. Contact

For privacy questions, data requests, or complaints:

**Vectorae — privacy contact (Rehan Masood)**
ahmadshahid@calendarharvest.com — founders: confirm before publishing (same address as the public website's privacy page)
[Your country] — TODO (founders): fill in before publishing

If you are in the EU and are not satisfied with our response, you have the right to lodge a complaint with your local supervisory authority.
