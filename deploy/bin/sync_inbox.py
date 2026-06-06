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
retired/cleared on the VPS is never removed from the Mac side). -a preserves
mtimes, which --update depends on. Idempotent; safe to run often.

Resurrection guard: the VPS retirement job can publish an rsync exclude file
(`BRUNOS_INBOX_EXCLUDE_FILE`) containing retired capture paths. When present,
this sync passes it to rsync with --exclude-from so a stale Mac copy cannot
recreate a VPS-deleted capture.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / ".claude" / "scripts"))

VPS_HOST = os.environ.get("VPS_HOST", "brunoos")
REMOTE_INBOX = "/home/bruno/BrunOS/Memory/_inbox"
EXCLUDE_FILE_ENV = "BRUNOS_INBOX_EXCLUDE_FILE"


def _reporter():
    """Track D Phase 1: this transport used to fail silently into an untailed
    launchd log. Reports via SyncReporter (status file + Slack + healthchecks.io).
    Returns None when reporting is disabled (tests) or imports fail (never block
    the sync on observability plumbing)."""
    try:
        from shared import load_env
        from sync_common import make_reporter

        load_env()  # BRUNOS_ALERT_CHANNEL + healthcheck URL on launchd runs
        return make_reporter("inbox-rsync", "BRUNOS_INBOX_RSYNC_HEALTHCHECK_URL")
    except Exception as e:  # noqa: BLE001
        print(f"==> reporter unavailable (continuing): {type(e).__name__}: {e}",
              file=sys.stderr)
        return None


def _report(reporter, *, ok: bool, kind: str = "", msg: str = "", extra: dict | None = None) -> None:
    if reporter is None:
        return
    from sync_common import report_outcome

    report_outcome(reporter, ok=ok, kind=kind, msg=msg, extra=extra)


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


def _rsync_cmd(src: Path, dest: str) -> list[str]:
    cmd = ["rsync", "-az", "--update", "-e", "ssh"]
    exclude = os.environ.get(EXCLUDE_FILE_ENV, "").strip()
    if exclude:
        p = Path(exclude).expanduser()
        if p.is_file():
            cmd += ["--exclude-from", str(p)]
        else:
            print(
                f"==> {EXCLUDE_FILE_ENV}={p} is not readable; continuing without excludes",
                file=sys.stderr,
            )
    cmd += [f"{src}/", dest]
    return cmd


def main() -> int:
    reporter = _reporter()
    src = _vault_path() / "Memory" / "_inbox"
    if not src.is_dir():
        print(f"==> no inbox dir at {src} — nothing to sync")
        _report(reporter, ok=True, extra={"note": "no inbox dir"})
        return 0
    dest = f"{VPS_HOST}:{REMOTE_INBOX}/"

    try:
        subprocess.run(
            ["ssh", VPS_HOST, f"mkdir -p {REMOTE_INBOX}"],
            check=True,
        )
    except (subprocess.CalledProcessError, OSError) as e:
        _report(reporter, ok=False, kind="ssh",
                msg=f"ssh mkdir on {VPS_HOST} failed: {type(e).__name__}: {e}")
        raise
    # trailing slash on src → push the contents of _inbox/ into the remote _inbox/.
    r = subprocess.run(_rsync_cmd(src, dest))
    if r.returncode == 0:
        print(f"==> inbox sync done → {dest}")
        _report(reporter, ok=True)
    else:
        print(f"==> rsync exited {r.returncode}", file=sys.stderr)
        _report(reporter, ok=False, kind="rsync", msg=f"rsync exited {r.returncode} → {dest}")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
