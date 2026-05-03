#!/usr/bin/env python3
"""PreToolUse hook: block destructive or exfiltrating Bash commands.

Stdlib only — runs under system python3 (no .venv).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import DANGEROUS_BASH_PATTERNS  # noqa: E402

_SUBSHELL = re.compile(r"\$\(([^)]*)\)|`([^`]*)`")
_PATH_PREFIX = re.compile(r"(^|\s)/(usr/local/|usr/|)bin/")


def _normalize_command(cmd: str, depth: int = 0) -> list[str]:
    """Return the command plus recursively unwrapped subshell snippets."""
    if depth > 5:
        return [_PATH_PREFIX.sub(r"\1", cmd)]
    out = [_PATH_PREFIX.sub(r"\1", cmd)]
    for match in _SUBSHELL.finditer(cmd):
        inner = match.group(1) or match.group(2) or ""
        out.extend(_normalize_command(inner, depth + 1))
    return out


def _emit_block(pattern: str) -> None:
    sys.stderr.write(
        f"Blocked dangerous command pattern: {pattern}. Ask Bruno before retrying.\n"
    )
    sys.stderr.flush()


def main() -> int:
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
    if (payload.get("tool_name") or "") != "Bash":
        return 0
    tool_input = payload.get("tool_input") or {}
    command = str(tool_input.get("command") or "")
    if not command:
        return 0
    variants = _normalize_command(command)
    for variant in variants:
        for pattern in DANGEROUS_BASH_PATTERNS:
            if re.search(pattern, variant, flags=re.IGNORECASE):
                _emit_block(pattern)
                return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
