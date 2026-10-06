#!/usr/bin/env bash
# Catalog defaults that depend on the server's memory, written into .env (deploy.sh runs this).
#   bash scripts/memory_defaults.sh [.env] [/proc/meminfo]
# Under 6 GB (the 4 GB CPX21): no Amazon, and the importer keeps its 1800m cap, because Amazon's import ran
# that box out of memory. From 6 GB up (an 8 GB rescale): the importer may use 4g, and Amazon can be turned
# on by setting CATALOG_SOURCE=asos,polyvore,amazon in .env. Values Renee set by hand are kept, except the
# two that would sink a small box.
set -euo pipefail
ENV_FILE=${1:-.env}
MEMINFO=${2:-/proc/meminfo}
touch "$ENV_FILE"

mem_kb=$(awk '/^MemTotal:/ {print $2}' "$MEMINFO")
if [ "${mem_kb:-0}" -lt $((6 * 1024 * 1024)) ]; then
  echo "== $((mem_kb / 1024)) MB of RAM: ASOS + Polyvore only, importer capped at 1800m"
  sed -i 's/^CATALOG_SOURCE=asos,polyvore,amazon$/CATALOG_SOURCE=asos,polyvore/' "$ENV_FILE"
  sed -i '/^IMPORTER_MEM_LIMIT=/d' "$ENV_FILE"  # docker-compose.yml's default, 1800m
else
  echo "== $((mem_kb / 1024)) MB of RAM: the importer may use 4g (Amazon: CATALOG_SOURCE=asos,polyvore,amazon)"
  grep -qE '^IMPORTER_MEM_LIMIT=.+' "$ENV_FILE" || echo "IMPORTER_MEM_LIMIT=4g" >> "$ENV_FILE"
fi
