#!/usr/bin/env python3
"""Standalone tests for the inbox under-clearing / watermark-skip fix (no pytest).
Run: uv run python tests/test_reflect_clearing_watermark.py

Bug: the watermark advanced over ALL captures in a batch, but only LLM-echoed ones
were cleared — so an omitted capture was left uncleared AND below the cursor, never
reprocessed (and so never transported). Fix:
  - the watermark advances only over the LEADING run of terminal captures
    (_leading_terminal_watermark), never past a still-open one;
  - a persistently-unclearable capture is bounded by an attempt counter and
    force-quarantined (terminal, never shared) so it stops blocking its project;
  - cleared AND quarantined are terminal in _unprocessed_captures.
"""

from __future__ import annotations

import importlib.util
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


def _write_capture(path: Path, created: str, status: str = "active",
                   export: str = "linos-protostack", attempts: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: inbox", f"created: {created}",
             f"default_export: {export}", f"share_status: {status}",
             "project: colinas", "source: test"]
    if attempts is not None:
        lines.append(f"clear_attempts: {attempts}")
    lines += ["---", "", "Body text with a detail.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


class _patch:
    """Tiny save/restore monkeypatch context for module attributes."""
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


T1 = "2026-05-21T14:00:00-03:00"
T2 = "2026-05-21T15:00:00-03:00"
T3 = "2026-05-21T16:00:00-03:00"


def test_leading_terminal_watermark():
    print("[test_leading_terminal_watermark]")
    f = mr._leading_terminal_watermark
    check(f([(T1, True), (T2, True), (T3, True)]) == (T3, True),
          "all terminal → advance to newest, all_terminal=True")
    check(f([(T1, True), (T2, False), (T3, True)]) == (T1, False),
          "open in middle → watermark held at last terminal-prefix (T1), stop")
    check(f([(T1, False), (T2, True)]) == (None, False),
          "first capture open → no advance (None), stop")
    check(f([]) == (None, True), "empty batch → (None, all_terminal=True)")
    check(f([(None, True), (T2, True)]) == (T2, True),
          "malformed (None created) terminal entry doesn't block advance")


def test_resolve_clears_echoed_capture():
    print("[test_resolve_clears_echoed_capture]")
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"  # no Memory/_excluded-people.md → empty excluded set
        cap = Path(td) / "colinas" / "a.md"
        _write_capture(cap, T1, status="active")
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None):
            fm, _ = mr._parse_capture(cap)
            status = mr._resolve_capture(cap, fm, {cap.name: "cleaned body, asides removed"}, "t")
        check(status == "cleared", "echoed+stripped capture → 'cleared'")
        fm2, body2 = mr._parse_capture(cap)
        check(fm2.get("share_status") == "cleared", "file now share_status: cleared")
        check("cleaned body" in body2, "body replaced with the cleaned text")


def test_resolve_bumps_then_quarantines():
    print("[test_resolve_bumps_then_quarantines]")
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        cap = Path(td) / "colinas" / "b.md"
        _write_capture(cap, T1, status="active")
        statuses = []
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None):
            for _ in range(mr.MAX_CLEAR_ATTEMPTS):
                fm, _b = mr._parse_capture(cap)  # re-parse: attempt counter lives in the file
                statuses.append(mr._resolve_capture(cap, fm, {}, "t"))  # never echoed
        check(statuses[:-1] == ["open"] * (mr.MAX_CLEAR_ATTEMPTS - 1),
              f"first {mr.MAX_CLEAR_ATTEMPTS - 1} attempts stay 'open' {statuses}")
        check(statuses[-1] == "quarantined",
              f"attempt {mr.MAX_CLEAR_ATTEMPTS} → 'quarantined' {statuses}")
        fm_final, _ = mr._parse_capture(cap)
        check(fm_final.get("share_status") == "quarantined", "file now share_status: quarantined")
        check(fm_final.get("clear_attempts") == str(mr.MAX_CLEAR_ATTEMPTS),
              f"clear_attempts persisted == {mr.MAX_CLEAR_ATTEMPTS}")


def test_unprocessed_skips_terminal_statuses():
    print("[test_unprocessed_skips_terminal_statuses]")
    with tempfile.TemporaryDirectory() as td:
        inbox = Path(td) / "sessions"
        _write_capture(inbox / "colinas" / "open.md", T1, status="active")
        _write_capture(inbox / "colinas" / "done.md", T2, status="cleared")
        _write_capture(inbox / "colinas" / "quar.md", T3, status="quarantined")
        with _patch(_inbox_sessions_dir=lambda: inbox, _log=lambda *a, **k: None):
            got = mr._unprocessed_captures("colinas", None)
        names = sorted(p.name for p in got)
        check(names == ["open.md"],
              f"only the active capture is unprocessed (cleared+quarantined skipped) {names}")


if __name__ == "__main__":
    test_leading_terminal_watermark()
    test_resolve_clears_echoed_capture()
    test_resolve_bumps_then_quarantines()
    test_unprocessed_skips_terminal_statuses()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)
