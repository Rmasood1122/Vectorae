Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# Google Workspace DNS setup (SPF, DKIM, DMARC) and Gmail access

If you send from a Gmail address on your own Google Workspace domain, Vectorae checks that domain's SPF, DKIM and DMARC records. This article lists exactly which records Vectorae looks for, what to publish so each check passes, what the results on screen mean, and why Google sometimes asks you to approve Vectorae again. For what the records are and how the health score is calculated, see [Deliverability and mailbox health](https://www.calendarharvest.com/help/deliverability-and-mailbox-health/).

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-18.

## Which records does Vectorae look for on my domain?

Vectorae checks the domain of each connected Gmail address. Shared domains such as gmail.com are skipped, because their reputation belongs to Google. It reads public DNS TXT records only; it never changes your DNS.

| Check | Where Vectorae looks | What counts as present |
| --- | --- | --- |
| SPF | a TXT record on your domain itself | a record starting `v=spf1` |
| DKIM | a TXT record at `google._domainkey.<your domain>` | a record containing `v=DKIM1` or `p=` |
| DMARC | a TXT record at `_dmarc.<your domain>` | a record starting `v=DMARC1`; its `p=` value is read as the policy |

`google` is the DKIM selector Google Workspace uses by default, and it is the only one the **Email health** card checks. A DNS lookup that fails, or takes longer than 5 seconds, is treated as "no record". You add the records at whoever hosts your domain's DNS; Vectorae cannot do it for you.

## What SPF record should I publish?

Vectorae sends through the Gmail API, so your mail leaves from Google's servers. Your SPF record has to authorise them. Vectorae's own suggestion, when SPF is missing, is a TXT record like:

`v=spf1 include:_spf.google.com ~all`

If you already have an SPF record for other services, add `include:_spf.google.com` to it instead of creating a second one. The launch preflight (below) fails a domain that publishes more than one SPF record, one whose SPF does not reach `_spf.google.com`, or one that needs more than 10 DNS lookups to evaluate. The **Email health** card is more lenient: it only checks that a `v=spf1` record exists.

## Where does the DKIM key from Google go?

You generate the DKIM key in Google Admin; Vectorae's own messages say "Turn on DKIM signing in Google Admin". Publish the key Google gives you as a TXT record at the host `google._domainkey` on your domain (the full name is `google._domainkey.<your domain>`). That is where Vectorae looks.

- If you chose a selector other than `google` in Google Admin, the **Email health** card shows DKIM as missing even when signing works. Its line reads "DKIM not found under the Google selector", because that card checks only that selector. An administrator can add your selector to the `deliverability_dkim_selectors` setting, but only the launch preflight reads it.
- If the record has an empty `p=` value, the preflight reports the key as revoked and asks you to generate a new key in Google Admin.

## Which DMARC record fixes "No DMARC record"?

Publish a TXT record at the host `_dmarc` on your domain (`_dmarc.<your domain>`) starting with `v=DMARC1` and containing a `p=` policy. Vectorae's minimum is:

`v=DMARC1; p=none`

`p=none` only monitors. It satisfies the check, but the domain score still loses 10 points and the card shows "DMARC policy is p=none (monitoring only)". A `p=quarantine` or `p=reject` policy removes that deduction. Keep exactly one DMARC record: the preflight fails a domain with two, because receivers ignore all of them, and fails one whose `p=` is not `none`, `quarantine` or `reject`.

The **Email health** card looks only at `_dmarc` on your exact sending domain. The launch preflight also accepts a DMARC record on the parent domain (the last two parts of the name), so a subdomain can pass the preflight while the card still shows DMARC missing.

## What do the results on the Email health card mean?

Go to **Settings**, open the **Deliverability** tab, and find the **Email health** card. Each sending domain has a panel:

- **SPF**, **DMARC**, **DKIM**: "OK" means the record was found, "missing" means it was not, and "unknown" means the domain has not been checked yet. DMARC also shows its policy, for example `p=none`.
- **Blocklists**: "OK" when the domain is on no list checked. If it is listed, a red notice names the lists and says campaigns were paused. A note such as "1 could not be queried" means a list did not answer, which is never counted as listed.
- **Health**: the score out of 100, marked "above threshold" or "below 70" (the default threshold), or "not checked yet".

After you change DNS, press **Run check now**; the toast "Checks complete" means the panel is up to date. If the card says "Connect a Gmail account on your own domain to monitor it", your connected address is on a shared domain such as gmail.com.

## What extra checks run before a campaign launches?

If your administrator has turned on the Deliverability Autopilot (`deliverability_autopilot_enabled`, off by default), launching, approving or resuming a campaign that sends email first runs a preflight on every connected mailbox. It blocks the launch when SPF, DKIM or DMARC fails the stricter rules above, when neither SPF nor DKIM can pass aligned with DMARC, or when no Gmail mailbox is connected. It also checks the one-click unsubscribe headers. The refusal starts "Deliverability preflight failed:", lists each problem with its fix, and ends "Fix these, then launch again (Settings > Deliverability)." A `p=none` policy passes, with a warning. If the preflight itself cannot run, the launch is blocked rather than allowed.

With the Autopilot off, a low score only warns you: "Email health for … is …/100 (below 70). Sending now risks the spam folder".

## Why does Google ask me to approve Vectorae again?

Vectorae asks Google for your consent on every connect, on purpose, so that Google always returns the long-lived permission that lets Vectorae keep working while you are away. So pressing **Connect** or **Reconnect** always shows Google's approval screen; that is expected.

You do not need to reconnect when your access expires. Vectorae renews it automatically when it is within 120 seconds of expiring, and stores it encrypted.

You do need to reconnect when:

- Vectorae's access was removed in your Google account. Renewal then fails, **Test** on the Gmail card shows "gmail: unhealthy ✗", and email messages go back to the queue and are retried; nothing goes out until you reconnect. The strategy page's sending diagnosis then shows "Google refused your Gmail connection (revoked or expired)." with the fix "Reconnect Gmail.", and **Test** shows the same problem;
- your account was connected before calendar or draft access was added (see [Sending email through Gmail](https://www.calendarharvest.com/help/email-sending-gmail/#when-do-i-need-to-reconnect));
- connecting ends with "Google did not return a refresh\_token — re-run the auth flow (prompt=consent) so offline access is granted." Press **Reconnect** and approve again.

Reconnecting keeps the permissions you granted before and updates the same connection.

## Related

- [Deliverability and mailbox health](https://www.calendarharvest.com/help/deliverability-and-mailbox-health/)
- [Sending email through Gmail](https://www.calendarharvest.com/help/email-sending-gmail/)
- [Pause, stop and the kill switch](https://www.calendarharvest.com/help/pause-stop-kill-switch/)
- [Troubleshooting](https://www.calendarharvest.com/help/troubleshooting/)
- [Known limitations](https://www.calendarharvest.com/help/known-limitations/)
- [Compliance](https://www.calendarharvest.com/help/compliance/)

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