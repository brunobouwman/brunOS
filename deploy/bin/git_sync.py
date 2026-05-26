#!/usr/bin/env python3
"""Run simonthum git-sync on the vault from the FDA-granted `uv` launchd context.

WHY this shim exists: the vault lives under ~/Documents (macOS TCC-protected). A
launchd agent that execs /usr/local/bin/git-sync directly gets "Operation not
permitted" reading the vault. `~/.local/bin/uv` already holds Full Disk Access
(granted for codex-watcher), so launching git-sync as `uv run python git_sync.py`
inherits that access and the git-sync subprocess + its git children can read the
vault. Mirrors the codex-watcher / inbox-rsync pattern.

Invocation matches the VPS vault-sync unit exactly: `/usr/local/bin/git-sync`
with no args, cwd = the vault repo. The VPS installs the binary via
bootstrap-bruno.sh; on the Mac install it the same way (one-time, needs sudo):

  sudo curl -fsSL https://raw.githubusercontent.com/simonthum/git-sync/master/git-sync -o /usr/local/bin/git-sync
  sudo chmod +x /usr/local/bin/git-sync

The vault repo must already be configured: branch.main.sync=true,
branch.main.syncNewFiles=true, and the concat-both merge driver registered
(deploy/bin/install-merge-driver.sh) — all done.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GIT_SYNC = "/usr/local/bin/git-sync"


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
    if not os.path.exists(GIT_SYNC):
        print(
            f"==> {GIT_SYNC} not installed — install it (one-time, sudo):\n"
            "    sudo curl -fsSL "
            "https://raw.githubusercontent.com/simonthum/git-sync/master/git-sync "
            "-o /usr/local/bin/git-sync && sudo chmod +x /usr/local/bin/git-sync",
            file=sys.stderr,
        )
        return 1
    vault = _vault_path()
    if not (vault / ".git").is_dir():
        print(f"==> vault repo not found at {vault}", file=sys.stderr)
        return 1
    return subprocess.run([GIT_SYNC], cwd=str(vault)).returncode


if __name__ == "__main__":
    sys.exit(main())
