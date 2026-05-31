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
import fcntl  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import urllib.request  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import (  # noqa: E402
    STATE_DIR,
    _ts_brt,
    load_env,
    load_state,
    now_brt,
    save_state,
    vault_path,
)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
STATE_FILE = STATE_DIR / "vault-sync-state.json"
LOCK_FILE = STATE_DIR / "locks" / "vault-sync.run.lock"
MERGE_DRIVER = REPO_ROOT / "deploy" / "bin" / "git-merge-concat"
REMOTE = "origin"
BRANCH = "main"
NET_TIMEOUT = 90  # seconds for fetch / push
ALERT_REPEAT_SECONDS = 3600  # while failing, re-alert at most hourly
SCHEMA_VERSION = 1
LOG_PREFIX = "[vault-sync]"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class GitError(Exception):
    """A git subprocess returned non-zero."""

    def __init__(self, cmd: str, cp: subprocess.CompletedProcess):
        self.cmd = cmd
        self.cp = cp
        tail = (cp.stderr or cp.stdout or "").strip().splitlines()
        msg = tail[-1] if tail else "(no output)"
        super().__init__(f"git {cmd} failed (rc={cp.returncode}): {msg}")


class SyncConflict(Exception):
    """A merge produced real (non-append-only) conflicts; tree was aborted clean."""

    def __init__(self, paths: list[str], detail: str = ""):
        self.paths = paths
        self.detail = detail
        super().__init__(f"unresolved conflict on: {', '.join(paths) or '(unknown)'}")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}", flush=True)


def _host_label() -> str:
    lbl = os.environ.get("BRUNOS_SYNC_HOST_LABEL", "").strip()
    if lbl:
        return lbl
    return (socket.gethostname() or "unknown").split(".")[0]


