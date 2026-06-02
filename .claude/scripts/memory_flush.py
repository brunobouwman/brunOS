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
import re  # noqa: E402
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
# Per-session line watermark for incremental (chat-resume) flushes — see
# _slice_incremental. Distinct from LAST_FLUSH_PATH (60s dedup timestamps).
FLUSH_OFFSETS_PATH = STATE_DIR / "flush_offsets.json"

# When the flush model returns content we can't parse as the {work, personal}
# JSON, which bucket should the unparsed blob fall into? Default "personal" =
# privacy-first (daily log only, never a company inbox). A deployment can set
# BRUNOS_FLUSH_PARSE_FALLBACK=work to prefer not losing project continuity — it's
# a per-company privacy-vs-continuity business decision, hence configurable.
_FLUSH_PARSE_FALLBACK = (
    os.environ.get("BRUNOS_FLUSH_PARSE_FALLBACK", "personal").strip().lower()
)

SYSTEM_PROMPT = """You distil an agent session transcript (Claude Code or Codex) \
into durable memory for BrunOS, Bruno's second brain. Return ONE JSON object \
(no preamble, no fenced blocks):

{
  "work": "<terse markdown bullets — project/professional content only>",
  "personal": "<terse markdown bullets — Bruno's personal asides only, or empty string>"
}

WORK FIELD: decisions made, lessons learned, surprising findings, blockers, TODOs, \
architecture choices, client/project facts. Must be safe to share with a company-brain \
consumer. NEVER include: personal mood, health, family, personal finances, personal \
relationship asides, Bruno's non-work thoughts.

PERSONAL FIELD: mood notes, health, family mentions, personal decisions, any \
non-work aside Bruno mentioned. If the session contains no personal content, \
return an empty string "".

NEVER include credentials in EITHER field, even if they appear in the transcript. \
This includes: passwords, API keys, OAuth tokens, bearer tokens, JWTs, SSH keys, \
private keys, connection strings, session cookies, AWS access keys. Abstract them \
(e.g., "rotated the prod DB password" — not the password itself). Same rule for \
internal IPs: refer to roles ("the prod DB"), not literal addresses.

Format for each field: terse markdown bullets, each self-contained (no pronouns \
referring to prior bullets). Maximum 10 bullets per field. If neither field \
contains anything worth capturing, output exactly:

FLUSH_OK

(no preamble, no JSON, no explanation — just the literal string FLUSH_OK)."""


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


def _load_offset(session_id: str) -> int:
    state = load_state(FLUSH_OFFSETS_PATH, default={}) or {}
    v = state.get(session_id)
    return v if isinstance(v, int) and v >= 0 else 0


def _record_offset(session_id: str, total_lines: int) -> None:
    state = load_state(FLUSH_OFFSETS_PATH, default={}) or {}
    state[session_id] = total_lines
    save_state(FLUSH_OFFSETS_PATH, state)


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


def _parse_flush_output(raw: str) -> tuple[str, str]:
    """Parse structured flush output into (work_text, personal_text).

    Returns ("", "") for FLUSH_OK.
    On JSON parse failure, preserves the content but routes it per
    _FLUSH_PARSE_FALLBACK: "personal" (default, privacy-first → daily log only,
    never a company inbox) or "work" (continuity-first → eligible for the inbox).
    """
    stripped = raw.strip()
    if stripped == "FLUSH_OK":
        return "", ""
    # Try JSON parse (tolerate markdown fences)
    candidate = stripped
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1)
    else:
        # Try to find bare { ... }
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            work = str(parsed.get("work") or "").strip()
            personal = str(parsed.get("personal") or "").strip()
            return work, personal
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback (data-preserving, never loses content): route the unparsed blob
    # per the configured privacy-vs-continuity preference. Default = personal.
    if _FLUSH_PARSE_FALLBACK == "work":
        return stripped, ""
    return "", stripped


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
    # Incremental flush (chat resume): the same session_id is reaped repeatedly
    # against one ever-growing JSONL. Distil only lines past the watermark so the
    # daily log doesn't re-accumulate earlier bullets. JSONL-line based, so it
    # only applies to the claude-code origin (codex uses a parsed stream).
    incremental = bool(kickoff.get("_incremental")) and origin == "claude-code"
    transcript_text = (
        _load_session_transcript(transcript_ref, origin) if transcript_ref else None
    )
    if transcript_text is None:
        _unlink(kickoff_path)
        return 0

    new_offset: int | None = None
    if incremental:
        lines = transcript_text.splitlines()
        new_offset = len(lines)
        transcript_text = "\n".join(lines[_load_offset(session_id):])

    if len(transcript_text) < MIN_TRANSCRIPT_BYTES:
        # Too little NEW content to justify a Sonnet call; leave the watermark so
        # the tail accumulates and gets flushed once it's substantial.
        _unlink(kickoff_path)
        return 0

    if len(transcript_text) > MAX_INPUT_CHARS:
        transcript_text = transcript_text[-MAX_INPUT_CHARS:]

    try:
        output = asyncio.run(_consolidate(transcript_text))
    except Exception as e:
        sys.stderr.write(f"memory_flush: SDK call failed: {type(e).__name__}: {e}\n")
        return 0

    work_text, personal_text = _parse_flush_output(output)

    if not work_text and not personal_text:
        # FLUSH_OK or parse returned nothing — record and exit cleanly
        _record_flush(session_id)
        if new_offset is not None:
            _record_offset(session_id, new_offset)
        _unlink(kickoff_path)
        return 0

    project = (kickoff.get("_project") or "").strip()
    default_export = (kickoff.get("_default_export") or "").strip()
    source = kickoff.get("_source") or "session-end"
    ts_label = now_brt().strftime("%H:%M")

    try:
        if project and project.lower() != "brunos":
            # Shareable capture: only work content goes to the project inbox.
            # Personal content goes directly to today's daily log — it has
            # NO code path to the company inbox (Layer 1 structural separation).
            if work_text:
                work_block = f"\n## Memory flush ({ts_label})\n\n{work_text}\n"
                write_inbox_capture(
                    project=project,
                    default_export=default_export or "personal",
                    session_id=session_id,
                    source=source,
                    body=work_block,
                )
            if personal_text:
                personal_block = f"\n## Personal note ({ts_label})\n\n{personal_text}\n"
                append_to_daily_log(personal_block)
        else:
            # Personal / BrunOS session — all content goes to the daily log.
            parts = []
            if work_text:
                parts.append(f"## Memory flush ({ts_label})\n\n{work_text}")
            if personal_text:
                parts.append(f"## Personal note ({ts_label})\n\n{personal_text}")
            if parts:
                append_to_daily_log("\n" + "\n\n".join(parts) + "\n")
    except Exception as e:
        sys.stderr.write(f"memory_flush: write failed: {type(e).__name__}: {e}\n")
        return 0

    _record_flush(session_id)
    if new_offset is not None:
        _record_offset(session_id, new_offset)
    _unlink(kickoff_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
