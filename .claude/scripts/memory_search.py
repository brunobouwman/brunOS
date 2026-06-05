"""Hybrid memory search: vector top-k×3 + FTS top-k×3 → RRF → one-hop graph
augment over wikilinks → top-k JSON."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db import chunks_for_files, connect, keyword_search, neighbor_files, vector_search
from embeddings import embed_query

RRF_K = 60

# Graph augmentation (C1 retrieval-v2) — one-hop spreading activation over the
# wikilink graph after RRF. gbrain's single biggest retrieval lever. All tunable
# (eval/eval.py optimizes these). Disabled by --no-graph / BRUNOS_SEARCH_NO_GRAPH,
# and skipped entirely when --path-prefix scopes the search to one folder.
GRAPH_SEED_FILES = int(os.environ.get("BRUNOS_GRAPH_SEED_FILES", "5"))
GRAPH_MAX_NEIGHBORS = int(os.environ.get("BRUNOS_GRAPH_MAX_NEIGHBORS", "10"))
GRAPH_MAX_CHUNKS_PER_NEIGHBOR = int(os.environ.get("BRUNOS_GRAPH_MAX_CHUNKS", "3"))
GRAPH_BETA = float(os.environ.get("BRUNOS_GRAPH_BETA", "0.05"))  # neighbor boost = BETA × best-chunk score of strongest connecting seed; 0.05 = eval-measured sweet spot (higher regresses MRR on a near-ceiling baseline)
# Re-rank already-retrieved neighbors (lift linked siblings)? Off by default —
# the eval showed it demotes precise answers on a near-ceiling baseline. When
# off, graph is INJECT-ONLY (surface missed linked docs into the candidate pool;
# never reorder existing RRF hits) → provably cannot regress precision/MRR.
GRAPH_RERANK = os.environ.get("BRUNOS_GRAPH_RERANK", "0") == "1"


def rrf_fuse(rankings: list[list[dict]], top_k: int) -> list[dict]:
    scores: dict[int, float] = defaultdict(float)
    lookup: dict[int, dict] = {}
    for ranking in rankings:
        for rank, row in enumerate(ranking):
            cid = row["id"]
            scores[cid] += 1.0 / (RRF_K + rank + 1)
            if cid not in lookup:
                lookup[cid] = row
    sorted_ids = sorted(scores.keys(), key=lambda c: -scores[c])[:top_k]
    out = []
    for cid in sorted_ids:
        row = dict(lookup[cid])
        row["score"] = scores[cid]
        out.append(row)
    return out


def graph_augment(conn, fused: list[dict], k: int) -> list[dict]:
    """One-hop graph augmentation of an RRF-scored candidate pool.

    Seeds = the top files by summed RRF score; pulls in their bidirectional
    wikilink neighbors and boosts/injects those files' chunks by
    GRAPH_BETA × Σ(connecting seed scores). A neighbor linked from several strong
    seeds therefore rises further. Re-ranks and returns the top k.
    """
    if not fused:
        return fused[:k]
    # Two per-file aggregates: `total` (sum of chunk scores) picks the seed files;
    # `best` (single strongest chunk ≈ one RRF hit) sizes the boost so it stays
    # comparable to a lexical hit and doesn't scale with how many chunks a file has.
    file_total: dict[str, float] = defaultdict(float)
    file_best: dict[str, float] = defaultdict(float)
    for row in fused:
        s = row.get("score", 0.0)
        file_total[row["file_path"]] += s
        file_best[row["file_path"]] = max(file_best[row["file_path"]], s)

    seeds = sorted(file_total, key=lambda p: -file_total[p])[:GRAPH_SEED_FILES]
    seed_set = set(seeds)
    nmap = neighbor_files(conn, seeds)

    # Spreading activation: a non-seed neighbor inherits the BEST-chunk score of its
    # STRONGEST connecting seed (max, not sum) — "as relevant as the best retrieved
    # doc that links it." Max avoids hub domination (a densely-linked index page
    # would otherwise out-rank the leaf that actually answers the query).
    strength: dict[str, float] = defaultdict(float)
    for seed, neighbors in nmap.items():
        for nb in neighbors:
            if nb not in seed_set:
                strength[nb] = max(strength[nb], file_best[seed])
    if not strength:
        return fused[:k]
    top: dict[str, float] = dict(
        sorted(strength.items(), key=lambda kv: -kv[1])[:GRAPH_MAX_NEIGHBORS]
    )

    merged: dict[int, dict] = {row["id"]: dict(row) for row in fused}
    # 1) Re-rank (opt-in): boost already-retrieved chunks of a boosted neighbor
    #    file. Off by default — lifts linked siblings but can demote the precise
    #    answer on a near-ceiling baseline (measured).
    if GRAPH_RERANK:
        for row in merged.values():
            bonus = top.get(row["file_path"])
            if bonus:
                row["score"] = row.get("score", 0.0) + GRAPH_BETA * bonus
    # 2) Surface (always): inject head chunks of boosted neighbors that weren't
    #    retrieved at all → recall enhancement; can't demote an existing hit.
    for ch in chunks_for_files(conn, list(top), GRAPH_MAX_CHUNKS_PER_NEIGHBOR):
        if ch["id"] not in merged:
            row = dict(ch)
            row["score"] = GRAPH_BETA * top[ch["file_path"]]
            row["graph"] = True
            merged[ch["id"]] = row

    return sorted(merged.values(), key=lambda r: -r.get("score", 0.0))[:k]


def search(
    query: str,
    k: int = 10,
    path_prefix: str | None = None,
    use_graph: bool = True,
) -> list[dict]:
    if os.environ.get("BRUNOS_SEARCH_NO_GRAPH"):
        use_graph = False
    # Graph augment only makes sense on an unscoped search — a --path-prefix is a
    # deliberate folder scope that cross-folder neighbors would violate.
    use_graph = use_graph and path_prefix is None
    conn = connect()
    try:
        qemb = embed_query(query)
        inner_k = max(k * 3, 30)
        vec = vector_search(conn, qemb, k=inner_k, path_prefix=path_prefix)
        kw = keyword_search(conn, query, k=inner_k, path_prefix=path_prefix)
        # Fuse a larger pool than k so the graph step has real material to seed from.
        fused = rrf_fuse([vec, kw], top_k=inner_k)
        if use_graph:
            return graph_augment(conn, fused, k)
        return fused[:k]
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--path-prefix", default=None)
    ap.add_argument("--no-graph", action="store_true",
                    help="disable wikilink graph augmentation (baseline RRF only)")
    args = ap.parse_args()
    results = search(
        args.query, k=args.k, path_prefix=args.path_prefix, use_graph=not args.no_graph
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
