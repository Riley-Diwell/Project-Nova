#!/usr/bin/env bash
# Ad-hoc smoke test for the live nova-v2 Cloud Run deploy - covers the paths
# that were NOT exercised by the original /event + /tools/gain curl checks:
# /persona*, /event/continue (need_more round-trip), and a real (non-mock)
# Claude call. Run this yourself - it pulls NOVA_API_KEY from Secret Manager
# into a local shell var and never prints it.
#
# Usage: bash test_live.sh
set -euo pipefail

BASE_URL="https://nova-v2-1021689546881.australia-southeast1.run.app"
PROJECT="project-nova-505909"

API_KEY="$(gcloud secrets versions access latest --secret=nova-api-key --project "$PROJECT")"

hdr=(-H "X-Nova-Api-Key: $API_KEY" -H "Content-Type: application/json")

echo "=== 1. GET /persona (list beliefs) ==="
curl -s "${hdr[@]}" "$BASE_URL/persona" | tee /tmp/persona_list.json
echo -e "\n"

echo "=== 2. GET /persona/graph (knowledge map) ==="
curl -s "${hdr[@]}" "$BASE_URL/persona/graph" | tee /tmp/persona_graph.json
echo -e "\n"

echo "=== 3. POST /persona/consolidate?preview=true (safe, writes nothing) ==="
curl -s -X POST "${hdr[@]}" "$BASE_URL/persona/consolidate?preview=true" | tee /tmp/consolidate_preview.json
echo -e "\n"

echo "=== 4. POST /event - real Claude call (plain voice event) ==="
EVENT_ID="$(python -c 'import uuid; print(uuid.uuid4())')"
BODY=$(cat <<JSON
{
  "event": {
    "type": "voice",
    "id": "$EVENT_ID",
    "timestamp": "$(python -c 'import datetime; print(datetime.datetime.utcnow().isoformat()+"Z")')",
    "text": "Hey Nova, how's it going?"
  },
  "user_state": {
    "confidence": 0.9
  }
}
JSON
)
echo "$BODY" | curl -s -X POST "${hdr[@]}" -d @- "$BASE_URL/event" | tee /tmp/event_plain.json
echo -e "\n"
echo "--> Check the 'speech' field above. If it starts with '[mock] received event'"
echo "    NOVA_MOCK_LLM is set on the deployed service (should NOT be). Any other"
echo "    natural-sounding reply confirms a real Anthropic API call happened."
echo

echo "=== 5. POST /event - try to trigger a need_more (calendar range) pause ==="
EVENT_ID2="$(python -c 'import uuid; print(uuid.uuid4())')"
BODY2=$(cat <<JSON
{
  "event": {
    "type": "voice",
    "id": "$EVENT_ID2",
    "timestamp": "$(python -c 'import datetime; print(datetime.datetime.utcnow().isoformat()+"Z")')",
    "text": "What have I got on next Friday afternoon?"
  },
  "user_state": {
    "confidence": 0.9,
    "calendar_ctx": "free"
  }
}
JSON
)
RESP=$(echo "$BODY2" | curl -s -X POST "${hdr[@]}" -d @- "$BASE_URL/event")
echo "$RESP" | tee /tmp/event_needmore.json
echo -e "\n"

SESSION_ID=$(echo "$RESP" | python -c 'import json,sys; d=json.load(sys.stdin); print(d.get("session_id",""))' 2>/dev/null || true)

if [ -n "${SESSION_ID:-}" ]; then
  echo "=== 6. POST /event/continue - resume session $SESSION_ID ==="
  CONT_BODY=$(cat <<JSON
{
  "session_id": "$SESSION_ID",
  "result": []
}
JSON
)
  echo "$CONT_BODY" | curl -s -X POST "${hdr[@]}" -d @- "$BASE_URL/event/continue" | tee /tmp/event_continue.json
  echo -e "\n"
else
  echo "=== 6. No need_more pause this time (Claude answered directly, e.g. said it"
  echo "    has no calendar access) - status was 'ok' not 'need_more'. Re-run step 5"
  echo "    a couple times, or phrase the voice text to more clearly ask about a"
  echo "    specific date range, until you see status=need_more with a session_id,"
  echo "    then re-run this script (or just step 6 manually) to exercise /event/continue."
fi

echo
echo "Done. Raw responses saved under /tmp/*.json for inspection."
