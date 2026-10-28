# Feature Group 9 — Trust & deliverability

## Email health (Settings › Deliverability)
For every sending domain (the domain of each connected Gmail address; shared
consumer domains such as gmail.com are skipped — their reputation is Google's):

| Check | How |
|---|---|
| SPF | TXT record starting `v=spf1` |
| DMARC | TXT at `_dmarc.<domain>`; `p=none` scores lower than quarantine/reject |
| DKIM | the Google Workspace selector (`google._domainkey`) |
| Bounce rate | this account's sends over the last 30 days |
| Blocklists | MXToolbox with an API key; otherwise Spamhaus DBL, SURBL and URIBL via DNS |
| Inbox placement | Mailreach account reputation, when a Mailreach key is configured |

Health score 0–100: −25 no SPF, −25 no DMARC (−10 for `p=none`), −15 DKIM not
found, up to −30 for bounces over 2%, −40 if blacklisted; with Mailreach data
the lower of the two scores wins. Below `deliverability_health_threshold`
(default 70) the user gets a `deliverability_warning` alert once, and a
campaign launch returns a `warning`.

With the **Deliverability Autopilot** on (`deliverability_autopilot_enabled`,
default off — see `f03-deliverability-autopilot.md`) a launch, approval or
resume is **blocked** (409) when the sending mailbox fails the SPF / DKIM /
DMARC-alignment preflight or the one-click unsubscribe headers are missing
from the message the send path builds. The warning above is unchanged.

Spamhaus refuses queries from public DNS resolvers (answering `127.255.255.x`);
those lists are shown as "could not be queried", never as listed. Use a
resolver of your own or an MXToolbox key for reliable results.

## Blacklist monitoring
Daily at 06:10 UTC (and on demand). When a domain is **newly** listed, every
active campaign of the account is paused (`paused_blacklist`) and a critical
`domain_blacklisted` alert goes out by push, Slack and webhook. Delisting does
not resume anything automatically. Admin: `blacklist_monitoring_enabled`.

## Reply fraud detection
Replies classified interested / question / objection / not interested get a
second pass. A no-reply sender or an auto-reply subject is conclusive; two
weaker signals (e.g. "we received your message" and "ticket #") are too; one
weak signal asks Claude. Automated replies are stored as
`automated_response` — visible, but not counted as replies: no REPLIED
outcome, no status change, the sequence neither stops nor advances, and they
are excluded from reply rates, sentiment and the learning loop. Admin:
`reply_fraud_detection_enabled`.

## Region-aware compliance
The lead's region comes from its enrichment country, falling back to its
timezone (`app/services/compliance_region.py`). Every send, suppression block
and missing-consent skip writes a `compliance_audit_log` row: region, regime,
legal basis and the checks made (unsubscribe link, sender identity, open
tracking, consent). GDPR/UK recipients get a right-to-object notice and CASL
recipients a sender/unsubscribe notice above the standard footer; EU/UK
recipients never get an open-tracking pixel.

| Region | Regime | Basis recorded |
|---|---|---|
| US | CAN-SPAM | opt-out regime |
| CA | CASL | implied consent assumed — verify for your prospects |
| EU | GDPR | legitimate interest (B2B), right to object stated |
| UK | UK GDPR / PECR | legitimate interest, corporate subscriber |
| SG | PDPA | business contact information; DNC registry **not** checked |
| AU / NZ | Spam Act / UEMA | inferred consent |

Vectorae does not check national Do-Not-Call registries and cannot verify
implied consent; the audit log records that. None of this is legal advice.
