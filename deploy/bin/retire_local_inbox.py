#!/usr/bin/env python3
"""Producer-side retirement of local (Mac) inbox captures.

The Mac is the inbox PRODUCER: hooks write captures, `sync_inbox.py` rsyncs them
one-way to the VPS, and reflection runs ONLY on the VPS (stamping share_status:
cleared, consolidating folders, eventually retiring). `_inbox/` is gitignored and
the rsync is one-way, so the cleared status NEVER comes back — the Mac keeps its
originals as `active` forever, which (a) is stale cruft and (b) is a failover
hazard: a Mac reflection run (no local watermark) would reprocess every `active`
capture and duplicate-promote.

This job converges the Mac by RETIRING (deleting) a local capture once the VPS
has the same capture in a TERMINAL status — i.e. its knowledge is already
extracted (and already back on the Mac via the git-synced MEMORY.md /
projects/<slug>.md). The Mac copy is then truly redundant.

SAFETY (this deletes vault files):
  - dry-run by default; pass --apply to actually delete.
  - never deletes if the VPS terminal set is empty/unavailable (fail-safe).
  - --min-age-hours grace so a just-rsynced capture is never retired.
  - matches by CANONICAL slug + filename, so a pre-fix stray (vertik-lab-agent/X)
    is retired when the VPS has the consolidated vertik/X cleared.

Usage:
  uv run python deploy/bin/retire_local_inbox.py [--apply] [--min-age-hours N]
      [--terminal-status cleared,quarantined] [--vps-ssh-host brunoos]
      [--vps-inbox-path /home/bruno/BrunOS/Memory/_inbox/sessions]
      [--vps-state-file PATH]   # offline/test: file of "<slug>/<filename>" lines
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import (  # noqa: E402
    canonicalize_slug,
    load_env,
    now_brt,
    parse_capture,
    parse_iso,
    vault_path,
)
from sync_common import make_reporter, report_outcome  # noqa: E402

load_env()

DEFAULT_SSH_HOST = "brunoos"
DEFAULT_VPS_INBOX = "/home/bruno/BrunOS/Memory/_inbox/sessions"


def _log(msg: str) -> None:
    sys.stdout.write(msg + "\n")


def _vps_terminal_set_via_ssh(host: str, inbox_path: str, statuses: list[str]) -> set[str] | None:
    """Return {"<slug>/<filename>"} of VPS captures in a terminal status, or None on failure.

    One round trip: grep the VPS inbox for the terminal share_status lines and
    print each matching file path, which we reduce to slug/filename.
    """
    alt = "|".join(statuses)
    remote = (
        f"grep -rlE '^share_status: ({alt})' {inbox_path}/*/ 2>/dev/null || true"
    )
    try:
        out = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, remote],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        _log(f"  ! VPS ssh failed ({type(e).__name__}: {e})")
        return None
    if out.returncode != 0:
        _log(f"  ! VPS ssh returned {out.returncode}: {out.stderr.strip()[:200]}")
        return None
    result: set[str] = set()
    for line in out.stdout.splitlines():
        p = line.strip()
        if not p.endswith(".md"):
            continue
        parts = Path(p).parts
        if len(parts) >= 2:
            result.add(_canon_key(parts[-2], parts[-1]))
    return result


def _canon_key(slug: str, filename: str) -> str:
    """Key both sides by CANONICAL slug + filename so a Mac stray (vertik-lab-agent/X)
    matches the VPS copy whether or not the VPS folders were consolidated."""
    return f"{canonicalize_slug(slug) or slug}/{filename}"


def _terminal_set_from_file(path: Path) -> set[str] | None:
    try:
        out: set[str] = set()
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            slug, _, name = ln.rpartition("/")
            out.add(_canon_key(slug, name) if slug else ln)
        return out
    except OSError as e:
        _log(f"  ! state file unreadable ({e})")
        return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    ap.add_argument("--min-age-hours", type=float, default=48.0)
    ap.add_argument("--terminal-status", default="cleared,quarantined")
    ap.add_argument("--vps-ssh-host", default=None)
    ap.add_argument("--vps-inbox-path", default=DEFAULT_VPS_INBOX)
    ap.add_argument("--vps-state-file", default=None, help="offline override (tests)")
    ap.add_argument("--vault", default=None)
    args = ap.parse_args(argv)

    # Track D Phase 1: report scheduled runs (status file + Slack + healthchecks.io).
    # DELIBERATELY reports in dry-run mode too — the launchd unit runs dry-run
    # during its review period, and the dead-man switch must prove the job runs
    # either way. --vps-state-file (offline/test mode) stays silent.
    reporter = None if args.vps_state_file else make_reporter(
        "inbox-retire", "BRUNOS_INBOX_RETIRE_HEALTHCHECK_URL"
    )

    vault = Path(args.vault).expanduser() if args.vault else vault_path()
    sessions = vault / "Memory" / "_inbox" / "sessions"
    if not sessions.is_dir():
        _log(f"no inbox sessions dir at {sessions}; nothing to do")
        report_outcome(reporter, ok=True, extra={"note": "no inbox sessions dir"})
        return 0

    statuses = [s.strip() for s in args.terminal_status.split(",") if s.strip()]

    if args.vps_state_file:
        terminal = _terminal_set_from_file(Path(args.vps_state_file))
    else:
        host = args.vps_ssh_host or DEFAULT_SSH_HOST
        _log(f"querying VPS ({host}) for terminal captures …")
        terminal = _vps_terminal_set_via_ssh(host, args.vps_inbox_path, statuses)

    # Fail-safe: never delete on an unavailable or empty terminal set.
    if terminal is None:
        _log("ABORT: could not read VPS terminal set — nothing retired (fail-safe).")
        report_outcome(reporter, ok=False, kind="terminal-set",
                       msg="could not read VPS terminal set (ssh failure?) — fail-safe abort")
        return 2
    if not terminal:
        _log("ABORT: VPS terminal set is empty — refusing to delete (fail-safe).")
        report_outcome(reporter, ok=False, kind="terminal-set",
                       msg="VPS terminal set is empty — fail-safe abort")
        return 2
    _log(f"VPS reports {len(terminal)} terminal capture(s).")

    cutoff = now_brt() - timedelta(hours=args.min_age_hours)
    retire: list[Path] = []
    too_young = not_terminal = 0

    for d in sorted(sessions.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        canon = canonicalize_slug(d.name) or d.name
        for cap in sorted(d.glob("*.md")):
            key = f"{canon}/{cap.name}"
            if key not in terminal:
                not_terminal += 1
                continue
            parsed = parse_capture(cap)
            created = parse_iso(parsed[0].get("created")) if parsed else None
            if created is not None and created > cutoff:
                too_young += 1
                continue
            retire.append(cap)

    _log(
        f"\n{'APPLY' if args.apply else 'DRY-RUN'}: {len(retire)} local capture(s) to retire "
        f"({not_terminal} not-yet-terminal on VPS, {too_young} within {args.min_age_hours}h grace)"
    )
    by_slug: dict[str, int] = {}
    for cap in retire:
        by_slug[cap.parent.name] = by_slug.get(cap.parent.name, 0) + 1
    for slug, n in sorted(by_slug.items()):
        _log(f"  {slug}: {n}")

    if not args.apply:
        _log("\n(dry-run — re-run with --apply to delete)")
        report_outcome(reporter, ok=True, extra={
            "mode": "dry-run", "retire_candidates": len(retire),
            "not_terminal": not_terminal, "too_young": too_young,
        })
        return 0

    deleted = 0
    for cap in retire:
        try:
            cap.unlink()
            deleted += 1
        except OSError as e:
            _log(f"  ! failed to delete {cap}: {e}")
    # remove now-empty stray dirs (canonical dirs with live captures stay).
    for d in sorted(sessions.iterdir()):
        if d.is_dir() and not d.name.startswith("_") and not any(d.glob("*.md")):
            try:
                d.rmdir()
                _log(f"  removed empty folder {d.name}/")
            except OSError:
                pass
    _log(f"\nretired {deleted} capture(s).")
    report_outcome(reporter, ok=True, extra={
        "mode": "apply", "retired": deleted, "retire_candidates": len(retire),
        "not_terminal": not_terminal, "too_young": too_young,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
