#!/usr/bin/env python3
"""Standalone tests for the _compact_if_over_cap cap-relative floor (no pytest).
Run: uv run python tests/test_compaction_floor.py

Regression for the projects/vertik.md deadlock (2026-06-03): the doc bloated to
3x its cap, and the old `compacted < original * 0.5` guard aborted EVERY rescue
(a 24KB->7KB compaction is a >50% shrink), so it could never get under cap and
burned a Sonnet call per run forever. The fix floors against the CAP, not the
original size: a legitimate large shrink applies; only an implausibly tiny
(truncated/garbage) result is rejected. The function also reports still_over_cap
so monitoring can alert instead of bloating silently.
"""

from __future__ import annotations

import importlib.util
import sys
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


FM = "---\ntype: project\n---\n"


class _stub_reason:
    """Patch mr._reason with an async stub returning a fixed compacted body."""

    def __init__(self, body):
        self.body = body
        self._orig = None

    def __enter__(self):
        self._orig = mr._reason

        async def _fake(prompt_text, *, system_prompt=None, max_turns=1):
            return self.body

        mr._reason = _fake
        # silence logging
        self._orig_log = mr._log
        mr._log = lambda *a, **k: None
        return self

    def __exit__(self, *exc):
        mr._reason = self._orig
        mr._log = self._orig_log


def test_under_cap_noop():
    print("test: input already under cap → returned unchanged, not over")
    text = FM + ("x" * 100)
    out, over = mr._compact_if_over_cap(text, 8192)
    check(out == text, "text unchanged")
    check(over is False, "still_over_cap == False")


def test_large_rescue_applies():
    print("test: 24KB doc, cap 8192, compactor returns 7KB → APPLIES (was the deadlock)")
    big = FM + ("y" * 24000)
    compacted = "z" * 7000  # 7KB: a >50% shrink of the 24KB body — old guard aborted this
    with _stub_reason(compacted):
        out, over = mr._compact_if_over_cap(big, 8192)
    check(compacted in out, "compacted body applied (not the original)")
    check(out.startswith(FM), "frontmatter re-attached")
    check(over is False, "result under cap → still_over_cap == False")


def test_tiny_result_aborts():
    print("test: 24KB doc, compactor returns 200B → ABORT (below cap*0.25 floor)")
    big = FM + ("y" * 24000)
    with _stub_reason("z" * 200):
        out, over = mr._compact_if_over_cap(big, 8192)
    check(out == big, "original kept (garbage/truncated result rejected)")
    check(over is True, "still_over_cap == True (signals monitoring)")


def test_still_over_cap_reported():
    print("test: compactor returns 10KB for an 8KB cap → applies but reports over-cap")
    big = FM + ("y" * 24000)
    with _stub_reason("z" * 10000):  # above floor (2048) but still over the 8192 cap
        out, over = mr._compact_if_over_cap(big, 8192)
    check(("z" * 10000) in out, "applied (it's smaller than the 24KB original)")
    check(over is True, "still_over_cap == True")


def main():
    test_under_cap_noop()
    test_large_rescue_applies()
    test_tiny_result_aborts()
    test_still_over_cap_reported()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
