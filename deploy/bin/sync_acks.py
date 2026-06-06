#!/usr/bin/env python3
"""VPS-local push of LinOS ACK manifests back to the BrunOS producer.

linos → bruno one-way transport — the RETURN leg of the federation loop, the
mirror image of deploy/bin/sync_cleared_inbox.py (bruno → linos).

The LinOS consumer (linos_consumer.py) writes an ack manifest
`<capture_id>.json` to `LinOS/Memory/_acks/brunos/` for every BrunOS capture it
durably integrates. Those acks live inside linos's 0700 home, so bruno cannot
read them — yet bruno's VPS-side retirement job (retire_vps_inbox.py) needs them
to know which cleared `linos-protostack` captures are safe to delete. This
transport runs as `linos` and rsyncs the ack JSONs into a bruno-owned,
linos-writable drop (`/home/bruno/linos-acks/brunos/` by default), which bruno's
retire job reads via `BRUNOS_LINOS_ACK_DIR`.

Coexistence (mirror of the cleared-push):
  - runs as `linos`; the ONLY data crossing is the ack manifests linos explicitly
    pushes out. bruno never reads /home/linos, linos never reads /home/bruno —
    it only WRITES into the one drop bruno granted it (one-time least-privilege
    ACL; see deploy/README.md § Ack return). bruno's home stays 0700 otherwise.
  - acks are write-once / immutable: `--ignore-existing` never re-transfers, and
    there is NO `--delete` — a retired or pruned ack is never resurrected.
    Idempotent: a second run with no new acks is a no-op.

Why this is its own leg (not folded into the consumer): the consumer runs as
linos and writes acks into linos's vault; getting them to bruno is a separate
trust-boundary crossing that must be auditable and independently monitored.

Config (env or CLI flag; flag wins):
  --producer            default "brunos" (the acked producer's slug → _acks/<p>/)
  --src    LINOS_ACKS_SRC    default <LINOS_VAULT_PATH>/Memory/_acks/<producer>
  --dst    BRUNOS_ACK_DEST   default /home/bruno/linos-acks/<producer>
  --dry-run                  select + report, write nothing

Exit: 0 on success (including "nothing to sync"); non-zero on rsync failure or a
missing dest drop (the one-time ACL setup must create it first).
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

from shared import load_env  # noqa: E402
from sync_common import make_reporter, report_outcome  # noqa: E402

DEFAULT_PRODUCER = "brunos"


def _default_src(producer: str) -> str:
    vault = os.environ.get("LINOS_VAULT_PATH", "/home/linos/LinOS")
    return str(Path(vault) / "Memory" / "_acks" / producer)


def _default_dst(producer: str) -> str:
    return f"/home/bruno/linos-acks/{producer}"


def select_acks(src: Path) -> list[str]:
    """Return the basenames of ack manifests (*.json) under src, sorted.

    Flat dir of `<capture_id>.json` files. The consumer's `.gitkeep` and any
    non-json are skipped, so the manifest is exactly the acks.
    """
    if not src.is_dir():
        return []
    return sorted(p.name for p in src.glob("*.json") if p.is_file())


def _rsync(src: Path, dst: str, manifest: Path) -> int:
    # --files-from basenames are relative to src; trailing slash on src required.
    # --ignore-existing: acks are immutable, never re-transfer. NO --delete.
    cmd = [
        "rsync", "-a", "--ignore-existing", "--no-implied-dirs",
        "--files-from", str(manifest),
        f"{src}/", dst if dst.endswith("/") else dst + "/",
    ]
    return subprocess.run(cmd).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--producer", default=DEFAULT_PRODUCER,
                    help="acked producer slug (→ _acks/<producer>/); default brunos")
    ap.add_argument("--src", default=None,
                    help="ack source dir (default $LINOS_ACKS_SRC or <LINOS_VAULT_PATH>/Memory/_acks/<producer>)")
    ap.add_argument("--dst", default=None,
                    help="ack dest drop (default $BRUNOS_ACK_DEST or /home/bruno/linos-acks/<producer>)")
    ap.add_argument("--dry-run", action="store_true", help="select + report, write nothing")
    args = ap.parse_args(argv)

    load_env()
    producer = args.producer
    src = Path(args.src or os.environ.get("LINOS_ACKS_SRC") or _default_src(producer))
    dst = args.dst or os.environ.get("BRUNOS_ACK_DEST") or _default_dst(producer)

    # Track D Phase 1: real runs report (status file + Slack + healthchecks.io);
    # dry-runs stay silent. BRUNOS_DISABLE_REPORTING=1 disables (tests).
    reporter = None if args.dry_run else make_reporter(
        "linos-ack-sync", "LINOS_ACK_SYNC_HEALTHCHECK_URL"
    )

    acks = select_acks(src)
    print(f"==> {producer}: {len(acks)} ack manifest(s) in {src}")

    if args.dry_run:
        for a in acks:
            print(f"    would sync: {a}")
        return 0
    if not acks:
        print("==> nothing to sync")
        report_outcome(reporter, ok=True, extra={"acks": 0, "src": str(src)})
        return 0

    # Do NOT create the dest under bruno's home — the one-time ACL setup owns that
    # (linos only WRITES into the drop bruno granted). A remote host:path dest is
    # the operator's responsibility.
    dst_local = ":" not in dst
    if dst_local and not Path(dst).is_dir():
        msg = (f"dest drop {dst} does not exist — run the one-time ACL setup "
               f"(deploy/README.md § Ack return) so linos can write it")
        print(f"==> {msg}", file=sys.stderr)
        report_outcome(reporter, ok=False, kind="config", msg=msg)
        return 2

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write("\n".join(acks) + "\n")
        manifest = Path(fh.name)
    try:
        rc = _rsync(src, dst, manifest)
    finally:
        manifest.unlink(missing_ok=True)

    if rc == 0:
        print(f"==> synced {len(acks)} ack(s) → {dst}")
        report_outcome(reporter, ok=True, extra={"acks": len(acks), "dst": dst})
    else:
        print(f"==> rsync exited {rc}", file=sys.stderr)
        report_outcome(reporter, ok=False, kind="rsync", msg=f"rsync exited {rc} → {dst}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
