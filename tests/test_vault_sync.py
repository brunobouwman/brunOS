#!/usr/bin/env python3
"""Standalone tests for vault_sync.py (no pytest dependency).

Builds a bare "remote" repo + two clones (mac, vps) in a temp dir — all local,
no network — and drives the real vault_sync functions to assert the reliability
guarantees:

  * dirty tree → auto-commit + push
  * divergent APPEND-ONLY edits both sides → concat-both auto-merges, clean
  * divergent NON-APPEND conflict → merge --abort, WORKING TREE CLEAN AFTER,
    SyncConflict raised, and a re-run stays clean (never dead-loops)
  * non-fast-forward push → refetch + remerge + retry succeeds
  * --dry-run mutates nothing
  * idle _run() writes a success status file

Run:  uv run python tests/test_vault_sync.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

import vault_sync as vs  # noqa: E402
import shared  # noqa: E402

# --------------------------------------------------------------------------- #
# Tiny assert framework
# --------------------------------------------------------------------------- #
_PASS = 0
_FAIL = 0


def check(cond: bool, label: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def git(cwd: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    if cp.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} -> {cp.returncode}: {cp.stderr}")
    return cp.stdout.strip()


def porcelain(cwd: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(cwd), capture_output=True, text=True
    ).stdout.strip()


def set_identity(clone: Path, who: str) -> None:
    git(clone, "config", "user.email", f"{who}@test.local")
    git(clone, "config", "user.name", who)


def make_world(tmp: Path) -> tuple[Path, Path, Path]:
    """Bare remote + mac clone (seeded) + vps clone, branch=main."""
    remote = tmp / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "-b", "main")

    mac = tmp / "mac"
    git(tmp, "clone", str(remote), "mac")
    set_identity(mac, "mac")
    (mac / "Memory" / "daily").mkdir(parents=True)
    # .gitattributes wires the concat-both driver to append-only files.
    (mac / ".gitattributes").write_text(
        "Memory/daily/*.md merge=concat-both\nMemory/HABITS.md  merge=concat-both\n"
    )
    (mac / "Memory" / "MEMORY.md").write_text("# MEMORY\nline-base\n")
    (mac / "Memory" / "HABITS.md").write_text("# HABITS\n- base\n")
    (mac / "Memory" / "daily" / "2026-01-01.md").write_text("# 2026-01-01\nbase entry\n")
    git(mac, "add", "-A")
    git(mac, "commit", "-m", "seed")
    git(mac, "push", "-u", "origin", "main")

    vps = tmp / "vps"
    git(tmp, "clone", str(remote), "vps")
    set_identity(vps, "vps")
    return remote, mac, vps


def prep(clone: Path) -> None:
    """Register identity + concat-both driver (what preflight does in prod)."""
    vs.preflight(clone, dry_run=False)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_dirty_commit_and_push(tmp: Path) -> None:
    print("[test] dirty tree -> auto-commit + push")
    _, mac, vps = make_world(tmp)
    prep(vps)
    (vps / "Memory" / "newnote.md").write_text("hello from vps\n")
    res = vs.sync(vps, dry_run=False)
    check(res["committed"] is True, "vps committed its new file")
    check(res["pushed"] == 1, "vps pushed 1 commit")
    check(porcelain(vps) == "", "vps tree clean after sync")

    prep(mac)
    res2 = vs.sync(mac, dry_run=False)
    check(res2["pulled"] == 1, "mac pulled the vps commit")
    check((mac / "Memory" / "newnote.md").exists(), "mac now has newnote.md")


def test_append_only_concat_merge(tmp: Path) -> None:
    print("[test] divergent append-only edits -> concat-both auto-merge")
    _, mac, vps = make_world(tmp)
    prep(mac)
    prep(vps)
    daily = "Memory/daily/2026-01-01.md"
    # mac appends + pushes
    (mac / daily).write_text((mac / daily).read_text() + "MAC LINE\n")
    vs.sync(mac, dry_run=False)
    # vps appends a DIFFERENT line to the same file, then syncs
    (vps / daily).write_text((vps / daily).read_text() + "VPS LINE\n")
    res = vs.sync(vps, dry_run=False)
    check(porcelain(vps) == "", "vps tree clean after concat merge")
    merged = (vps / daily).read_text()
    check("MAC LINE" in merged and "VPS LINE" in merged, "both appends survived concat")
    check(res["pushed"] >= 1, "vps pushed the merge")


def test_real_conflict_aborts_clean(tmp: Path) -> None:
    print("[test] non-append conflict -> merge --abort, clean tree, no dead-loop")
    _, mac, vps = make_world(tmp)
    prep(mac)
    prep(vps)
    # mac edits MEMORY.md (NOT an append-only file) and pushes
    (mac / "Memory" / "MEMORY.md").write_text("# MEMORY\nMAC-VERSION\n")
    vs.sync(mac, dry_run=False)
    # vps makes a conflicting edit to the same region
    (vps / "Memory" / "MEMORY.md").write_text("# MEMORY\nVPS-VERSION\n")

    raised = False
    try:
        vs.sync(vps, dry_run=False)
    except vs.SyncConflict as e:
        raised = True
        check("Memory/MEMORY.md" in e.paths, "conflict reported on MEMORY.md")
    check(raised, "SyncConflict raised on real conflict")
    check(porcelain(vps) == "", "vps tree CLEAN after abort (not bricked)")
    check(
        not (vps / ".git" / "MERGE_HEAD").exists(),
        "no merge left in progress",
    )
    check("VPS-VERSION" in (vps / "Memory" / "MEMORY.md").read_text(), "vps kept its own version")

    # Re-run: must still raise but stay clean — proves no dead-loop / corruption.
    raised2 = False
    try:
        vs.sync(vps, dry_run=False)
    except vs.SyncConflict:
        raised2 = True
    check(raised2, "re-run still raises (still diverged)")
    check(porcelain(vps) == "", "vps tree STILL clean on re-run")


def test_push_race_retry(tmp: Path) -> None:
    print("[test] non-fast-forward push -> refetch + remerge + retry")
    _, mac, vps = make_world(tmp)
    prep(mac)
    prep(vps)
    # vps gets 1 local commit (ahead) on a DIFFERENT file, without fetching.
    (vps / "Memory" / "vps_only.md").write_text("vps work\n")
    git(vps, "add", "-A")
    git(vps, "commit", "-m", "vps local")
    # meanwhile mac advances the remote on yet another file.
    (mac / "Memory" / "mac_only.md").write_text("mac work\n")
    vs.sync(mac, dry_run=False)
    # Direct push from vps is now rejected (non-fast-forward); _push must recover.
    vs._push(vps)
    check(porcelain(vps) == "", "vps clean after push-retry")
    behind = vs._count(vps, "HEAD..origin/main")
    ahead = vs._count(vps, "origin/main..HEAD")
    check(behind == 0 and ahead == 0, "vps fully synced after retry")
    # remote has both files
    git(mac, "pull", "--no-edit", "origin", "main")
    check((mac / "Memory" / "vps_only.md").exists(), "remote received vps commit via retry")


def test_dry_run_noop(tmp: Path) -> None:
    print("[test] --dry-run mutates nothing")
    _, mac, vps = make_world(tmp)
    prep(vps)
    (vps / "Memory" / "scratch.md").write_text("scratch\n")
    before = porcelain(vps)
    res = vs.sync(vps, dry_run=True)
    check(porcelain(vps) == before, "tree unchanged after dry-run")
    check(res["committed"] is False, "dry-run did not commit")
    check(res["pushed"] == 0, "dry-run did not push")


def test_run_idle_writes_status(tmp: Path) -> None:
    print("[test] _run() idle -> success status file")
    _, mac, vps = make_world(tmp)
    # Point vault_path() + STATE_FILE at our scratch vps clone.
    shared.vault_path.cache_clear()
    import os

    os.environ["BRUNOS_VAULT_PATH"] = str(vps)
    os.environ.pop("BRUNOS_HEALTHCHECK_URL", None)
    os.environ.pop("BRUNOS_ALERT_CHANNEL", None)
    status_path = tmp / "vault-sync-state.json"
    orig = vs.REPORTER.status_file
    vs.REPORTER.status_file = status_path
    try:
        rc = vs._run(dry_run=False)
    finally:
        vs.REPORTER.status_file = orig
        shared.vault_path.cache_clear()
        os.environ.pop("BRUNOS_VAULT_PATH", None)
    check(rc == 0, "_run returned 0 on idle")
    check(status_path.exists(), "status file written")
    import json

    st = json.loads(status_path.read_text())
    check(st.get("last_success") is not None, "status has last_success")
    check(st.get("consecutive_failures") == 0, "status failures = 0")


def main() -> int:
    tests = [
        test_dirty_commit_and_push,
        test_append_only_concat_merge,
        test_real_conflict_aborts_clean,
        test_push_race_retry,
        test_dry_run_noop,
        test_run_idle_writes_status,
    ]
    for t in tests:
        with tempfile.TemporaryDirectory() as d:
            t(Path(d))
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
