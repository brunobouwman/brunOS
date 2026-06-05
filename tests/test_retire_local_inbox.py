#!/usr/bin/env python3
"""Standalone tests for producer-side local inbox retirement (no pytest).
Run: uv run python tests/test_retire_local_inbox.py

Covers: terminal-status match (canonical-slug aware), age grace, fail-safe on
empty/missing VPS set, dry-run inertness, and --apply deletion + empty-dir cleanup.
The VPS terminal set is injected via --vps-state-file (no SSH).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Track D: tests must never write real monitor state / ping healthchecks.
# (Inherited by the subprocess runs below; --vps-state-file mode is also
# reporting-silent by design.)
os.environ["BRUNOS_DISABLE_REPORTING"] = "1"

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "deploy" / "bin" / "retire_local_inbox.py"

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def _cap(p: Path, created: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ntype: inbox\ncreated: {created}\nshare_status: active\n---\n\nbody\n")


def _run(vault: Path, state: Path | None, *extra):
    args = [sys.executable, str(SCRIPT), "--vault", str(vault), "--min-age-hours", "1", *extra]
    if state is not None:
        args += ["--vps-state-file", str(state)]
    return subprocess.run(args, capture_output=True, text=True)


def _fresh_vault() -> Path:
    v = Path(tempfile.mkdtemp(prefix="sbvault_"))
    sess = v / "Memory" / "_inbox" / "sessions"
    # old captures (eligible by age)
    _cap(sess / "vertik" / "2026-05-20-100000-aaa.md", "2026-05-20T10:00:00-03:00")
    _cap(sess / "vertik-lab-agent" / "2026-05-21-100000-bbb.md", "2026-05-21T10:00:00-03:00")  # stray slug
    _cap(sess / "colinas" / "2026-05-22-100000-ccc.md", "2026-05-22T10:00:00-03:00")
    # a very recent capture (within grace) — must NOT be retired even if terminal
    _cap(sess / "vertik" / "2099-01-01-100000-zzz.md", "2099-01-01T10:00:00-03:00")
    return v


# 1) happy path: terminal set drives deletion; stray slug matches via canonical slug
v = _fresh_vault()
state = v / "vpsstate.txt"
# Mix: aaa reported under consolidated vertik/, bbb reported under the
# UNCONSOLIDATED stray slug (proves both sides canonicalize → still matches the
# Mac's vertik-lab-agent/bbb). zzz terminal but grace-skipped.
state.write_text(
    "vertik/2026-05-20-100000-aaa.md\n"
    "vertik-lab-agent/2026-05-21-100000-bbb.md\n"   # VPS not folder-consolidated
    "vertik/2099-01-01-100000-zzz.md\n"
)
r = _run(v, state)  # dry-run
# aaa + bbb (stray, matched via canonical slug) retire; zzz is terminal but
# within grace; ccc is not in the VPS set → 2 to retire.
check("2 local capture(s) to retire" in r.stdout, "dry-run counts canonical-slug matches incl stray (2)")
check("grace)" in r.stdout, "dry-run reports grace bucket")
check((v / "Memory/_inbox/sessions/vertik-lab-agent/2026-05-21-100000-bbb.md").exists(),
      "dry-run deletes nothing")

r = _run(v, state, "--apply")
sess = v / "Memory/_inbox/sessions"
check(not (sess / "vertik/2026-05-20-100000-aaa.md").exists(), "apply: terminal capture deleted")
check(not (sess / "vertik-lab-agent").exists(), "apply: drained stray folder removed")
check((sess / "vertik/2099-01-01-100000-zzz.md").exists(), "apply: within-grace capture preserved")
check((sess / "colinas/2026-05-22-100000-ccc.md").exists(),
      "apply: non-terminal (not in VPS set) capture preserved")

# 2) fail-safe: empty terminal set → abort, delete nothing
v2 = _fresh_vault()
empty = v2 / "empty.txt"
empty.write_text("")
r = _run(v2, empty, "--apply")
check(r.returncode == 2 and "empty" in r.stdout.lower(), "empty VPS set → abort (rc 2)")
check((v2 / "Memory/_inbox/sessions/vertik/2026-05-20-100000-aaa.md").exists(),
      "empty VPS set → nothing deleted")

# 3) fail-safe: missing state file → abort
v3 = _fresh_vault()
r = _run(v3, v3 / "does-not-exist.txt", "--apply")
check(r.returncode == 2, "unreadable VPS state → abort (rc 2)")
check((v3 / "Memory/_inbox/sessions/colinas/2026-05-22-100000-ccc.md").exists(),
      "unreadable VPS state → nothing deleted")

print(f"\n{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
