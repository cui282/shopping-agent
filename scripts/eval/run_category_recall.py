"""Evaluate operator-exported CategoryInsight retrieval predictions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.eval.recall_metrics import assert_release_gate, evaluate_recall


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, required=True, help="JSONL with retrieved/relevant IDs"
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--min-recall", type=float, default=0.75)
    parser.add_argument("--min-mrr", type=float, default=0.65)
    parser.add_argument("--min-ndcg", type=float, default=0.70)
    return parser.parse_args()


def main() -> int:
    args = _args()
    samples: list[tuple[list[str], list[str]]] = []
    for line_number, line in enumerate(args.input.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            samples.append((list(row["retrieved"]), list(row["relevant"])))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise SystemExit(f"invalid evaluation row at line {line_number}") from exc
    evaluation = evaluate_recall(samples, k=args.top_k)
    gate = assert_release_gate(
        evaluation,
        min_recall=args.min_recall,
        min_mrr=args.min_mrr,
        min_ndcg=args.min_ndcg,
    )
    print(f"Recall@{args.top_k} = {evaluation.recall_at_k:.3f}")
    print(f"MRR          = {evaluation.mrr:.3f}")
    print(f"NDCG@{args.top_k}   = {evaluation.ndcg_at_k:.3f}")
    if not gate.passed:
        print(f"release gate failed: {', '.join(gate.failures)}")
        return 1
    print("release gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
