#!/usr/bin/env python3
"""One-shot Codex rollout backfill — serialize last N days into the inbox.

Reuses the watcher's discovery + dedup state, but dispatches synchronously
(serialized) so the Sonnet distillation calls don't fan out into 30+
concurrent processes at once.

Run by hand:
    uv run python .claude/scripts/codex_backfill.py --since-days 15
        [--dry-run] [--sleep 0]

Adds successfully-dispatched session_ids to the same watcher state file
(.claude/data/state/codex_flushed.json) so the recurring watcher won't
re-flush them later.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("CLAUDE_INVOKED_BY", "codex-backfill")

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import (  # noqa: E402
    dispatch_flush,
    load_state,
    now_brt,
    save_state,
)
from codex_rollout import derive_project_slug, read_session_meta  # noqa: E402
from codex_watcher import (  # noqa: E402
    WATCHER_STATE_PATH,
    _iter_recent_rollouts,
)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Codex rollout backfill")
    p.add_argument("--since-days", type=int, default=15)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Seconds to wait between dispatches. 0 = back-to-back (sync mode "
             "already serializes, so any positive value just adds breathing "
             "room for rate-limit headroom).",
    )
    p.add_argument("--force", action="store_true",
                   help="Bypass per-session dedup state.")
    args = p.parse_args(argv)

    rollouts = _iter_recent_rollouts(args.since_days)
    if not rollouts:
        print(f"[codex-backfill] no rollouts in last {args.since_days}d")
        return 0

    flushed_state = load_state(WATCHER_STATE_PATH, default={}) or {}

    candidates: list[tuple[Path, dict]] = []
    n_no_meta = 0
    n_already = 0
    for path in rollouts:
        meta = read_session_meta(path)
        if meta is None:
            n_no_meta += 1
            continue
        sid = meta.get("id")
        if not sid:
            n_no_meta += 1
            continue
        if not args.force and sid in flushed_state:
            n_already += 1
            continue
        candidates.append((path, meta))

    print(
        f"[codex-backfill] scanned={len(rollouts)} candidates={len(candidates)} "
        f"no-meta={n_no_meta} already-flushed={n_already} "
        f"since={args.since_days}d sync=True sleep={args.sleep}s"
    )

    if args.dry_run:
        for path, meta in candidates:
            print(
                f"[codex-backfill] DRY would flush {meta.get('id')}  "
                f"cwd={meta.get('cwd')}  path={path.name}"
            )
        return 0

    n_dispatched = 0
    for idx, (path, meta) in enumerate(candidates, start=1):
        sid = meta["id"]
        cwd = meta.get("cwd")
        project_slug = derive_project_slug(meta)
        stdin_data = {
            "session_id": sid,
            "transcript_path": str(path),
            "_origin": "codex",
        }
        print(
            f"[codex-backfill] ({idx}/{len(candidates)}) dispatching {sid}  "
            f"cwd={cwd}  slug={project_slug}  path={path.name}"
        )
        dispatch_flush(
            stdin_data,
            source="codex-backfill",
            project=project_slug,
            default_export="personal",
            sync=True,
        )
        flushed_state[sid] = now_brt().isoformat()
        # Persist state after every dispatch so a mid-run interrupt doesn't
        # cause re-flushing on resume.
        save_state(WATCHER_STATE_PATH, flushed_state)
        n_dispatched += 1
        if args.sleep > 0 and idx < len(candidates):
            time.sleep(args.sleep)

    print(f"[codex-backfill] done. dispatched={n_dispatched}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
