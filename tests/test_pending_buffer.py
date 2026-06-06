#!/usr/bin/env python3
"""Standalone tests for the pending-personal buffer surfacing (no pytest).
Run: uv run python tests/test_pending_buffer.py

The hourly inbox pass buffers personal items in personal_pending.json; they're not
in the vault/index until the daily curation pass. These tests cover the two
intraday surfacing paths: shared.format_personal_pending (for build_context) and
memory_search's lexical buffer match (appended to unscoped results, skipped when
--path-prefix scopes to a vault folder).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / ".claude" / "scripts" / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

shared = _load("shared", "shared.py")
ms = _load("memory_search", "memory_search.py")

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


class _patch:
    def __init__(self, target, **kw):
        self.target = target
        self.kw = kw
        self.orig = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.orig[k] = getattr(self.target, k)
            setattr(self.target, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.orig.items():
            setattr(self.target, k, v)


ITEMS = [
    {"type": "lesson", "text": "Always verify the eval fails on pre-fix code", "source": "vertik", "ts": "t"},
    {"type": "fact", "text": "Floripa move is mid-July", "source": "daily-log", "ts": "t"},
]


def test_format_empty():
    print("[test_format_empty]")
    check(shared.format_personal_pending([]) == "", "empty buffer → empty string")


def test_format_renders_block():
    print("[test_format_renders_block]")
    out = shared.format_personal_pending(ITEMS)
    check(out.startswith("## Pending personal"), "has heading")
    check("(lesson) Always verify the eval fails" in out, "lesson rendered with type")
    check("_[vertik]_" in out, "source provenance rendered")
    check("Floripa move is mid-July" in out, "second item rendered")


def test_load_personal_pending_reads_file():
    print("[test_load_personal_pending_reads_file]")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "personal_pending.json"
        p.write_text(json.dumps(ITEMS), encoding="utf-8")
        with _patch(shared, PERSONAL_PENDING_PATH=p):
            got = shared.load_personal_pending()
        check(got == ITEMS, "loads list from file")
    check(shared.load_personal_pending.__module__ == "shared", "helper lives in shared")


def test_pending_matches_overlap():
    print("[test_pending_matches_overlap]")
    with _patch(ms, load_personal_pending=lambda: ITEMS):
        hits = ms.pending_matches("how do I verify an eval before declaring it fixed")
    check(len(hits) == 1, f"one item matches on 'verify'/'eval' ({len(hits)})")
    h = hits[0]
    check(h["pending"] is True, "row tagged pending:true")
    check(h["file_path"].endswith("personal_pending.json"), "pseudo file_path points at the buffer")
    check(h["id"] < 0, "negative pseudo-id (never collides with a real chunk id)")
    check("[pending lesson]" in h["content"], "content carries the type tag")


def test_pending_matches_no_overlap_and_stopwords():
    print("[test_pending_matches_no_overlap_and_stopwords]")
    with _patch(ms, load_personal_pending=lambda: ITEMS):
        check(ms.pending_matches("kubernetes ingress tuning") == [], "no overlap → no rows")
        # only short/stopword-ish tokens (<3 chars) → no qtokens → no rows
        check(ms.pending_matches("is it ok") == [], "all-short-token query → no rows")


def test_search_appends_pending_unscoped_skips_scoped():
    print("[test_search_appends_pending_unscoped_skips_scoped]")
    vault_row = {"id": 1, "file_path": "projects/x.md", "chunk_idx": 0, "content": "...", "score": 0.02}

    class _Conn:
        def close(self):
            pass

    with _patch(
        ms,
        connect=lambda: _Conn(),
        embed_query=lambda q: [0.0],
        vector_search=lambda *a, **k: [],
        keyword_search=lambda *a, **k: [],
        rrf_fuse=lambda rankings, top_k: [vault_row],
        graph_augment=lambda conn, fused, k: fused[:k],
        load_personal_pending=lambda: ITEMS,
    ):
        unscoped = ms.search("verify the eval", k=5, path_prefix=None)
        scoped = ms.search("verify the eval", k=5, path_prefix="projects")
        disabled = ms.search("verify the eval", k=5, path_prefix=None, include_pending=False)
    check(any(r.get("pending") for r in unscoped), "unscoped search appends pending rows")
    check(unscoped[0]["id"] == 1, "vault hit still ranked first (pending appended after)")
    check(not any(r.get("pending") for r in scoped), "scoped (--path-prefix) search omits pending")
    check(not any(r.get("pending") for r in disabled), "include_pending=False omits pending")


def main():
    test_format_empty()
    test_format_renders_block()
    test_load_personal_pending_reads_file()
    test_pending_matches_overlap()
    test_pending_matches_no_overlap_and_stopwords()
    test_search_appends_pending_unscoped_skips_scoped()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
