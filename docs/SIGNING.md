# SIGNING.md — Vectorae Android Signing

> **This file documents a ONE-TIME manual step you must perform before the first Play Store release.**
> The keystore you create here is the PERMANENT identity of your app on the Play Store.
> It cannot be recovered, replaced, or transferred. If it is lost, you must publish a new app and lose all installs and reviews.

---

## What is a keystore and why does it matter?

Android requires all APKs and AABs to be signed with a private key before they can be installed or uploaded to the Play Store. The Play Store uses the signing key to verify that updates come from the original publisher — **every update you ever release must be signed with the same key**. If the keystore file or its passwords are lost, you cannot update the app.

---

## Step 1 — Generate the release keystore

Run this command **once** on your development machine. Replace the placeholders:

```bash
keytool -genkey -v \
  -keystore clienthunter-release.keystore \
  -alias clienthunter \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000
```

**Windows CMD:**
```cmd
keytool -genkey -v -keystore clienthunter-release.keystore -alias clienthunter -keyalg RSA -keysize 2048 -validity 10000
```

`keytool` is bundled with the JDK — it is in `%JAVA_HOME%\bin\` (Windows) or on PATH if JDK is installed.

You will be prompted for:
- **Keystore password** — choose a strong random password (≥20 chars). Write it down now.
- **Key password** — can be the same as keystore password for simplicity.
- **Distinguished name fields** — your name, organisation, city, country. These appear in the cert; they do not appear in the Play Store listing. Fill them accurately but they are not public-facing.

Parameters explained:
- `-validity 10000` — ~27.4 years. TODO: verify current Google minimum validity period for Play Store apps. As of 2021, apps must be valid past October 22, 2033; 10,000 days from any 2024+ generation date satisfies this comfortably.
- `-keysize 2048` — RSA 2048 is the current Play Store minimum. TODO: verify if RSA 4096 is now recommended.
- `-keyalg RSA` — required algorithm. TODO: verify if EC (ECDSA) is supported as an alternative.

---

## Step 2 — Store it securely (CRITICAL)

### DO NOT put the keystore in:
- ❌ The git repository (ever, even in a branch)
- ❌ OneDrive, Google Drive, Dropbox, or any cloud sync folder — if the cloud service is breached, your signing key is exposed
- ❌ A shared Slack/Teams channel or email attachment

### DO put the keystore in:
- ✅ **Primary:** A password manager that supports file attachments (1Password, Bitwarden, KeePass). Store the keystore file AND both passwords as a single vault entry named "Vectorae Android Release Keystore".
- ✅ **Backup:** An encrypted offline backup. Recommended approach:
  - Create a VeraCrypt container (https://www.veracrypt.fr) on a USB drive
  - Copy the keystore into the container
  - Store the USB in a physically secure location (safe, fireproof box)
  - Test the backup: mount the container and verify the keystore opens

If you are a team, exactly one person holds the keystore and CI uses the base64-encoded version (Step 4). The raw file never goes to CI as a file.

---

## Step 3 — Set local environment variables

For local machine builds, create `frontend/android/local.properties` (gitignored — see `local.properties.example`):

```properties
sdk.dir=C:\\Users\\<you>\\AppData\\Local\\Android\\Sdk
KEYSTORE_PATH=C:\\Users\\<you>\\secrets\\clienthunter-release.keystore
KEYSTORE_PASSWORD=<your-keystore-password>
KEY_ALIAS=clienthunter
KEY_PASSWORD=<your-key-password>
```

Add to your backend `.env` as well (the backend does not use these, but they are documented here for the full picture):

```env
KEYSTORE_PATH=C:\Users\<you>\secrets\clienthunter-release.keystore
KEYSTORE_PASSWORD=<your-keystore-password>
KEY_ALIAS=clienthunter
KEY_PASSWORD=<your-key-password>
```

---

## Step 4 — Encode for CI (GitHub Actions / Gitea / Railway)

CI cannot access your local filesystem, so the keystore is base64-encoded and stored as a CI secret:

**Encode the keystore:**

```bash
# Linux / macOS
base64 -i clienthunter-release.keystore | tr -d '\n'

# Windows PowerShell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("clienthunter-release.keystore"))
```

Copy the output (it will be a very long string with no newlines).

**Add these secrets to GitHub Actions** (Settings → Secrets → Actions):

| Secret name | Value |
|---|---|
| `KEYSTORE_BASE64` | The base64 string from the command above |
| `KEYSTORE_PASSWORD` | Your keystore password |
| `KEY_ALIAS` | `clienthunter` |
| `KEY_PASSWORD` | Your key password |
| `NEXT_PUBLIC_API_URL_PROD` | `https://api.clienthunter.app` (your cloud backend URL) |
| `FIREBASE_GOOGLE_SERVICES_JSON_BASE64` | base64 of `android/app/google-services.json` |

The CI workflow (`android-release.yml`) decodes `KEYSTORE_BASE64` to a RAM-backed temp directory, uses it to sign the AAB, and deletes it immediately — the raw keystore never touches the CI disk in a persistent way.

**Why base64 and not a file upload?**
GitHub Actions secrets are strings, not files. Base64 converts the binary keystore to a string that can be stored as a secret and decoded back to the exact binary at build time.

---

## Step 5 — Play App Signing (optional but recommended)

After the first AAB upload, Google offers to manage your signing key for you (Play App Signing). If you opt in:
- Google re-signs the final APK delivered to users with a Google-managed key
- Your upload key (the one you created above) is used only to authenticate uploads
- If your upload key is compromised, Google can re-issue it — the app is still deliverable
- This is the safest long-term option; highly recommended

TODO: verify current Play App Signing enrollment flow in the Play Console.

---

## Step 6 — Verify the keystore

Before relying on it for production, verify the keystore is valid:

```bash
keytool -list -v -keystore clienthunter-release.keystore -alias clienthunter
```

Output should show:
- Entry type: `PrivateKeyEntry`
- Certificate fingerprint (SHA-256): **copy this value** — you will need it for `assetlinks.json` (App Links verification, filled in below)

Update `frontend/public/.well-known/assetlinks.json`:
```json
"sha256_cert_fingerprints": [
  "AA:BB:CC:DD:..."   ← paste the SHA-256 from keytool -list output here
]
```

---

## Quick reference — the 5 values that matter

| Variable | What it is | Where it lives |
|---|---|---|
| `KEYSTORE_PATH` | Absolute path to the `.keystore` file | `local.properties` (local) / decoded from CI secret |
| `KEYSTORE_PASSWORD` | Password for the keystore file itself | `local.properties` (local) / CI secret |
| `KEY_ALIAS` | Alias of the key entry inside the keystore | `local.properties` (local) / CI secret |
| `KEY_PASSWORD` | Password for the specific key entry | `local.properties` (local) / CI secret |
| `KEYSTORE_BASE64` | Base64-encoded keystore file | CI secret only — never in a file |
