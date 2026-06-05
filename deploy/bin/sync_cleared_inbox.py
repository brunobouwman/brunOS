#!/usr/bin/env python3
"""VPS-local push of CLEARED + IN-SCOPE inbox captures to a consumer brain.

bruno → LinOS one-way transport. Runs as `bruno` (the only user that can read
/home/bruno/BrunOS). Selects ONLY captures that pass the consumer's federation
gate — `default_export` ∈ the consumer's declared scope AND
`share_status == "cleared"` — and rsyncs just those into a consumer-readable
dest dir. The consuming brain (LinOS) therefore:

  1. NEVER has access to bruno's home (`/home/bruno` stays 0700; LinOS only ever
     reads its own dest dir), and
  2. can NEVER receive an uncleared OR out-of-scope capture — the filter IS the
     privacy boundary, enforced at the transport, BEFORE LinOS can see anything.

WHY this matters — `share_status: cleared` alone is NOT a sufficient gate.
Reflection stamps `cleared` on EVERY capture once personal asides are stripped,
regardless of destination — so most `default_export: personal` captures (Vertik,
chat-ui, lab-agent work) are also `cleared`. Gating on `cleared` alone would leak
all of those to LinOS. The scope check (`default_export == linos-protostack` via
`validate_consumer_read`) is what actually authorizes a capture for LinOS. This
transport requires BOTH, mirroring linos_consumer.py's own two-gate check —
defense-in-depth so an out-of-scope capture is never even physically present in
LinOS's tree.

WHY a Python pre-pass and not a plain rsync rule: rsync's --include/--exclude
match on PATH, not file CONTENT. The gate lives in each capture's YAML
frontmatter, so selection must parse the file. This builds a --files-from
manifest of eligible captures, then hands it to rsync for the copy (so we keep
-a / --update / no---delete semantics).

Safety (mirrors deploy/bin/sync_inbox.py): --update never clobbers a newer dest
file; NO --delete (a capture retired on the consumer side is never resurrected).
Idempotent. A not-yet-cleared capture is simply skipped until a later run after
BrunOS reflection stamps it cleared — so scheduling this AFTER reflect and BEFORE
the consumer is all the ordering required.

Config (env or CLI flag; flag wins):
  --src       BRUNOS_INBOX_SRC   default /home/bruno/BrunOS/Memory/_inbox/sessions
  --dst       LINOS_INBOX_DEST   REQUIRED (e.g. /home/linos/brunos-inbox/sessions)
  --consumer                     default "linos" (key into CONSUMER_READ_SCOPES)
  --dry-run                      select + report, write nothing

Exit: 0 on success (including "nothing eligible"); non-zero on rsync failure or
a missing/unknown consumer.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / ".claude" / "scripts"))

from shared import _FM_RE, CONSUMER_READ_SCOPES, load_env, validate_consumer_read  # noqa: E402
from sync_common import make_reporter, report_outcome  # noqa: E402

DEFAULT_SRC = "/home/bruno/BrunOS/Memory/_inbox/sessions"

_SCALAR_FM_RE = __import__("re").compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$")


def _parse_frontmatter(path: Path) -> dict[str, str] | None:
    """Return the scalar YAML frontmatter of a capture, or None if malformed.

    Only scalar `key: value` fields are read — enough for the gate
    (default_export, share_status). Block-list fields (tags:) are skipped.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FM_RE.match(text)
    if not m:
        return None
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        sm = _SCALAR_FM_RE.match(line)
        if sm and sm.group(2).strip():
            fm[sm.group(1)] = sm.group(2).strip()
    return fm


def _is_eligible(fm: dict, consumer: str) -> bool:
    """A capture is eligible iff it passes the consumer scope gate AND is cleared."""
    if not validate_consumer_read(fm, consumer):
        return False
    return (fm.get("share_status") or "").strip() == "cleared"


