#!/usr/bin/env python3
"""Reliable VPS code-sync — pull-only consumer + slackbot recycle, monitored.

Replaces deploy/bin/code-sync.sh. Never wedge the repo; alert loudly; recover
only what's safe (diverged -> alert, no auto-reset; dirty -> stash + proceed).
"""

from __future__ import annotations

import os

os.environ.setdefault("CLAUDE_INVOKED_BY", "code-sync")

import argparse  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import STATE_DIR, _ts_brt, load_env  # noqa: E402
from sync_common import (  # noqa: E402
    NET_TIMEOUT,
    GitError,
    SyncReporter,
    count,
    git,
    git_ok,
    host_label,
)

REMOTE, BRANCH = "origin", "main"
SLACKBOT_UNIT = "brunoosbrain-slackbot.service"
BOT_CODE_RE = re.compile(
    r"^\.claude/chat/|^\.claude/scripts/(shared|sanitize)\.py$|^\.claude/hooks/session-start-context\.py$"
)

REPORTER = SyncReporter(
    service="code-sync",
    status_file=STATE_DIR / "code-sync-state.json",
    lock_file=STATE_DIR / "locks" / "code-sync.run.lock",
    healthcheck_env="BRUNOS_CODESYNC_HEALTHCHECK_URL",
)


class CodeSyncDiverged(Exception):
    """Pull-only consumer has local commits — ff-only impossible. Needs a human."""


def _recycle_slackbot() -> bool:
    cp = subprocess.run(
        ["sudo", "-n", "/usr/bin/systemctl", "try-restart", SLACKBOT_UNIT],
        capture_output=True,
        text=True,
    )
    if cp.returncode == 0:
        REPORTER.log("slackbot recycled (try-restart)")
        return True
    REPORTER.log("WARN: slackbot recycle failed — check /etc/sudoers.d/brunoosbrain-codesync")
    return False


def preflight(repo: Path, *, dry_run: bool) -> None:
    if not (repo / ".git").is_dir():
        raise RuntimeError(f"code repo not found at {repo} (.git missing)")
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != BRANCH:
        raise CodeSyncDiverged(f"on branch '{branch}', expected '{BRANCH}'")
    if not git(repo, "config", "user.email").stdout.strip():
        label = host_label()
        if dry_run:
            REPORTER.log(f"DRY preflight: would set commit identity for {label}")
        else:
            git(repo, "config", "user.email", f"code-sync+{label}@brunos.local")
            git(repo, "config", "user.name", f"BrunOS code-sync ({label})")


def sync(repo: Path, *, dry_run: bool) -> dict:
    before = git_ok(repo, "rev-parse", "HEAD")
    git_ok(repo, "fetch", REMOTE, timeout=NET_TIMEOUT)
    behind = count(repo, f"HEAD..{REMOTE}/{BRANCH}")
    if not behind:
        return {"pulled": 0, "recycled": False, "stashed": False, "before": before, "after": before}
    if dry_run:
        REPORTER.log(f"DRY: would ff-pull {behind} commit(s)")
        return {"pulled": 0, "recycled": False, "stashed": False, "before": before, "after": before}
    stashed = False
    if git(repo, "status", "--porcelain").stdout.strip():
        git(repo, "stash", "push", "-u", "-m", f"code-sync auto-stash {_ts_brt()}")
        stashed = True
        REPORTER.log("WARN: code repo had local changes — stashed aside before pull")
        REPORTER.send_alert(
            f"⚠ code-sync on {host_label()}: pull-only consumer had local changes — "
            f"stashed them ('git stash list' to inspect)."
        )
    cp = git(repo, "merge", "--ff-only", f"{REMOTE}/{BRANCH}")
    if cp.returncode != 0:
        raise CodeSyncDiverged((cp.stderr or cp.stdout or "ff-only merge failed").strip())
    after = git_ok(repo, "rev-parse", "HEAD")
    changed = [
        ln.strip()
        for ln in git(repo, "diff", "--name-only", before, after).stdout.splitlines()
        if ln.strip()
    ]
    recycled = False
    if any(BOT_CODE_RE.search(p) for p in changed):
        REPORTER.log(f"bot code changed ({before[:9]}..{after[:9]}) — recycling slackbot")
        recycled = _recycle_slackbot()
    else:
        REPORTER.log(f"pulled {before[:9]}..{after[:9]} — no bot-code change, slackbot left running")
    return {"pulled": behind, "recycled": recycled, "stashed": stashed, "before": before, "after": after}


def _run(dry_run: bool) -> int:
    repo = REPO_ROOT
    state = REPORTER.load()
    attempt_ts = _ts_brt()
    try:
        preflight(repo, dry_run=dry_run)
        result = sync(repo, dry_run=dry_run)
    except CodeSyncDiverged as e:
        msg = (
            f"code repo diverged from {REMOTE}/{BRANCH}: {e} — manual reconciliation needed "
            f"(no auto-reset; tree intact)"
        )
        REPORTER.log(f"FAIL [diverged] {msg}")
        if not dry_run:
            REPORTER.record_failure(state, attempt_ts, "diverged", msg)
        return 1
    except subprocess.TimeoutExpired as e:
        msg = f"git network op timed out after {e.timeout:.0f}s"
        REPORTER.log(f"FAIL [timeout] {msg}")
        if not dry_run:
            REPORTER.record_failure(state, attempt_ts, "timeout", msg)
        return 1
    except Exception as e:  # noqa: BLE001
        kind = "git" if isinstance(e, GitError) else type(e).__name__
        REPORTER.log(f"FAIL [{kind}] {e}")
        if not dry_run:
            REPORTER.record_failure(state, attempt_ts, kind, str(e))
        return 1

    if dry_run:
        REPORTER.log(f"DRY ok: would pull {result['pulled']}")
        return 0

    REPORTER.record_success(
        state,
        attempt_ts,
        extra={
            "pulled": result["pulled"],
            "recycled": result["recycled"],
            "stashed": result["stashed"],
            "head": result["after"],
        },
    )
    REPORTER.log(f"ok (pulled {result['pulled']}, recycled={result['recycled']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_env()
    p = argparse.ArgumentParser(description="Reliable VPS code-sync.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--emit-alert", metavar="MSG")
    args = p.parse_args(argv)

    if args.emit_alert is not None:
        REPORTER.send_alert(f"⚠ {args.emit_alert}")
        return 0

    if not REPORTER.try_lock():
        REPORTER.log("another code_sync run is in progress — skipping this tick")
        return 0
    try:
        return _run(args.dry_run)
    finally:
        REPORTER.unlock()


if __name__ == "__main__":
    sys.exit(main())
