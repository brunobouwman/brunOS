#!/usr/bin/env python3
"""Standalone tests for VPS-side inbox retirement (federation F2). No pytest.
Run: uv run python tests/test_retire_vps_inbox.py

Covers the per-capture decision matrix (processed/ack/fallback/grace/quarantine/
undated), dry-run inertness, --apply deletion, empty-dir cleanup, and the
resurrection-guard ledger + rsync exclude file. LinOS acks are injected as files
in a temp --ack-dir (no live consumer).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

# Tests must never write real monitor state / ping healthchecks.
os.environ["BRUNOS_DISABLE_REPORTING"] = "1"

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "deploy" / "bin" / "retire_vps_inbox.py"
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "deploy" / "bin"))

from shared import now_brt  # noqa: E402
import retire_vps_inbox as R  # noqa: E402
import sync_inbox as SI  # noqa: E402

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def _iso(days_ago=0, hours_ago=0):
    return (now_brt() - timedelta(days=days_ago, hours=hours_ago)).isoformat()


def _cap(p: Path, *, created: str, status: str = "cleared", export: str = "personal"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntype: inbox\ncreated: {created}\n"
        f"default_export: {export}\nshare_status: {status}\n---\n\nbody\n"
    )


def _write_ack(ack_dir: Path, capture_id: str):
    ack_dir.mkdir(parents=True, exist_ok=True)
    (ack_dir / f"{capture_id}.json").write_text(
        json.dumps({"capture_id": capture_id, "schema_version": 1})
    )


def _run(vault: Path, ack_dir: Path, *extra):
    args = [sys.executable, str(SCRIPT), "--vault", str(vault),
            "--ack-dir", str(ack_dir), "--min-age-hours", "48",
            "--fallback-days", "15", *extra]
    return subprocess.run(args, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# 1) Unit-level decision matrix via classify_capture (no subprocess)
# ---------------------------------------------------------------------------
now = now_brt()
ACKED = {"linos/acked-cap"}


def _lookup(consumer, cid):
    return f"{consumer}/{cid}" in ACKED


def _decide(status, export, created, cid="x"):
    return R.classify_capture(
        {"share_status": status, "default_export": export, "created": created},
        cid, now=now, min_age_hours=48, fallback_days=15, ack_lookup=_lookup,
        ack_available=lambda _consumer: True,
    )


check(_decide("cleared", "personal", _iso(days_ago=20)) == ("retire", "ready"),
      "personal cleared old → retire/ready")
check(_decide("cleared", "personal", _iso(hours_ago=1)) == ("skip", "too-young"),
      "personal cleared fresh → skip/too-young")
check(_decide("active", "personal", _iso(days_ago=20)) == ("skip", "not-processed"),
      "active old → skip/not-processed (never auto-deleted)")
check(_decide("quarantined", "personal", _iso(days_ago=20)) == ("skip", "quarantined"),
      "quarantined old → skip/quarantined (kept for review)")
check(_decide("cleared", "personal", None) == ("skip", "undated"),
      "undated → skip/undated")
check(_decide("cleared", "linos-protostack", _iso(days_ago=5)) == ("skip", "awaiting-ack"),
      "linos cleared unacked <15d → skip/awaiting-ack")
check(_decide("cleared", "linos-protostack", _iso(days_ago=20)) == ("retire", "fallback"),
      "linos cleared unacked ≥15d → retire/fallback")
check(_decide("cleared", "linos-protostack", _iso(days_ago=20), cid="acked-cap") == ("retire", "ready"),
      "linos cleared ACKED → retire/ready (no fallback wait)")
check(_decide("cleared", "discard", _iso(days_ago=20)) == ("retire", "ready"),
      "discard cleared old → retire/ready (no consumer)")


# ---------------------------------------------------------------------------
# 1b) Mac rsync resurrection guard wiring
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmpdir:
    exclude_file = Path(tmpdir) / "excludes.txt"
    exclude_file.write_text("/sessions/colinas/old.md\n", encoding="utf-8")
    old_val = os.environ.get(SI.EXCLUDE_FILE_ENV)
    os.environ[SI.EXCLUDE_FILE_ENV] = str(exclude_file)
    try:
        cmd = SI._rsync_cmd(Path("/tmp/src"), "brunoos:/home/bruno/BrunOS/Memory/_inbox/")
        check("--exclude-from" in cmd and str(exclude_file) in cmd,
              "sync_inbox passes BRUNOS_INBOX_EXCLUDE_FILE to rsync")
    finally:
        if old_val is None:
            os.environ.pop(SI.EXCLUDE_FILE_ENV, None)
        else:
            os.environ[SI.EXCLUDE_FILE_ENV] = old_val


# ---------------------------------------------------------------------------
# 2) End-to-end dry-run + --apply against a temp vault
# ---------------------------------------------------------------------------
def _fresh_vault() -> Path:
    v = Path(tempfile.mkdtemp(prefix="vpsvault_"))
    s = v / "Memory" / "_inbox" / "sessions"
    _cap(s / "vertik" / "p-ready.md", created=_iso(days_ago=20))                       # retire ready
    _cap(s / "vertik" / "p-young.md", created=_iso(hours_ago=2))                       # skip too-young
    _cap(s / "vertik" / "p-active.md", created=_iso(days_ago=20), status="active")     # skip not-processed
    _cap(s / "vertik" / "p-quar.md", created=_iso(days_ago=20), status="quarantined")  # skip quarantined
    _cap(s / "colinas" / "l-acked.md", created=_iso(days_ago=20), export="linos-protostack")    # retire ready (ack)
    _cap(s / "colinas" / "l-wait.md", created=_iso(days_ago=5), export="linos-protostack")       # skip awaiting-ack
    _cap(s / "colinas" / "l-fallback.md", created=_iso(days_ago=20), export="linos-protostack")  # retire fallback
    return v


# State (ledger + excludes) is written under the REAL repo STATE_DIR; redirect it
# to a temp dir so the test never touches live runtime state.
_tmp_state = Path(tempfile.mkdtemp(prefix="vpsstate_"))
R.RETIRED_LEDGER_PATH = _tmp_state / "retired_inbox.json"
R.EXCLUDES_PATH = _tmp_state / "inbox-retired-excludes.txt"

v = _fresh_vault()
ack = Path(tempfile.mkdtemp(prefix="vpsack_"))
_write_ack(ack, "l-acked")  # only this linos capture is acked
sess = v / "Memory/_inbox/sessions"

# dry-run: 3 to retire (p-ready, l-acked, l-fallback), nothing deleted.
r = _run(v, ack)  # NB subprocess uses its own STATE_DIR, but dry-run writes no ledger
check("3 capture(s) to retire" in r.stdout, f"dry-run retire count = 3 (got: {r.stdout.strip().splitlines()[-3:]})")
check("2 ready, 1 fallback" in r.stdout, "dry-run breakdown: 2 ready, 1 fallback")
check("awaiting-ack" in r.stdout, "dry-run reports awaiting-ack skip")
check((sess / "vertik/p-ready.md").exists(), "dry-run deletes nothing")

# --apply (in-process so the redirected ledger/excludes paths take effect).
rc = R.main(["--vault", str(v), "--ack-dir", str(ack),
             "--min-age-hours", "48", "--fallback-days", "15", "--apply"])
check(rc == 0, "apply exits 0")
check(not (sess / "vertik/p-ready.md").exists(), "apply: personal cleared old deleted")
check(not (sess / "colinas/l-acked.md").exists(), "apply: linos acked deleted")
check(not (sess / "colinas/l-fallback.md").exists(), "apply: linos fallback deleted")
check((sess / "vertik/p-young.md").exists(), "apply: within-grace preserved")
check((sess / "vertik/p-active.md").exists(), "apply: active (unprocessed) preserved")
check((sess / "vertik/p-quar.md").exists(), "apply: quarantined preserved")
check((sess / "colinas/l-wait.md").exists(), "apply: awaiting-ack (<15d) preserved")

# Resurrection-guard artifacts.
ledger = json.loads(R.RETIRED_LEDGER_PATH.read_text())
retired_keys = set(ledger.get("retired", {}))
check(retired_keys == {"vertik/p-ready.md", "colinas/l-acked.md", "colinas/l-fallback.md"},
      f"ledger holds the 3 retired keys (got {sorted(retired_keys)})")
check(ledger["retired"]["colinas/l-fallback.md"]["reason"] == "fallback",
      "ledger records the fallback reason")
excludes = R.EXCLUDES_PATH.read_text().splitlines()
check("/sessions/vertik/p-ready.md" in excludes, "exclude file anchors path for rsync --exclude-from")
check(all(line.startswith("/sessions/") for line in excludes if line), "exclude lines are root-anchored")

# Empty-dir cleanup: vertik still has captures (kept), colinas has l-wait (kept).
check((sess / "vertik").is_dir() and (sess / "colinas").is_dir(),
      "non-drained folders preserved")


# ---------------------------------------------------------------------------
# 3) ack-dir absent ⇒ linos captures do NOT fallback (consumer not live), personal OK
# ---------------------------------------------------------------------------
v2 = _fresh_vault()
missing_ack = Path(tempfile.mkdtemp(prefix="vpsnoack_")) / "does-not-exist"
r = _run(v2, missing_ack)  # dry-run
# With no ack dir, the consumer side is not considered live. Personal/discard
# captures can retire, but LinOS-bound captures wait instead of using fallback.
check("1 capture(s) to retire" in r.stdout, "missing ack dir: only personal captures retire")
check("1 ready, 0 fallback" in r.stdout, "missing ack dir: fallback is disabled")
check("awaiting-ack" in r.stdout, "missing ack dir: linos captures wait for ack path")

print(f"\n{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
