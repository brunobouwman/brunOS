#!/usr/bin/env python3
"""SessionEnd hook: snapshot transcript and fire-and-forget memory_flush.py.

Recursion-guarded: exits 0 immediately if CLAUDE_INVOKED_BY is set.
Without this guard, memory_flush.py's own session would re-trigger SessionEnd
and infinite-loop. Same logic as pre-compact-flush.py with a different source.
Fails open: any exception writes to stderr and exits 0.

Phase A cross-repo capture: accepts optional --project and --default-export
flags. When invoked from another repo's `.claude/settings.json` by absolute
path, those flags route the capture into BrunOS/Memory/_inbox/sessions/<project>/
instead of today's daily log. Without flags, behaviour is unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--project", default=None)
    p.add_argument("--default-export", dest="default_export", default=None)
    args, _ = p.parse_known_args(argv)
    return args


def main() -> int:
    if os.environ.get("CLAUDE_INVOKED_BY"):
        return 0
    args = _parse_args(sys.argv[1:])
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError) as e:
        sys.stderr.write(f"session-end-flush: stdin parse failed ({e})\n")
        return 0
    try:
        from shared import derive_project_slug, dispatch_flush

        project = args.project if args.project else derive_project_slug()
        dispatch_flush(
            data,
            source="session-end",
            project=project,
            default_export=args.default_export,
        )
    except Exception as e:
        sys.stderr.write(f"session-end-flush: {type(e).__name__}: {e}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
