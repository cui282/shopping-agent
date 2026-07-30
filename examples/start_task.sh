#!/usr/bin/env bash
set -euo pipefail

shopping_agent_api_base="${SHOPPING_AGENT_API_BASE:-http://127.0.0.1:8000}"

curl --fail-with-body \
  --request POST \
  --header 'Content-Type: application/json' \
  --data '{"query":"预算 1200 元，找一款轻便降噪耳机，不要皮革","user_id":"local-client","upload_ids":[]}' \
  "${shopping_agent_api_base}/api/task"
