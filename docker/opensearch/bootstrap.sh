#!/bin/sh
set -eu

shopping_agent_opensearch_url="${OPENSEARCH_URL:-http://opensearch:9200}"
shopping_agent_category_index="${OPENSEARCH_CATEGORY_INDEX:-shopping_agent_category_kb}"
shopping_agent_search_pipeline="${OPENSEARCH_SEARCH_PIPELINE:-shopping-agent-hybrid-pipeline}"

curl --silent --show-error --fail \
  --request PUT \
  --header 'Content-Type: application/json' \
  --data-binary '@/config/hybrid-pipeline.json' \
  "${shopping_agent_opensearch_url}/_search/pipeline/${shopping_agent_search_pipeline}"

if curl --silent --show-error --fail --head \
  "${shopping_agent_opensearch_url}/${shopping_agent_category_index}" >/dev/null; then
  printf '%s\n' "OpenSearch index ${shopping_agent_category_index} already exists"
else
  curl --silent --show-error --fail \
    --request PUT \
    --header 'Content-Type: application/json' \
    --data-binary '@/config/category-index.json' \
    "${shopping_agent_opensearch_url}/${shopping_agent_category_index}"
fi
