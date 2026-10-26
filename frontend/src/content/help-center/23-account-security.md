---
id: account-security
title: Account security, sign-in and API keys
audience: customer
last_verified: 2026-10-11
owner: help-center
sources:
  - app/api/auth.py
  - app/services/auth.py
  - app/services/auth_sessions.py
  - app/services/password_reset.py
  - app/config.py
  - app/core/rate_limiting.py
  - app/services/network_guard.py
  - app/services/api_keys.py
  - app/api/api_keys.py
  - app/services/admin_service.py
  - app/services/identity.py
  - app/api/billing.py
  - frontend/src/app/(auth)/login/page.tsx
  - frontend/src/app/(auth)/forgot-password/page.tsx
  - frontend/src/app/(auth)/reset-password/page.tsx
  - frontend/src/app/(app)/profile/page.tsx
  - frontend/src/components/settings/WebhooksCard.tsx
  - frontend/src/components/shell/Shell.tsx
  - frontend/src/lib/storage.ts
  - docs/program/BASELINE.md
---

# Account security, sign-in and API keys

This article covers how signing in works, how long you stay signed in, how to change or reset your password, how personal API keys work, and why an account can be blocked or suspended. It describes the product as it works today, including the security features it does not have yet.

## How does signing in work?

You sign in with your email address and password. Passwords must be at least 8 characters. Victorae stores only a one-way hash of your password (PBKDF2-SHA256), never the password itself.

If the email or the password is wrong, the message is always "invalid email or password". Victorae does not say which one was wrong, so nobody can use the sign-in page to find out whether an address has an account.

New accounts must confirm their email address before they can use the app. Accounts created through public sign-up also go through identity onboarding, which includes verifying a mobile phone number. You can put the phone step off for the current browser session, but a verified phone number is required before you can choose a paid plan. (A production deployment with deliberately no text-message service cannot send codes, so it skips this step instead.)

## How many sign-in attempts are allowed?

Sign-in attempts are limited to 10 per 15 minutes. The limit is counted in two ways at once: per network address and per email address. That second count stops someone from guessing one account's password from many different addresses. Both successful and failed attempts count. When you reach the limit, wait for the 15-minute window to pass and try again. The number is a deployment setting; 10 is the default.

## How long do I stay signed in?

The sign-in page has a **Keep me signed in** box, which is ticked by default.

- **Ticked:** you stay signed in for 30 days from your last use. Each time the app refreshes your session, the 30 days start again, so an account you use regularly is not asked to sign in again. An account left unused for 30 days must sign in again.
- **Not ticked:** the session ends when you close the browser, or after 12 hours, whichever comes first.

Behind the scenes, the app uses a short access pass that lasts 15 minutes and renews it automatically. Each renewal can be used only once. If an old, already-used session pass shows up again (which suggests someone copied it), Victorae signs out that whole session and records the event in the security log.

These durations are deployment settings; the numbers above are the defaults.

## How do I sign out or change my password?

- **Sign out:** use **Log out** in the app's side menu. This ends the session on the server, not just in your browser.
- **Change password:** open **Profile**, go to **Change password**, enter your **Current password**, your **New password** and **Repeat new password**, then press **Change password**. The new password must be different from the current one. Changing it signs you out on every device, including the one you are using, and you are taken to the sign-in page. It also revokes all your personal API keys, so create new ones afterwards for any integration that needs them.

Password changes are limited to 10 attempts per 15 minutes, so a stolen session cannot be used to guess your old password.

There is no screen that lists your active sessions. To sign out everywhere, change your password (or reset it, below): every session ends, and anything signed in with the old password, on any device, is refused from then on.

## Can I reset a forgotten password or turn on two-factor sign-in?

You can reset a forgotten password yourself:

1. On the sign-in page, press **Forgot password?**.
2. Enter the email address you sign in with and press **Email me a reset link**.
3. Open the link in the email, enter your **New password** and **Repeat new password** (at least 8 characters), and press **Set new password**.
4. Sign in with the new password.

The link works once and expires after 60 minutes. Asking again within a minute sends nothing and keeps your current link working; asking later sends a new link and the older one stops working. Resetting your password signs you out on every device and revokes all your personal API keys. For your security, the page shows the same message whether or not the address has an account, and nothing is sent to an address without one. Reset requests are limited to 10 per 15 minutes, counted per network address and per email address, and an account gets at most one reset email a minute and at most 5 in 24 hours (after that the page still shows the same message, but no email is sent). A suspended account cannot reset its password. The link lifetime is a deployment setting; 60 minutes is the default.

Two-factor sign-in is not available.

## Why am I told I'm using a VPN?

On production deployments, Victorae blocks sign-up and sign-in from VPNs, proxies, Tor and hosting or data-centre networks. You then see:

"It looks like you're connecting through a VPN, proxy or hosting network. Please disable your VPN or proxy and try again from your normal network."

Turn off your VPN or proxy and try again from your normal connection. If you see "We couldn't verify your network just now. Please try again in a minute.", the network check itself was unavailable; wait a minute and retry.

Administrators can turn this blocking off, or allow specific network addresses, for example an office connection that is wrongly classed as a hosting network. By default, if the outside service that checks addresses is down, sign-in is allowed rather than blocked.

## Why can't I create an account?

Public sign-up can be closed or limited by your deployment:

- If sign-up is closed, you see "Sign-up is closed on this Victorae deployment. Ask your administrator for an account."
- If sign-up is limited to certain email domains, addresses from other domains see "Sign-up on this Victorae deployment is limited to approved email domains. Ask your administrator for an account."

By default sign-up is open to any email domain. Existing accounts can always sign in.

## How do personal API keys work?

API keys let tools such as Zapier, Make or your own scripts act as you through the Victorae API.

1. Open **Settings** and the **Integrations** tab.
2. In the **API keys** card, type a **Name** and press **Create key**.
3. Copy the key straight away. It is shown once and never again ("Copy it now — it will not be shown again.").

Facts about keys:

- A key starts with `lpk_`. Victorae stores only a fingerprint (hash) of it, so it cannot show it to you later.
- A key acts as you on the normal API, but it never works on administrator pages, even if your account is an administrator.
- A key cannot download your whole account's data; that needs a signed-in person and a password.
- Changing or resetting your password revokes every key.
- You can have at most 10 active keys. Revoke one before creating another.
- Use **Revoke** to disable a key immediately. Revoke any key you no longer need.

## What happens if my account is suspended?

An administrator can suspend an account. While it is suspended, every request from it is refused with "account suspended", and all of its active campaigns are paused. You may still get past the sign-in page, but nothing in the app will load. When the administrator lifts the suspension, the campaigns that were paused because of it become active again. Contact support if you think your account was suspended by mistake.

## Related
- [Getting started](03-getting-started.md)
- [Privacy, data retention and deletion](21-privacy-data-retention-deletion.md)
- [Workspaces and agency mode](24-workspaces-and-agency-mode.md)
- [CRM and integrations](16-crm-and-integrations.md)
- [Troubleshooting](27-troubleshooting.md)
- [Known limitations](29-known-limitations.md)
