#!/usr/bin/env python3
"""Memory doctor — Track D Phase 1 health probe for the RAG memory layer.

"The brain can't do memory search" was completely invisible before this: a
corrupted/stale memory.db just makes every search return garbage or nothing,
heartbeat logs a re-index failure to stderr and carries on, and the agent
quietly gets dumber. This doctor makes that failure mode page someone.

Three checks:
  1. DB openable + sane — sqlite connects, PRAGMA quick_check passes, and the
     schema is non-empty.
  2. Index freshness — the newest .md mtime under <vault>/Memory/ vs the DB
     file's mtime. The DB is touched by every (re)index, so a DB that hasn't
     moved for hours after vault changes means indexing stopped (dead
     heartbeat stage 1, wedged indexer, …). Threshold --staleness-hours
     (default 3; the daily timer runs at 09:15 BRT, after the 08:00/08:30
     heartbeat ticks have reindexed).
  3. Search canary — run memory_search.py with a known query end-to-end
     (embedding model load → vector+FTS retrieval → RRF) and assert it returns
     ≥1 result. Query override: BRUNOS_SEARCH_CANARY_QUERY or --query.

Reports via the shared SyncReporter: status file + rate-limited Slack alert +
healthchecks.io dead-man's-switch (BRUNOS_MEMORY_DOCTOR_HEALTHCHECK_URL).
Kept standalone (not folded into federation_doctor) so federation health and
memory health page independently — and to avoid churning a file the retrieval
work is actively editing.

CLI:
  --dry-run            evaluate + print, no reporting
  --skip-canary        skip the search canary (fast mode: db + staleness only)
  --staleness-hours N  freshness threshold (default 3)
  --query TEXT         canary query override
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import STATE_DIR, _ts_brt, load_env, vault_path  # noqa: E402
from sync_common import make_reporter  # noqa: E402

load_env()

DB_PATH = STATE_DIR / "memory.db"
DEFAULT_STALENESS_HOURS = 3.0
DEFAULT_CANARY_QUERY = "What are Bruno's active projects and recent decisions?"
CANARY_TIMEOUT = 240  # embedding model load can be slow on a cold cache


def _log(msg: str) -> None:
    print(msg, flush=True)


# --- pure helpers (unit-tested in tests/test_memory_doctor.py) ---


def newest_md_mtime(memory_dir: Path) -> float | None:
    """Newest mtime across all .md files under Memory/ (None if none found)."""
    newest: float | None = None
    if not memory_dir.is_dir():
        return None
    for p in memory_dir.rglob("*.md"):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if newest is None or m > newest:
            newest = m
    return newest


def staleness_violation(
    db_mtime: float | None,
    vault_newest: float | None,
    max_hours: float,
) -> str | None:
    """Index-freshness verdict. The index is stale when the vault has changed
    and the DB hasn't been touched within max_hours of that change."""
    if db_mtime is None:
        return "memory.db missing — index never built on this host?"
    if vault_newest is None:
        return None  # empty vault: nothing to be stale against
    lag_hours = (vault_newest - db_mtime) / 3600.0
    if lag_hours > max_hours:
        return (
            f"index stale: vault changed {lag_hours:.1f}h after the last index "
            f"write (> {max_hours:.1f}h) — is heartbeat/memory_index running?"
        )
    return None


def parse_canary_output(stdout: str) -> int | None:
    """Result count from memory_search.py JSON output. None = unparseable."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    return len(data)


# --- probes ---


def check_db(db_path: Path = DB_PATH) -> tuple[str | None, dict]:
    """(violation_or_None, extra). Opens read-only; quick_check for corruption."""
    extra: dict = {}
    if not db_path.exists():
        return f"memory.db missing at {db_path}", extra
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
            if not row or str(row[0]).lower() != "ok":
                return f"memory.db quick_check failed: {row[0] if row else '?'}", extra
            n_tables = con.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            extra["db_tables"] = int(n_tables)
            if n_tables == 0:
                return "memory.db has no tables — empty/never-indexed DB", extra
        finally:
            con.close()
    except sqlite3.Error as e:
        return f"memory.db unreadable: {type(e).__name__}: {e}", extra
    return None, extra


def run_canary(query: str) -> tuple[str | None, dict]:
    """(violation_or_None, extra). Full end-to-end search via memory_search.py."""
    script = REPO_ROOT / ".claude" / "scripts" / "memory_search.py"
    cmd = [sys.executable, str(script), query, "--k", "3"]
    try:
        cp = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CANARY_TIMEOUT,
            cwd=str(REPO_ROOT),
        )
    except subprocess.TimeoutExpired:
        return f"search canary timed out ({CANARY_TIMEOUT}s)", {}
    except OSError as e:
        return f"search canary failed to launch: {e}", {}
    if cp.returncode != 0:
        tail = (cp.stderr or cp.stdout or "").strip().splitlines()[-1:]
        return f"search canary exited {cp.returncode}: {tail[0] if tail else ''}", {}
    n = parse_canary_output(cp.stdout)
    if n is None:
        return "search canary output is not a JSON list", {}
    if n < 1:
        return f"search canary returned 0 results for {query!r}", {"canary_results": 0}
    return None, {"canary_results": n}


# --- main ---


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Memory-layer health probe (Track D Phase 1)")
    ap.add_argument("--dry-run", action="store_true", help="evaluate + print, no reporting")
    ap.add_argument("--skip-canary", action="store_true", help="db + staleness only")
    ap.add_argument("--staleness-hours", type=float, default=DEFAULT_STALENESS_HOURS)
    ap.add_argument("--query", default=os.environ.get(
        "BRUNOS_SEARCH_CANARY_QUERY", DEFAULT_CANARY_QUERY))
    args = ap.parse_args(argv[1:])

    try:
        vault = vault_path()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    violations: list[str] = []
    extra: dict = {}

    v, e = check_db()
    extra.update(e)
    if v:
        violations.append(v)

    db_mtime = DB_PATH.stat().st_mtime if DB_PATH.exists() else None
    vault_newest = newest_md_mtime(vault / "Memory")
    extra["db_mtime"] = db_mtime
    extra["vault_newest_md_mtime"] = vault_newest
    # A missing DB is already check_db's violation — don't double-report it.
    if db_mtime is not None:
        sv = staleness_violation(db_mtime, vault_newest, args.staleness_hours)
        if sv:
            violations.append(sv)

    # Skip the (slow) canary when the DB is already known-broken — the alert
    # already fires and the canary would just add a redundant failure.
    if not args.skip_canary and not violations:
        cv, ce = run_canary(args.query)
        extra.update(ce)
        if cv:
            violations.append(cv)

    _log(f"memory doctor ({_ts_brt()}): extra={extra} violations={violations or 'none'}")

    if args.dry_run:
        return 1 if violations else 0

    reporter = make_reporter("memory-doctor", "BRUNOS_MEMORY_DOCTOR_HEALTHCHECK_URL")
    if reporter is None:
        return 1 if violations else 0
    state = reporter.load()
    state.update(extra)
    if violations:
        reporter.record_failure(
            state, _ts_brt(), kind="memory-health", msg=" | ".join(violations)
        )
        return 1
    reporter.record_success(state, _ts_brt(), extra=extra)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
