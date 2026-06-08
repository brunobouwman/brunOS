#!/usr/bin/env python3
"""Tests for the daemon write/delete path-boundary guard (.claude/hooks/path-boundary.py).

Runs the hook as a subprocess, feeding PreToolUse JSON on stdin + setting the
CLAUDE_INVOKED_BY / BRUNOS_VAULT_PATH env, and asserts block vs pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".claude" / "hooks" / "path-boundary.py"


def _run(tool: str, tool_input: dict, *, invoked_by: str | None, vault: str) -> bool:
    """Return True if the hook BLOCKED the call."""
    env = dict(os.environ)
    env["BRUNOS_VAULT_PATH"] = vault
    if invoked_by is None:
        env.pop("CLAUDE_INVOKED_BY", None)
    else:
        env["CLAUDE_INVOKED_BY"] = invoked_by
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )
    out = (proc.stdout or "").strip()
    if not out:
        return False
    try:
        return json.loads(out).get("decision") == "block"
    except json.JSONDecodeError:
        return False


def main() -> int:
    passed = 0
    failed = 0
    with tempfile.TemporaryDirectory() as vault:
        vfile = str(Path(vault) / "Memory" / "note.md")
        repo_file = str(REPO_ROOT / ".agent" / "plans" / "x.md")
        outside = "/etc/passwd_clone.md"
        outside_home = str(Path.home() / "other-brain" / "secret.md")

        cases: list[tuple[str, bool]] = []

        def check(label: str, got_block: bool, want_block: bool) -> None:
            nonlocal passed, failed
            ok = got_block == want_block
            cases.append((label, ok))
            if ok:
                passed += 1
            else:
                failed += 1

        # --- Non-guarded context (interactive / dev-task): everything passes ---
        check("interactive: out-of-tree write passes",
              _run("Write", {"file_path": outside}, invoked_by=None, vault=vault), False)
        check("interactive: rm passes",
              _run("Bash", {"command": "rm /tmp/foo.md"}, invoked_by=None, vault=vault), False)
        check("dev-task: out-of-tree write passes",
              _run("Write", {"file_path": outside}, invoked_by="dev-task", vault=vault), False)

        # --- Guarded context (chat): out-of-tree writes blocked, in-tree allowed ---
        check("chat: write inside vault passes",
              _run("Write", {"file_path": vfile}, invoked_by="chat", vault=vault), False)
        check("chat: write inside repo passes",
              _run("Edit", {"file_path": repo_file}, invoked_by="chat", vault=vault), False)
        check("chat: write to /etc blocked",
              _run("Write", {"file_path": outside}, invoked_by="chat", vault=vault), True)
        check("chat: write to another home blocked",
              _run("Write", {"file_path": outside_home}, invoked_by="chat", vault=vault), True)
        check("chat: `..` traversal out blocked",
              _run("Write", {"file_path": vfile + "/../../../../../../etc/x.md"},
                   invoked_by="chat", vault=vault), True)

        # --- Guarded context (heartbeat): deletes/moves blocked anywhere ---
        check("heartbeat: rm single file blocked",
              _run("Bash", {"command": "rm " + vfile}, invoked_by="heartbeat", vault=vault), True)
        check("heartbeat: mv blocked",
              _run("Bash", {"command": "mv a.md b.md"}, invoked_by="heartbeat", vault=vault), True)
        check("heartbeat: shred blocked",
              _run("Bash", {"command": "shred -u x"}, invoked_by="heartbeat", vault=vault), True)
        check("heartbeat: find -delete blocked",
              _run("Bash", {"command": "find . -name '*.md' -delete"}, invoked_by="heartbeat", vault=vault), True)

        # --- Guarded context: Bash READS unaffected ---
        check("chat: git status passes",
              _run("Bash", {"command": "git status"}, invoked_by="chat", vault=vault), False)
        check("chat: systemctl status passes",
              _run("Bash", {"command": "systemctl status brunoosbrain-heartbeat"}, invoked_by="chat", vault=vault), False)
        check("chat: query.py read passes",
              _run("Bash", {"command": "uv run python .claude/scripts/query.py slack channels"}, invoked_by="chat", vault=vault), False)
        check("chat: npm (not rm) passes",
              _run("Bash", {"command": "npm run build"}, invoked_by="chat", vault=vault), False)
        check("chat: 'perform' (not rm) passes",
              _run("Bash", {"command": "echo perform"}, invoked_by="chat", vault=vault), False)

    for label, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    print(f"\nResults: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
