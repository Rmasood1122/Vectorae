Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# Data processing and subprocessors

This article lists the outside companies that store or process data for Vectorae, what each one receives, and where the data is hosted. It also says which of them are live and which are only built so far. For what Vectorae itself stores and how to delete it, see [Privacy, data retention and deletion](https://www.calendarharvest.com/help/privacy-data-retention-deletion/).

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-30.

## Which outside companies handle my data all the time?

These providers run the service itself, so they hold or carry your data whenever you use Vectorae:

- **Neon** (managed PostgreSQL): the main database. It holds your account, products, strategies, prospects, messages, replies, meetings and the consent and suppression records. (Voicemail audio files are the exception: they are stored on the server's own disk.)
- **Upstash** (managed Redis): the job queue for background work, and a short-term cache of answers from the contact-finding providers.
- **Render**: runs the Vectorae API and the background worker. Your data passes through these servers while it is processed, and Render's request logs record IP addresses.
- **Vercel**: hosts the web app you sign in to.
- **Anthropic** (Claude models): writes research, strategies and message drafts, and classifies replies. See below.
- **Resend**: sends account emails such as sign-up verification and password-reset links. It receives your email address and the email's content.

The public **Privacy** page lists these providers as always in use, and also lists **Stripe** (payments) there. In the code, Stripe receives data only when you take a paid plan, so it is in the feature list below.

## Where is my data hosted?

The deployment configuration runs the API and the worker on Render in the Oregon region, in the United States. The database is on Neon, the queue and cache on Upstash, and the web app on Vercel. The code does not record which Neon or Upstash region is used; the public **Privacy** page states that the main database is hosted on servers in the European Union and the United States.

So you should expect your data, and your prospects' data, to be processed in the United States. The **Privacy** page says that transfers from the UK or EU rely on safeguards such as Standard Contractual Clauses. There is no option today to choose the region your data is stored in.

The founders' design decision is that one deployment serves one customer organisation. The production deployment keeps public sign-up open, so in practice several customers' accounts share one database there, kept apart by ownership checks in the code. See [Security FAQ for buyers](https://www.calendarharvest.com/help/security-faq-for-buyers/).

## Does Vectorae send my prospects to an AI company?

Yes. Research, writing and reply handling are done by Anthropic's Claude models through Anthropic's API, using the deployment's own API key. What is sent depends on the task:

- **Research and strategy:** your product description and ideal-customer profile, and the prospect data gathered for a strategy (name, job title, company, and research notes such as recent posts or company news).
- **Personalised messages:** the prospect's details and research.
- **Reply handling:** the text of replies your prospects send, so they can be classified and a draft answer suggested.
- **Meetings:** meeting transcripts and notes, when you use summaries, prep briefs or follow-up drafts.
- **Support chat:** when the assistant runs with a live model, your question, the recent messages in that chat and the matching help-center text.

**OpenAI** can also receive the same research brief, so its answer can be compared with Claude's. This is off by default: it needs both an administrator setting (`consensus_enabled`) and an OpenAI key.

The code does not set how long Anthropic or OpenAI keep the requests they receive; that is governed by their own terms.

## Which services receive data only when I use a feature?

These receive data only when the feature is used or the account is connected.

**Live, with parts of the API handling marked in the code as not yet checked against the vendor's documentation:**

- **Apollo**: your ideal-customer search criteria; returns matching prospects and enriches their details.
- **Hunter**: a prospect's name and company domain to find an email address, and email addresses to verify.
- **Google (Gmail)**: messages are sent from, and replies read in, the mailbox you connect.
- **Meta (WhatsApp Business)**: the prospect's phone number and the message.

**Built, not live yet:**

- **Unipile (LinkedIn)**: your connected LinkedIn session, the profiles you contact and the messages you send. Tested only against simulated services.
- **Recall.ai**: the meeting link; its bot joins and records the call and returns the transcript. Never called live.
- **Stripe**: your email address and account id when you take a paid plan; card details are entered on Stripe's own checkout page. Online checkout is tested only against simulated services.
- **Twilio**: your mobile number, to send phone verification codes. Not configured in production.

**Used only when a key or connection is set up, usually by an administrator:**

- **NewsAPI**: a prospect's company name, to find recent news about it.
- **RapidAPI**: a prospect's LinkedIn profile address, as a fallback for reading their recent posts.
- **Vapi.ai and ElevenLabs**: for AI phone calls and voicemail, the prospect's phone number, the call's prompt and the voicemail text; ElevenLabs also receives your voice samples if you create a voice clone.
- **Mailreach**: your sending email address, to check inbox placement.
- **Google Calendar and Meet**: meeting details and attendee emails, through your connected Google account, to create Meet links. **Google Postmaster Tools** is asked for your sending domain's spam rate; that import is built but switched off.
- **Firebase Cloud Messaging**: notification titles and text sent to the Android app.
- **AegisAudit, PostIQ and SIGNALFORGE**: separate tools that can be connected; today Vectorae only checks the connection (and, for PostIQ and SIGNALFORGE, the key), and sends them no drafts or prospect data.

**IP checking services:** ipapi.co or ipinfo.io receive your IP address after sign-up and at the onboarding identity step to find its country; proxycheck.io or IPQualityScore receive it at sign-up and sign-in on production deployments to spot VPNs.

The **Privacy** page also lists Calendly, Zoom, HubSpot, Salesforce and Slack, used when you connect them. See [CRM and integrations](https://www.calendarharvest.com/help/crm-and-integrations/) and [Known limitations](https://www.calendarharvest.com/help/known-limitations/).

## Is Voyage AI one of Vectorae's providers?

Not yet. On 2026-10-18 the founders approved Voyage AI to power search in the in-app help assistant: it turns text into search vectors and ranks results, so the assistant can find the help-center passages that best match a question. When that change ships, Voyage AI will receive the help-center article text and your support questions exactly as you type them. A question can contain anything you type into it, including details about a prospect, so leave out what you would not want shared.

Until that change ships, Voyage AI is not in use and receives nothing from Vectorae. Support questions today go only to Anthropic, as described above.

## How long is data kept, inside and outside Vectorae?

- **Your account data** stays in the database until you delete it or delete your account.
- **Support chat history** is deleted 30 days after a conversation's last message.
- **Cached contact-finding answers** in Redis expire after 30 days; search results after 1 day.
- **Billing records** are kept for 7 years after an account is deleted, without your name or email address.
- **Consent and suppression records** are kept without a time limit, so a person who opted out is never contacted again.

Outside providers keep data under their own terms. When you delete your account, Vectorae disconnects your LinkedIn account at Unipile, but it does not ask Stripe to delete its customer record, and it does not revoke its access inside Google, HubSpot, Salesforce or Slack. Details are in [Privacy, data retention and deletion](https://www.calendarharvest.com/help/privacy-data-retention-deletion/).

## Is there a data processing agreement?

No data processing agreement (DPA) or downloadable subprocessor list is published in the product today. The public **Privacy** page is the policy, and it lists the email address for privacy requests. If you need a signed agreement, ask support before relying on one (see [Contact support and status](https://www.calendarharvest.com/help/contact-support-and-status/)).

For requests from the people you contact, the product lets you:

- erase one prospect's data, which also puts their email, phone number and LinkedIn profile on the do-not-contact list;
- download everything your account holds as one file, and delete your whole account;
- rely on a consent ledger that records every opt-in, opt-out and erasure.

See [Privacy, data retention and deletion](https://www.calendarharvest.com/help/privacy-data-retention-deletion/) and [Compliance](https://www.calendarharvest.com/help/compliance/).

## Related

- [Security FAQ for buyers](https://www.calendarharvest.com/help/security-faq-for-buyers/)
- [Privacy, data retention and deletion](https://www.calendarharvest.com/help/privacy-data-retention-deletion/)
- [Compliance](https://www.calendarharvest.com/help/compliance/)
- [CRM and integrations](https://www.calendarharvest.com/help/crm-and-integrations/)
- [Lead sourcing and email verification](https://www.calendarharvest.com/help/lead-sourcing-and-email-verification/)
- [Known limitations](https://www.calendarharvest.com/help/known-limitations/)
- [Contact support and status](https://www.calendarharvest.com/help/contact-support-and-status/)

## Related

- [All help articles](https://www.calendarharvest.com/help/)
- [Vectorae FAQ](https://www.calendarharvest.com/faq/)

[Book a call](https://www.calendarharvest.com/contact/) [See how it works](https://www.calendarharvest.com/how-it-works/)

## Company

- [About](https://www.calendarharvest.com/about/)
- [Contact](https://www.calendarharvest.com/contact/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Status](https://www.calendarharvest.com/status/)

## Who it's for

- [Agencies](https://www.calendarharvest.com/for/agencies/)
- [B2B service firms](https://www.calendarharvest.com/for/b2b-service-firms/)
- [Local businesses](https://www.calendarharvest.com/for/local-businesses/)

## Compare

- [vs Apollo](https://www.calendarharvest.com/compare/leadpilot-vs-apollo/)
- [vs Instantly](https://www.calendarharvest.com/compare/leadpilot-vs-instantly/)
- [vs Smartlead](https://www.calendarharvest.com/compare/leadpilot-vs-smartlead/)
- [vs Clay](https://www.calendarharvest.com/compare/leadpilot-vs-clay/)
- [vs Artisan](https://www.calendarharvest.com/compare/leadpilot-vs-artisan/)
- [All comparisons](https://www.calendarharvest.com/compare/)

## Resources

- [What is Vectorae](https://www.calendarharvest.com/what-is-leadpilot/)
- [FAQ](https://www.calendarharvest.com/faq/)
- [Help center](https://www.calendarharvest.com/help/)
- [Outbound statistics](https://www.calendarharvest.com/outbound-statistics/)

## Legal

- [Privacy Policy](https://www.calendarharvest.com/privacy/)
- [Terms of Service](https://www.calendarharvest.com/terms/)

© 2026 Rehan Masood. Vectorae is operated by Rehan Masood.