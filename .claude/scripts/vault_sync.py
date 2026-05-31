#!/usr/bin/env python3
"""Reliable vault git-sync — owns the sync transaction end-to-end.

Replaces simonthum's ``/usr/local/bin/git-sync`` for the BrunOS vault. We own
both ends of this sync (Mac + VPS), so we own the loop instead of depending on
a tool whose failure semantics keep biting us:

  * simonthum ``git rebase``s on divergence and, on ANY real conflict, leaves a
    half-rebased tree and exits 1 — every subsequent 2-min run then refuses
    until a human intervenes. That dead-loop silently froze the vault for ~5
    days, twice.
  * it needs per-clone ``branch.<b>.syncNewFiles`` config that drifts (set
    nowhere in provisioning), so untracked files abort every run.
  * it has no alerting / health signal, so the outage was invisible.

This script fixes all three:

  1. **Never leaves a broken tree.** On a real (non-append-only) merge conflict
     it ``git merge --abort``s back to a clean, usable state, alerts loudly, and
     exits non-zero. The next tick simply retries; nothing is bricked or lost.
  2. **Self-heals its own config** every run (``preflight``) — commit identity
     + the concat-both merge driver — so a host cannot be born broken. New files
     are handled deterministically via our own ``git add -A`` (no syncNewFiles).
  3. **Observable.** Writes a status file, fires a rate-limited Slack alert on
     failure, and pings a healthchecks.io dead-man's-switch every run so a TOTAL
     outage (timer/host dead) is caught externally within the grace window.

Append-only files (``Memory/daily/*.md``, ``Memory/HABITS.md``) auto-merge via
the concat-both driver registered in ``.gitattributes`` — those never surface as
conflicts.

Invocation (both hosts, via uv so macOS Full-Disk-Access inherits to git):
    uv run python .claude/scripts/vault_sync.py            # one sync pass
    uv run python .claude/scripts/vault_sync.py --dry-run  # report only, no writes
    uv run python .claude/scripts/vault_sync.py --emit-alert "<msg>"  # systemd OnFailure
"""

from __future__ import annotations

import os

# Recursion-guard / telemetry marker, consistent with the other automated
# scripts. vault_sync imports no Agent SDK, but the marker keeps SessionEnd /
# PreCompact hooks inert for anything this process might spawn.
os.environ.setdefault("CLAUDE_INVOKED_BY", "vault-sync")

import argparse  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import (  # noqa: E402
    STATE_DIR,
    _ts_brt,
    load_env,
    vault_path,
)

# All git helpers + the status-file / Slack-alert / healthcheck / run-lock
# runtime now live in sync_common, shared verbatim with code_sync, so the two
# syncs' observability can't drift. vault_sync keeps only its vault-specific
# transaction logic (concat-both preflight, merge-or-abort, push-retry).
from sync_common import (  # noqa: E402
    NET_TIMEOUT,
    GitError,
    SyncReporter,
    count as _count,
    git as _git,
    git_ok as _git_ok,
    host_label as _host_label,
    is_push_reject as _is_push_reject,
)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
STATE_FILE = STATE_DIR / "vault-sync-state.json"
LOCK_FILE = STATE_DIR / "locks" / "vault-sync.run.lock"
MERGE_DRIVER = REPO_ROOT / "deploy" / "bin" / "git-merge-concat"
REMOTE = "origin"
BRANCH = "main"

REPORTER = SyncReporter(
    service="vault-sync",
    status_file=STATE_FILE,
    lock_file=LOCK_FILE,
    healthcheck_env="BRUNOS_HEALTHCHECK_URL",
)

# Status-file + alert + healthcheck + lock all route through REPORTER; the
# free-function names below stay as thin shims so the rest of this module (and
# its test suite) reads unchanged after the migration.
_log = REPORTER.log


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class SyncConflict(Exception):
    """A merge produced real (non-append-only) conflicts; tree was aborted clean."""

    def __init__(self, paths: list[str], detail: str = ""):
        self.paths = paths
        self.detail = detail
        super().__init__(f"unresolved conflict on: {', '.join(paths) or '(unknown)'}")


# --------------------------------------------------------------------------- #
# Sync transaction
# --------------------------------------------------------------------------- #
def preflight(vault: Path, *, dry_run: bool) -> None:
    """Assert + self-heal the config invariants this sync depends on."""
    if not (vault / ".git").is_dir():
        raise RuntimeError(f"vault repo not found at {vault} (.git missing)")

    if not _git(vault, "config", "user.email").stdout.strip():
        label = _host_label()
        if dry_run:
            _log(f"DRY preflight: would set commit identity for {label}")
        else:
            _git(vault, "config", "user.email", f"vault-sync+{label}@brunos.local")
            _git(vault, "config", "user.name", f"BrunOS sync ({label})")
            _log(f"preflight: set commit identity for {label}")

    if not _git(vault, "config", "merge.concat-both.driver").stdout.strip():
        if not MERGE_DRIVER.exists():
            _log(f"preflight: WARN merge driver missing at {MERGE_DRIVER}")
        elif dry_run:
            _log("DRY preflight: would register concat-both merge driver")
        else:
            _git(vault, "config", "merge.concat-both.name", "Concat both sides for append-only files")
            _git(vault, "config", "merge.concat-both.driver", f"{MERGE_DRIVER} %O %A %B %P")
            _log("preflight: registered concat-both merge driver")

    if not (vault / ".gitattributes").exists():
        _log("preflight: WARN .gitattributes missing — concat-both won't apply to daily logs")


