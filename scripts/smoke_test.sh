#!/usr/bin/env bash
# End-to-end smoke test against a running stack (docker compose up).
# Creates a profile, uploads a look, waits for the worker, and checks the gateway's rate limit.
set -euo pipefail
BASE=${BASE:-http://localhost:8080}
json() { python3 -c "import sys, json; print(json.load(sys.stdin)$1)"; }

echo "== health";  curl -fsS "$BASE/healthz"; echo
USER_ID=$(curl -fsS -X POST "$BASE/api/users" -H 'content-type: application/json' \
  -d '{"nickname":"smoke","height_cm":165,"weight_kg":55,"age":26,"body_shape":"pear","preferred_styles":["old_money"],"budget_per_item":30}' | json '["id"]')
echo "== created user $USER_ID"

IMG=$(mktemp --suffix=.png); printf '\x89PNG\r\n\x1a\nsmoke-%s' "$RANDOM$RANDOM" > "$IMG"
LOOK_ID=$(curl -fsS -F "user_id=$USER_ID" -F "image=@$IMG;type=image/png" "$BASE/api/looks" | json '["id"]')
echo "== uploaded look $LOOK_ID"
for _ in $(seq 1 60); do
  STATUS=$(curl -fsS "$BASE/api/looks/$LOOK_ID" | json '["status"]')
  [[ "$STATUS" == done || "$STATUS" == failed ]] && break
  sleep 1
done
[[ "$STATUS" == done ]] || { echo "look ended as $STATUS"; exit 1; }
PICKS=$(curl -fsS "$BASE/api/looks/$LOOK_ID" | json '["result"]["sections"][0]["picks"].__len__()')
echo "== look done, first item has $PICKS picks"
(( PICKS > 0 ))

TRENDS=$(curl -fsS "$BASE/api/trends" | json '["trends"].__len__()')
echo "== $TRENDS trends"

CODES=$(for _ in $(seq 1 15); do curl -s -o /dev/null -w "%{http_code}\n" -F "user_id=$USER_ID" -F "image=@$IMG;type=image/png" "$BASE/api/looks"; done)
grep -q 429 <<<"$CODES" || { echo "gateway did not rate-limit uploads"; exit 1; }
echo "== gateway rate limit ok"
echo "SMOKE TEST PASSED"
