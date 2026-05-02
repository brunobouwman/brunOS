#!/usr/bin/env python3
"""Transcript consolidator. Spawned detached by PreCompact and SessionEnd hooks.

CLAUDE_INVOKED_BY MUST be set before importing claude_agent_sdk to keep the
hooks short-circuiting when this script's own session emits SessionEnd. Set
it as the very first executable statement.

Skips:
  - transcript path missing
  - transcript <2KB (interactive sessions don't justify a Sonnet call)
  - dedup hit (same session_id flushed within 60s)
  - empty SDK output or exactly "FLUSH_OK"

On success: appends `## Memory flush (HH:MM)` + bullets to today's daily log,
then unlinks the transcript.
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
    _ts_brt,
)

DEDUP_WINDOW_S = 60
MIN_TRANSCRIPT_BYTES = 2048
MAX_INPUT_CHARS = 200_000
LAST_FLUSH_PATH = STATE_DIR / "last_flush.json"

SYSTEM_PROMPT = """You distil a Claude Code session transcript into durable memory \
for BrunOS. Output only what is worth remembering across sessions: decisions made, \
lessons learned, surprising findings, blockers and TODOs. Skip routine tool output, \
repeated context, and conversational filler.

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


def _load_transcript(path: Path) -> str | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return json.dumps(data, ensure_ascii=False)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 0
    transcript_path = Path(argv[1])
    if not transcript_path.exists():
        return 0

    try:
        size = transcript_path.stat().st_size
    except OSError:
        return 0
    if size < MIN_TRANSCRIPT_BYTES:
        try:
            transcript_path.unlink()
        except OSError:
            pass
        return 0

    payload_text = _load_transcript(transcript_path)
    if payload_text is None:
        return 0

    try:
        meta = json.loads(payload_text)
    except json.JSONDecodeError:
        meta = {}
    session_id = (meta.get("session_id") if isinstance(meta, dict) else None) or "unknown"

    if _within_dedup_window(session_id):
        try:
            transcript_path.unlink()
        except OSError:
            pass
        return 0

    if len(payload_text) > MAX_INPUT_CHARS:
        payload_text = payload_text[-MAX_INPUT_CHARS:]

    try:
        output = asyncio.run(_consolidate(payload_text))
    except Exception as e:
        sys.stderr.write(f"memory_flush: SDK call failed: {type(e).__name__}: {e}\n")
        return 0

    if not output or output.strip() == "FLUSH_OK":
        _record_flush(session_id)
        try:
            transcript_path.unlink()
        except OSError:
            pass
        return 0

    header = f"## Memory flush ({now_brt().strftime('%H:%M')})"
    block = f"\n{header}\n\n{output.strip()}\n"
    try:
        append_to_daily_log(block)
    except Exception as e:
        sys.stderr.write(f"memory_flush: append failed: {type(e).__name__}: {e}\n")
        return 0

    _record_flush(session_id)
    try:
        transcript_path.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
