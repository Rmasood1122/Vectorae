---
id: mobile-app
title: The Android app
audience: customer
last_verified: 2026-10-11
owner: help-center
sources:
  - frontend/capacitor.config.ts
  - frontend/src/components/providers/NativeProvider.tsx
  - frontend/src/hooks/usePushNotifications.ts
  - frontend/src/hooks/useBackButton.ts
  - frontend/src/components/ui/OfflineBanner.tsx
  - frontend/src/components/shell/Shell.tsx
  - frontend/src/components/shell/nav.ts
  - frontend/src/lib/storage.ts
  - app/api/auth.py
  - app/services/auth_sessions.py
  - app/workers/notification_tasks.py
  - app/services/sequence_engine.py
  - app/services/notifications.py
  - app/services/event_bus.py
  - app/verification/loop.py
  - app/services/whatsapp_templates.py
  - app/services/mailbox_health.py
  - app/api/analytics.py
  - docs/README_M7_COMPLETE.md
  - docs/README_M7_MOBILE_SECTION.md
  - FEATURES.md
  - docs/program/BASELINE.md
  - app/services/daily_send_approval.py
---

# The Android app

Vectorae has an Android app. It is the same Vectorae app you use in a web browser, packaged to run on an Android phone, with a few phone-specific additions such as push notifications. This article explains what the app does, how it differs from the web version, and what it cannot do.

## What is the Android app?

The Android app is built from the same code as the web app, using a tool called Capacitor that wraps the web app for Android. Its app ID is `com.clienthunter.app` and its name on the phone is Vectorae.

Because it is the same code, the screens are the same as on the web: Pipeline, Inbox, CRM, Campaigns, Calendar, Meetings, Strategies, Analytics, Network, Learn, Team, Clients, Profile and Settings. On a phone these appear as a bar of tabs along the bottom of the screen instead of a side menu.

There is no iPhone (iOS) app.

## Does my phone need to be on for campaigns to run?

No. All the work happens on Vectorae's servers: research, lead sourcing, sending emails and WhatsApp messages, follow-ups and bookings. The app is a window onto that server. It does not send messages itself and does not run campaign logic on the phone. A phone that is switched off, offline or has the app closed does not pause anything.

To actually pause sending, use **Pause campaign** on the Campaigns page, in the app or on the web.

## What is different from the web version?

The app adds a few things that only make sense on a phone:

- **Push notifications** – see the next question.
- **Back button** – the Android back button goes back one screen. On a main section (such as Strategies, Campaigns, Analytics or Settings) it asks "Exit Vectorae?" before closing the app.
- **Offline banner** – if your phone has no connection, a banner says "No connection" and "Scheduled sends run on Vectorae's servers, not on this device, so losing your connection does not stop them. You'll be back in sync when connected." If the phone is online but cannot reach Vectorae's server, it says "Server temporarily unreachable" and "We can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically." The same banner appears in the web app.
- **Links** – tapping a notification opens the matching screen in the app.
- **Status bar colour** follows your chosen theme.

The bottom tab bar holds many destinations, so on a narrow phone it scrolls sideways. Swipe the bar to reach the tabs at the end, such as Profile and Settings.

## Which push notifications will I get?

After you sign in, the app asks for permission to show notifications. If you allow it, your phone is registered with Vectorae for push notifications. Vectorae sends push notifications for events such as:

- a strategy ready, or a strategy that needs your review;
- a new reply from a lead;
- a meeting booked;
- a campaign paused automatically because of a high bounce rate;
- a mailbox paused because of poor sending health, or a sending domain found on a blocklist;
- a message held for review;
- today's automatic sending waiting for your approval (at most once a day, with the queued counts per channel);
- a WhatsApp template approved or rejected (sent to administrators only);
- an SDR launch waiting for your approval, or a decision on your own launch.

A campaign you pause yourself does not send you a push notification, because you already know.

Push notifications only work if your Vectorae deployment has push notifications (Firebase) set up. Parts of the push setup in the app are marked in the code as not yet verified against the current version of the push service, so treat push notifications as a convenience, not as the only place you check for replies. Everything they announce is also visible in the app itself.

If you refused permission, you can turn notifications on later in your phone's settings for the Vectorae app.

## How does signing in work on the phone?

You sign in with the same email and password as on the web, and the same rules apply (see Account security). On the phone, your sign-in is kept in the app's private storage on the device, which other apps cannot read. Signing out with **Log out** ends the session on the server too.

## What can't I do in the app?

- You cannot use the app offline to make changes. Creating a strategy, launching a campaign or changing a lead all need a connection to Vectorae's server. Some screens you opened recently may still show their last data while offline.
- There is no iOS version.
- The app does not send outreach from your phone or use your phone's number or SIM. WhatsApp messages go out from the deployment's WhatsApp Business number, through the server.

## Where do I get the app?

The app is built for distribution through the Google Play Store. Whether it is currently published there for your deployment is not confirmed in this help center. Ask your administrator or contact support for the current way to install it. If your team runs its own Vectorae deployment, the app must be built to point at that deployment's server.

## Related
- [What is Vectorae](01-what-is-leadpilot.md)
- [Pausing and stopping outreach](25-pause-stop-kill-switch.md)
- [Account security](23-account-security.md)
- [Replies and the unified inbox](13-replies-and-unified-inbox.md)
- [Known limitations](29-known-limitations.md)
- [Contact support and status](30-contact-support-and-status.md)
