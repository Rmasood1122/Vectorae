# SECURITY.md — Vectorae

This document covers every secret the system holds, where it is stored, how to rotate it, what to do if it is compromised, and the recommended rotation schedule.

---

## Secrets Inventory

| Secret | Storage Location | Encrypted at Rest | Notes |
|---|---|---|---|
| `ENCRYPTION_KEY` | `.env` file + server env | N/A — this IS the encryption key | Must never touch the DB |
| `SECRET_KEY` | `.env` file | No | Used for JWT signing — rotate immediately if compromised |
| Gmail OAuth `refresh_token` | PostgreSQL `integration_tokens.encrypted_value` | Yes (Fernet) | Per user |
| Gmail OAuth `client_secret` | `.env` file | No | Shared across all users |
| WhatsApp `access_token` | PostgreSQL `integration_tokens.encrypted_value` | Yes (Fernet) | Expires ~60 days |
| WhatsApp `app_secret` | `.env` file | No | For webhook signature verification |
| Apollo API key | PostgreSQL `integration_tokens.encrypted_value` | Yes (Fernet) | Per user |
| Hunter API key | PostgreSQL `integration_tokens.encrypted_value` | Yes (Fernet) | Per user |
| Calendly OAuth token | PostgreSQL `integration_tokens.encrypted_value` | Yes (Fernet) | Per user |
| Firebase service account JSON | `.env` as `FIREBASE_CREDENTIALS_JSON` | No | Keep off disk |
| Android signing keystore | `SIGNING.md` | Externally | Stored offline |
| Database password | `.env` file | No | Used in `DATABASE_URL` |
| Redis password | `.env` file | No | Used in `REDIS_URL` |
| `WHATSAPP_VERIFY_TOKEN` | `.env` file | No | Meta webhook verification |
| `CALENDLY_WEBHOOK_SECRET` | `.env` file | No | Calendly webhook HMAC |

---

## ENCRYPTION_KEY: Rotation Procedure

The `ENCRYPTION_KEY` (Fernet symmetric key) encrypts all integration tokens stored in PostgreSQL. Rotate it when: the key is suspected compromised, as a scheduled practice, or after a team member departure.

### Step-by-step rotation

1. **Generate a new key** on the server (not your local machine):
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Save the output — this is `NEW_KEY`.

2. **Set both keys in `.env`** (do NOT remove the old key yet):
   ```
   ENCRYPTION_KEY=<NEW_KEY>
   ENCRYPTION_KEY_PREVIOUS=<OLD_KEY>
   ```

3. **Restart the API server** so the new config is loaded.

4. **Run the rotation endpoint** (admin only):
   ```bash
   curl -X POST https://yourdomain.com/api/v1/admin/rotate-encryption-key \
     -H "Authorization: Bearer <admin_jwt>"
   ```
   This decrypts all tokens with the old key and re-encrypts with the new key. Returns `{"rotated": N, "failed": 0}`.

5. **Verify** that integrations still work:
   - Run a test strategy or trigger a Gmail health check at `GET /health/channels`
   - If any channel reports `needs_reauth`, the user must re-authenticate (rotation did not lose data — tokens may have been unset if they were never encrypted in the first place)

6. **Remove `ENCRYPTION_KEY_PREVIOUS`** from `.env` and restart the server.

7. **Backup the new `.env`** encrypted to your secure backup location (see `BACKUP.md`).

---

## SECRET_KEY: Rotation Procedure

`SECRET_KEY` signs JWTs. Rotating it immediately invalidates all active sessions.

1. Generate a new value: `python -c "import secrets; print(secrets.token_hex(64))"`
2. Update `.env`: `SECRET_KEY=<NEW_VALUE>`
3. Restart the server. All users are logged out and must log in again.
4. If this was a compromise response, also invalidate any long-lived refresh tokens you suspect were stolen (currently requires a DB query to clear specific user tokens).

---

## WhatsApp Access Token: Rotation Procedure

WhatsApp access tokens expire approximately every 60 days (system user tokens can be set to never-expire — use those in production).

1. In Meta Business Suite → System Users → generate a new token.
2. Update the token via the settings UI: `Settings → Integrations → WhatsApp → Update Token`.
3. The new token is encrypted and stored via `TokenStore.set()`.
4. Verify: trigger a test WhatsApp template send from the admin panel.

---

## Gmail OAuth refresh_token: Rotation Procedure

Gmail refresh tokens do not expire unless the user revokes access or there is a long period of inactivity (6 months).

1. If a refresh token is compromised: go to https://myaccount.google.com/permissions → revoke ClientHunter access.
2. The user re-authenticates via `Settings → Integrations → Gmail → Re-connect`.
3. The new refresh token is encrypted and stored automatically.

---

## Compromise Response

If you believe **any** secret has been compromised:

1. **Immediately rotate the affected secret** using the procedure above.
2. **Check logs** at `/admin/task-errors` for any unusual activity in the past 24h.
3. **Check the suppression list** for unexpected additions.
4. **For ENCRYPTION_KEY compromise**: rotate the key immediately (above). Then rotate all downstream integration credentials (Gmail, WhatsApp, Apollo, Hunter, Calendly) because the attacker could have decrypted and read them.
5. **For SECRET_KEY compromise**: rotate immediately. All active user sessions are invalidated. Inform users to log back in.
6. **Notify affected users** if their personal data may have been accessed.
7. **GDPR obligation**: if EU user data was potentially exposed, you have 72 hours to notify your supervisory authority.

---

## Recommended Rotation Schedule

| Secret | Frequency | Trigger |
|---|---|---|
| `ENCRYPTION_KEY` | Every 6 months | Calendar reminder or team member departure |
| `SECRET_KEY` | Every 6 months | Calendar reminder or any compromise |
| WhatsApp access token | Every 60 days (or use never-expire system user token) | Meta expiry |
| Apollo / Hunter API keys | On plan change or team member departure | — |
| Database password | Every 6 months | Calendar reminder |
| Android signing keystore | Never (immutable — losing it means publishing a new app) | See SIGNING.md |

---

## What Is NOT in This Document

- **User passwords**: stored as bcrypt hashes. Not recoverable — users reset via email.
- **Lead PII**: covered by GDPR procedures in `docs/compliance.md`.
- **Backup encryption**: covered in `BACKUP.md`.
