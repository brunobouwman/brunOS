"""Hybrid memory search: vector top-k×3 + FTS top-k×3 → RRF fusion → top-k JSON."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db import connect, keyword_search, vector_search
from embeddings import embed_query

RRF_K = 60


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


def search(query: str, k: int = 10, path_prefix: str | None = None) -> list[dict]:
    conn = connect()
    try:
        qemb = embed_query(query)
        inner_k = max(k * 3, 30)
        vec = vector_search(conn, qemb, k=inner_k, path_prefix=path_prefix)
        kw = keyword_search(conn, query, k=inner_k, path_prefix=path_prefix)
    finally:
        conn.close()
    return rrf_fuse([vec, kw], top_k=k)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--path-prefix", default=None)
    args = ap.parse_args()
    results = search(args.query, k=args.k, path_prefix=args.path_prefix)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
