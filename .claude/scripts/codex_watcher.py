#!/usr/bin/env python3
"""Codex rollout watcher — scheduled "session-ended" detector.

Codex (OpenAI CLI / Desktop) writes each session as a JSONL rollout under
~/.codex/sessions/YYYY/MM/DD/. There is no SessionEnd hook event in Codex,
only Stop (per-turn). We approximate SessionEnd by polling: a rollout whose
file mtime hasn't moved for N minutes is treated as "ended" and dispatched
through the same memory_flush.py pipeline Claude Code sessions use, with
_origin=codex so the Codex-aware parser kicks in.

Per-session dedup state at .claude/data/state/codex_flushed.json maps
session_id → flushed_at_iso. Once flushed, a rollout is never re-flushed.

Invoked from launchd every 5 minutes. Safe to run by hand:
    uv run python .claude/scripts/codex_watcher.py [--dry-run]
        [--idle-minutes N] [--since-days N] [--force]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("CLAUDE_INVOKED_BY", "codex-watcher")

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import (  # noqa: E402
    STATE_DIR,
    dispatch_flush,
    load_state,
    now_brt,
    save_state,
)
from codex_rollout import derive_project_slug, read_session_meta  # noqa: E402


CODEX_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"
WATCHER_STATE_PATH = STATE_DIR / "codex_flushed.json"
DEFAULT_IDLE_MINUTES = 10
DEFAULT_SINCE_DAYS = 7


def _iter_recent_rollouts(since_days: int) -> list[Path]:
    """Return JSONL rollouts modified within `since_days` of now.

    Walks ~/.codex/sessions/YYYY/MM/DD/. Sorting by mtime ascending so the
    oldest stale ones get flushed first if a run touches many at once.
    """
    if not CODEX_SESSIONS_ROOT.exists():
        return []
    cutoff = (now_brt() - timedelta(days=since_days)).timestamp()
    candidates: list[tuple[float, Path]] = []
    for path in CODEX_SESSIONS_ROOT.rglob("rollout-*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        candidates.append((mtime, path))
    candidates.sort()
    return [p for _, p in candidates]


def _load_flushed() -> dict:
    return load_state(WATCHER_STATE_PATH, default={}) or {}


def _save_flushed(state: dict) -> None:
    save_state(WATCHER_STATE_PATH, state)


def _is_idle(path: Path, idle_minutes: int) -> bool:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    age_s = datetime.now().timestamp() - mtime
    return age_s >= idle_minutes * 60


def _flush_one(path: Path, *, dry_run: bool) -> tuple[str, str | None]:
    """Dispatch a single rollout. Returns (status, session_id).

    Status values: 'flushed', 'skip-no-meta', 'skip-no-session-id',
    'skip-empty-cwd', 'dry-run'.
    """
    meta = read_session_meta(path)
    if meta is None:
        return ("skip-no-meta", None)
    session_id = meta.get("id")
    if not session_id:
        return ("skip-no-session-id", None)
    project_slug = derive_project_slug(meta)

    if dry_run:
        return ("dry-run", session_id)

    stdin_data = {
        "session_id": session_id,
        "transcript_path": str(path),
        "_origin": "codex",
    }
    dispatch_flush(
        stdin_data,
        source="codex-watcher",
        project=project_slug,
        default_export="personal",
    )
    return ("flushed", session_id)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Codex rollout watcher")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--idle-minutes", type=int, default=DEFAULT_IDLE_MINUTES)
    p.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS)
    p.add_argument("--force", action="store_true",
                   help="Bypass per-session dedup state.")
    args = p.parse_args(argv)

    rollouts = _iter_recent_rollouts(args.since_days)
    if not rollouts:
        print(f"[codex-watcher] no rollouts in last {args.since_days}d")
        return 0

    flushed_state = _load_flushed()
    n_total = len(rollouts)
    n_idle = 0
    n_already = 0
    n_dispatched = 0
    n_dry = 0
    n_skip = 0

    for path in rollouts:
        if not _is_idle(path, args.idle_minutes):
            continue
        n_idle += 1
        # Peek session_id before dedup decision.
        meta = read_session_meta(path)
        if meta is None:
            n_skip += 1
            continue
        session_id = meta.get("id")
        if not session_id:
            n_skip += 1
            continue
        if not args.force and session_id in flushed_state:
            n_already += 1
            continue
        status, _ = _flush_one(path, dry_run=args.dry_run)
        if status == "dry-run":
            n_dry += 1
            print(f"[codex-watcher] DRY would flush {session_id}  cwd={meta.get('cwd')}  path={path.name}")
        elif status == "flushed":
            n_dispatched += 1
            flushed_state[session_id] = now_brt().isoformat()
            print(f"[codex-watcher] dispatched {session_id}  cwd={meta.get('cwd')}  path={path.name}")
        else:
            n_skip += 1

    if not args.dry_run and n_dispatched:
        _save_flushed(flushed_state)

    print(
        f"[codex-watcher] scanned={n_total} idle={n_idle} "
        f"dispatched={n_dispatched} dry={n_dry} "
        f"already-flushed={n_already} skip={n_skip} "
        f"idle-threshold={args.idle_minutes}m since={args.since_days}d"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
