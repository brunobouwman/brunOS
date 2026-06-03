#!/usr/bin/env python3
"""Standalone tests for memory_reflect inbox batching (no pytest).
Run: uv run python tests/test_reflect_inbox_batching.py

Covers the control flow added in fix/reflect-inbox-batching: per-project captures
are processed in bounded batches and the watermark is saved AFTER each batch, so a
mid-project failure/timeout persists completed batches instead of losing everything.
The per-batch Sonnet work (_process_inbox_batch) is stubbed — these tests exercise
the loop in _run_inbox_stage only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "memory_reflect", REPO_ROOT / ".claude" / "scripts" / "memory_reflect.py"
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


class _Harness:
    """Monkeypatch memory_reflect's I/O + Sonnet boundary; record batch calls."""

    def __init__(self, captures, fail_on_batch=None, dry_run=False):
        self.captures = captures
        self.fail_on_batch = fail_on_batch  # 0-based batch index that returns None
        self.dry_run = dry_run
        self.batch_calls = []   # list of (label, [captures...])
        self.saved_states = []  # snapshot of state dict at each save
        self._saved = {}
        self._orig = {}

    def _patch(self, name, fn):
        self._orig[name] = getattr(mr, name)
        setattr(mr, name, fn)

    def __enter__(self):
        self._patch("_iter_inbox_projects", lambda: ["proj"])
        self._patch("_unprocessed_captures", lambda slug, wm: list(self.captures))
        self._patch("vault_path", lambda: Path("/tmp"))
        self._patch("_log", lambda *a, **k: None)
        self._patch("load_state", lambda *a, **k: {})

        def _save_state(path, state):
            self._saved = dict(state)
            self.saved_states.append(dict(state))

        self._patch("save_state", _save_state)

        def _proc(slug, label, batch, memory_path, dry_run):
            self.batch_calls.append((label, list(batch)))
            idx = len(self.batch_calls) - 1
            if dry_run:
                return (None, False, [])
            if self.fail_on_batch is not None and idx == self.fail_on_batch:
                return (None, True, ["call_failed"])  # failure → don't advance, stop
            return (batch[-1], False, [])  # success → advance to newest, continue

        self._patch("_process_inbox_batch", _proc)
        return self

    def __exit__(self, *exc):
        for name, fn in self._orig.items():
            setattr(mr, name, fn)

    def run(self):
        mr._run_inbox_stage(self.dry_run)


def caps(n):
    return [f"c{i:02d}" for i in range(n)]


def test_multi_batch_all_succeed():
    print("test: 43 captures → 6 batches, watermark advances per batch")
    with _Harness(caps(43)) as h:
        h.run()
    sizes = [len(b) for _, b in h.batch_calls]
    check(sizes == [8, 8, 8, 8, 8, 3], f"batch sizes {sizes} == [8,8,8,8,8,3]")
    check(len(h.saved_states) == 6, f"saved {len(h.saved_states)} times == 6")
    check(h._saved.get("proj") == "c42", f"final watermark {h._saved.get('proj')} == c42")
    # watermark is monotonic across saves
    seq = [s["proj"] for s in h.saved_states]
    check(seq == sorted(seq), f"watermark monotonic {seq}")


def test_failure_stops_and_preserves_progress():
    print("test: failure on batch 3 stops project, keeps batches 1-2")
    with _Harness(caps(43), fail_on_batch=2) as h:
        h.run()
    # batches 0,1 succeed (+save), batch 2 returns None → break; 3 calls total
    check(len(h.batch_calls) == 3, f"{len(h.batch_calls)} batch calls == 3 (stopped early)")
    check(len(h.saved_states) == 2, f"saved {len(h.saved_states)} times == 2")
    check(h._saved.get("proj") == "c15", f"watermark held at batch-2 max {h._saved.get('proj')} == c15")


def test_single_batch_label_is_slug():
    print("test: ≤8 captures → single batch, label == slug")
    with _Harness(caps(5)) as h:
        h.run()
    check(len(h.batch_calls) == 1, "one batch call")
    check(h.batch_calls[0][0] == "proj", f"label {h.batch_calls[0][0]!r} == 'proj'")
    check(h._saved.get("proj") == "c04", "watermark = last capture")


def test_dry_run_no_save_all_batches():
    print("test: dry-run processes every batch, saves nothing")
    with _Harness(caps(20), dry_run=True) as h:
        h.run()
    check(len(h.batch_calls) == 3, f"{len(h.batch_calls)} batch calls == 3 (all visited)")
    check(h.saved_states == [], "no watermark saves in dry-run")


def main():
    test_multi_batch_all_succeed()
    test_failure_stops_and_preserves_progress()
    test_single_batch_label_is_slug()
    test_dry_run_no_save_all_batches()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
