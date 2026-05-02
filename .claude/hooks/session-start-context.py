#!/usr/bin/env python3
"""SessionStart hook: dump BrunOS canonical context into the model's window.

Reads (in order): SOUL.md, USER.md, MEMORY.md, last 3 daily logs, HEARTBEAT.md, HABITS.md.
Falls through to BOOTSTRAP.md if it exists.
Fails open: any unexpected exception writes to stderr and exits 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import vault_path  # noqa: E402


CANONICAL_ORDER = ["SOUL.md", "USER.md", "MEMORY.md"]
TAIL_ORDER = ["HEARTBEAT.md", "HABITS.md"]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _wrap(name: str, body: str) -> str:
    return f"<!-- {name} -->\n{body.rstrip()}\n"


def build_context() -> str:
    vp = vault_path()
    memory = vp / "Memory"

    bootstrap = memory / "BOOTSTRAP.md"
    if bootstrap.exists():
        return _wrap("BOOTSTRAP.md", _read(bootstrap))

    parts: list[str] = []
    for name in CANONICAL_ORDER:
        body = _read(memory / name)
        if body:
            parts.append(_wrap(name, body))

    daily_dir = memory / "daily"
    if daily_dir.is_dir():
        candidates = sorted(
            (p for p in daily_dir.glob("*.md") if not p.stem.startswith("_")),
            reverse=True,
        )[:3]
        for p in candidates:
            body = _read(p)
            if body:
                parts.append(_wrap(f"daily/{p.name}", body))

    for name in TAIL_ORDER:
        body = _read(memory / name)
        if body:
            parts.append(_wrap(name, body))

    return "\n".join(parts)


def main() -> int:
    try:
        sys.stdin.read()
    except Exception:
        pass
    try:
        ctx = build_context()
        out = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": ctx,
            }
        }
        sys.stdout.write(json.dumps(out))
        sys.stdout.flush()
    except Exception as e:
        sys.stderr.write(f"session-start-context: {type(e).__name__}: {e}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
