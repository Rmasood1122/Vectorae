#!/bin/bash
# nginx/certbot-setup.sh — Obtain initial SSL certificates via Let's Encrypt
#
# Usage:
#   bash nginx/certbot-setup.sh yourdomain.com your-email@example.com
#
# Run BEFORE starting nginx with SSL config.
# After first-run, certbot renews automatically (cron or systemd timer).

set -e

DOMAIN="${1:?Usage: $0 <domain> <email>}"
EMAIL="${2:?Usage: $0 <domain> <email>}"
API_DOMAIN="api.${DOMAIN}"

echo "================================================================"
echo " Vectorae — SSL Certificate Setup"
echo " Domain:     ${DOMAIN}"
echo " API Domain: ${API_DOMAIN}"
echo " Email:      ${EMAIL}"
echo "================================================================"

# Install certbot if not present
if ! command -v certbot &>/dev/null; then
    echo "[1/4] Installing certbot…"
    apt-get update -q && apt-get install -y -q certbot python3-certbot-nginx
fi

# Create web root directory for ACME challenge
mkdir -p /var/www/certbot

echo "[2/4] Obtaining certificates for ${DOMAIN} and ${API_DOMAIN}…"

# Standalone mode — requires ports 80 and 443 to be free
certbot certonly \
    --standalone \
    --non-interactive \
    --agree-tos \
    --email "${EMAIL}" \
    -d "${DOMAIN}" \
    -d "www.${DOMAIN}" \
    -d "${API_DOMAIN}" \
    --preferred-challenges http

echo "[3/4] Setting up auto-renewal cron job…"
(crontab -l 2>/dev/null || true; echo "0 0 * * * certbot renew --quiet --post-hook 'docker-compose -f /app/docker-compose.prod.yml exec nginx nginx -s reload'") | crontab -

echo "[4/4] Done."
echo ""
echo "Certificates installed at:"
echo "  /etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
echo "  /etc/letsencrypt/live/${DOMAIN}/privkey.pem"
echo "  /etc/letsencrypt/live/${API_DOMAIN}/fullchain.pem"
echo ""
echo "Now update nginx/nginx.conf with your actual domain names, then:"
echo "  docker-compose -f docker-compose.prod.yml up -d"
