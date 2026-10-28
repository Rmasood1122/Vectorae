# BACKUP.md — Vectorae Backup & Restore

All business-critical data lives in PostgreSQL. Redis is cache/queue — it is NOT backed up (Celery will replay pending tasks on restart; cache repopulates automatically).

---

## What to Back Up

| Data | Location | Criticality |
|---|---|---|
| PostgreSQL database | `postgres_data` Docker volume | CRITICAL |
| `.env.production` | Server `/app/.env.production` | CRITICAL |
| `ENCRYPTION_KEY` | In `.env.production` | CRITICAL (without it, stored tokens are unreadable) |
| SSL certificates | `/etc/letsencrypt/` | High (auto-renewed by certbot) |
| Android signing keystore | Offline storage | CRITICAL (irreplaceable) |

---

## Automated Nightly Backup Script

Install this on your server as a cron job:

```bash
nano /usr/local/bin/clienthunter-backup.sh
```

```bash
#!/bin/bash
# Automated PostgreSQL backup for Vectorae
# Run nightly: 0 2 * * * /usr/local/bin/clienthunter-backup.sh

set -e

BACKUP_DIR=/var/backups/clienthunter
CONTAINER=clienthunter-postgres-1   # adjust if your container name differs
DB_NAME=clienthunter
DB_USER=clienthunter
RETENTION_DAYS=30
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# 1. PostgreSQL dump
echo "[1] Dumping PostgreSQL..."
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc \
  > "$BACKUP_DIR/postgres_${DATE}.dump"

# 2. Compress and encrypt the dump (optional — uses .env.production as additional entropy)
# If you want to encrypt backups, use: gpg --symmetric --cipher-algo AES256

# 3. Backup .env.production (excluding credentials from logs)
cp /app/.env.production "$BACKUP_DIR/env_${DATE}.bak"
chmod 600 "$BACKUP_DIR/env_${DATE}.bak"

# 4. Prune old backups
find "$BACKUP_DIR" -name "postgres_*.dump" -mtime +"$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -name "env_*.bak" -mtime +"$RETENTION_DAYS" -delete

# 5. Upload to offsite storage (example: rsync to a backup VPS or S3)
# Uncomment and configure one of:
# rsync -az "$BACKUP_DIR/" user@backup-server:/backups/clienthunter/
# aws s3 sync "$BACKUP_DIR/" s3://your-bucket/clienthunter-backups/ --only-show-errors

echo "[DONE] Backup written to $BACKUP_DIR"
```

```bash
chmod +x /usr/local/bin/clienthunter-backup.sh
# Add to cron (runs at 2am daily)
(crontab -l 2>/dev/null || true; echo "0 2 * * * /usr/local/bin/clienthunter-backup.sh >> /var/log/clienthunter-backup.log 2>&1") | crontab -
```

---

## Restore from Backup

### Database restore

```bash
# 1. Stop the API (so no writes happen during restore)
docker compose -f docker-compose.prod.yml stop api celery-worker-pipeline celery-worker-outreach celery-worker-learning celery-beat

# 2. Drop and recreate the database
docker exec clienthunter-postgres-1 psql -U clienthunter -c "DROP DATABASE clienthunter;"
docker exec clienthunter-postgres-1 psql -U clienthunter -c "CREATE DATABASE clienthunter;"

# 3. Restore the dump
docker exec -i clienthunter-postgres-1 pg_restore -U clienthunter -d clienthunter \
  < /var/backups/clienthunter/postgres_YYYYMMDD_HHMMSS.dump

# 4. Restart everything
docker compose -f docker-compose.prod.yml up -d
```

### Environment restore

```bash
cp /var/backups/clienthunter/env_YYYYMMDD_HHMMSS.bak /app/.env.production
# Then restart all services
docker compose -f docker-compose.prod.yml up -d
```

---

## Backup Verification (Monthly)

1. Take the latest backup file
2. Spin up a test instance: `docker compose -f docker-compose.prod.yml up -d postgres`
3. Restore the dump to the test instance
4. Run `alembic current` and spot-check key tables:
   ```sql
   SELECT COUNT(*) FROM users;
   SELECT COUNT(*) FROM strategies;
   SELECT COUNT(*) FROM leads;
   SELECT COUNT(*) FROM outcomes;
   ```
5. Confirm counts match expectations

---

## Disaster Recovery Target

| Metric | Target |
|---|---|
| Recovery Point Objective (RPO) | 24 hours (nightly backup) |
| Recovery Time Objective (RTO) | 4 hours |
| Backup retention | 30 days on-server, 90 days offsite |

For stronger RPO (sub-hour), enable PostgreSQL WAL archiving or use a managed database (e.g., Railway Postgres, Supabase, or AWS RDS) with point-in-time recovery.
