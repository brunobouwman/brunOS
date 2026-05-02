#!/usr/bin/env python3
"""PreCompact hook: snapshot transcript and fire-and-forget memory_flush.py.

Recursion-guarded: exits 0 immediately if CLAUDE_INVOKED_BY is set.
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
        sys.stderr.write(f"pre-compact-flush: stdin parse failed ({e})\n")
        return 0
    try:
        from shared import dispatch_flush

        dispatch_flush(data, source="pre-compact")
    except Exception as e:
        sys.stderr.write(f"pre-compact-flush: {type(e).__name__}: {e}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
