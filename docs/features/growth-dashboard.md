# Growth dashboard (Admin > Growth)

Founder request, 2026-11-03: one live page showing who signed up, from where,
and which accounts bought a subscription — visible to named people only.

## Who can see it
`GET /admin/growth` and the page `/admin/growth` require BOTH:
1. the account is an admin (`users.is_admin`), verified, not suspended, not an API key
   (`deps.require_admin`), and
2. its email is on `GROWTH_DASHBOARD_VIEWERS` (comma-separated, case-insensitive).

Default list (app/config.py): devsahmad37@gmail.com, vectorae@calendarharvest.com,
ahmadshahid@calendaharvest.com, rehanmasood@calendarharvest.com,
shahidussain@calendarharvest.com. An empty value closes the page to everyone.
Each listed person must also be promoted: `python -m app.cli.create_admin --email <email> --promote-only`.

Every read is logged (`admin.growth_dashboard_read`, with the viewer's email).

## What it shows
- Totals: sign-ups (24h / 7d / period), verified, refused attempts, purchases,
  paying and trialing subscriptions, cancellations, conversions, MRR per currency.
- Daily sign-ups vs purchases, top countries, top sources.
- Sign-ups: when, email, IP, IP country, stated country, company, source / UTM
  campaign / landing page, verification, plan, last active.
- Purchases: when, account, plan, price, status, trial end, renewal date,
  sign-up country / IP / source, days from sign-up to purchase, Stripe customer.

"Live" = the page polls every 15 seconds while open.

## Where "source" comes from
The browser records the first page of a visit (UTM tags, referring host, landing
path) in sessionStorage and sends it with `POST /auth/signup` as `source`. The
server keeps UTM tags, the referrer HOST only and the landing PATH only
(`signup_watch.clean_source`), in the sign-up row's `details_json`. It is scrubbed
on account erasure with the rest of that row. A bad or missing source never
blocks a sign-up.

## Money rules
Stub subscriptions (no Stripe) are listed, never counted. MRR counts `active` and
`past_due` monthly/annual Stripe subscriptions (annual / 12), summed per currency.
