---
id: security-faq-for-buyers
title: Security FAQ for buyers
audience: prospect
last_verified: 2026-10-18
owner: help-center
sources:
  - frontend/src/content/product-facts.json
  - app/services/auth.py
  - app/services/auth_sessions.py
  - app/api/auth.py
  - app/config.py
  - app/core/crypto.py
  - app/services/api_keys.py
  - app/services/webhook_delivery.py
  - app/services/safe_fetch.py
  - app/services/audit_trail.py
  - app/api/audit.py
  - app/services/security_audit.py
  - app/services/account_export.py
  - render.yaml
  - docs/program/DECISIONS.md
  - frontend/src/app/(app)/settings/page.tsx
  - frontend/src/lib/settings-tabs.ts
  - frontend/src/components/settings/AuditTrailCard.tsx
  - frontend/src/components/settings/WebhooksCard.tsx
---

# Security FAQ for buyers

This article answers the security questions a buyer or security reviewer usually asks before choosing Victorae: how passwords, keys and secrets are stored, how webhooks are signed and kept away from internal systems, what audit records exist, and where the service runs. It describes what the code does today, including what is not in place yet. For the sign-in rules an everyday user needs, see [Account security](23-account-security.md).

## How does Victorae store passwords and handle sign-in sessions?

Victorae never stores your password. It stores a one-way hash made with PBKDF2-HMAC-SHA256 at 600,000 iterations, with a random 16-byte salt for each password. When you sign in, the hash is recomputed and compared in constant time, so the comparison itself does not leak timing information. Passwords must be at least 8 characters.

Sign-in uses signed tokens (JWT, HS256). A short-lived access token is used for each request, and a longer-lived refresh token renews it. Refresh tokens are tied to a session record on the server, so signing out really ends the session, and each refresh token can be used only once. If an already-used refresh token turns up again, which suggests it was copied, the whole session is signed out and the event is written to the security log. In the web app the refresh token is kept in an HttpOnly cookie, which page scripts cannot read. How long sessions last, and the sign-in attempt limits, are in [Account security](23-account-security.md).

## How are API keys and connection tokens protected?

**Personal API keys.** Victorae keeps only a SHA-256 fingerprint of each key, plus its first few characters so you can recognise it in the list. The full key is shown once when it is created and cannot be shown again, because Victorae does not have it.

**Connections to other services.** Access tokens for connected services (for example Gmail, WhatsApp, Calendly, and Apollo or Hunter keys) are encrypted before they are written to the database, using Fernet symmetric encryption with a key that is set on the server and never stored in the code. The encryption key can be rotated: during a rotation the previous key is used only to read old values, and can be removed afterwards.

**Webhook signing secrets** are stored encrypted the same way. A secret starts with `whsec_` and is shown once, when you create the webhook.

Your data export leaves all of these out on purpose: it contains no password hash, no key fingerprints, no encrypted tokens and no webhook secrets.

## How can my endpoint check that a webhook really came from Victorae?

Every webhook delivery is signed with the webhook's own signing secret. The request carries these headers:

- `X-LeadPilot-Event`: the event name.
- `X-LeadPilot-Delivery`: a delivery id. Use it to ignore a retry you already processed.
- `X-LeadPilot-Signature`: `t=<unix time>,v1=<signature>`, where the signature is an HMAC-SHA256 of the time, a dot, and the raw request body.

To verify a delivery, recompute the HMAC with your secret and compare it with `v1`, and reject a timestamp more than 300 seconds (five minutes) old, so an old request cannot be replayed. An older `X-ClientHunter-Signature` header (an HMAC-SHA256 of the body alone) is still sent for compatibility; prefer the `v1` one.

A delivery that fails is retried, up to 6 attempts in all. You create webhooks and copy the secret in **Settings**, **Integrations** tab, **Webhooks (Zapier, Make)** card. For the events and payloads, see [Webhook events, signatures and Slack alerts](37-webhooks-and-slack-events.md).

## Can a webhook or website address be used to reach Victorae's internal network?

