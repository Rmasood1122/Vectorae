# Firebase Setup Checklist — Vectorae Push Notifications

Complete these steps in the Firebase console before running the backend
notification service. All steps are user actions (not code).

TODO: verify against current Firebase console layout — Google reorganises the
console periodically.

---

## Step 1 — Create Firebase project

1. Go to https://console.firebase.google.com
2. Click **Add project**
3. Name: `clienthunter-enterprise` (or your preferred name)
4. Disable Google Analytics if not needed (saves complexity)
5. Click **Create project**

---

## Step 2 — Add your Android app

1. In the project overview, click the **Android** icon (Add app)
2. **Android package name**: `com.clienthunter.app`
   ⚠️ This must EXACTLY match the `appId` in `frontend/capacitor.config.ts`
3. **App nickname**: ClientHunter
4. **Debug signing certificate SHA-1**: optional for push, required for some features
   - Get it: `cd frontend/android && ./gradlew signingReport`
   - TODO: verify gradle command for current Android Gradle Plugin version
5. Click **Register app**
6. **Download `google-services.json`** → place at `frontend/android/app/google-services.json`

⚠️ `google-services.json` is in `.gitignore` — it contains API keys.
   Every developer must download their own copy (project-level credentials are shared
   but the file itself is not committed). Add to team 1Password / secrets manager.

---

## Step 3 — Enable Cloud Messaging

1. In the Firebase console left sidebar: **Build → Cloud Messaging**
2. If prompted, enable the Cloud Messaging API
3. Note the **Server key** (or use the newer project credentials approach):
   - Old approach (deprecated but still works): copy the **Server key** from Cloud Messaging settings
   - New approach (recommended): use a **service account** — see Step 4
   - TODO: verify current Firebase Admin SDK auth approach (server key vs service account)

---

## Step 4 — Download service account credentials (for backend)

1. In Firebase console: **Project settings** (gear icon) → **Service accounts**
2. Click **Generate new private key**
3. Download the JSON file — example filename: `clienthunter-firebase-adminsdk-xxxxx.json`
4. Store it somewhere OUTSIDE the git repo (e.g. `/home/user/secrets/`)
5. Set the env var in your backend `.env`:
   ```
   FIREBASE_CREDENTIALS_PATH=/home/user/secrets/clienthunter-firebase-adminsdk-xxxxx.json
   ```
6. On Railway/Render: store the JSON contents as a secret env var, write it to a
   temp file on startup. A helper script for this is in the deployment docs.

---

## Step 5 — Wire `google-services.json` into the Android build

The file downloaded in Step 2 must be at `frontend/android/app/google-services.json`.
This is already in `.gitignore`. Run:

```cmd
REM Windows CMD
copy path\to\downloaded\google-services.json frontend\android\app\google-services.json
```

Then:

```cmd
npm run mobile:build
npm run mobile:open
REM Run the app in Android Studio — FCM will initialise on startup
```

---

## Step 6 — Test push on a physical device

FCM push notifications **do not work on Android emulators** without Google Play Services.
To test:

1. Connect a physical Android device with USB debugging on
2. `npm run mobile:run`
3. Watch the backend log for: `FCM sent to token …`
4. The notification should appear on the device

Alternative: **Firebase Test Lab** (cloud device farm — requires billing).
TODO: verify Firebase Test Lab setup for the current console layout.

---

## Environment variables added by Chunk 3

Add these to `backend/.env`:

```env
# Firebase Admin SDK (M7 Chunk 3 — push notifications)
FIREBASE_CREDENTIALS_PATH=/path/to/your/firebase-service-account.json
```

The `backend/.env.example` has been updated with this entry.

---

## iOS note (out of scope for M7)

iOS push notifications require:
- An Apple Developer account ($99/year)
- An APNs (Apple Push Notification service) certificate or key
- Upload the APNs key to Firebase: **Project settings → Cloud Messaging → Apple app configuration**
- The Capacitor `ios/` platform added (`npx cap add ios`)
- A Mac + Xcode for building

None of this is in M7 scope. When you are ready, the backend notification service
(`notifications.py`) already supports iOS — `send_to_device` uses FCM which handles
both Android and iOS.
