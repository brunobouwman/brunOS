#!/usr/bin/env python3
"""Standalone tests for inbox slug canonicalization at the write boundary (no pytest).
Run: uv run python tests/test_inbox_slug_canonicalize.py

Regression for the codex-precompact slug split: an explicit --project flag
(e.g. --project=vertik-lab-agent) bypasses path-derivation's canonicalize_slug,
so write_inbox_capture must canonicalize too — otherwise a single repo splits
across multiple inbox folders (vertik / vertik-lab-agent / vertik-lab-agent-chat-ui).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "shared", REPO_ROOT / ".claude" / "scripts" / "shared.py"
)
shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shared)

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


# canonicalize_slug unit coverage
check(shared.canonicalize_slug("vertik-lab-agent") == "vertik", "vertik-lab-agent → vertik")
check(shared.canonicalize_slug("vertik-lab-agent-chat-ui") == "vertik", "chat-ui → vertik")
check(shared.canonicalize_slug("lab-agent") == "vertik", "lab-agent (worktree basename) → vertik")
check(shared.canonicalize_slug("memorial-colinas") == "colinas", "memorial-colinas → colinas")
check(shared.canonicalize_slug("random-repo") == "random-repo", "unknown slug passes through")
check(shared.canonicalize_slug(None) is None, "None → None")

# write_inbox_capture must land every vertik variant in the SAME canonical folder,
# even when an explicit (non-canonical) project name is passed.
_tmp = tempfile.mkdtemp(prefix="sbvault_")
os.environ["BRUNOS_VAULT_PATH"] = _tmp
shared.vault_path.cache_clear()  # lru_cache — drop any prior resolution
try:
    landed = {}
    for proj in ["vertik", "vertik-lab-agent", "vertik-lab-agent-chat-ui", "lab-agent"]:
        p = shared.write_inbox_capture(
            project=proj, default_export="personal",
            session_id="sess", source="test", body="body",
        )
        landed[proj] = p.parent.name
    check(set(landed.values()) == {"vertik"},
          f"all vertik variants land in one folder (got {sorted(set(landed.values()))})")
    check(landed["vertik-lab-agent"] == "vertik",
          "explicit --project=vertik-lab-agent canonicalized at write boundary")

    # colinas variant
    pc = shared.write_inbox_capture(
        project="memorial-colinas", default_export="linos-protostack",
        session_id="sess", source="test", body="body",
    )
    check(pc.parent.name == "colinas", "memorial-colinas capture lands in colinas/")

    # only the canonical folders exist (no stray dirs created)
    sessions = REPO_ROOT  # placeholder; real check below
    inbox = Path(_tmp) / "Memory" / "_inbox" / "sessions"
    dirs = sorted(d.name for d in inbox.iterdir() if d.is_dir())
    check(dirs == ["colinas", "vertik"], f"no stray slug dirs created (got {dirs})")
finally:
    shared.vault_path.cache_clear()
    os.environ.pop("BRUNOS_VAULT_PATH", None)
    shutil.rmtree(_tmp, ignore_errors=True)

print(f"\n{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
