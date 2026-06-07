#!/usr/bin/env python3
"""BrainBench-lite — retrieval eval harness for BrunOS memory search (C1).

Runs the labelled queries in queries.jsonl through memory_search.search() across
three graph configs and reports file-level P@5 / Recall@k / MRR:

  • OFF      — baseline hybrid RRF, no graph (BRUNOS_SEARCH_NO_GRAPH)
  • inject   — graph ON, inject-only (the shipped default): surface linked-but-
               missed neighbors into the candidate pool, never reorder RRF hits
  • rerank   — graph ON + BRUNOS_GRAPH_RERANK: also lift already-retrieved siblings

  uv run python eval/eval.py [--k 10] [--queries eval/queries.jsonl]

This is the "prove it helps" number for graph traversal (the gate before any
reranker work) and doubles as a published benchmark artifact. See eval/README.md
for the methodology, the current re-decision, and metric-interpretation notes.

Gold labels in queries.jsonl are HAND-VERIFIED against vault content — refine and
expand them as the vault grows; the harness is only as honest as the labels.
Metrics are at the FILE level (top-k chunks are collapsed to their ordered unique
file_paths before scoring against the gold set). The pending-personal buffer is
disabled so runs are reproducible day-to-day.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".claude" / "scripts"))

import memory_search  # noqa: E402
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


def _eval(queries: list[dict], k: int, use_graph: bool, rerank: bool) -> dict[str, float]:
    # graph_augment reads the module-global GRAPH_RERANK at call time, so the
    # benchmark toggles it per-config (same convention as tests/test_graph_augment).
    # include_pending=False: the pending buffer is today's not-yet-curated items —
    # excluding it keeps the benchmark reproducible across days.
    prev = memory_search.GRAPH_RERANK
    memory_search.GRAPH_RERANK = rerank
    try:
        agg = {"p_at_5": 0.0, "recall": 0.0, "mrr": 0.0}
        for q in queries:
            ranked = _ordered_files(
                search(q["query"], k=k, use_graph=use_graph, include_pending=False)
            )
            m = _metrics(ranked, set(q["gold"]))
            for key in agg:
                agg[key] += m[key]
    finally:
        memory_search.GRAPH_RERANK = prev
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
    n_multi = sum(1 for q in queries if len(q["gold"]) > 1)
    off = _eval(queries, args.k, use_graph=False, rerank=False)
    inject = _eval(queries, args.k, use_graph=True, rerank=False)
    rerank = _eval(queries, args.k, use_graph=True, rerank=True)

    print(
        f"\nBrainBench-lite — {len(queries)} queries "
        f"({n_multi} multi-doc), k={args.k}, file-level metrics\n"
    )
    hdr = f"  {'metric':<12} {'OFF':>9} {'inject':>9} {'rerank':>9} {'Δrerank':>9}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    labels = {"p_at_5": f"P@{P_AT}", "recall": f"Recall@{args.k}", "mrr": "MRR"}
    for key, label in labels.items():
        d = rerank[key] - off[key]
        print(
            f"  {label:<12} {off[key]:>9.3f} {inject[key]:>9.3f} "
            f"{rerank[key]:>9.3f} {d:>+9.3f}"
        )
    print(
        "\n  inject ≡ OFF at the file level is expected: injected neighbors score "
        "below the\n  fused-pool floor and truncate out of top-k. See eval/README.md.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
