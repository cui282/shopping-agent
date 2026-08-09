# CategoryInsight retrieval evaluation

The evaluation helper is offline and consumes operator-owned JSONL predictions. Runtime traffic
and credentials are never written to the repository. Each row contains the ordered IDs returned by
the retrieval path and the operator-labeled relevant IDs:

```json
{"query":"旅行三件套","retrieved":["c_017","c_001"],"relevant":["c_001","c_017","c_042"]}
```

Run it with the document's v1 release thresholds:

```bash
uv run python scripts/eval/run_category_recall.py --input /path/to/category_recall_predictions.jsonl
```

The command reports Recall@K, MRR, and NDCG@K and exits non-zero when the release gate fails.
`RERANKER_ENDPOINT` remains an optional inference-only cross-encoder channel; missing or failing
reranking preserves the hybrid order and is surfaced as degraded provider metadata.
