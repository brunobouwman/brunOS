#!/usr/bin/env python3
"""PreToolUse hook: block Edit|Write to BrunOS/Memory/SOUL.md when CLAUDE_INVOKED_BY=reflection.

Belt-and-suspenders for the reflection script (which uses no tools today).
Pass-through for every other invocation context.

Stdlib only — runs under system python3 (no .venv).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import vault_path  # noqa: E402


def _resolve_target_paths() -> set[Path]:
    """Two acceptable canonical locations for SOUL.md."""
    out: set[Path] = set()
    try:
        vp = vault_path()
    except RuntimeError:
        return out
    out.add((vp / "Memory" / "SOUL.md").resolve())
    out.add((vp / "SOUL.md").resolve())
    return out


def _matches_soul(file_path_str: str) -> bool:
    if not file_path_str:
        return False
    candidate = Path(file_path_str)
    if not candidate.is_absolute():
        candidate = (REPO_ROOT / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate in _resolve_target_paths() or candidate.name == "SOUL.md" and (
        "Memory" in candidate.parts
    )


def _emit_block(reason: str) -> None:
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    sys.stdout.flush()


def main() -> int:
    if os.environ.get("CLAUDE_INVOKED_BY") != "reflection":
        return 0
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    if not raw:
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    tool_name = payload.get("tool_name") or ""
    if tool_name not in ("Edit", "Write"):
        return 0
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or ""
    if _matches_soul(file_path):
        _emit_block(
            "SOUL.md is write-protected during reflection. "
            "Append proposed changes to today's daily log under "
            "'## SUGGESTED SOUL CHANGES (REVIEW MANUALLY)' instead."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
