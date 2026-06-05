#!/usr/bin/env python3
"""Standalone tests for wikilink edge extraction + resolution (no pytest).
Run: uv run python tests/test_graph_edges.py

Covers the C1 graph layer's index-time half: `extract_links` (regex, alias/heading
strip, dedup), `resolve_link` (dangling→None, ambiguous-basename→shortest path,
path-slug), and the `replace_edges`/`neighbor_files` DB round-trip (bidirectional,
self-link dropped).
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".claude" / "scripts"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mi = _load("memory_index", ".claude/scripts/memory_index.py")
import db as dbmod  # noqa: E402

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def test_extract_links():
    print("test: extract_links strips alias/heading, dedups, order-preserving")
    text = "intro [[Foo]] mid [[Bar|an alias]] then [[Baz#section]] and again [[Foo]]"
    check(mi.extract_links(text) == ["Foo", "Bar", "Baz"], "targets parsed")
    check(mi.extract_links("no links here") == [], "no links → []")


def test_basename_map_and_resolve():
    print("test: basename map + resolve (ambiguous → shortest path, dangling → None)")
    files = ["projects/vertik.md", "projects/vertik/vertik.md", "HABITS.md",
             "projects/Brain/baas_security_privacy.md"]
    by_path = set(files)
    m = mi.build_basename_map(files)
    check(m["vertik"][0] == "projects/vertik.md", "ambiguous [[vertik]] → shallowest")
    check(mi.resolve_link("vertik", m, by_path) == "projects/vertik.md", "resolve ambiguous")
    check(mi.resolve_link("HABITS", m, by_path) == "HABITS.md", "case-insensitive basename")
    check(mi.resolve_link("baas_security_privacy", m, by_path)
          == "projects/Brain/baas_security_privacy.md", "nested basename")
    check(mi.resolve_link("does_not_exist", m, by_path) is None, "dangling → None")
    check(mi.resolve_link("projects/vertik/vertik", m, by_path)
          == "projects/vertik/vertik.md", "explicit path slug resolves")


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE edges (src_path TEXT, dst_path TEXT, UNIQUE(src_path, dst_path));"
    )
    return conn


def test_edges_roundtrip():
    print("test: replace_edges + neighbor_files (bidirectional, self-link dropped)")
    conn = _mem_conn()
    dbmod.replace_edges(conn, "a.md", ["b.md", "a.md", "c.md", "b.md"])  # self + dup
    dbmod.replace_edges(conn, "d.md", ["b.md"])
    edges = {(r["src_path"], r["dst_path"]) for r in conn.execute("SELECT * FROM edges")}
    check(("a.md", "a.md") not in edges, "self-link dropped")
    check(edges == {("a.md", "b.md"), ("a.md", "c.md"), ("d.md", "b.md")}, "deduped edges")

    nb = dbmod.neighbor_files(conn, ["a.md"])
    check(sorted(nb["a.md"]) == ["b.md", "c.md"], "forward neighbors of a")
    nb_b = dbmod.neighbor_files(conn, ["b.md"])
    check(sorted(nb_b["b.md"]) == ["a.md", "d.md"], "backlinks into b (bidirectional)")

    # re-indexing a.md replaces its edges
    dbmod.replace_edges(conn, "a.md", ["c.md"])
    nb2 = dbmod.neighbor_files(conn, ["a.md"])
    check(nb2["a.md"] == ["c.md"], "replace_edges fully refreshes src's edges")


def main():
    test_extract_links()
    test_basename_map_and_resolve()
    test_edges_roundtrip()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
