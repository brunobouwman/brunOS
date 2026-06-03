#!/usr/bin/env python3
"""Standalone tests for memory_reflect's monitoring verdict (no pytest).
Run: uv run python tests/test_reflect_monitoring.py

Verifies _run emits exactly one success/failure verdict covering BOTH stages,
ONLY on a full scheduled run (both stages, no --project, not --dry-run), and that
one project's soft failure is reported (with affected slugs) without masking the
others. This is the BaaS-critical part: the dead-man's-switch must go green only
when the whole pipeline was clean.
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


class _harness:
    """Patch the two stages + the reporter; record verdict calls."""

    def __init__(self, daily=(0, []), inbox=None, inbox_raises=False):
        self.daily = daily
        self.inbox = inbox if inbox is not None else []
        self.inbox_raises = inbox_raises
        self.calls = []  # ("success"|"failure", kwargs)
        self._orig = {}

    def _patch(self, name, fn):
        self._orig[name] = getattr(mr, name)
        setattr(mr, name, fn)

    def __enter__(self):
        self._patch("_log", lambda *a, **k: None)
        self._patch("_run_daily_stage", lambda dry: self.daily)

        def _inbox(dry, only_project=None):
            if self.inbox_raises:
                raise RuntimeError("boom")
            return list(self.inbox)

        self._patch("_run_inbox_stage", _inbox)

        rep = mr.REFLECT_REPORTER
        self._orig_rep = (rep.load, rep.record_success, rep.record_failure)
        rep.load = lambda: {}
        rep.record_success = lambda state, ts, *a, **k: self.calls.append(("success", k))
        rep.record_failure = lambda state, ts, *a, **k: self.calls.append(("failure", {"kind": (a[0] if a else k.get("kind")), **k}))
        return self

    def __exit__(self, *exc):
        for name, fn in self._orig.items():
            setattr(mr, name, fn)
        rep = mr.REFLECT_REPORTER
        rep.load, rep.record_success, rep.record_failure = self._orig_rep


def test_clean_full_run_records_success():
    print("test: both stages clean → exactly one success verdict")
    with _harness(daily=(0, []), inbox=[]) as h:
        rc = mr._run(dry_run=False)
    check(rc == 0, "rc == 0")
    check([c[0] for c in h.calls] == ["success"], f"one success ({[c[0] for c in h.calls]})")


def test_inbox_soft_failure_records_failure():
    print("test: one project json_parse fail → failure verdict, slug in paths")
    with _harness(daily=(0, []), inbox=[("vertik", "json_parse")]) as h:
        rc = mr._run(dry_run=False)
    check(len(h.calls) == 1 and h.calls[0][0] == "failure", "one failure verdict")
    kw = h.calls[0][1]
    check("json_parse" in str(kw.get("kind")), f"kind has json_parse ({kw.get('kind')})")
    check(kw.get("paths") == ["vertik"], f"paths == ['vertik'] ({kw.get('paths')})")


def test_other_projects_not_masked():
    print("test: failure lists only the failed slug; success path still distinct")
    with _harness(daily=(0, []), inbox=[("vertik", "quarantined")]) as h:
        mr._run(dry_run=False)
    kw = h.calls[0][1]
    check(kw.get("paths") == ["vertik"], "only vertik flagged (colinas unaffected)")


def test_daily_hard_failure():
    print("test: daily rc=1 → failure verdict even if inbox clean")
    with _harness(daily=(1, ["daily_call_failed"]), inbox=[]) as h:
        rc = mr._run(dry_run=False)
    check(rc == 1, "rc == 1")
    check(h.calls[0][0] == "failure", "failure verdict")


def test_inbox_crash_records_failure():
    print("test: inbox stage crash → rc=1 + failure verdict")
    with _harness(daily=(0, []), inbox_raises=True) as h:
        rc = mr._run(dry_run=False)
    check(rc == 1, "rc == 1")
    check(h.calls and h.calls[0][0] == "failure", "failure verdict on crash")


def test_dry_run_no_verdict():
    print("test: --dry-run emits NO verdict (doesn't touch the switch)")
    with _harness(daily=(0, []), inbox=[]) as h:
        mr._run(dry_run=True)
    check(h.calls == [], "no verdict in dry-run")


def test_partial_run_no_verdict():
    print("test: --project / --inbox-only emits NO verdict (not a full run)")
    with _harness(daily=(0, []), inbox=[]) as h:
        mr._run(dry_run=False, do_daily=False, only_project="vertik")
    check(h.calls == [], "no verdict for partial run")


def main():
    test_clean_full_run_records_success()
    test_inbox_soft_failure_records_failure()
    test_other_projects_not_masked()
    test_daily_hard_failure()
    test_inbox_crash_records_failure()
    test_dry_run_no_verdict()
    test_partial_run_no_verdict()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
