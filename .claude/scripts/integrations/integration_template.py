"""Template for a Phase 4 integration. Copy-rename to `<platform>.py`.

The contract:
  1. dataclass model       — what the rest of the codebase consumes
  2. _client()             — load token from os.environ; explicit RuntimeError if missing
  3. query fns             — return list[Dataclass]; wrap network calls in shared.with_retry
  4. (optional) state diff — load_state / save_state under STATE_DIR / "<name>-state.json"
  5. format_for_context()  — markdown for Phase 6 heartbeat / Phase 8 sanitize
  6. add_subparser(sub)    — argparse registration
  7. cli(args)             — dispatch entrypoint, returns exit code

DO NOT:
  - Set CLAUDE_INVOKED_BY (Phase 4 has no Agent SDK calls).
  - Print or log tokens, full email bodies, or OAuth refresh tokens.
  - Use `with_retry` for auth/permission errors — it only retries on 429/500/502/503.
  - Import platform SDKs at module top if the SDK might not be installed
    (registry.py imports this template's module name to enumerate, so keep it inert).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import STATE_DIR  # noqa: E402

NAME = "template"
ENV_VAR = "TEMPLATE_TOKEN"
STATE_PATH = STATE_DIR / f"{NAME}-state.json"


@dataclass(frozen=True)
class Item:
    """Replace with your platform's primary entity (Channel, Message, Issue, ...)."""

    id: str
    title: str


_CLIENT = None


def _client():
    """Load token, build authenticated client, cache module-level."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    token = os.environ.get(ENV_VAR, "").strip()
    if not token:
        raise RuntimeError(f"{ENV_VAR} not set in environment (.claude/.env)")
    raise NotImplementedError("Replace with real client construction.")


def example_query(client) -> list[Item]:
    """Replace with your real read function. Wrap network in shared.with_retry."""
    raise NotImplementedError


def format_for_context(items: list[Item]) -> str:
    if not items:
        return f"_No {NAME} items._\n"
    lines = [f"### {NAME.title()}", ""]
    for it in items:
        lines.append(f"- {it.id}: {it.title}")
    return "\n".join(lines) + "\n"


def add_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(NAME, help=f"{NAME} integration (template)")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("noop", help="placeholder subcommand")
    p.set_defaults(_handler=cli)


def cli(args: argparse.Namespace) -> int:
    print(f"[{NAME}] template — replace with real integration", file=sys.stderr)
    return 0
