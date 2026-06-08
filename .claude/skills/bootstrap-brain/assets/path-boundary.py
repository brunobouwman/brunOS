#!/usr/bin/env python3
"""PreToolUse hook: daemon write/delete path-boundary guard.

For autonomous daemon contexts (CLAUDE_INVOKED_BY in GUARDED_CONTEXTS) — which have NO
human to approve at execution time — this converts two SOUL rules from soft (the model
honoring SOUL) to HARD (deterministic, fires regardless of permission_mode):

  - "no out-of-tree write": Write/Edit/MultiEdit/NotebookEdit are denied when the
    RESOLVED target (symlinks + `..` collapsed) is outside the brain's allowlist
    {code-repo root, vault}.
  - "never delete": Bash single-file destructive ops (rm/rmdir/unlink/shred/mv/truncate/
    find -delete) are denied anywhere — SOUL says archive or mark status, never delete.
    (dangerous-bash.py already covers the catastrophic `rm -rf`/`dd`/`mkfs` cases.)

Pass-through for every non-guarded context: interactive sessions (a human approves),
dev-task project worktrees (legitimately out-of-tree), reflection/dream/consumer (no
write tools). Symlinks and `..` are resolved before the boundary check so it cannot be
walked around. Bash READS (systemctl, journalctl, git status, query.py, memory_search.py)
are unaffected — the guard keys on the destructive verbs + the write target, not on Bash.

Runs via `uv run python` (settings.json), so it can import `shared`. Mirrors the
block-secrets/protect-soul soft-block convention: {"decision":"block","reason":...}.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

# Autonomous, no-human-at-execution surfaces that run an agent with Write/Edit/Bash.
# Extend when a new such daemon is added. NOT interactive (CLAUDE_INVOKED_BY unset),
# NOT dev-task (project worktrees), NOT reflection/dream/consumer (no write tools).
GUARDED_CONTEXTS = {"chat", "heartbeat"}

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Single-file destructive verbs. Each requires a command-position delimiter before the
# verb so substrings (npm, warm, perform, remove, rmdir-vs-rm) don't false-match.
_DELIM = r"(^|[\s;&|`(])"
_DESTRUCTIVE_RE = [
    re.compile(_DELIM + r"rm\s"),
    re.compile(_DELIM + r"rmdir\s"),
    re.compile(_DELIM + r"unlink\s"),
    re.compile(_DELIM + r"shred\b"),
    re.compile(_DELIM + r"mv\s"),
    re.compile(_DELIM + r"truncate\b"),
    re.compile(r"-delete\b"),  # find ... -delete
]


def _allowlist() -> list[Path]:
    """Resolved roots the daemon may write inside: the code repo + the brain's vault."""
    roots: list[Path] = [REPO_ROOT.resolve()]
    vault: Path | None = None
    try:
        from shared import vault_path  # type: ignore

        vault = Path(vault_path()).resolve()
    except Exception:
        env = os.environ.get("BRUNOS_VAULT_PATH")
        if env:
            vault = Path(env).resolve()
        elif (REPO_ROOT / "BrunOS").is_dir():
            vault = (REPO_ROOT / "BrunOS").resolve()
    if vault is not None:
        roots.append(vault)
    return roots


def _within(target: Path, roots: list[Path]) -> bool:
    try:
        rt = Path(os.path.realpath(str(target)))
    except Exception:
        return False
    for root in roots:
        if rt == root or root in rt.parents:
            return True
    return False


def _emit_block(reason: str) -> None:
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    sys.stdout.flush()


def main() -> int:
    if os.environ.get("CLAUDE_INVOKED_BY") not in GUARDED_CONTEXTS:
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}

    if tool in WRITE_TOOLS:
        fp = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if not fp:
            return 0
        cand = Path(fp)
        if not cand.is_absolute():
            cand = REPO_ROOT / cand
        roots = _allowlist()
        # If we resolved a vault root (len>1), enforce; if only the repo resolved AND the
        # target isn't under it, fail-open rather than brick the daemon on a bad env.
        if not _within(cand, roots):
            if len(roots) > 1:
                _emit_block(
                    "Path-boundary guard: this autonomous daemon may only write inside "
                    f"its own brain (code repo + vault). Target {fp!r} resolves outside "
                    "that boundary. Write inside the vault or the code repo instead."
                )
        return 0

    if tool == "Bash":
        cmd = tool_input.get("command") or ""
        if any(rx.search(cmd) for rx in _DESTRUCTIVE_RE):
            _emit_block(
                "Path-boundary guard: deletes/moves are blocked for this autonomous "
                "daemon (SOUL: never delete — archive or mark status instead). Use the "
                "Write/Edit tools or a status change rather than rm/mv/shred/truncate."
            )
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
