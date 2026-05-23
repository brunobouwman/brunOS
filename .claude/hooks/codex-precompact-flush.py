#!/usr/bin/env python3
"""Codex PreCompact hook: snapshot rollout and fire-and-forget memory_flush.py.

Codex (OpenAI CLI / Desktop) fires this before the context window is compacted.
We dispatch through the same memory_flush.py pipeline Claude Code uses, with
_origin=codex so the Codex JSONL parser kicks in. The codex_watcher.py polling
loop catches sessions that end WITHOUT a compact event; this hook covers
long sessions that compact mid-flight (where we'd otherwise wait until
session-end-via-idle to capture).

Recursion-guarded: exits 0 immediately if CLAUDE_INVOKED_BY is set.
Fails open: any exception writes to stderr and exits 0.

Codex stdin shape (per developers.openai.com/codex/hooks):
    {session_id, transcript_path, cwd, hook_event_name, model, turn_id?,
     permission_mode, trigger ("manual"|"auto")}

We pull cwd to derive the project slug — that lets per-repo hook installs
skip the --project flag when the cwd already matches the convention. The
--project / --default-export flags override the auto-derivation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--project", default=None)
    p.add_argument("--default-export", dest="default_export", default=None)
    args, _ = p.parse_known_args(argv)
    return args


def main() -> int:
    if os.environ.get("CLAUDE_INVOKED_BY"):
        return 0
    args = _parse_args(sys.argv[1:])
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError) as e:
        sys.stderr.write(f"codex-precompact-flush: stdin parse failed ({e})\n")
        return 0
    try:
        from shared import derive_project_slug_from_path, dispatch_flush

        if args.project:
            project = args.project
        else:
            cwd = data.get("cwd")
            project = derive_project_slug_from_path(cwd) if cwd else None

        data["_origin"] = "codex"
        dispatch_flush(
            data,
            source="codex-precompact",
            project=project,
            default_export=args.default_export,
        )
    except Exception as e:
        sys.stderr.write(f"codex-precompact-flush: {type(e).__name__}: {e}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
