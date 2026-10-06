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
set_default DAILY_LOOK_LIMIT 50
set_default DAILY_TRYON_LIMIT 20
set_default DAILY_STYLIST_LIMIT 100
set_default CATALOG_SOURCE asos,polyvore,amazon  # a value already in .env (e.g. asos,polyvore) is kept
grep -qE '^ANTHROPIC_API_KEY=.+' .env || echo "!! ANTHROPIC_API_KEY is empty: the site will use the offline fake model"

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
echo "== building and starting the site"
$COMPOSE build
$COMPOSE up -d --wait caddy
# The worker, and the one-off catalog importer: a new catalog is imported in its own process while the
# site keeps serving the old one, then swapped in. Follow it with: docker compose logs -f importer
$COMPOSE up -d worker importer

SITE=$(grep -E '^SITE_ADDRESS=' .env | cut -d= -f2)
CODE=$(grep -E '^ACCESS_CODE=' .env | cut -d= -f2)
echo
echo "Lookmate is live:  https://$SITE"
echo "Access code:       $CODE"
echo "Catalog:           https://$SITE/healthz  (\"pending\": true while the importer is still working)"
