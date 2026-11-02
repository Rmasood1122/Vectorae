Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# Deliverability and mailbox health

Deliverability is whether your emails reach the inbox. Vectorae checks two things: the health of each **sending domain** (DNS records, bounces, blocklists) and the health of each **mailbox** you send from. A mailbox in trouble is slowed down or paused automatically, and paused messages wait rather than being dropped. You find both under **Settings**, on the **Deliverability** tab.

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-10.

## What are SPF, DKIM and DMARC, and why do they matter?

They are DNS records on your domain that tell receiving mail servers your emails are genuine:

- **SPF** lists which servers may send mail for your domain.
- **DKIM** adds a signature to each email.
- **DMARC** tells receivers what to do with mail that fails those checks. A DMARC policy of `p=none` only monitors; `quarantine` or `reject` give stronger protection.

Missing records make your mail more likely to land in spam. You or whoever manages your domain adds them at your DNS provider. Vectorae only reads them; it cannot change them.

## How is my domain's email health scored?

The **Email health** card checks the domain of each connected Gmail address. Shared consumer domains such as gmail.com are skipped, because their reputation belongs to Google, not to you.

The score starts at 100 and loses points for:

- no SPF record: −25
- no DMARC record: −25 (or −10 if DMARC is `p=none`)
- DKIM not found under Google's selector (`google._domainkey`): −15
- a bounce rate over 2% for your sends in the last 30 days: up to −30
- the domain being on a blocklist: −40

If the deployment has a Mailreach key configured, its inbox-placement reputation is also read and the lower of the two scores is used. The Mailreach connection is marked in the code as not yet checked against Mailreach's current documentation, so treat it as not yet verified against the live service.

Below 70 (the default threshold), you get a one-time warning, and launching a campaign returns a warning too. Click **Run check now** to check immediately.

## What happens if my domain is blocklisted?

Domains are checked against blocklists daily at 06:10 UTC and on demand. Without an MXToolbox key, Vectorae queries Spamhaus DBL, SURBL and URIBL directly. Spamhaus refuses queries from public DNS servers; when that happens the list is shown as "could not be queried", never as listed.

When a domain is **newly** listed, every active campaign on your account is paused, and an urgent alert goes out by push notification and Slack. Being removed from the list does **not** restart anything; you resume campaigns yourself.

## What is mailbox health?

Two mailboxes on the same domain can have very different reputations, so Vectorae also scores each **mailbox** and acts only on the one in trouble. The **Mailbox health** card shows each mailbox's score out of 100, its state, the reasons, its complaint and bounce rates, and how much it sent today. Scores refresh every 4 hours; **Check now** refreshes them immediately. A mailbox that has not been checked yet is shown as unchecked.

The score starts at 100 and loses points for:

- no SPF: −25; no DMARC: −20 (−10 for `p=none`); no DKIM: −10
- complaint rate of 0.1% or more: −35; 0.3% or more: −65
- bounce rate of 2% or more: −35; 5% or more: −65
- a volume spike (3 times or more the 7-day average, with at least 20 sends today): −10

A brand-new mailbox with no history is not penalised for missing rates. A mailbox missing all three DNS records lands at 45: slowed down, not stopped.

## Why does it say the complaint rate is a "proxy"?

Vectorae has no connection to Gmail's spam-report data (a feedback loop or Google Postmaster Tools), so it cannot see when someone clicks "report spam". Instead, the complaint rate counts **unsubscribes plus replies asking you to stop**, divided by sends. The card labels it "(proxy)". It is a stand-in for the real spam-complaint rate, not the same thing.

## What do "throttled" and "paused" mean?

| Score | State | Effect |
| --- | --- | --- |
| 70 or more | healthy | normal daily limit |
| 40 to 69 | throttled | the daily limit is cut to 25% of the normal warm-up allowance, never below 2 a day |
| under 40 | paused | that mailbox sends nothing |

When a mailbox is paused, its messages are moved to the next day's send window with the reason recorded. They are deferred, never cancelled. When a throttle and a compliance rule both set a limit, the smaller one applies; a throttle can never raise a limit.

A paused mailbox is **never** resumed automatically, even if a later check scores higher. Click **Resume** on the mailbox. If its score is still under 70, it comes back throttled, not at full speed.

These limits are applied to email sends from your connected Gmail mailboxes. If the health check itself fails, the mailbox is treated as healthy, so a fault in the check does not stop your sending.

## What else pauses sending automatically?

A whole campaign is paused if its bounce rate goes over 3% once at least 10 messages have been sent (deployment defaults; a workspace compliance rule can set a lower threshold, never a higher one). It stays paused until a person resumes it. For daily caps, the warm-up ramp and send windows, see [Email sending through Gmail](https://www.calendarharvest.com/help/email-sending-gmail/).

## Related

- [Email sending through Gmail](https://www.calendarharvest.com/help/email-sending-gmail/)
- [Compliance](https://www.calendarharvest.com/help/compliance/)
- [Analytics, health score and ROI](https://www.calendarharvest.com/help/analytics-health-score-roi/)
- [Pause, stop and the kill switch](https://www.calendarharvest.com/help/pause-stop-kill-switch/)
- [Troubleshooting](https://www.calendarharvest.com/help/troubleshooting/)

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