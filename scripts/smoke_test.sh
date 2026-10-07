#!/usr/bin/env bash
# End-to-end smoke test against a running stack (docker compose up).
# Creates a profile, uploads a look and a selfie, waits for the worker, builds a lookbook,
# and checks the gateway's rate limit.
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
# Dupes are strict (same type, colour and length): a piece may have no match, but then it says so in a note.
EXPLAINED=$(curl -fsS "$BASE/api/looks/$LOOK_ID" | json '["result"]["sections"]' | python3 -c "
import ast, sys
sections = ast.literal_eval(sys.stdin.read())
print(int(bool(sections) and all(s['picks'] or s.get('note') for s in sections)))")
echo "== look done: every piece has picks or a note ($EXPLAINED)"
(( EXPLAINED == 1 ))

TRENDS=$(curl -fsS "$BASE/api/trends" | json '["trends"].__len__()')
echo "== $TRENDS trends"

ANALYSIS_ID=$(curl -fsS -F "photos=@$IMG;type=image/png" "$BASE/api/users/$USER_ID/analyses" | json '["id"]')
for _ in $(seq 1 40); do
  STATUS=$(curl -fsS "$BASE/api/analyses/$ANALYSIS_ID" | json '["status"]')
  [[ "$STATUS" == done || "$STATUS" == failed ]] && break; sleep 1
done
echo "== analysis $ANALYSIS_ID: $STATUS"; [[ "$STATUS" == done ]]
OUTFITS=$(curl -fsS "$BASE/api/users/$USER_ID/lookbook?mode=seasons" | json '["sections"][0]["outfits"].__len__()')
echo "== $OUTFITS spring outfits"; (( OUTFITS > 0 ))

CODES=$(for _ in $(seq 1 15); do curl -s -o /dev/null -w "%{http_code}\n" -F "user_id=$USER_ID" -F "image=@$IMG;type=image/png" "$BASE/api/looks"; done)
grep -q 429 <<<"$CODES" || { echo "gateway did not rate-limit uploads"; exit 1; }
echo "== gateway rate limit ok"
echo "SMOKE TEST PASSED"
