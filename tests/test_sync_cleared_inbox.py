#!/usr/bin/env python3
"""Standalone tests for deploy/bin/sync_cleared_inbox.py (no pytest).

Run: uv run python tests/test_sync_cleared_inbox.py

Covers the federation transport's privacy gate: only captures that are BOTH
in-scope (default_export ∈ consumer scope) AND cleared are selected/synced —
crucially, a `personal` + `cleared` capture is NOT leaked, and an in-scope but
un-cleared capture is held back.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "sync_cleared_inbox", REPO / "deploy" / "bin" / "sync_cleared_inbox.py"
)
sci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sci)

_PASS = _FAIL = _SKIP = 0


def check(cond: bool, msg: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {msg}")
    else:
        _FAIL += 1
        print(f"  FAIL {msg}")


def _make_capture(root: Path, slug: str, name: str, export: str, status: str) -> Path:
    p = root / slug / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = (f"---\ntype: inbox\ncreated: 2026-05-21T14:25:35-03:00\n"
          f"default_export: {export}\nshare_status: {status}\n"
          f"project: {slug}\nsource: test\n---\n\nBody for {name}.\n")
    p.write_text(fm, encoding="utf-8")
    return p


def _seed(root: Path) -> None:
    # in-scope + cleared → ELIGIBLE
    _make_capture(root, "colinas", "a-linos-cleared", "linos-protostack", "cleared")
    _make_capture(root, "colinas", "b-linos-cleared", "linos-protostack", "cleared")
    # in-scope but NOT cleared → held back
    _make_capture(root, "colinas", "c-linos-active", "linos-protostack", "active")
    # personal + cleared → MUST NOT leak (the whole point)
    _make_capture(root, "colinas", "d-personal-cleared", "personal", "cleared")
    _make_capture(root, "vertik", "e-personal-cleared", "personal", "cleared")
    # discard target, cleared → excluded
    _make_capture(root, "vertik", "f-discard", "discard", "cleared")


def test_select_eligible() -> None:
    print("[test_select_eligible]")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "sessions"
        _seed(src)
        rels, stats = sci.select_eligible(src, "linos")

        check(stats["eligible"] == 2, "exactly 2 eligible (the linos-protostack+cleared pair)")
        check(stats["uncleared"] == 1, "1 held back as uncleared (linos-protostack+active)")
        # personal(x2) + discard(x1) = 3 out of scope
        check(stats["out_of_scope"] == 3, "3 skipped out-of-scope (2 personal + 1 discard)")
        names = sorted(Path(r).stem for r in rels)
        check(names == ["a-linos-cleared", "b-linos-cleared"],
              "selected paths are exactly the in-scope cleared captures")
        check(all("personal" not in n for n in names),
              "NO personal+cleared capture leaks into the selection")


def test_unknown_consumer_denied() -> None:
    print("[test_unknown_consumer_denied]")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "sessions"
        _seed(src)
        rels, stats = sci.select_eligible(src, "nope")
        check(rels == [] and stats["eligible"] == 0,
              "unknown consumer selects nothing (fail-closed)")


def test_skips_underscore_dirs() -> None:
    print("[test_skips_underscore_dirs]")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "sessions"
        _seed(src)
        # an archived/in-scope+cleared capture under _archive must be ignored
        _make_capture(src, "colinas/_archive", "z-archived", "linos-protostack", "cleared")
        rels, stats = sci.select_eligible(src, "linos")
        check(stats["eligible"] == 2, "_archive/ subdir is not descended into")


def test_actual_sync_copies_only_eligible() -> None:
    print("[test_actual_sync_copies_only_eligible]")
    if shutil.which("rsync") is None:
        global _SKIP
        _SKIP += 1
        print("  skip rsync not on PATH")
        return
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "sessions"
        dst = Path(td) / "linos-inbox"
        _seed(src)
        rc = sci.main(["--src", str(src), "--dst", str(dst), "--consumer", "linos"])
        check(rc == 0, "main() exits 0")
        copied = sorted(p.stem for p in dst.rglob("*.md"))
        check(copied == ["a-linos-cleared", "b-linos-cleared"],
              "dest contains ONLY the 2 in-scope cleared captures")
        check(not (dst / "vertik").exists(),
              "vertik/ (personal-only) never created in dest")


def test_dry_run_writes_nothing() -> None:
    print("[test_dry_run_writes_nothing]")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "sessions"
        dst = Path(td) / "linos-inbox"
        _seed(src)
        rc = sci.main(["--src", str(src), "--dst", str(dst), "--dry-run"])
        check(rc == 0, "dry-run exits 0")
        check(not dst.exists(), "dry-run creates no dest dir / writes nothing")


if __name__ == "__main__":
    test_select_eligible()
    test_unknown_consumer_denied()
    test_skips_underscore_dirs()
    test_actual_sync_copies_only_eligible()
    test_dry_run_writes_nothing()
    print()
    print(f"Results: {_PASS} passed, {_FAIL} failed, {_SKIP} skipped")
    if _FAIL:
        sys.exit(1)
