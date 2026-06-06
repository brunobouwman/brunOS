#!/usr/bin/env python3
"""VPS-side (consumer-aware) retirement of BrunOS inbox captures — federation F2.

The VPS is where reflection runs AND where both signals needed for the
"fully consumed?" decision co-reside: BrunOS's own processed marker
(`share_status: cleared`, stamped by the reflection inbox stage) and the LinOS
consumer's ack manifest. So the deletion decision lives here, not on the Mac.

Per capture under Memory/_inbox/sessions/<slug>/*.md, DELETE it once:
  - BrunOS has PROCESSED it (`share_status == "cleared"`), AND
  - every company-brain consumer it is destined for has ACKED it (an ack
    manifest `<capture_id>.json` exists in that consumer's ack dir),
  OR, for a company-destined capture a live consumer never acks, a 15-day
  FALLBACK (so a stuck consumer can't pin a cleared capture in the inbox forever).

  - default_export ∈ {personal, discard}  → no external consumer; retire on the
    processed signal alone (+ grace).
  - default_export == linos-protostack     → require the LinOS ack, ELSE fallback.

NEVER retired:
  - `active` (unprocessed) captures — the 15-day fallback relaxes only the ACK
    requirement, never the PROCESSED one. An aging `active` capture means
    reflection is stuck (surfaced by the reflect dead-man / alerts); we do NOT
    silently drop unextracted knowledge.
  - `quarantined` captures — they failed to clear 3× and are kept for manual
    review (fail-safe: never shared, never auto-deleted).

RESURRECTION GUARD — the Mac→VPS rsync (deploy/bin/sync_inbox.py: -a --update, no
--delete) would re-add a VPS-deleted capture from the Mac's surviving original,
where VPS reflection would reprocess it (raw `active` again) → duplicate-promote.
Two layers defend against this:
  1. Mac self-prune (deploy/bin/retire_local_inbox.py) deletes the Mac copy once
     the VPS holds it terminal — the PRIMARY guard, scheduled earlier in the day.
  2. This job appends every retired key to a ledger (retired_inbox.json) and an
     rsync --exclude-from file (inbox-retired-excludes.txt). When that file is
     delivered to the Mac and wired into sync_inbox.py (BRUNOS_INBOX_EXCLUDE_FILE),
     rsync won't push a retired capture back even if the Mac copy survives —
     belt-and-suspenders against a stale Mac that missed step 1.

SAFETY (this deletes vault files):
  - dry-run by default; pass --apply to actually delete.
  - --min-age-hours grace (default 48) — never retire a just-written capture.
  - ack dir unreadable/absent ⇒ company-destined captures do NOT retire, even
    by fallback. The 15-day fallback only activates once the consumer's ack path
    exists, proving the consumer side is deployed. Personal/discard captures are
    unaffected by the ack dir.

DEPLOYMENT — DO NOT ENABLE the timer until the LinOS consumer is live and acking
(see brunoosbrain-inbox-retire-vps.timer). Before then, running with --apply would
retire every cleared linos-protostack capture via the 15-day fallback BEFORE LinOS
ever consumed it. Personal captures are always safe to retire; the gate is the
LinOS-bound ones.

Usage:
  uv run python deploy/bin/retire_vps_inbox.py [--apply] [--min-age-hours N]
      [--fallback-days 15] [--ack-dir DIR] [--vault DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

import os  # noqa: E402

from shared import (  # noqa: E402
    STATE_DIR,
    canonicalize_slug,
    load_env,
    load_state,
    now_brt,
    parse_capture,
    parse_iso,
    save_state,
    vault_path,
)
from sync_common import make_reporter, report_outcome  # noqa: E402

load_env()

DEFAULT_FALLBACK_DAYS = 15
DEFAULT_MIN_AGE_HOURS = 48.0

# Map a capture's default_export → the consumer slug whose ack gates retirement.
# Anything absent here (personal, discard) has no external consumer: a cleared
# capture is fully consumed once BrunOS has processed it.
EXPORT_CONSUMER: dict[str, str] = {
    "linos-protostack": "linos",
}

# Where each consumer's ack manifests land on bruno's side. LinOS writes acks
# into its own 0700 home (LinOS/Memory/_acks/brunos/), which bruno cannot read,
# so the LinOS node delivers them to a bruno-readable dir — mirror of the
# cleared-inbox push in the other direction. Configurable; absent ⇒ wait.
ACK_DIR_ENV = {
    "linos": "BRUNOS_LINOS_ACK_DIR",
}
ACK_DIR_DEFAULT = {
    "linos": "/home/bruno/linos-acks/brunos",
}

RETIRED_LEDGER_PATH = STATE_DIR / "retired_inbox.json"
EXCLUDES_PATH = STATE_DIR / "inbox-retired-excludes.txt"
LEDGER_CAP = 10000


def _log(msg: str) -> None:
    sys.stdout.write(msg + "\n")


# ---------------------------------------------------------------------------
# Ack lookup
# ---------------------------------------------------------------------------


def _ack_dir_for(consumer: str, override: str | None) -> Path:
    if override:
        return Path(override).expanduser()
    env_key = ACK_DIR_ENV.get(consumer)
    val = os.environ.get(env_key) if env_key else None
    return Path(val).expanduser() if val else Path(ACK_DIR_DEFAULT[consumer])


def _make_ack_lookup(override: str | None):
    """Return (acked, ack_dir_available) callables.

    `acked` iff <ack_dir>/<capture_id>.json exists AND parses with a matching
    capture_id (a corrupt/mismatched ack is treated as not-acked → the capture
    waits for the 15-day fallback rather than retiring on a bad signal).

    Fallback is gated separately by ack_dir_available. If the ack dir itself is
    absent/unreadable, the consumer is not considered live and LinOS-bound
    captures wait instead of aging out."""

    dirs: dict[str, Path] = {}

    def ack_dir(consumer: str) -> Path:
        if consumer not in dirs:
            dirs[consumer] = _ack_dir_for(consumer, override)
        return dirs[consumer]

    def available(consumer: str) -> bool:
        return ack_dir(consumer).is_dir()

    def lookup(consumer: str, capture_id: str) -> bool:
        ack_path = ack_dir(consumer) / f"{capture_id}.json"
        if not ack_path.is_file():
            return False
        try:
            data = json.loads(ack_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(data, dict) and data.get("capture_id") == capture_id

    return lookup, available


# ---------------------------------------------------------------------------
# Per-capture decision (pure — the unit under test)
# ---------------------------------------------------------------------------


def classify_capture(
    fm: dict,
    capture_id: str,
    *,
    now,
    min_age_hours: float,
    fallback_days: int,
    ack_lookup,
    ack_available=lambda _consumer: True,
) -> tuple[str, str]:
    """Decide a capture's fate. Returns (action, reason).

    action ∈ {"retire", "skip"}.
    retire reasons: "ready" (processed + no-consumer-or-acked),
                    "fallback" (processed + consumer + unacked + aged out).
    skip reasons:   "undated", "quarantined", "not-processed", "too-young",
                    "awaiting-ack".
    """
    created = parse_iso(fm.get("created"))
    if created is None:
        return ("skip", "undated")

    status = (fm.get("share_status") or "").strip()
    if status == "quarantined":
        return ("skip", "quarantined")
    if status != "cleared":  # active, or anything not-yet-processed
        return ("skip", "not-processed")

    age = now - created
    if age < timedelta(hours=min_age_hours):
        return ("skip", "too-young")

    export = (fm.get("default_export") or "").strip()
    consumer = EXPORT_CONSUMER.get(export)
    if consumer is None:
        return ("retire", "ready")  # cleared + no external consumer

    if ack_lookup(consumer, capture_id):
        return ("retire", "ready")
    if ack_available(consumer) and age >= timedelta(days=fallback_days):
        return ("retire", "fallback")
    return ("skip", "awaiting-ack")


# ---------------------------------------------------------------------------
# Ledger + rsync exclude file (resurrection guard)
# ---------------------------------------------------------------------------


def _record_retired(keys_with_reason: list[tuple[str, str]]) -> None:
    """Append retired keys to the ledger and regenerate the rsync exclude file.

    Ledger: {"retired": {"<canon-slug>/<file>": {"retired_at": iso, "reason": r}}}.
    Excludes: one "/sessions/<canon-slug>/<file>" line per retired key, anchored
    to the _inbox/ transfer root for rsync --exclude-from."""
    ledger = load_state(RETIRED_LEDGER_PATH, default={}) or {}
    retired: dict = ledger.get("retired") or {}
    ts = now_brt().isoformat()
    for key, reason in keys_with_reason:
        retired[key] = {"retired_at": ts, "reason": reason}
    # Cap (keep most-recently-retired) to bound the file.
    if len(retired) > LEDGER_CAP:
        ordered = sorted(retired.items(), key=lambda kv: kv[1].get("retired_at", ""))
        retired = dict(ordered[-LEDGER_CAP:])
    ledger["retired"] = retired
    save_state(RETIRED_LEDGER_PATH, ledger)

    lines = "".join(f"/sessions/{k}\n" for k in sorted(retired))
    EXCLUDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = EXCLUDES_PATH.with_suffix(EXCLUDES_PATH.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(lines, encoding="utf-8")
    os.replace(tmp, EXCLUDES_PATH)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    ap.add_argument("--min-age-hours", type=float, default=DEFAULT_MIN_AGE_HOURS)
    ap.add_argument("--fallback-days", type=int, default=DEFAULT_FALLBACK_DAYS)
    ap.add_argument("--ack-dir", default=None,
                    help="override the LinOS ack dir (default: $BRUNOS_LINOS_ACK_DIR)")
    ap.add_argument("--vault", default=None)
    args = ap.parse_args(argv)

    # Track D Phase 1 reporting: mirror retire_local_inbox.py — report in dry-run
    # mode too, so the dead-man proves the job runs during its disabled/review
    # period. BRUNOS_DISABLE_REPORTING=1 (tests / ad-hoc) → make_reporter None.
    reporter = make_reporter("inbox-retire-vps", "BRUNOS_INBOX_RETIRE_VPS_HEALTHCHECK_URL")

    vault = Path(args.vault).expanduser() if args.vault else vault_path()
    sessions = vault / "Memory" / "_inbox" / "sessions"
    if not sessions.is_dir():
        _log(f"no inbox sessions dir at {sessions}; nothing to do")
        report_outcome(reporter, ok=True, extra={"note": "no inbox sessions dir"})
        return 0

    ack_lookup, ack_available = _make_ack_lookup(args.ack_dir)
    now = now_brt()

    retire: list[tuple[Path, str, str]] = []  # (path, canon-key, reason)
    skips: dict[str, int] = {}
    malformed = 0

    for d in sorted(sessions.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        canon = canonicalize_slug(d.name) or d.name
        for cap in sorted(d.glob("*.md")):
            if cap.stem.startswith("_"):
                continue
            parsed = parse_capture(cap)
            if parsed is None:
                malformed += 1
                continue
            fm, _ = parsed
            action, reason = classify_capture(
                fm, cap.stem, now=now,
                min_age_hours=args.min_age_hours,
                fallback_days=args.fallback_days,
                ack_lookup=ack_lookup,
                ack_available=ack_available,
            )
            if action == "retire":
                retire.append((cap, f"{canon}/{cap.name}", reason))
            else:
                skips[reason] = skips.get(reason, 0) + 1

    ready = sum(1 for _, _, r in retire if r == "ready")
    fallback = sum(1 for _, _, r in retire if r == "fallback")
    skip_str = ", ".join(f"{n} {k}" for k, n in sorted(skips.items())) or "none"
    _log(
        f"\n{'APPLY' if args.apply else 'DRY-RUN'}: {len(retire)} capture(s) to retire "
        f"({ready} ready, {fallback} fallback) | skipped: {skip_str}"
        f"{f' | {malformed} malformed' if malformed else ''}"
    )
    by_slug: dict[str, int] = {}
    for cap, _, _ in retire:
        by_slug[cap.parent.name] = by_slug.get(cap.parent.name, 0) + 1
    for slug, n in sorted(by_slug.items()):
        _log(f"  {slug}: {n}")

    if not args.apply:
        _log("\n(dry-run — re-run with --apply to delete)")
        report_outcome(reporter, ok=True, extra={
            "mode": "dry-run", "retire_candidates": len(retire),
            "ready": ready, "fallback": fallback, "skipped": skips,
            "malformed": malformed,
        })
        return 0

    deleted = 0
    retired_keys: list[tuple[str, str]] = []
    for cap, key, reason in retire:
        try:
            cap.unlink()
            deleted += 1
            retired_keys.append((key, reason))
        except OSError as e:
            _log(f"  ! failed to delete {cap}: {e}")
    if retired_keys:
        _record_retired(retired_keys)
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
        "mode": "apply", "retired": deleted, "ready": ready, "fallback": fallback,
        "skipped": skips, "malformed": malformed,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
