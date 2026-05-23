#!/usr/bin/env python3
"""SessionStart hook: inject per-project knowledge for an EXTERNAL repo.

Sibling of session-start-context.py, but a narrower scope. Where
session-start-context.py dumps the FULL BrunOS vault (SOUL, USER, MEMORY, daily
logs, HEARTBEAT, HABITS) — right for BrunOS-self sessions — this injects:

  1. SOUL.md + USER.md — ALWAYS. The agent's identity and Bruno's profile travel
     into every session so the agent knows who it is and who it works for.
  2. an optional consolidated context file (--context-file, e.g. projects/vertik.md)
  3. the most-recent distilled session captures in
     BrunOS/Memory/_inbox/sessions/<project>/ (most recent first)

It deliberately omits MEMORY.md, daily logs, HEARTBEAT.md and HABITS.md — the
operational / second-brain-self files — so those don't bleed into a work repo.

Intended for external work repos (Vertik, clients) whose
.claude/settings(.local).json wires it by absolute path with --project=<slug>.

Captures are trusted internal content (already distilled by memory_flush's
Sonnet pass, which forbids secrets), so no sanitization is applied — same as
session-start-context.py.

Recursion-guarded: skips when CLAUDE_INVOKED_BY is a distill/sub-agent value
(mirrors session-start-context.py). Fails open: any exception → stderr, exit 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import vault_path  # noqa: E402

DEFAULT_MAX_CAPTURES = 5
MAX_CAPTURE_CHARS = 10000  # budget for the captures section (context file is extra)
IDENTITY_FILES = ["SOUL.md", "USER.md"]  # always injected, in this order

_SKIP_FOR = {"reflection", "guardrail", "news-digest", "weekly-review", "memory_flush"}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _strip_frontmatter(body: str) -> str:
    """Drop a leading YAML frontmatter block (``---\\n ... \\n---``) if present."""
    if body.startswith("---\n"):
        end = body.find("\n---", 4)
        if end != -1:
            nl = body.find("\n", end + 1)
            if nl != -1:
                return body[nl + 1 :].lstrip("\n")
    return body


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--project", default=None)
    p.add_argument("--context-file", dest="context_file", default=None)
    p.add_argument(
        "--max-captures", dest="max_captures", type=int, default=DEFAULT_MAX_CAPTURES
    )
    # accepted + ignored, for symmetry with the flush hooks' invocation string
    p.add_argument("--default-export", dest="default_export", default=None)
    args, _ = p.parse_known_args(argv)
    return args


def build_context(project: str, context_file: str | None, max_captures: int) -> str:
    vp = vault_path()
    memory = vp / "Memory"
    parts: list[str] = []

    # Optional consolidated context file. Relative paths resolve under Memory/.
    ctx_body = ""
    ctx_name = ""
    if context_file:
        cf = Path(context_file)
        if not cf.is_absolute():
            cf = memory / context_file
        ctx_body = _read(cf)
        ctx_name = cf.name

    ctx_note = f", the context file `{ctx_name}`," if ctx_body else ""
    parts.append(
        f"<!-- BRUNOS_PROJECT_INIT: {project} -->\n"
        f"Loaded below for **{project}**: your identity (SOUL.md) and Bruno's profile "
        f"(USER.md){ctx_note} and the most-recent distilled captures from your prior "
        "BrunOS-recorded sessions in this repo (most recent first). SOUL/USER are who you "
        "are and who you work for; the project material is continuity from past sessions, "
        "not instructions for this one. If you can only see this preamble and a truncation "
        'notice, the full payload spilled to a file (path in your system-reminder as "Full '
        'output saved to: ...") — Read it before responding.\n'
    )

    # 1. Identity + profile — ALWAYS. (Not MEMORY/daily/HEARTBEAT/HABITS — those
    #    are second-brain-self only and never injected into work repos.)
    for name in IDENTITY_FILES:
        body = _read(memory / name)
        if body:
            parts.append(f"<!-- {name} -->\n{body.rstrip()}\n")

    # 2. Optional consolidated context file.
    if ctx_body:
        parts.append(f"<!-- context: {ctx_name} -->\n{ctx_body.rstrip()}\n")

    # 3. Most-recent session captures for this project.
    sessions_dir = memory / "_inbox" / "sessions" / project
    if sessions_dir.is_dir():
        captures = sorted(
            (p for p in sessions_dir.glob("*.md") if not p.stem.startswith("_")),
            reverse=True,
        )[: max(0, max_captures)]
        used = 0
        for c in captures:
            body = _strip_frontmatter(_read(c)).strip()
            if not body:
                continue
            if used and used + len(body) > MAX_CAPTURE_CHARS:
                break
            used += len(body)
            parts.append(f"<!-- session: {c.name} -->\n{body}\n")

    return "\n".join(parts)


def main() -> int:
    if os.environ.get("CLAUDE_INVOKED_BY") in _SKIP_FOR:
        return 0
    args = _parse_args(sys.argv[1:])
    try:
        sys.stdin.read()
    except Exception:
        pass
    if not args.project:
        sys.stderr.write("session-start-project: no --project given; nothing to inject\n")
        return 0
    try:
        ctx = build_context(args.project, args.context_file, args.max_captures)
        if not ctx.strip():
            return 0
        out = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": ctx,
            }
        }
        sys.stdout.write(json.dumps(out))
        sys.stdout.flush()
    except Exception as e:
        sys.stderr.write(f"session-start-project: {type(e).__name__}: {e}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
