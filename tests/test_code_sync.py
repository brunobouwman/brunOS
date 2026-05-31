#!/usr/bin/env python3
"""Standalone tests for code_sync.py (no pytest). Run: uv run python tests/test_code_sync.py"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

import code_sync as cs  # noqa: E402

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def git(cwd, *a):
    cp = subprocess.run(["git", *a], cwd=str(cwd), capture_output=True, text=True)
    if cp.returncode != 0:
        raise RuntimeError(f"git {' '.join(a)} -> {cp.stderr}")
    return cp.stdout.strip()


def porc(cwd):
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(cwd), capture_output=True, text=True
    ).stdout.strip()


def ident(c, who):
    git(c, "config", "user.email", f"{who}@t.local")
    git(c, "config", "user.name", who)


def world(tmp):
    remote = tmp / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "-b", "main")
    mac = tmp / "mac"
    git(tmp, "clone", str(remote), "mac")
    ident(mac, "mac")
    (mac / ".claude" / "chat").mkdir(parents=True)
    (mac / ".claude" / "chat" / "bot.py").write_text("# bot\n")
    (mac / "README.md").write_text("base\n")
    git(mac, "add", "-A")
    git(mac, "commit", "-m", "seed")
    git(mac, "push", "-u", "origin", "main")
    vps = tmp / "vps"
    git(tmp, "clone", str(remote), "vps")
    ident(vps, "vps")
    return remote, mac, vps


def push(mac, path, body, msg):
    (mac / path).parent.mkdir(parents=True, exist_ok=True)
    (mac / path).write_text(body)
    git(mac, "add", "-A")
    git(mac, "commit", "-m", msg)
    git(mac, "push", "origin", "main")


def t_happy(tmp):
    print("[test] plain pull, no bot-code change")
    _, mac, vps = world(tmp)
    cs.preflight(vps, dry_run=False)

    def boom():
        raise AssertionError("should not recycle")

    cs._recycle_slackbot = boom
    push(mac, "README.md", "base\nmore\n", "docs")
    r = cs.sync(vps, dry_run=False)
    check(r["pulled"] == 1, "pulled 1")
    check(r["recycled"] is False, "no recycle")
    check(porc(vps) == "", "clean")


def t_recycle(tmp):
    print("[test] bot-code change -> recycle")
    _, mac, vps = world(tmp)
    cs.preflight(vps, dry_run=False)
    seen = {"v": False}

    def rec():
        seen["v"] = True
        return True

    cs._recycle_slackbot = rec
    push(mac, ".claude/chat/bot.py", "# bot v2\n", "bot")
    r = cs.sync(vps, dry_run=False)
    check(seen["v"] is True, "recycle invoked")
    check(r["recycled"] is True, "recycled flag")


def t_diverged(tmp):
    print("[test] local commit -> CodeSyncDiverged, clean, no dead-loop")
    _, mac, vps = world(tmp)
    cs.preflight(vps, dry_run=False)
    (vps / "local.md").write_text("vps\n")
    git(vps, "add", "-A")
    git(vps, "commit", "-m", "vps local")
    push(mac, "README.md", "base\nx\n", "mac")

    def raises():
        try:
            cs.sync(vps, dry_run=False)
            return False
        except cs.CodeSyncDiverged:
            return True

    check(raises(), "raised CodeSyncDiverged")
    check(porc(vps) == "", "clean (not wedged)")
    check((vps / "local.md").exists(), "local commit intact")
    check(raises(), "re-run still raises clean")


def t_dirty(tmp):
    print("[test] dirty tree -> stash + ff proceeds")
    _, mac, vps = world(tmp)
    cs.preflight(vps, dry_run=False)
    cs.REPORTER.send_alert = lambda text: True
    (vps / "README.md").write_text("base\nLOCAL\n")
    push(mac, "other.md", "mac other\n", "mac")
    r = cs.sync(vps, dry_run=False)
    check(r["stashed"] is True, "stashed")
    check(r["pulled"] == 1, "pulled after stash")
    check(porc(vps) == "", "clean after")
    check(git(vps, "stash", "list") != "", "stash preserved")


def t_dry(tmp):
    print("[test] dry-run no-op")
    _, mac, vps = world(tmp)
    cs.preflight(vps, dry_run=False)
    push(mac, "README.md", "base\nz\n", "m")
    before = git(vps, "rev-parse", "HEAD")
    r = cs.sync(vps, dry_run=True)
    check(r["pulled"] == 0, "dry-run pulled 0")
    check(git(vps, "rev-parse", "HEAD") == before, "HEAD unchanged")


def main():
    for t in (t_happy, t_recycle, t_diverged, t_dirty, t_dry):
        with tempfile.TemporaryDirectory() as d:
            t(Path(d))
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
