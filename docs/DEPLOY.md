# DEPLOY.md — Vectorae

Zero-to-live deployment: from a fresh clone to a production system with Android app.
Follow each section in order.

---

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Docker + Docker Compose | Latest | https://docs.docker.com/get-docker |
| Python | 3.11+ | https://python.org |
| Node.js | 18 LTS | https://nodejs.org |
| Android Studio | Latest stable | https://developer.android.com/studio |
| JDK | 17 | Bundled with Android Studio or https://adoptium.net |
| git | Any | System package manager |

---

## Step 1 — Clone and configure

```cmd
git clone <your-repo-url>
cd clienthunter-enterprise

REM Copy env template
copy .env.example .env

REM Edit .env — fill in ALL values (see comments inside the file):
REM   DATABASE_URL, REDIS_URL, ANTHROPIC_API_KEY
REM   APOLLO_API_KEY, HUNTER_API_KEY
REM   GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET
REM   WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_BUSINESS_ACCOUNT_ID
REM   WHATSAPP_VERIFY_TOKEN
REM   CALENDLY_API_KEY, CALENDLY_WEBHOOK_SECRET
REM   JWT_SECRET (generate: python -c "import secrets; print(secrets.token_urlsafe(48))")
REM   CORS_ORIGINS=https://yourdomain.com
REM   FIREBASE_CREDENTIALS_PATH=/path/to/firebase-service-account.json
notepad .env
```

---

## Step 2 — Deploy backend to Railway / Render / your server

### Option A — Railway (recommended for solo founders)

1. Install Railway CLI: `npm install -g @railway/cli`
2. `railway login`
3. `railway init` (creates a new project)
4. Add PostgreSQL and Redis plugins in the Railway dashboard
5. Set all env vars from `.env` in Railway → Variables tab
6. `railway up` — deploys the backend

Note the deployed URL (e.g. `https://clienthunter-production.up.railway.app`).

### Option B — Docker Compose (self-hosted VPS)

```bash
# On your VPS (Ubuntu 22.04 recommended)
git clone <your-repo-url> && cd clienthunter-enterprise
cp .env.example .env && nano .env   # fill in all values

docker compose up --build -d

# Run DB migrations
docker compose exec api alembic upgrade head

# Verify
curl https://yourserver.com/health
# → {"status":"ok","version":"0.7.0"}
```

### Verify backend is live

```bash
curl https://your-backend-url/health
# → {"status":"ok","version":"0.7.0"}
```

---

## Step 3 — Run database migrations

```cmd
REM If using Railway:
railway run alembic upgrade head

REM If using Docker Compose:
docker compose exec api alembic upgrade head

REM Verify migrations ran (should show 0009_device_tokens as the latest):
alembic current
```

---

## Step 4 — Configure Firebase (push notifications)

See `FIREBASE_SETUP_CHECKLIST.md` for the full walkthrough.

Quick summary:
1. Create Firebase project at https://console.firebase.google.com
2. Add Android app with package name `com.clienthunter.app`
3. Download `google-services.json` → `frontend/android/app/google-services.json`
4. Download service account JSON → set `FIREBASE_CREDENTIALS_PATH` in your backend `.env`
5. Restart the backend

---

## Step 5 — Deploy web frontend (optional — for the browser UI)

```cmd
cd frontend
npm install
npm run build

REM Deploy the `out/` directory to:
REM   Vercel:   vercel deploy --prod
REM   Netlify:  netlify deploy --dir=out --prod
REM   Railway:  configure static site pointing to out/
REM   Any CDN:  upload the out/ directory
```

Set `NEXT_PUBLIC_API_URL` to your backend URL before building.

---

## Step 6 — Generate release signing keystore (ONCE)

See `SIGNING.md` for full instructions.

```cmd
keytool -genkey -v -keystore clienthunter-release.keystore -alias clienthunter -keyalg RSA -keysize 2048 -validity 10000

REM Store securely in your password manager (see SIGNING.md)
REM Add base64-encoded version to CI secrets:
REM   powershell: [Convert]::ToBase64String([IO.File]::ReadAllBytes("clienthunter-release.keystore"))
```

---

## Step 7 — Configure CI secrets

In GitHub → Settings → Secrets → Actions, add:

| Secret | Value |
|---|---|
| `KEYSTORE_BASE64` | Output of base64 encode command above |
| `KEYSTORE_PASSWORD` | Your keystore password |
| `KEY_ALIAS` | `clienthunter` |
| `KEY_PASSWORD` | Your key password |
| `NEXT_PUBLIC_API_URL_PROD` | Your backend URL |
| `FIREBASE_GOOGLE_SERVICES_JSON_BASE64` | base64 of `google-services.json` |

---

## Step 8 — Build signed AAB via CI

Push to `main` or create a `release/v0.7.0` tag:

```cmd
git tag release/v0.7.0
git push origin release/v0.7.0
```

GitHub Actions runs `.github/workflows/android-release.yml` automatically.
Download the `clienthunter-prod-release-*.aab` artifact from the Actions run.

---

## Step 9 — Upload to Play Store and publish

See `PLAY_STORE_CHECKLIST.md` for the full walkthrough.

Quick summary:
1. Create app in Play Console (one-time)
2. Complete store listing, content rating, data safety form
3. Upload the AAB to the Internal Testing track
4. Test on a real device from the internal testing link
5. Create Production release → submit for review
6. Wait 1–7 days for review, then publish

---

## Step 10 — Verify end-to-end

After publish:

- [ ] App installs from Play Store
- [ ] Login works against production backend
- [ ] Strategy pipeline starts when initiated
- [ ] Push notification received when strategy completes
- [ ] Deep link in notification opens correct in-app view
- [ ] Meeting booking via Calendly works and updates lead status
- [ ] `/privacy` page accessible at `https://app.clienthunter.com/privacy`

---

## Updating after first release

```cmd
REM 1. Make changes, bump versionCode in android/app/build.gradle (increment by 1)
REM    Also bump versionName if this is a user-facing feature release

REM 2. Push to main → CI builds new signed AAB

REM 3. Download AAB from CI artifact

REM 4. Play Console → Production → Create new release → upload new AAB → submit
```

---

## Environment variable reference (complete list)

See `.env.example` for all variables with comments. Required for a live system:

**Backend runtime:**
`DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, `ANTHROPIC_API_KEY`,
`APOLLO_API_KEY`, `HUNTER_API_KEY`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`,
`WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_BUSINESS_ACCOUNT_ID`,
`WHATSAPP_VERIFY_TOKEN`, `CALENDLY_API_KEY`, `CALENDLY_WEBHOOK_SECRET`,
`JWT_SECRET`, `CORS_ORIGINS`, `FIREBASE_CREDENTIALS_PATH`

**CI / build:**
`KEYSTORE_BASE64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`,
`NEXT_PUBLIC_API_URL_PROD`, `FIREBASE_GOOGLE_SERVICES_JSON_BASE64`