def select_eligible(src: Path, consumer: str) -> tuple[list[str], dict[str, int]]:
    """Walk src/<slug>/*.md and return (relative-paths-eligible, stats).

    Paths are relative to `src` (for rsync --files-from). Stats counts the skip
    reasons so the run is auditable (no silent drops).
    """
    rels: list[str] = []
    stats = {"eligible": 0, "out_of_scope": 0, "uncleared": 0, "malformed": 0}
    if not src.is_dir():
        return rels, stats
    for slug_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        if slug_dir.name.startswith("_"):
            continue
        for cap in sorted(slug_dir.glob("*.md")):
            if cap.stem.startswith("_"):
                continue
            fm = _parse_frontmatter(cap)
            if fm is None:
                stats["malformed"] += 1
                continue
            if not validate_consumer_read(fm, consumer):
                stats["out_of_scope"] += 1
                continue
            if (fm.get("share_status") or "").strip() != "cleared":
                stats["uncleared"] += 1
                continue
            stats["eligible"] += 1
            rels.append(str(cap.relative_to(src)))
    return rels, stats


def _rsync(src: Path, dst: str, manifest: Path) -> int:
    # --files-from paths are relative to src; trailing slash on src required.
    cmd = [
        "rsync", "-a", "--update", "--no-implied-dirs",
        "--files-from", str(manifest),
        f"{src}/", dst if dst.endswith("/") else dst + "/",
    ]
    return subprocess.run(cmd).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default=os.environ.get("BRUNOS_INBOX_SRC", DEFAULT_SRC))
    ap.add_argument("--dst", default=os.environ.get("LINOS_INBOX_DEST"))
    ap.add_argument("--consumer", default="linos")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    # Track D Phase 1: real runs report (status file + Slack + healthchecks.io);
    # dry-runs stay silent. BRUNOS_DISABLE_REPORTING=1 disables (tests).
    load_env()
    reporter = None if args.dry_run else make_reporter(
        "linos-inbox-sync", "BRUNOS_LINOS_INBOX_SYNC_HEALTHCHECK_URL"
    )

    if args.consumer not in CONSUMER_READ_SCOPES:
        print(f"==> unknown consumer {args.consumer!r} "
              f"(known: {sorted(CONSUMER_READ_SCOPES)})", file=sys.stderr)
        report_outcome(reporter, ok=False, kind="config",
                       msg=f"unknown consumer {args.consumer!r}")
        return 2
    if not args.dst:
        print("==> no dest: set LINOS_INBOX_DEST or pass --dst", file=sys.stderr)
        report_outcome(reporter, ok=False, kind="config",
                       msg="no dest: set LINOS_INBOX_DEST or pass --dst")
        return 2

    src = Path(args.src)
    rels, stats = select_eligible(src, args.consumer)
    print(f"==> {args.consumer}: {stats['eligible']} eligible "
          f"(skipped {stats['out_of_scope']} out-of-scope, "
          f"{stats['uncleared']} uncleared, {stats['malformed']} malformed) "
          f"from {src}")

    if args.dry_run:
        for r in rels:
            print(f"    would sync: {r}")
        return 0
    if not rels:
        print("==> nothing eligible — nothing to sync")
        report_outcome(reporter, ok=True, extra={"select_stats": stats, "synced": 0})
        return 0

    # mkdir dest (local path only; a remote host:path dest is the operator's job)
    if ":" not in args.dst:
        Path(args.dst).mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write("\n".join(rels) + "\n")
        manifest = Path(fh.name)
    try:
        rc = _rsync(src, args.dst, manifest)
    finally:
        manifest.unlink(missing_ok=True)

    if rc == 0:
        print(f"==> synced {len(rels)} cleared+in-scope captures → {args.dst}")
        report_outcome(reporter, ok=True,
                       extra={"select_stats": stats, "synced": len(rels)})
    else:
        print(f"==> rsync exited {rc}", file=sys.stderr)
        report_outcome(reporter, ok=False, kind="rsync",
                       msg=f"rsync exited {rc} → {args.dst}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
