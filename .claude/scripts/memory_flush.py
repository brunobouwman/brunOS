#!/usr/bin/env python3
"""Transcript consolidator. Spawned detached by PreCompact and SessionEnd hooks.

Receives a "kickoff" file containing the hook stdin metadata (session_id,
transcript_path, reason, ...). The actual conversation lives at
kickoff["transcript_path"] — Claude Code's session JSONL — and is loaded
separately. The 2KB size filter applies to that real transcript, not the
kickoff metadata.

CLAUDE_INVOKED_BY MUST be set before importing claude_agent_sdk to keep the
hooks short-circuiting when this script's own session emits SessionEnd. Set
it as the very first executable statement.

Skips:
  - kickoff missing or unparseable
  - referenced transcript missing or <2KB (interactive sessions don't justify a Sonnet call)
  - dedup hit (same session_id flushed within 60s)
  - empty SDK output or exactly "FLUSH_OK"

On success: appends `## Memory flush (HH:MM)` + bullets to today's daily log,
then unlinks the kickoff.
"""

from __future__ import annotations

import os

os.environ.setdefault("CLAUDE_INVOKED_BY", "memory_flush")

import asyncio  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import (  # noqa: E402
    STATE_DIR,
    append_to_daily_log,
    load_state,
    now_brt,
    save_state,
    trim_dedup_entries,
    write_inbox_capture,
    _ts_brt,
)

DEDUP_WINDOW_S = 60
MIN_TRANSCRIPT_BYTES = 2048
MAX_INPUT_CHARS = 200_000
LAST_FLUSH_PATH = STATE_DIR / "last_flush.json"

SYSTEM_PROMPT = """You distil an agent session transcript (Claude Code or Codex) \
into durable memory for BrunOS. Output only what is worth remembering across \
sessions: decisions made, lessons learned, surprising findings, blockers and TODOs. \
Skip routine tool output, repeated context, and conversational filler.

NEVER include credentials in your output, even if they appear in the transcript. \
This includes: passwords, API keys, OAuth tokens, bearer tokens, JWTs, SSH keys, \
private keys, postgres/mysql/mongodb connection strings (`postgresql://user:pass@...`), \
session cookies, AWS access keys, secrets in env-var assignments \
(`OPENAI_API_KEY=sk-...`, `PGPASSWORD=...`, `GOOGLE_API_KEY=...`). When a decision \
or lesson genuinely requires referring to one of these, abstract it (e.g., \
"rotated the Vertik prod DB password" — not the password itself). Same rule for \
internal IPs of private infrastructure: refer to roles ("the prod DB") rather than \
literal addresses. The bar: if a bullet contains a string that could authenticate \
someone or grant access to a system, rewrite it.

Format: terse markdown bullets, each one self-contained (no pronouns referring to \
prior bullets). Maximum 12 bullets. If nothing in the transcript meets the bar, \
output exactly:

FLUSH_OK

(no preamble, no explanation)."""


def _within_dedup_window(session_id: str) -> bool:
    state = load_state(LAST_FLUSH_PATH, default={}) or {}
    ts = state.get(session_id)
    if not ts:
        return False
    try:
        last = datetime.fromisoformat(ts)
    except ValueError:
        return False
    return (now_brt() - last).total_seconds() < DEDUP_WINDOW_S


def _record_flush(session_id: str) -> None:
    state = load_state(LAST_FLUSH_PATH, default={}) or {}
    state[session_id] = _ts_brt()
    state = trim_dedup_entries(state, max_age_days=1)
    save_state(LAST_FLUSH_PATH, state)


def _extract_text(msg) -> str:
    direct = getattr(msg, "text", None)
    if isinstance(direct, str) and direct:
        return direct
    chunks: list[str] = []
    content = getattr(msg, "content", None)
    if content is None:
        return ""
    try:
        iterator = iter(content)
    except TypeError:
        return ""
    for block in iterator:
        t = getattr(block, "text", None)
        if isinstance(t, str) and t:
            chunks.append(t)
    return "\n".join(chunks)


async def _consolidate(transcript_text: str) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        allowed_tools=[],
        setting_sources=None,
        system_prompt=SYSTEM_PROMPT,
        max_turns=1,
        model="claude-sonnet-4-6",
    )
    parts: list[str] = []
    async for msg in query(prompt=transcript_text, options=options):
        text = _extract_text(msg)
        if text:
            parts.append(text)
    return "".join(parts).strip()


def _load_kickoff(path: Path) -> dict | None:
    """Read the small handoff file written by dispatch_flush — hook stdin metadata."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _load_session_transcript(transcript_ref: str, origin: str = "claude-code") -> str | None:
    """Read the agent session transcript referenced by the kickoff.

    For Claude Code (`origin="claude-code"`, default), reads the JSONL file
    as raw text — the existing distillation prompt handles Claude Code's
    JSONL turn shape directly.

    For Codex rollouts (`origin="codex"`), uses the codex_rollout parser to
    extract a clean USER/ASSISTANT plaintext stream, dropping reasoning
    blobs, base_instructions, tool-call noise, and token-count events.
    """
    p = Path(transcript_ref)
    if not p.exists():
        return None
    if origin == "codex":
        from codex_rollout import parse_rollout

        result = parse_rollout(p)
        if result is None:
            return None
        _meta, text = result
        return text or None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 0
    kickoff_path = Path(argv[1])
    if not kickoff_path.exists():
        return 0

    kickoff = _load_kickoff(kickoff_path)
    if kickoff is None:
        _unlink(kickoff_path)
        return 0

    session_id = kickoff.get("session_id") or "unknown"

    if _within_dedup_window(session_id):
        _unlink(kickoff_path)
        return 0

    transcript_ref = kickoff.get("transcript_path")
    origin = (kickoff.get("_origin") or "claude-code").strip().lower()
    transcript_text = (
        _load_session_transcript(transcript_ref, origin) if transcript_ref else None
    )
    if transcript_text is None or len(transcript_text) < MIN_TRANSCRIPT_BYTES:
        _unlink(kickoff_path)
        return 0

    if len(transcript_text) > MAX_INPUT_CHARS:
        transcript_text = transcript_text[-MAX_INPUT_CHARS:]

    try:
        output = asyncio.run(_consolidate(transcript_text))
    except Exception as e:
        sys.stderr.write(f"memory_flush: SDK call failed: {type(e).__name__}: {e}\n")
        return 0

    if not output or output.strip() == "FLUSH_OK":
        _record_flush(session_id)
        _unlink(kickoff_path)
        return 0

    project = (kickoff.get("_project") or "").strip()
    default_export = (kickoff.get("_default_export") or "").strip()
    source = kickoff.get("_source") or "session-end"
    header = f"## Memory flush ({now_brt().strftime('%H:%M')})"
    block = f"\n{header}\n\n{output.strip()}\n"

    try:
        if project and project.lower() != "brunos":
            write_inbox_capture(
                project=project,
                default_export=default_export or "personal",
                session_id=session_id,
                source=source,
                body=block,
            )
        else:
            append_to_daily_log(block)
    except Exception as e:
        sys.stderr.write(f"memory_flush: write failed: {type(e).__name__}: {e}\n")
        return 0

    _record_flush(session_id)
    _unlink(kickoff_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
