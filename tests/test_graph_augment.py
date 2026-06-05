#!/usr/bin/env python3
"""Standalone tests for the query-time graph augment (no pytest).
Run: uv run python tests/test_graph_augment.py

Covers `graph_augment`: injects a linked-but-missed neighbor's chunk (recall),
is a no-op when there are no edges, respects the inject-only default (never
demotes a retrieved hit), and re-ranks only when GRAPH_RERANK is enabled.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".claude" / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "memory_search", REPO / ".claude" / "scripts" / "memory_search.py"
)
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def _conn(edges, chunks):
    """In-memory DB. chunks: list of (id, file_path, chunk_idx, content)."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE edges (src_path TEXT, dst_path TEXT, UNIQUE(src_path,dst_path));"
        "CREATE TABLE chunks (id INTEGER PRIMARY KEY, file_path TEXT, chunk_idx INT, content TEXT, mtime REAL);"
    )
    c.executemany("INSERT INTO edges VALUES (?,?)", edges)
    c.executemany(
        "INSERT INTO chunks(id,file_path,chunk_idx,content,mtime) VALUES (?,?,?,?,0)", chunks
    )
    return c


class _set:
    def __init__(self, **kw):
        self.kw = kw
        self.orig = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.orig[k] = getattr(ms, k)
            setattr(ms, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.orig.items():
            setattr(ms, k, v)


def test_no_edges_is_noop():
    print("test: no edges → graph_augment returns the RRF result unchanged")
    conn = _conn([], [(1, "seed.md", 0, "x")])
    fused = [{"id": 1, "file_path": "seed.md", "chunk_idx": 0, "content": "x", "score": 0.016}]
    out = ms.graph_augment(conn, fused, k=10)
    check([r["id"] for r in out] == [1], "unchanged")
    check(not any(r.get("graph") for r in out), "nothing marked graph")


def test_injects_missed_neighbor():
    print("test: a linked-but-missed neighbor gets injected (recall), marked graph")
    # seed.md retrieved; nb.md linked from seed.md but NOT in the fused pool.
    conn = _conn([("seed.md", "nb.md")],
                 [(1, "seed.md", 0, "x"), (2, "nb.md", 0, "y")])
    fused = [{"id": 1, "file_path": "seed.md", "chunk_idx": 0, "content": "x", "score": 0.016}]
    with _set(GRAPH_RERANK=False):
        out = ms.graph_augment(conn, fused, k=10)
    ids = [r["id"] for r in out]
    check(1 in ids and 2 in ids, "seed kept + neighbor injected")
    inj = next(r for r in out if r["id"] == 2)
    check(inj.get("graph") is True, "injected neighbor flagged graph=True")
    check(out[0]["id"] == 1, "inject-only never demotes the retrieved seed")


def test_inject_only_preserves_order():
    print("test: inject-only keeps existing hits ahead of injected neighbors")
    conn = _conn([("seed.md", "nb.md")],
                 [(1, "seed.md", 0, "x"), (2, "other.md", 0, "z"), (3, "nb.md", 0, "y")])
    fused = [
        {"id": 1, "file_path": "seed.md", "chunk_idx": 0, "content": "x", "score": 0.016},
        {"id": 2, "file_path": "other.md", "chunk_idx": 0, "content": "z", "score": 0.015},
    ]
    with _set(GRAPH_RERANK=False):
        out = ms.graph_augment(conn, fused, k=10)
    check([r["id"] for r in out][:2] == [1, 2], "both RRF hits stay on top, in order")
    check(out[-1]["id"] == 3 and out[-1].get("graph"), "neighbor appended last")


def test_rerank_boosts_retrieved_neighbor():
    print("test: GRAPH_RERANK lifts an already-retrieved linked sibling")
    # nb.md is retrieved BELOW other.md but is linked from the top seed → should rise.
    conn = _conn([("seed.md", "nb.md")],
                 [(1, "seed.md", 0, "x"), (2, "other.md", 0, "z"), (3, "nb.md", 0, "y")])
    fused = [
        {"id": 1, "file_path": "seed.md", "chunk_idx": 0, "content": "x", "score": 0.030},
        {"id": 2, "file_path": "other.md", "chunk_idx": 0, "content": "z", "score": 0.016},
        {"id": 3, "file_path": "nb.md", "chunk_idx": 0, "content": "y", "score": 0.015},
    ]
    # GRAPH_SEED_FILES=1 so only the top file (seed.md) is a seed; nb/other are
    # non-seed neighbors eligible for boost.
    with _set(GRAPH_RERANK=True, GRAPH_BETA=0.5, GRAPH_SEED_FILES=1):
        out = ms.graph_augment(conn, fused, k=10)
    # nb (3) boosted by 0.5 * best-chunk-of-seed (0.030) = +0.015 → 0.030, leads other (0.016)
    pos = {r["id"]: i for i, r in enumerate(out)}
    check(pos[3] < pos[2], "linked sibling nb rose above other under rerank")


def main():
    test_no_edges_is_noop()
    test_injects_missed_neighbor()
    test_inject_only_preserves_order()
    test_rerank_boosts_retrieved_neighbor()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
