#!/usr/bin/env bash
# Deploy or update Lookmate on a fresh Ubuntu server. Run from the project folder on the server:
#   bash scripts/deploy.sh
# Idempotent: safe to re-run after copying new code.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "== installing Docker"
  curl -fsSL https://get.docker.com | sh
fi

touch .env
set_default() {  # add KEY=VALUE to .env unless KEY already has a value
  grep -qE "^$1=.+" .env || { sed -i "/^$1=/d" .env; echo "$1=$2" >> .env; }
}

PUBLIC_IP=$(curl -fsS https://api.ipify.org)
set_default SITE_ADDRESS "${PUBLIC_IP//./-}.sslip.io"
set_default ACCESS_CODE "$(tr -dc 'a-z0-9' </dev/urandom | head -c 8)"
set_default DAILY_LOOK_LIMIT 200
grep -qE '^ANTHROPIC_API_KEY=.+' .env || echo "!! ANTHROPIC_API_KEY is empty: the site will use the offline fake model"

echo "== building and starting (first run downloads the catalog, this takes a few minutes)"
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --wait

SITE=$(grep -E '^SITE_ADDRESS=' .env | cut -d= -f2)
CODE=$(grep -E '^ACCESS_CODE=' .env | cut -d= -f2)
echo
echo "Lookmate is live:  https://$SITE"
echo "Access code:       $CODE"
