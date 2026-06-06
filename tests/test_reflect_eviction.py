#!/usr/bin/env python3
"""Standalone tests for the Phase B reflect finalization (no pytest).
Run: uv run python tests/test_reflect_eviction.py

Covers:
  - _evict_to_archive_if_over_cap: an over-cap MEMORY.md sheds its OLDEST dated
    bullet from the LARGEST section into Memory/_archive/MEMORY-archive.md until
    under cap — lossless (the bullet lands in the archive verbatim, nothing is
    deleted), undated context bullets are untouched, and still_over_cap is reported
    when no dated bullet remains to peel.
  - _buffer_personal: personal items are appended to personal_pending.json (the
    hourly inbox pass no longer writes MEMORY.md per batch).
  - _run_memory_curation_stage: drains the buffer into MEMORY.md once + evicts
    once, then clears the buffer.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "memory_reflect", REPO / ".claude" / "scripts" / "memory_reflect.py"
)
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

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
    def __init__(self, **kw):
        self.kw = kw
        self.orig = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.orig[k] = getattr(mr, k)
            setattr(mr, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.orig.items():
            setattr(mr, k, v)


FM = "---\ntype: system\n---\n"


def _doc(bullets_by_section: dict[str, list[str]]) -> str:
    parts = [FM]
    for section, bullets in bullets_by_section.items():
        parts.append(section)
        parts.append("")
        parts.extend(bullets)
        parts.append("")
    return "\n".join(parts)


def test_under_cap_noop():
    print("[test_under_cap_noop]")
    text = FM + "## A\n\n- **2026-01-01** — small\n"
    out, evicted, over = mr._evict_to_archive_if_over_cap(text, 8192, dry_run=True)
    check(out == text, "text unchanged")
    check(evicted == [], "nothing evicted")
    check(over is False, "still_over_cap False")


def test_evicts_oldest_from_largest_section():
    print("[test_evicts_oldest_from_largest_section]")
    # Section "Big" is far larger; its oldest dated bullet must be the victim.
    big = [f"- **2026-05-{d:02d}** — big bullet number {d} " + ("x" * 200) for d in range(2, 12)]
    big.insert(0, "- **2026-01-01** — OLDEST big bullet " + ("x" * 200))
    small = ["- **2025-01-01** — tiny old bullet", "- not a dated bullet, keep me"]
    text = _doc({"## Big": big, "## Small": small})
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None):
            out, evicted, over = mr._evict_to_archive_if_over_cap(text, 2048, dry_run=False)
        check(len(out.encode()) <= 2048, f"under cap after eviction ({len(out.encode())}B)")
        check(over is False, "still_over_cap False")
        # The oldest dated bullet from the LARGEST section (Big/2026-01-01) goes first.
        check("OLDEST big bullet" not in out, "oldest big bullet evicted from MEMORY")
        # The undated context bullet in Small is never touched.
        check("not a dated bullet, keep me" in out, "undated bullet preserved (lossless ordering)")
        # The tiny old bullet in the SMALL section survives (largest-section rule).
        check("tiny old bullet" in out, "older bullet in small section kept (largest-first)")
        archive = (vault / "Memory" / "_archive" / "MEMORY-archive.md").read_text()
        check("OLDEST big bullet" in archive, "evicted bullet landed in archive verbatim (lossless)")
        check(mr.MEMORY_ARCHIVE_SECTION in archive, "archive has the evicted section header")


def test_still_over_when_no_dated_bullets():
    print("[test_still_over_when_no_dated_bullets]")
    # Over cap but every bullet is undated → cannot evict losslessly → report over.
    text = _doc({"## A": ["- undated " + ("y" * 4000), "- also undated " + ("y" * 4000)]})
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None):
            out, evicted, over = mr._evict_to_archive_if_over_cap(text, 2048, dry_run=False)
        check(evicted == [], "nothing evicted (no dated bullets)")
        check(over is True, "still_over_cap True (signals monitoring)")
        check(out == text, "MEMORY unchanged (nothing dropped)")


def test_dry_run_writes_no_archive():
    print("[test_dry_run_writes_no_archive]")
    big = [f"- **2026-05-{d:02d}** — bullet {d} " + ("x" * 200) for d in range(1, 12)]
    text = _doc({"## Big": big})
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None):
            out, evicted, over = mr._evict_to_archive_if_over_cap(text, 2048, dry_run=True)
        check(len(evicted) > 0, "dry-run computes would-evict list")
        check(not (vault / "Memory" / "_archive" / "MEMORY-archive.md").exists(),
              "dry-run writes no archive file")


def test_buffer_personal_appends():
    print("[test_buffer_personal_appends]")
    with tempfile.TemporaryDirectory() as td:
        buf_path = Path(td) / "personal_pending.json"
        with _patch(PERSONAL_PENDING_PATH=buf_path, _log=lambda *a, **k: None):
            n1 = mr._buffer_personal(
                [{"type": "lesson", "text": "L1", "promote": True},
                 {"type": "fact", "text": "F1", "promote": True},
                 {"type": "soul-suggestion", "text": "ignore me", "promote": True}],
                "vertik",
            )
            n2 = mr._buffer_personal([{"type": "decision", "text": "D1"}], "daily-log")
        check(n1 == 2, f"buffered 2 valid items (soul-suggestion filtered) got {n1}")
        check(n2 == 1, "second call appends")
        buf = json.loads(buf_path.read_text())
        check(len(buf) == 3, f"buffer has 3 items total ({len(buf)})")
        check({b["source"] for b in buf} == {"vertik", "daily-log"}, "source provenance kept")


def test_curation_drains_buffer_and_clears():
    print("[test_curation_drains_buffer_and_clears]")
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        mem = vault / "Memory" / "MEMORY.md"
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text(FM + "## Lessons\n\n- **2026-01-01** — existing\n", encoding="utf-8")
        buf_path = Path(td) / "personal_pending.json"
        buf_path.write_text(json.dumps([
            {"type": "lesson", "text": "buffered lesson", "source": "vertik", "ts": "t"},
        ]), encoding="utf-8")
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None,
                    PERSONAL_PENDING_PATH=buf_path):
            rc, fails = mr._run_memory_curation_stage(dry_run=False)
        check(rc == 0 and fails == [], "curation clean")
        body = mem.read_text()
        check("buffered lesson" in body, "buffered item written to MEMORY.md")
        check("existing" in body, "existing item preserved")
        check(json.loads(buf_path.read_text()) == [], "buffer cleared after write")


def test_curation_dry_run_keeps_buffer():
    print("[test_curation_dry_run_keeps_buffer]")
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        mem = vault / "Memory" / "MEMORY.md"
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text(FM + "## Lessons\n\n- old\n", encoding="utf-8")
        buf_path = Path(td) / "personal_pending.json"
        buf_path.write_text(json.dumps([
            {"type": "lesson", "text": "should stay buffered", "source": "x", "ts": "t"},
        ]), encoding="utf-8")
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None,
                    PERSONAL_PENDING_PATH=buf_path):
            rc, fails = mr._run_memory_curation_stage(dry_run=True)
        check(rc == 0, "dry-run rc 0")
        check("should stay buffered" not in mem.read_text(), "MEMORY.md untouched in dry-run")
        check(len(json.loads(buf_path.read_text())) == 1, "buffer NOT cleared in dry-run")


def main():
    test_under_cap_noop()
    test_evicts_oldest_from_largest_section()
    test_still_over_when_no_dated_bullets()
    test_dry_run_writes_no_archive()
    test_buffer_personal_appends()
    test_curation_drains_buffer_and_clears()
    test_curation_dry_run_keeps_buffer()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
