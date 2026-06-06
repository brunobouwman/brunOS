#!/usr/bin/env python3
"""Standalone tests for deploy/bin/sync_acks.py (no pytest).

Run: uv run python tests/test_sync_acks.py

Covers the federation RETURN leg (linos → bruno ack push): select only *.json
acks (skip .gitkeep / non-json), idempotent --ignore-existing copy, fail-closed
when the dest drop is absent (the one-time ACL setup must create it), and a
dry-run / nothing-to-sync that writes nothing.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Track D: tests must never write real monitor state / ping healthchecks.
os.environ["BRUNOS_DISABLE_REPORTING"] = "1"

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "sync_acks", REPO / "deploy" / "bin" / "sync_acks.py"
)
sa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sa)

_PASS = _FAIL = _SKIP = 0


def check(cond: bool, msg: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {msg}")
    else:
        _FAIL += 1
        print(f"  FAIL {msg}")


def _make_ack(src: Path, capture_id: str) -> Path:
    src.mkdir(parents=True, exist_ok=True)
    p = src / f"{capture_id}.json"
    p.write_text(json.dumps({"capture_id": capture_id, "consumer": "linos"}), encoding="utf-8")
    return p


def _seed(src: Path) -> None:
    _make_ack(src, "2026-05-19-182135-7de4c62e")
    _make_ack(src, "2026-05-21-142535-1e08437f")
    _make_ack(src, "2026-05-24-193944-ce8b0b29")
    (src / ".gitkeep").write_text("", encoding="utf-8")          # must be skipped
    (src / "notes.md").write_text("not an ack", encoding="utf-8")  # must be skipped


def test_select_acks() -> None:
    print("[test_select_acks]")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "_acks" / "brunos"
        _seed(src)
        acks = sa.select_acks(src)
        check(acks == [
            "2026-05-19-182135-7de4c62e.json",
            "2026-05-21-142535-1e08437f.json",
            "2026-05-24-193944-ce8b0b29.json",
        ], "only *.json acks, sorted (.gitkeep + .md skipped)")
        check(sa.select_acks(Path(td) / "missing") == [], "absent src → []")


def test_dry_run_writes_nothing() -> None:
    print("[test_dry_run_writes_nothing]")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "_acks" / "brunos"
        dst = Path(td) / "drop"
        _seed(src)
        rc = sa.main(["--src", str(src), "--dst", str(dst), "--dry-run"])
        check(rc == 0, "dry-run exits 0")
        check(not dst.exists(), "dry-run writes nothing / creates no dest")


def test_missing_dest_fails_closed() -> None:
    print("[test_missing_dest_fails_closed]")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "_acks" / "brunos"
        dst = Path(td) / "drop"  # NOT created — simulates ACL setup not yet run
        _seed(src)
        rc = sa.main(["--src", str(src), "--dst", str(dst)])
        check(rc == 2, "absent dest drop → rc 2 (never creates dirs under the producer's home)")
        check(not dst.exists(), "did not create the dest itself")


def test_nothing_to_sync() -> None:
    print("[test_nothing_to_sync]")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "_acks" / "brunos"
        src.mkdir(parents=True)
        (src / ".gitkeep").write_text("", encoding="utf-8")
        dst = Path(td) / "drop"  # absent — must NOT matter when there's nothing to sync
        rc = sa.main(["--src", str(src), "--dst", str(dst)])
        check(rc == 0, "no acks → rc 0 before the dest check")
        check(not dst.exists(), "nothing written")


def test_actual_sync_and_idempotent() -> None:
    print("[test_actual_sync_and_idempotent]")
    if shutil.which("rsync") is None:
        global _SKIP
        _SKIP += 1
        print("  skip rsync not on PATH")
        return
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "_acks" / "brunos"
        dst = Path(td) / "drop"
        _seed(src)
        dst.mkdir(parents=True)  # the one-time ACL setup creates the drop
        rc = sa.main(["--src", str(src), "--dst", str(dst)])
        check(rc == 0, "main() exits 0")
        copied = sorted(p.name for p in dst.glob("*.json"))
        check(len(copied) == 3, f"all 3 acks copied ({len(copied)})")
        check(not (dst / ".gitkeep").exists(), ".gitkeep not copied")
        check(not (dst / "notes.md").exists(), "non-ack not copied")
        # idempotent: add one new ack, re-run → only the new one transfers, rc 0
        _make_ack(src, "2026-05-25-101010-aaaaaaaa")
        rc2 = sa.main(["--src", str(src), "--dst", str(dst)])
        check(rc2 == 0, "second run exits 0")
        check(len(list(dst.glob("*.json"))) == 4, "new ack added; existing untouched")


if __name__ == "__main__":
    test_select_acks()
    test_dry_run_writes_nothing()
    test_missing_dest_fails_closed()
    test_nothing_to_sync()
    test_actual_sync_and_idempotent()
    print()
    print(f"Results: {_PASS} passed, {_FAIL} failed, {_SKIP} skipped")
    if _FAIL:
        sys.exit(1)
