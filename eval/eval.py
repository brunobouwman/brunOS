#!/usr/bin/env python3
"""BrainBench-lite — retrieval eval harness for BrunOS memory search (C1).

Runs the labelled queries in queries.jsonl through memory_search.search() with
graph augmentation OFF vs ON and reports file-level P@5 / Recall@k / MRR. This is
the "prove it helps" number for graph traversal (and the gate before any reranker
work), and doubles as a published benchmark artifact.

  uv run python eval/eval.py [--k 10] [--queries eval/queries.jsonl]

Gold labels in queries.jsonl are a STARTER set — refine/expand them; the harness
is only as honest as the labels. Metrics are at the FILE level (top-k chunks are
collapsed to their ordered unique file_paths before scoring against the gold set).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".claude" / "scripts"))

from memory_search import search  # noqa: E402

P_AT = 5


def _ordered_files(results: list[dict]) -> list[str]:
    """Collapse ranked chunks → ordered unique file_paths (first occurrence wins)."""
    seen: set[str] = set()
    out: list[str] = []
    for r in results:
        fp = r["file_path"]
        if fp not in seen:
            seen.add(fp)
            out.append(fp)
    return out


def _metrics(ranked_files: list[str], gold: set[str]) -> dict[str, float]:
    top5 = ranked_files[:P_AT]
    p_at_5 = sum(1 for f in top5 if f in gold) / P_AT
    recall = (sum(1 for f in ranked_files if f in gold) / len(gold)) if gold else 0.0
    mrr = 0.0
    for rank, f in enumerate(ranked_files, start=1):
        if f in gold:
            mrr = 1.0 / rank
            break
    return {"p_at_5": p_at_5, "recall": recall, "mrr": mrr}


def _eval(queries: list[dict], k: int, use_graph: bool) -> dict[str, float]:
    agg = {"p_at_5": 0.0, "recall": 0.0, "mrr": 0.0}
    for q in queries:
        ranked = _ordered_files(search(q["query"], k=k, use_graph=use_graph))
        m = _metrics(ranked, set(q["gold"]))
        for key in agg:
            agg[key] += m[key]
    n = len(queries) or 1
    return {key: v / n for key, v in agg.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--queries", default=str(Path(__file__).parent / "queries.jsonl"))
    args = ap.parse_args()

    queries = [
        json.loads(line)
        for line in Path(args.queries).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    off = _eval(queries, args.k, use_graph=False)
    on = _eval(queries, args.k, use_graph=True)

    print(f"\nBrainBench-lite — {len(queries)} queries, k={args.k}, file-level metrics\n")
    print(f"  {'metric':<12} {'graph OFF':>10} {'graph ON':>10} {'Δ':>9}")
    print("  " + "-" * 43)
    labels = {"p_at_5": f"P@{P_AT}", "recall": f"Recall@{args.k}", "mrr": "MRR"}
    for key, label in labels.items():
        d = on[key] - off[key]
        print(f"  {label:<12} {off[key]:>10.3f} {on[key]:>10.3f} {d:>+9.3f}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