def _merge_or_abort(vault: Path) -> None:
    """Merge origin/branch. On real conflict, abort back to a clean tree + raise."""
    cp = _git(
        vault, "-c", "merge.renameLimit=999999", "merge", "--no-edit", f"{REMOTE}/{BRANCH}"
    )
    if cp.returncode == 0:
        return
    # concat-both already auto-resolved any append-only files; whatever remains
    # in --diff-filter=U is a genuine conflict we will not guess at.
    conflicted = [
        ln.strip()
        for ln in _git(vault, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
        if ln.strip()
    ]
    _git(vault, "merge", "--abort")  # CRITICAL: never leave a dead-looped tree
    raise SyncConflict(conflicted, (cp.stderr or "").strip())


def _push(vault: Path) -> None:
    """Push, with one refetch+remerge+retry on a non-fast-forward (two-host race)."""
    cp = _git(vault, "push", REMOTE, BRANCH, timeout=NET_TIMEOUT)
    if cp.returncode == 0:
        return
    if not _is_push_reject(cp):
        raise GitError("push", cp)
    _log("push rejected (two-host race?) — refetch + remerge + retry")
    _git_ok(vault, "fetch", REMOTE, timeout=NET_TIMEOUT)
    _merge_or_abort(vault)  # may raise SyncConflict
    cp2 = _git(vault, "push", REMOTE, BRANCH, timeout=NET_TIMEOUT)
    if cp2.returncode != 0:
        raise GitError("push (retry)", cp2)


def sync(vault: Path, *, dry_run: bool) -> dict:
    """One full sync pass. Returns {'pulled', 'pushed', 'committed'}."""
    _git_ok(vault, "fetch", REMOTE, timeout=NET_TIMEOUT)

    dirty = bool(_git(vault, "status", "--porcelain").stdout.strip())
    committed = False
    if dirty:
        if dry_run:
            _log("DRY: would commit local changes")
        else:
            _git_ok(vault, "add", "-A")
            _git_ok(vault, "commit", "-m", f"{_host_label()} auto-sync {_ts_brt()}")
            committed = True
            _log("committed local changes")

    behind = _count(vault, f"HEAD..{REMOTE}/{BRANCH}")
    pulled = 0
    if behind:
        if dry_run:
            _log(f"DRY: would merge {behind} commit(s) from {REMOTE}/{BRANCH}")
        else:
            _merge_or_abort(vault)
            pulled = behind

    ahead = _count(vault, f"{REMOTE}/{BRANCH}..HEAD")
    pushed = 0
    if ahead:
        if dry_run:
            _log(f"DRY: would push {ahead} commit(s)")
        else:
            _push(vault)
            pushed = ahead

    return {"pulled": pulled, "pushed": pushed, "committed": committed}


# --------------------------------------------------------------------------- #
# Run orchestration (status / alert / healthcheck / lock via REPORTER)
# --------------------------------------------------------------------------- #
def _run(dry_run: bool) -> int:
    vault = vault_path()
    state = REPORTER.load()
    attempt_ts = _ts_brt()

    try:
        preflight(vault, dry_run=dry_run)
        result = sync(vault, dry_run=dry_run)
    except SyncConflict as e:
        msg = f"unresolved merge conflict on: {', '.join(e.paths) or '(unknown)'} — manual merge needed (tree left clean, retrying)"
        _log(f"FAIL [conflict] {msg}")
        if not dry_run:
            REPORTER.record_failure(state, attempt_ts, "conflict", msg, e.paths)
        return 1
    except subprocess.TimeoutExpired as e:
        msg = f"git network op timed out after {e.timeout:.0f}s"
        _log(f"FAIL [timeout] {msg}")
        if not dry_run:
            REPORTER.record_failure(state, attempt_ts, "timeout", msg)
        return 1
    except Exception as e:  # noqa: BLE001 — any failure → loud + clean
        kind = "git" if isinstance(e, GitError) else type(e).__name__
        _log(f"FAIL [{kind}] {e}")
        if not dry_run:
            REPORTER.record_failure(state, attempt_ts, kind, str(e))
        return 1

    if dry_run:
        _log(f"DRY ok: would pull {result['pulled']} / push {result['pushed']}")
        return 0

    REPORTER.record_success(
        state,
        attempt_ts,
        extra={
            "behind": _count(vault, f"HEAD..{REMOTE}/{BRANCH}"),
            "ahead": _count(vault, f"{REMOTE}/{BRANCH}..HEAD"),
        },
    )
    _log(f"ok (pulled {result['pulled']}, pushed {result['pushed']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(description="Reliable vault git-sync.")
    parser.add_argument(
        "--dry-run", action="store_true", help="report planned actions; write nothing"
    )
    parser.add_argument(
        "--emit-alert",
        metavar="MSG",
        help="send MSG to the ops Slack channel and exit (used by systemd OnFailure)",
    )
    args = parser.parse_args(argv)

    if args.emit_alert is not None:
        REPORTER.send_alert(f"⚠ {args.emit_alert}")
        return 0  # never fail the OnFailure unit

    if not REPORTER.try_lock():
        _log("another vault_sync run is in progress — skipping this tick")
        return 0
    try:
        return _run(args.dry_run)
    finally:
        REPORTER.unlock()


if __name__ == "__main__":
    sys.exit(main())