Two features make Victorae call an address you give it: outgoing webhooks, and **Quickstart** (off by default), which reads your company's website. Both are guarded against being pointed at internal systems (known as SSRF).

For **webhooks**:

- the address must use `https`;
- it may not contain a user name or password;
- `localhost` and names ending in `.localhost`, `.local` or `.internal` are refused;
- a raw IP address is refused unless it is a public internet address, so private, loopback and link-local addresses (including the cloud metadata address) are rejected;
- at every delivery the host name is looked up again, and the delivery is refused if it points to any non-public address;
- redirects are not followed, so a public address cannot bounce the request to an internal one.

For the **Quickstart** website reader, the same rules apply, plus: only port 443, at most 3 redirects (each one checked like the first address), an 8-second time limit and at most 512,000 bytes read. A plain `http://` address is upgraded to `https://`.

One gap remains and is documented in the code: there is a short window between Victorae's address check and the actual connection in which a hostile DNS server could change its answer ("DNS rebinding"). Closing it fully needs an outbound proxy, which is not in place.

## Is there an audit trail of what happened in an account?

There are two records.

**The activity audit trail** covers outreach: every send, open, click, reply and booked meeting for a lead. Each record holds ids, the sequence step, the variant and the reply classification, never an email address, a name or message text, so it can be handed to a third party and survives a GDPR erasure. Records are hash-chained: each one carries a SHA-256 hash of its content and of the record before it, so editing, deleting or inserting a record breaks the chain at that point. The application refuses edits and deletes, and in PostgreSQL a database trigger refuses them even for direct SQL. In **Settings**, **Deliverability** tab, the **Activity audit trail** card has **Verify chain**, **Signed JSON** and **PDF report**. The JSON report is signed with Ed25519; the matching public key is published by the API, and anyone holding a report can check it offline or through the API without an account.

**The security log** records account-level actions: sign-outs, password changes and resets, detected reuse of a session token, data exports and refused export attempts, deleted strategies, imports and deletions of uploaded networks, account deletion, and similar events. There is no screen for it in the app; it is included in your account data export.

Neither record logs every field edit (for example a changed lead title), so there is no full per-field change history.

## Where does Victorae run, and are customers kept apart?

The deployment configuration in the code runs the API and the background worker on Render (region: Oregon, in the United States), the database on Neon (PostgreSQL), the job queue and cache on Upstash (Redis) and the web app on Vercel. The deployment settings call for encrypted connections to both: `sslmode=require` for the database and `rediss://` (TLS) for Redis. See [Data processing and subprocessors](43-data-processing-and-subprocessors.md) for what each provider receives.

The founders' design decision is that one deployment serves one customer organisation. The production deployment keeps public sign-up open, so in practice accounts from different customers share one database there. Each account's data is kept apart by ownership checks in the code, which the automated tests check. Those tests run on a lightweight test database; the same checks have not yet been proven on PostgreSQL (see [Known limitations](29-known-limitations.md)).

## What security features does Victorae not have today?

Be aware of these before you buy:

- **No security certification.** Victorae does not claim SOC 2, ISO 27001 or any other certification.
- **No two-factor sign-in**, and no single sign-on.
- **No list of active sessions.** Changing or resetting the password signs out everywhere and revokes API keys.
- **No per-field change history**, as described above.
- **DNS-rebinding window** on webhooks and Quickstart, as described above.
- **Separation not yet proven on PostgreSQL**, as described above.

If your review needs something not covered here, ask support (see [Contact support and status](30-contact-support-and-status.md)) before relying on it.

## Related
- [Account security, sign-in and API keys](23-account-security.md)
- [Data processing and subprocessors](43-data-processing-and-subprocessors.md)
- [Privacy, data retention and deletion](21-privacy-data-retention-deletion.md)
- [Webhook events, signatures and Slack alerts](37-webhooks-and-slack-events.md)
- [Compliance](20-compliance.md)
- [Workspaces and agency mode](24-workspaces-and-agency-mode.md)
- [Known limitations](29-known-limitations.md)
