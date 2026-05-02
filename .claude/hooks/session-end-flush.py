#!/usr/bin/env python3
"""SessionEnd hook: snapshot transcript and fire-and-forget memory_flush.py.

Recursion-guarded: exits 0 immediately if CLAUDE_INVOKED_BY is set.
Without this guard, memory_flush.py's own session would re-trigger SessionEnd
and infinite-loop. Same logic as pre-compact-flush.py with a different source.
Fails open: any exception writes to stderr and exits 0.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))


def main() -> int:
    if os.environ.get("CLAUDE_INVOKED_BY"):
        return 0
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError) as e:
        sys.stderr.write(f"session-end-flush: stdin parse failed ({e})\n")
        return 0
    try:
        from shared import dispatch_flush

        dispatch_flush(data, source="session-end")
    except Exception as e:
        sys.stderr.write(f"session-end-flush: {type(e).__name__}: {e}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
