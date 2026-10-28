# M7 Chunks 2 + 3 — Native Behaviors + Push Notifications

## Chunk 2 — Native behaviors

### What was built

| Feature | Files |
|---|---|
| Platform detection | `src/lib/platform.ts` |
| Secure token storage | `src/lib/storage.ts`, `src/lib/auth-session.ts` |
| Deep links (custom scheme + App Links) | `src/lib/deeplinks.ts`, `AndroidManifest.xml` |
| Network state (offline vs backend down) | `src/hooks/useNetwork.ts`, `src/contexts/NetworkContext.tsx` |
| Android back button | `src/hooks/useBackButton.ts` |
| Status bar theming | `src/hooks/useStatusBar.ts` |
| Offline banner (non-blocking) | `src/components/ui/OfflineBanner.tsx` |
| Pull-to-refresh + haptics | removed 2026-10-11: the component was never mounted in any page (docs/program/fixes/product-defects.md #24) |
| Safe area insets | `src/styles/globals.css` |
| Native boot (single entry point) | `src/components/providers/NativeProvider.tsx` |
| App Links verification file | `public/.well-known/assetlinks.json` |
| Asset generation scripts | `scripts/generate-assets.sh`, `scripts/generate-assets.cmd` |
| Capacitor plugins added | `@capacitor/preferences`, `@capacitor/app`, `@capacitor/splash-screen`, `@capacitor/status-bar`, `@capacitor/network`, `@capacitor/haptics` |

### Verifying Chunk 2

```cmd
cd frontend
npm install
npm run mobile:build
npm run mobile:run
```

Check in the emulator / physical device:

- [ ] Splash screen shows, then fades out (not just a white flash)
- [ ] Status bar color matches the app's primary blue (`#2563eb` default)
- [ ] Pull down on leads kanban → spinner appears → haptic fires on physical device
- [ ] Android back button at dashboard root → "Exit Vectorae?" dialog
- [ ] Android back button inside a strategy detail → navigates back to strategy list
- [ ] Turn off WiFi on device → yellow "No connection" banner appears without blocking the app
- [ ] Turn WiFi back on → banner disappears within 30 seconds
- [ ] Open a deep link: `adb shell am start -W -a android.intent.action.VIEW -d "clienthunter:///strategies/1" com.clienthunter.app`
  - App should open and navigate to the strategy detail view

### Token storage migration from M5

M5 used `localStorage` for JWT tokens. M7 Chunk 2 replaces this.

Search and replace in `src/lib/api/` and any auth context files:

```
localStorage.setItem('access_token', ...)   →   setAccessToken(...)    (async)
localStorage.setItem('refresh_token', ...)  →   setRefreshToken(...)   (async)
localStorage.getItem('access_token')        →   await getAccessToken()
localStorage.getItem('refresh_token')       →   await getRefreshToken()
localStorage.removeItem('access_token')     →   (inside clearSession())
```

Import from: `import { setAccessToken, getAccessToken, clearSession } from '@/lib/auth-session'`

### App Links setup (before Chunk 4)

1. Confirm your production domain (update `APP_LINKS_HOST` in `src/lib/deeplinks.ts`)
2. Deploy the Next.js export to that domain — `public/.well-known/assetlinks.json` will be served automatically
3. Fill in the real `sha256_cert_fingerprints` in `assetlinks.json` after signing in Chunk 4
4. Test: `adb shell pm get-app-links com.clienthunter.app`

---

## Chunk 3 — FCM Push Notifications

### What was built

| Component | Files |
|---|---|
| FCM notification service | `backend/app/services/notifications.py` |
| Device token API | `backend/app/api/devices.py` |
| Device token DB model | `backend/app/db/models.py` (DeviceToken) |
| Alembic migration | `backend/alembic/versions/0009_device_tokens.py` |
| Client token lifecycle hook | `frontend/src/hooks/usePushNotifications.ts` |
| Capacitor push plugin | `@capacitor/push-notifications` |
| Firebase setup guide | `FIREBASE_SETUP_CHECKLIST.md` |
| Backend main.py | `/devices` router registered |

### Backend setup

```cmd
REM 1. Complete FIREBASE_SETUP_CHECKLIST.md (console steps + credential download)
REM 2. Set env var
set FIREBASE_CREDENTIALS_PATH=C:\path\to\firebase-service-account.json

REM 3. Run migration
cd backend
alembic upgrade head

REM 4. Restart backend
docker-compose up --build
```

### Wiring notification calls into existing services

The notification functions are ready — they just need to be called at the right moments.
Add these lines to the existing service files (do NOT restructure the services):

```python
# In app/services/pipeline.py — after strategy.status = 'complete':
from app.services.notifications import notify_strategy_ready
await notify_strategy_ready(user_id=strategy.user_id, strategy_id=strategy.id, db=db)

# In app/services/pipeline.py — after verification permanently fails:
from app.services.notifications import notify_strategy_needs_review
await notify_strategy_needs_review(user_id=strategy.user_id, strategy_id=strategy.id, db=db)

# In app/api/webhooks.py — inside the Calendly meeting_created handler:
from app.services.notifications import notify_meeting_booked
await notify_meeting_booked(user_id=..., lead_id=..., attendee_name=event.name, db=db)

# In app/services/outreach.py — after inserting a 'replied' outcome:
from app.services.notifications import notify_new_reply
await notify_new_reply(user_id=..., lead_id=lead.id, company=lead.company, db=db)

# In app/api/campaigns.py — after auto-pausing a campaign:
from app.services.notifications import notify_campaign_paused
await notify_campaign_paused(user_id=..., campaign_id=..., db=db)

# In app/services/whatsapp.py — after receiving a template status webhook:
from app.services.notifications import notify_whatsapp_template_status
await notify_whatsapp_template_status(user_id=..., template_name=..., approved=True, db=db)
```

### Testing push on device

```cmd
REM Physical device required (emulator without Google Play Services won't receive FCM)
npm run mobile:run

REM Then trigger a notification from the backend — easiest test:
REM POST /devices/register from the app (happens automatically on startup)
REM Then call notify_strategy_ready() from a Python shell:
cd backend
python -c "
import asyncio
from app.services.notifications import notify_strategy_ready
asyncio.run(notify_strategy_ready(user_id=1, strategy_id=1))
"
```

Watch for the push notification to appear on the device. Tap it → app opens at strategy detail.

---

## What M7's first 3 chunks have delivered

| Chunk | Delivered |
|---|---|
| **1 — Foundation** | Capacitor installed, Next.js static export, Android platform, CORS, dev/prod build profiles |
| **2 — Native behaviors** | Secure storage, deep links, back button, status bar, offline banner, pull-to-refresh, safe areas, splash screen, asset generation |
| **3 — Push notifications** | Firebase setup, backend FCM service, device token table + API, client token lifecycle, 6 notification event types |

## Chunk 4 — What comes next (signing + Play Store release)

- Generate a release signing keystore (keep this FOREVER — it is tied to your Play Store identity)
- Configure Gradle to use the keystore for release builds
- Build a release AAB (Android App Bundle — Play Store prefers this over APK)
- Fill in `sha256_cert_fingerprints` in `assetlinks.json` (App Links go live)
- Play Store: create app listing, upload AAB, complete content rating, privacy policy URL
- Internal testing track → closed testing → production release
- Final test checklist: back button, deep links, push, offline, theme, onboarding flow