def _git(cwd: Path, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _git_ok(cwd: Path, *args: str, timeout: float | None = None) -> str:
    cp = _git(cwd, *args, timeout=timeout)
    if cp.returncode != 0:
        raise GitError(" ".join(args), cp)
    return cp.stdout.strip()


def _count(cwd: Path, rng: str) -> int:
    out = _git_ok(cwd, "rev-list", "--count", rng)
    return int(out or "0")


def _is_push_reject(cp: subprocess.CompletedProcess) -> bool:
    blob = f"{cp.stdout}\n{cp.stderr}".lower()
    return any(
        s in blob
        for s in ("rejected", "non-fast-forward", "fetch first", "updates were rejected")
    )


def _older_than(ts_str: str | None, seconds: float) -> bool:
    """True if ts_str is None or older than `seconds` ago."""
    if not ts_str:
        return True
    try:
        then = datetime.fromisoformat(ts_str)
    except ValueError:
        return True
    return (now_brt() - then).total_seconds() > seconds


# --------------------------------------------------------------------------- #
# Observability
# --------------------------------------------------------------------------- #
def _send_alert(text: str) -> bool:
    """Post an alert to the ops Slack channel. Never raises; returns success."""
    _log(f"ALERT: {text.splitlines()[0]}")
    channel = os.environ.get("BRUNOS_ALERT_CHANNEL", "").strip()
    if not channel:
        _log("ALERT: BRUNOS_ALERT_CHANNEL unset — skipping Slack send")
        return False
    try:
        from integrations import slack

        client = slack._client()
        slack.send_message(client, channel=channel, text=text)
        return True
    except Exception as e:  # noqa: BLE001 — alerting must never crash the sync
        _log(f"ALERT: Slack send failed: {type(e).__name__}: {e}")
        return False


def _healthcheck(success: bool) -> None:
    """Ping the healthchecks.io dead-man's-switch. Never raises."""
    url = os.environ.get("BRUNOS_HEALTHCHECK_URL", "").strip()
    if not url:
        return
    target = url if success else url.rstrip("/") + "/fail"
    try:
        urllib.request.urlopen(target, timeout=10)  # noqa: S310 — fixed trusted URL
    except Exception as e:  # noqa: BLE001
        _log(f"healthcheck ping ({'ok' if success else 'fail'}) failed: {type(e).__name__}")


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
# State + run orchestration
# --------------------------------------------------------------------------- #
def _record_success(state: dict, vault: Path, attempt_ts: str) -> None:
    state.update(
        {
            "_schema_version": SCHEMA_VERSION,
            "host": _host_label(),
            "last_success": _ts_brt(),
            "last_attempt": attempt_ts,
            "last_error": None,
            "behind": _count(vault, f"HEAD..{REMOTE}/{BRANCH}"),
            "ahead": _count(vault, f"{REMOTE}/{BRANCH}..HEAD"),
            "consecutive_failures": 0,
        }
    )
    save_state(STATE_FILE, state)


def _record_failure(
    state: dict, attempt_ts: str, kind: str, msg: str, paths: list[str] | None
) -> None:
    fails = int(state.get("consecutive_failures", 0)) + 1
    sig = f"{kind}:{','.join(paths) if paths else ''}"
    last_sig = (state.get("last_error") or {}).get("signature")
    last_alert = state.get("last_alert_ts")

    state.update(
        {
            "_schema_version": SCHEMA_VERSION,
            "host": _host_label(),
            "last_attempt": attempt_ts,
            "last_error": {
                "type": kind,
                "msg": msg,
                "paths": paths or [],
                "signature": sig,
                "ts": attempt_ts,
            },
            "consecutive_failures": fails,
        }
    )

    # Rate-limited: alert on the first failure, on a changed error signature, or
    # at most hourly while a failure persists — so a stuck sync can't spam Slack
    # every 2 minutes.
    if fails == 1 or sig != last_sig or _older_than(last_alert, ALERT_REPEAT_SECONDS):
        host = _host_label()
        if _send_alert(f"⚠ vault sync FAILED on {host} [{kind}] (failure #{fails})\n{msg}"):
            state["last_alert_ts"] = _ts_brt()

    save_state(STATE_FILE, state)


def _run(dry_run: bool) -> int:
    vault = vault_path()
    state = load_state(STATE_FILE, default={}) or {}
    attempt_ts = _ts_brt()

    try:
        preflight(vault, dry_run=dry_run)
        result = sync(vault, dry_run=dry_run)
    except SyncConflict as e:
        msg = f"unresolved merge conflict on: {', '.join(e.paths) or '(unknown)'} — manual merge needed (tree left clean, retrying)"
        _log(f"FAIL [conflict] {msg}")
        if not dry_run:
            _record_failure(state, attempt_ts, "conflict", msg, e.paths)
            _healthcheck(success=False)
        return 1
    except subprocess.TimeoutExpired as e:
        msg = f"git network op timed out after {e.timeout:.0f}s"
        _log(f"FAIL [timeout] {msg}")
        if not dry_run:
            _record_failure(state, attempt_ts, "timeout", msg, None)
            _healthcheck(success=False)
        return 1
    except (GitError, Exception) as e:  # noqa: BLE001 — any failure → loud + clean
        kind = "git" if isinstance(e, GitError) else type(e).__name__
        _log(f"FAIL [{kind}] {e}")
        if not dry_run:
            _record_failure(state, attempt_ts, kind, str(e), None)
            _healthcheck(success=False)
        return 1

    if dry_run:
        _log(f"DRY ok: would pull {result['pulled']} / push {result['pushed']}")
        return 0

    _record_success(state, vault, attempt_ts)
    _healthcheck(success=True)
    _log(f"ok (pulled {result['pulled']}, pushed {result['pushed']})")
    return 0


def _try_lock() -> int | None:
    """Non-blocking exclusive lock so overlapping ticks don't stack up."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def _unlock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


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
        _send_alert(f"⚠ {args.emit_alert}")
        return 0  # never fail the OnFailure unit

    lock_fd = _try_lock()
    if lock_fd is None:
        _log("another vault_sync run is in progress — skipping this tick")
        return 0
    try:
        return _run(args.dry_run)
    finally:
        _unlock(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
