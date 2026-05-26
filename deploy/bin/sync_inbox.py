#!/usr/bin/env python3
"""Mac → VPS one-way push of the gitignored Memory/_inbox/ session captures.

WHY this exists (and why it's Python, run via `uv`, not a bash launchd job):
  The repo + vault live under ~/Documents, which macOS TCC protects. A launchd
  agent that shells out via /bin/bash gets "Operation not permitted" reading the
  vault. But `~/.local/bin/uv` already holds Full Disk Access (granted for the
  codex-watcher agent), so running this via `uv run python` inherits that access
  — no /bin/bash FDA grant and no repo move needed. The plist's ProgramArguments
  is therefore `uv run python deploy/bin/sync_inbox.py`, mirroring codex-watcher.

WHY rsync and not git-sync: Memory/_inbox/ is gitignored (raw, pre-strip
captures carry personal context), so git-sync never carries it. This is its
dedicated transport. The refined outputs the VPS produces (MEMORY.md personal
items + projects/<slug>.md continuity) are tracked vault files and flow BACK to
the Mac via git-sync. rsync feeds raw captures in; git-sync carries refined
knowledge out.

Safety: --update never overwrites a newer file on the VPS (so the VPS's
strip-in-place + share_status:cleared rewrites survive the next push — their
mtime is newer than the Mac original); NO --delete (a capture
retired/cleared on the VPS is never resurrected or removed from the Mac side).
-a preserves mtimes, which --update depends on. Idempotent; safe to run often.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VPS_HOST = os.environ.get("VPS_HOST", "brunoos")
REMOTE_INBOX = "/home/bruno/BrunOS/Memory/_inbox"


def _vault_path() -> Path:
    v = os.environ.get("BRUNOS_VAULT_PATH")
    if not v:
        envf = REPO / ".claude" / ".env"
        if envf.exists():
            for line in envf.read_text(encoding="utf-8").splitlines():
                if line.startswith("BRUNOS_VAULT_PATH="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return Path(v) if v else REPO / "BrunOS"


def main() -> int:
    src = _vault_path() / "Memory" / "_inbox"
    if not src.is_dir():
        print(f"==> no inbox dir at {src} — nothing to sync")
        return 0
    dest = f"{VPS_HOST}:{REMOTE_INBOX}/"

    subprocess.run(
        ["ssh", VPS_HOST, f"mkdir -p {REMOTE_INBOX}"],
        check=True,
    )
    # trailing slash on src → push the contents of _inbox/ into the remote _inbox/.
    r = subprocess.run(
        ["rsync", "-az", "--update", "-e", "ssh", f"{src}/", dest],
    )
    if r.returncode == 0:
        print(f"==> inbox sync done → {dest}")
    else:
        print(f"==> rsync exited {r.returncode}", file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
