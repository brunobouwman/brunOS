#!/usr/bin/env python3
"""SessionStart hook: dump BrunOS canonical context into the model's window.

Reads (in order): SOUL.md, USER.md, MEMORY.md, the pending-personal buffer
(today's extracted-but-not-yet-curated items), last 3 daily logs, HEARTBEAT.md,
HABITS.md. Falls through to BOOTSTRAP.md if it exists.

Skipped when CLAUDE_INVOKED_BY ∈ {reflection, news-digest, weekly-review,
memory_flush, chat} — those scripts compose their own minimal context and don't
benefit from the full vault dump (and reflection in particular suffers from
double-loading MEMORY.md when the hook fires on top of its already-included
input). The Slack chat bot ("chat") bakes the full vault block into its
system prompt (rebuilt fresh per session) AND its >cap dump would spill to a
file here — so the hook copy is a redundant truncation notice that tells the
bot to Read a file it already has; skip it. Heartbeat AGENT sessions DO want
the context (loaded via setting_sources=["project"]) so we don't skip "heartbeat".

Fails open: any unexpected exception writes to stderr and exits 0.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import format_personal_pending, vault_path  # noqa: E402


CANONICAL_ORDER = ["SOUL.md", "USER.md", "MEMORY.md"]
TAIL_ORDER = ["HEARTBEAT.md", "HABITS.md"]

PREAMBLE = (
    "<!-- BRUNOS_SESSION_INIT -->\n"
    "BrunOS session context loaded below: SOUL.md, USER.md, MEMORY.md, the pending-personal "
    "buffer (today, not yet curated), last 3 daily logs, HEARTBEAT.md, HABITS.md.\n\n"
    "IF YOU CAN ONLY SEE THIS PREAMBLE AND A TRUNCATION NOTICE (you are running under Claude "
    "Code and the hook output exceeded the inline cap), the full payload was spilled to a file. "
    "The path appears in your system-reminder as \"Full output saved to: ...\". READ that file "
    "with the Read tool BEFORE responding to Bruno — it is your working memory for this session, "
    "not optional context.\n\n"
    "If every section below is visible (Slack / Agent-SDK runtime, no truncation), no action "
    "needed — proceed normally.\n"
)


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
        return PREAMBLE + "\n" + _wrap("BOOTSTRAP.md", _read(bootstrap))

    parts: list[str] = [PREAMBLE]
    for name in CANONICAL_ORDER:
        body = _read(memory / name)
        if body:
            parts.append(_wrap(name, body))

    # Today's not-yet-curated personal items (Phase B buffer). Sits right after
    # MEMORY.md so the agent reads it as the fresh tail of durable memory.
    pending = format_personal_pending()
    if pending:
        parts.append(_wrap("pending-personal", pending))

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


_SKIP_FOR = {"reflection", "guardrail", "news-digest", "weekly-review", "memory_flush", "chat"}


def main() -> int:
    if os.environ.get("CLAUDE_INVOKED_BY") in _SKIP_FOR:
        return 0
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
