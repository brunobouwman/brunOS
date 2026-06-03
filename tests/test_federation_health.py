#!/usr/bin/env python3
"""Standalone tests for federation_doctor.health_verdict (no pytest).
Run: uv run python tests/test_federation_health.py

The doctor is the INDEPENDENT state-health dead-man's-switch (reflect proves the
writer ran; this proves on-disk state is healthy). Violations: oldest uncleared
capture older than the threshold (reflection stuck), quarantined captures present,
or a continuity doc over its cap (compaction not keeping up — the 2026-06-03
vertik deadlock symptom).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "federation_doctor", REPO / ".claude" / "scripts" / "federation_doctor.py"
)
fd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fd)

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def _stat(slug, **kw):
    base = {
        "slug": slug,
        "oldest_uncleared": None,
        "quarantined": 0,
        "project_doc_bytes": 0,
    }
    base.update(kw)
    return base


def test_all_healthy():
    print("test: clean inboxes → ok, no violations")
    v = fd.health_verdict([
        _stat("vertik", oldest_uncleared="2099-01-01T00:00:00-03:00", project_doc_bytes=4000),
        _stat("colinas"),
    ])
    check(v["ok"] is True, "ok == True")
    check(v["violations"] == [], "no violations")
    check(v["slugs"] == [], "no flagged slugs")


def test_stale_uncleared():
    print("test: capture uncleared since 2020 → stale violation")
    v = fd.health_verdict([_stat("vertik", oldest_uncleared="2020-01-01T00:00:00-03:00")])
    check(v["ok"] is False, "ok == False")
    check(any("oldest uncleared" in x for x in v["violations"]), "stale violation present")
    check(v["slugs"] == ["vertik"], "vertik flagged")


def test_quarantined():
    print("test: quarantined captures → violation")
    v = fd.health_verdict([_stat("vertik", quarantined=2)])
    check(v["ok"] is False, "ok == False")
    check(any("quarantined" in x for x in v["violations"]), "quarantine violation present")


def test_over_cap_doc():
    print("test: continuity doc over cap → violation (the deadlock symptom)")
    v = fd.health_verdict([_stat("vertik", project_doc_bytes=24769)])
    check(v["ok"] is False, "ok == False")
    check(any("compaction not keeping up" in x for x in v["violations"]), "over-cap violation present")
    # The pre-fix vertik.md (24769B) would have tripped this every day.


def test_under_cap_doc_ok():
    print("test: continuity doc just under cap (8120B) → no violation")
    v = fd.health_verdict([_stat("vertik", project_doc_bytes=8120)])
    check(v["ok"] is True, "8120B < 8192 cap → ok")


def test_multiple_inboxes_isolate():
    print("test: one bad inbox flags only itself")
    v = fd.health_verdict([
        _stat("vertik", project_doc_bytes=30000),
        _stat("colinas", oldest_uncleared="2099-01-01T00:00:00-03:00"),
    ])
    check(v["slugs"] == ["vertik"], f"only vertik flagged (got {v['slugs']})")


def main():
    test_all_healthy()
    test_stale_uncleared()
    test_quarantined()
    test_over_cap_doc()
    test_under_cap_doc_ok()
    test_multiple_inboxes_isolate()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
