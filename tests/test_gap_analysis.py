#!/usr/bin/env python3
"""Standalone tests for gap_analysis (no pytest).
Run: uv run python tests/test_gap_analysis.py

Covers the deterministic recency scan (`scan_gaps`): flags stale-active entities,
respects per-folder thresholds, skips closed (archived/done) entities, the
`_`-prefixed meta files, and the finance boundary; plus the once-daily surfacing
guard (`due_today` / `mark_surfaced`) and the notification/daily-log formatters.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".claude" / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "gap_analysis", REPO / ".claude" / "scripts" / "gap_analysis.py"
)
ga = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ga)

from shared import BRT, now_brt  # noqa: E402

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def _write(vault: Path, rel: str, *, status: str, age_days: float, now) -> None:
    """Create Memory/<rel> with frontmatter and an mtime age_days in the past."""
    p = vault / "Memory" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\n"
        f"type: {rel.split('/')[0].rstrip('s')}\n"
        "created: 2026-01-01T09:00-03:00\n"
        "updated: 2026-01-01T09:00-03:00\n"
        f"status: {status}\n"
        "---\n\n"
        f"# {p.stem}\n",
        encoding="utf-8",
    )
    mtime = (now - timedelta(days=age_days)).timestamp()
    import os

    os.utime(p, (mtime, mtime))


def _build_vault(tmp: Path, now):
    # projects threshold 14, clients 21, goals 10
    _write(tmp, "projects/fresh.md", status="active", age_days=2, now=now)
    _write(tmp, "projects/stale.md", status="active", age_days=20, now=now)
    _write(tmp, "projects/archived.md", status="archived", age_days=40, now=now)
    _write(tmp, "projects/_README.md", status="active", age_days=40, now=now)
    _write(tmp, "clients/recent_client.md", status="active", age_days=15, now=now)
    _write(tmp, "clients/old_client.md", status="active", age_days=25, now=now)
    _write(tmp, "goals/this_week.md", status="active", age_days=12, now=now)
    # personal isn't in DEFAULT_FOLDERS, but finance.md is an explicit exclusion
    _write(tmp, "personal/finance.md", status="active", age_days=99, now=now)


def test_scan_flags_stale_active(tmp):
    print("test: scan_gaps flags stale-active entities, most-stale first")
    now = now_brt()
    _build_vault(tmp, now)
    gaps = ga.scan_gaps(now, vault=tmp)
    paths = [g["path"] for g in gaps]
    check("projects/stale.md" in paths, "stale active project flagged (20d > 14)")
    check("clients/old_client.md" in paths, "old client flagged (25d > 21)")
    check("goals/this_week.md" in paths, "goal flagged (12d > 10)")
    check("projects/fresh.md" not in paths, "fresh project not flagged (2d)")
    check(
        "clients/recent_client.md" not in paths,
        "client under its 21d threshold not flagged (15d)",
    )
    check(paths[0] == "clients/old_client.md", "sorted most-stale first (25d leads)")


def test_scan_skips_closed_meta_and_finance(tmp):
    print("test: scan_gaps skips archived, _-prefixed, and finance.md")
    now = now_brt()
    _build_vault(tmp, now)
    gaps = ga.scan_gaps(now, vault=tmp, folders=("projects", "clients", "goals", "personal"))
    paths = [g["path"] for g in gaps]
    check("projects/archived.md" not in paths, "archived status skipped")
    check("projects/_README.md" not in paths, "_-prefixed meta file skipped")
    check("personal/finance.md" not in paths, "finance boundary file skipped")


def test_skips_dated_artifacts(tmp):
    print("test: scan_gaps skips dated review/snapshot artifacts in goals/")
    now = now_brt()
    _write(tmp, "goals/this_month.md", status="active", age_days=20, now=now)
    _write(tmp, "goals/2026-W18-review.md", status="active", age_days=31, now=now)
    _write(tmp, "goals/2026-05-01.md", status="active", age_days=40, now=now)
    gaps = ga.scan_gaps(now, vault=tmp)
    paths = [g["path"] for g in gaps]
    check("goals/this_month.md" in paths, "living goal flagged")
    check("goals/2026-W18-review.md" not in paths, "weekly-review snapshot skipped")
    check("goals/2026-05-01.md" not in paths, "date-stamped artifact skipped")


def test_uniform_days_override(tmp):
    print("test: --days uniform threshold (thresholds={}) overrides per-folder")
    now = now_brt()
    _build_vault(tmp, now)
    # Uniform 30d: only the 40d-mtime files would qualify, but those are
    # archived/_meta/finance and all skipped → no gaps.
    gaps = ga.scan_gaps(now, vault=tmp, thresholds={}, default_days=30)
    check(gaps == [], "uniform 30d threshold → nothing active is that stale")
    # Uniform 5d: every active file older than 5d qualifies.
    gaps5 = ga.scan_gaps(now, vault=tmp, thresholds={}, default_days=5)
    names = {g["name"] for g in gaps5}
    check(
        {"stale", "old_client", "this_week", "recent_client"} <= names,
        "uniform 5d catches all active files past 5d",
    )
    check("fresh" not in names, "2d-old file still under uniform 5d")


def test_once_daily_guard(tmp):
    print("test: due_today / mark_surfaced once-daily guard")
    now = now_brt()
    state = tmp / "gap-state.json"
    check(ga.due_today(now, state) is True, "no state → due")
    ga.mark_surfaced(now, [{"name": "x"}], state)
    check(ga.due_today(now, state) is False, "same day after mark → not due")
    check(ga.due_today(now + timedelta(days=1), state) is True, "next day → due again")


def test_gaps_to_surface_guard(tmp):
    print("test: gaps_to_surface respects the guard")
    now = now_brt()
    _build_vault(tmp, now)
    state = tmp / "gap-state2.json"
    first = ga.gaps_to_surface(now, state_path=state, vault=tmp)
    check(len(first) > 0, "first call returns gaps")
    ga.mark_surfaced(now, first, state)
    second = ga.gaps_to_surface(now, state_path=state, vault=tmp)
    check(second == [], "second call same day returns [] (already surfaced)")


def test_formatters(tmp):
    print("test: format_summary / format_block / format_table")
    now = now_brt()
    gaps = [
        {"path": "projects/a.md", "name": "a", "days": 30, "threshold": 14, "status": "active"},
        {"path": "clients/b.md", "name": "b", "days": 25, "threshold": 21, "status": "active"},
        {"path": "goals/c.md", "name": "c", "days": 12, "threshold": 10, "status": "active"},
        {"path": "projects/d.md", "name": "d", "days": 11, "threshold": 14, "status": "active"},
    ]
    summ = ga.format_summary(gaps, limit=3)
    check(summ.startswith("4 stale: a 30d, b 25d, c 12d"), "summary lists top-3 + count")
    check("+1 more" in summ, "summary notes overflow")
    check(ga.format_summary([]) == "", "empty gaps → empty summary")
    block = ga.format_block(gaps, now)
    check("## Knowledge gaps" in block and "`projects/a.md`" in block, "block has header + paths")
    table = ga.format_table(gaps)
    check("ENTITY" in table and "projects/a.md" in table, "table renders")
    check("No knowledge gaps" in ga.format_table([]), "empty table message")


def main():
    import tempfile

    tests = [
        test_scan_flags_stale_active,
        test_scan_skips_closed_meta_and_finance,
        test_skips_dated_artifacts,
        test_uniform_days_override,
        test_once_daily_guard,
        test_gaps_to_surface_guard,
        test_formatters,
    ]
    for t in tests:
        with tempfile.TemporaryDirectory() as d:
            t(Path(d))
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
