# DEPLOY.md — Victorae Deployment Guide

This document covers the complete path from a fresh Ubuntu 24.04 VPS to a running production instance. Read it fully before starting.

---

## Prerequisites

| Requirement | Minimum | Recommended |
|---|---|---|
| VPS RAM | 2 GB | 4 GB |
| VPS CPU | 2 vCPU | 4 vCPU |
| Disk | 40 GB SSD | 80 GB SSD |
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| Docker | 24+ | Latest |
| Domain | Required (for SSL) | — |

**Suggested providers**: DigitalOcean Droplet, Railway, Render, AWS EC2 t3.small.

---

## Step 1 — Provision the Server

```bash
# On your local machine — upload code to the server
rsync -az --exclude='.git' --exclude='node_modules' --exclude='__pycache__' \
  ./ root@YOUR_SERVER_IP:/app/

ssh root@YOUR_SERVER_IP
cd /app
```

---

## Step 2 — Install Docker

```bash
# On the server
apt-get update -y
apt-get install -y ca-certificates curl gnupg
curl -fsSL https://get.docker.com | sh
docker --version   # verify
docker compose version   # verify (v2 plugin)
```

---

## Step 3 — Configure Environment

```bash
cp .env.production.example .env.production
nano .env.production   # fill in every REQUIRED value
```

Minimum required values to fill:
- `SECRET_KEY` — generate with `python3 -c "import secrets; print(secrets.token_hex(64))"`
- `ENCRYPTION_KEY` — generate with `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `POSTGRES_PASSWORD` — strong random password
- `REDIS_PASSWORD` — strong random password
- `DATABASE_URL` — use the postgres password above
- `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` — use redis password above
- `ANTHROPIC_API_KEY` — your Anthropic API key
- All integration API keys (Apollo, Hunter, Gmail, WhatsApp, Calendly, Firebase)

---

## Step 4 — Obtain SSL Certificates

```bash
# Replace with your actual domain and email
bash nginx/certbot-setup.sh yourdomain.com your@email.com
```

Then update `nginx/nginx.conf` — replace `yourdomain.com` and `api.yourdomain.com` with your real domains.

---

## Step 5 — Build and Start

```bash
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

Monitor startup:
```bash
docker compose -f docker-compose.prod.yml logs -f api
```

Wait until you see: `Application startup complete.`

---

## Step 6 — Run Migrations

Migrations run automatically on API container startup. Verify:
```bash
docker compose -f docker-compose.prod.yml exec api alembic current
```
Should show: `0009_m8c3 (head)`

---

## Step 7 — Create the First Admin User

```bash
docker compose -f docker-compose.prod.yml exec api \
  python -m app.cli.create_admin --email admin@yourdomain.com
# Enter and confirm your password when prompted
```

---

## Step 8 — Verify Deployment

```bash
# Health check
curl https://api.yourdomain.com/health

# Expected response:
# {"status": "ok", "components": {"database": {"status": "ok"}, "redis": {"status": "ok"}, "celery": {"status": "ok", "last_beat_seconds_ago": ...}}}
```

---

## Step 9 — Configure Webhooks

### WhatsApp
1. In Meta Business Suite → your App → WhatsApp → Configuration
2. Set Callback URL: `https://api.yourdomain.com/api/v1/webhooks/whatsapp`
3. Set Verify Token to the value of `WHATSAPP_VERIFY_TOKEN` in your `.env.production`
4. Subscribe to: `messages`, `message_deliveries`, `message_statuses`

### Calendly
1. The API server auto-registers the Calendly webhook on startup if `CALENDLY_CLIENT_ID` is set
2. Verify: `GET https://api.yourdomain.com/health/channels` shows `calendly.webhook_configured: true`

---

## Monitoring

| URL | Purpose | Auth |
|---|---|---|
| `GET /health` | Core health check | Public |
| `GET /health/channels` | Integration status | User JWT |
| `GET /admin/health` | Full system health | Admin JWT |
| `GET /admin/task-errors` | Celery task failures | Admin JWT |
| `GET /admin/circuit-breakers` | Circuit breaker states | Admin JWT |
| `GET /admin/celery-stats` | Queue depths | Admin JWT |

---

## Updating

```bash
# Pull latest code
git pull  # or rsync from your dev machine

# Rebuild and restart (zero-downtime approach: restart api last)
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d --no-deps celery-worker-pipeline celery-worker-outreach celery-worker-learning celery-beat
docker compose -f docker-compose.prod.yml up -d --no-deps api
```

Migrations run automatically on API restart.

### Moving to the pgvector image (once, for installs made before 2026-10-25)

`docker-compose.prod.yml` now runs PostgreSQL from `pgvector/pgvector:0.8.1-pg16` instead of
`postgres:16-alpine`. That is the same image CI uses, and it is what the help-center search
needs (migration 0094). The new image is Debian-based: it sorts text with glibc, while the old
Alpine image used musl. **Do not start the new image on the old data volume.** Its text indexes
were built in the old order and can silently return wrong results. Move the data with a dump
and restore instead:

```bash
# 1. Before pulling: stop everything that writes, then take a dump with the OLD image.
docker compose -f docker-compose.prod.yml stop api celery-worker-pipeline celery-worker-outreach celery-worker-learning celery-beat
docker exec clienthunter-postgres-1 pg_dump -U clienthunter -d clienthunter -Fc \
  > /var/backups/clienthunter/before_pgvector.dump
docker compose -f docker-compose.prod.yml stop postgres

# 2. Keep the old volume as a fallback, under a new name, and free the original name.
#    Compose prefixes the volume with the project name; this finds it.
VOL=$(docker volume ls -q | grep -E '(^|_)postgres_data$')
echo "$VOL"          # check it is exactly one name before going on
docker compose -f docker-compose.prod.yml rm -f postgres
docker volume create "${VOL}_alpine"
docker run --rm -v "$VOL":/from -v "${VOL}_alpine":/to alpine sh -c "cp -a /from/. /to/"
docker volume rm "$VOL"

# 3. Pull the new compose file and start ONLY the database on a fresh volume.
git pull
docker compose -f docker-compose.prod.yml up -d postgres

# 4. Restore the dump, then start the rest (migrations run on API start).
docker exec -i clienthunter-postgres-1 pg_restore -U clienthunter -d clienthunter \
  < /var/backups/clienthunter/before_pgvector.dump
docker compose -f docker-compose.prod.yml up -d
```

If migration 0094 already ran on the old image, `help_chunks` has no `embedding` column.
Search then stays text-only (BM25 and full-text), and works. Adding the column later is its own
migration. To go back, stop the stack, copy `${VOL}_alpine` back into `$VOL` the same way, and run the
old image.

---

## Troubleshooting

### API won't start
```bash
docker compose -f docker-compose.prod.yml logs api
```
Common causes: wrong DATABASE_URL, missing ENCRYPTION_KEY, port conflict.

### Celery beat not running
```bash
docker compose -f docker-compose.prod.yml logs celery-beat
# Check admin panel → System Health → Celery heartbeat age
```

### WhatsApp webhook not receiving
- Verify Meta has the correct callback URL
- Check `GET /health/channels` for `whatsapp.webhook_reachable`
- Ensure port 443 is open on the server

### Database connection errors
```bash
docker compose -f docker-compose.prod.yml exec postgres psql -U clienthunter -d clienthunter -c "SELECT 1"
```

See `BACKUP.md` for backup and restore procedures.
