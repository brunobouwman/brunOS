#!/usr/bin/env python3
"""PreCompact hook: snapshot transcript and fire-and-forget memory_flush.py.

Recursion-guarded: exits 0 immediately if CLAUDE_INVOKED_BY is set.
Fails open: any exception writes to stderr and exits 0.

Phase A cross-repo capture: accepts optional --project and --default-export
flags. See session-end-flush.py for details — same routing logic.
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
        sys.stderr.write(f"pre-compact-flush: stdin parse failed ({e})\n")
        return 0
    try:
        from shared import dispatch_flush

        dispatch_flush(
            data,
            source="pre-compact",
            project=args.project,
            default_export=args.default_export,
        )
    except Exception as e:
        sys.stderr.write(f"pre-compact-flush: {type(e).__name__}: {e}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
