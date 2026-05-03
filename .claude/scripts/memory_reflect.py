"""Daily reflection: distil yesterday's daily log into MEMORY.md.

Single Sonnet 4.6 call (no tools, no skills) → JSON [{type, text, promote}] →
deterministic Python applies promotions. If MEMORY.md > 5KB after append, a
SECOND Sonnet call compacts older entries before re-writing.

CLAUDE_INVOKED_BY=reflection — the protect-soul.py PreToolUse hook keys off
this value to block any SOUL.md edits (belt-and-suspenders; reflection itself
uses no tools today).

Idempotent via .claude/data/state/last_reflection.json (records the last
YYYY-MM-DD already processed).
"""

from __future__ import annotations

import os

os.environ.setdefault("CLAUDE_INVOKED_BY", "reflection")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from datetime import timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import (  # noqa: E402
    STATE_DIR,
    _ts_brt,
    append_to_daily_log,
    atomic_write,
    file_lock,
    load_env,
    load_state,
    now_brt,
    save_state,
    vault_path,
)

load_env()

LAST_REFLECTION_PATH = STATE_DIR / "last_reflection.json"
DEBUG_DIR = STATE_DIR
MEMORY_REL = "Memory/MEMORY.md"
MEMORY_HARD_CAP_BYTES = 5120
COMPACTION_MIN_RATIO = 0.5  # abort apply if compaction shrinks >50%

SONNET_MODEL = "claude-sonnet-4-6"

REFLECTION_SYSTEM_PROMPT = """You distil yesterday's daily log into durable memory for BrunOS. Output a JSON array, no preamble, no fenced blocks:

[
  {"type": "decision" | "lesson" | "fact" | "status" | "soul-suggestion", "text": "...", "promote": true | false}
]

PROMOTE only what's worth remembering across sessions:
- decisions made (especially with reversal triggers)
- lessons learned (especially uncomfortable ones)
- durable facts about projects, clients, the user's situation
- status changes for active projects (start, finish, blocked, abandoned)

DO NOT PROMOTE:
- routine tool output, repeated context, conversational filler
- ephemeral state ("had a productive morning")
- things already in MEMORY.md (you'll be given its current content)
- one-off commits, single-tick heartbeat noise

Use type "soul-suggestion" ONLY for proposed changes to BrunOS's identity (tone, boundaries, voice). These will be surfaced to Bruno in today's daily log — never written to SOUL.md directly.

Cap at 8 promoted items. If nothing meets the bar, output exactly:

[]

(no preamble, no explanation, no markdown)."""


COMPACTION_SYSTEM_PROMPT = (
    "Compact this MEMORY content to under 4500 bytes. Preserve all section headings as-is "
    "and the most recent / most load-bearing entries in each section. Merge redundant bullets "
    "across sessions. Tighten prose (no hedging, no padding) without losing dates or reversal "
    "triggers. Drop the OLDEST low-signal entries first. Output raw markdown only — no preamble, "
    "no fenced blocks, no explanation. Do not include YAML frontmatter; start directly with "
    "the first heading."
)


SECTION_HEADERS = {
    "decision": "## Key durable decisions",
    "lesson": "## Lessons (curated by reflection)",
    "fact": "## Tax & financial structure",
    "status": "## Active projects",
}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


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


async def _reason(prompt_text: str, *, system_prompt: str | None, max_turns: int = 1) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        allowed_tools=[],
        setting_sources=None,
        system_prompt=system_prompt,
        max_turns=max_turns,
        model=SONNET_MODEL,
    )
    parts: list[str] = []
    async for msg in query(prompt=prompt_text, options=options):
        text = _extract_text(msg)
        if text:
            parts.append(text)
    return "".join(parts).strip()


def _yesterday_str(now_dt) -> str:
    return (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")


def _yesterday_log_path(now_dt) -> Path:
    return vault_path() / "Memory" / "daily" / f"{_yesterday_str(now_dt)}.md"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _parse_promotions(raw: str) -> list[dict] | None:
    """Pull a single JSON array out of Sonnet's output. Tolerant of fences / preamble."""
    if not raw or raw.strip() == "[]":
        return []
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        start = raw.find("[")
        end = raw.rfind("]")
        if start < 0 or end <= start:
            return None
        candidate = raw[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    out: list[dict] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        type_ = str(entry.get("type") or "").strip()
        promote = bool(entry.get("promote"))
        out.append({"type": type_, "text": text, "promote": promote})
    return out


def _split_memory(text: str) -> tuple[str, str]:
    """Return (frontmatter_block, body)."""
    m = re.match(r"\A(---\n.*?\n---\n)(.*)", text, re.DOTALL)
    if not m:
        return "", text
    return m.group(1), m.group(2)


def _append_promotions(memory_text: str, items: list[dict]) -> str:
    """Append promoted items under their section headers in MEMORY.md."""
    if not items:
        return memory_text
    fm, body = _split_memory(memory_text)
    today = now_brt().strftime("%Y-%m-%d")

    for item in items:
        section = SECTION_HEADERS.get(item["type"])
        if section is None:
            continue
        bullet = f"\n- **{today}** — {item['text'].rstrip()}"
        # Insert after the section header's first blank line.
        idx = body.find(section)
        if idx < 0:
            # Section missing — append at end.
            body = body.rstrip() + f"\n\n{section}\n{bullet}\n"
            continue
        # Find the next section start (or EOF).
        next_section_match = re.search(r"^## ", body[idx + len(section):], re.MULTILINE)
        if next_section_match:
            insert_at = idx + len(section) + next_section_match.start()
        else:
            insert_at = len(body)
        # Trim trailing blank lines before next section, then insert.
        head = body[:insert_at].rstrip("\n")
        tail = body[insert_at:]
        body = head + bullet + "\n\n" + tail.lstrip("\n")

    return fm + body


def _surface_soul_suggestions(items: list[dict]) -> None:
    soul_items = [it for it in items if it.get("type") == "soul-suggestion"]
    if not soul_items:
        return
    block_lines = ["", "## SUGGESTED SOUL CHANGES (REVIEW MANUALLY)", ""]
    for it in soul_items:
        block_lines.append(f"- {it['text'].rstrip()}")
    append_to_daily_log("\n".join(block_lines))


def _compact_if_over_cap(memory_text: str) -> str:
    """If memory_text > 5KB, run a Sonnet compaction call on the body only.

    Frontmatter is stripped before sending (the `claude` CLI subprocess treats
    leading `---` as a delimiter); re-attached after compaction. The
    instructions are embedded in the user message rather than passed via
    `--system-prompt` — empirically the bundled CLI fails with exit 1 when a
    long markdown body is paired with a long `--system-prompt` arg, even with
    `setting_sources=None` and the SessionStart hook short-circuited. Abort
    apply on shrink-too-far.
    """
    if len(memory_text.encode("utf-8")) <= MEMORY_HARD_CAP_BYTES:
        return memory_text
    _log(f"  MEMORY.md over cap ({len(memory_text.encode('utf-8'))}B) — compacting")
    fm, body = _split_memory(memory_text)
    if not fm:
        _log("  no frontmatter found; aborting compaction")
        return memory_text
    combined = (
        "INSTRUCTIONS:\n"
        f"{COMPACTION_SYSTEM_PROMPT}\n\n"
        "CONTENT TO COMPACT:\n"
        f"{body}"
    )
    try:
        compacted_body = asyncio.run(_reason(combined, system_prompt=None))
    except Exception as e:
        _log(f"  compaction call failed: {type(e).__name__}: {e}; keeping original")
        return memory_text
    if not compacted_body.strip():
        _log("  compaction returned empty; keeping original")
        return memory_text
    if compacted_body.lstrip().startswith("---"):
        _log("  compaction output included frontmatter despite instruction; stripping")
        compacted_body = re.sub(r"\A---\n.*?\n---\n", "", compacted_body, flags=re.DOTALL)
    if len(compacted_body) < len(body) * COMPACTION_MIN_RATIO:
        _log(
            f"  compaction shrunk too far "
            f"({len(body)} → {len(compacted_body)}); aborting apply"
        )
        return memory_text
    return fm + compacted_body


def _record_done(date_str: str) -> None:
    save_state(LAST_REFLECTION_PATH, {"last": date_str, "ts": _ts_brt()})


def _last_processed() -> str | None:
    state = load_state(LAST_REFLECTION_PATH, default=None) or {}
    return state.get("last") if isinstance(state, dict) else None


def _dump_debug(label: str, payload: str) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    p = DEBUG_DIR / f"reflect-debug-{label}-{now_brt().strftime('%Y%m%dT%H%M%S')}.txt"
    try:
        p.write_text(payload, encoding="utf-8")
        _log(f"  debug dump: {p}")
    except OSError as e:
        _log(f"  debug dump failed: {e}")


def _build_user_prompt(yesterday_log: str, memory_text: str) -> str:
    return (
        "## Current MEMORY.md\n\n"
        f"{memory_text}\n\n"
        "## Yesterday's daily log\n\n"
        f"{yesterday_log}\n"
    )


def _run(dry_run: bool) -> int:
    _log(f"reflection start ({_ts_brt()})")
    now_dt = now_brt()
    yesterday_str = _yesterday_str(now_dt)
    yesterday_path = _yesterday_log_path(now_dt)

    if not yesterday_path.exists():
        _log(f"  no daily log for {yesterday_str}; skipping")
        return 0

    last = _last_processed()
    if last == yesterday_str:
        _log(f"  already reflected on {yesterday_str}; skipping (dedup)")
        return 0

    yesterday_text = _read_text(yesterday_path)
    if len(yesterday_text.strip()) < 100:
        _log(f"  daily log {yesterday_str} too short ({len(yesterday_text)}B); skipping")
        if not dry_run:
            _record_done(yesterday_str)
        return 0

    memory_path = vault_path() / MEMORY_REL
    memory_text = _read_text(memory_path)

    user_prompt = _build_user_prompt(yesterday_text, memory_text)

    _log(f"  calling Sonnet 4.6 on {len(user_prompt)}-char prompt")
    try:
        raw = asyncio.run(
            _reason(user_prompt, system_prompt=REFLECTION_SYSTEM_PROMPT)
        )
    except Exception as e:
        _log(f"  reflection call failed: {type(e).__name__}: {e}")
        return 1

    promotions = _parse_promotions(raw)
    if promotions is None:
        _log("  reflection JSON parse failed; dumping debug and exiting")
        _dump_debug("sonnet-raw", raw)
        return 0

    promoted = [p for p in promotions if p.get("promote")]
    _log(f"  parsed {len(promotions)} items, {len(promoted)} promoted")

    if dry_run:
        sys.stdout.write(json.dumps(promotions, indent=2, ensure_ascii=False))
        sys.stdout.write("\n")
        _log("  dry-run; no vault writes, no state update")
        return 0

    if promoted:
        # 1) SOUL suggestions go to today's daily log.
        _surface_soul_suggestions(promoted)
        # 2) Append non-soul promotions to MEMORY.md.
        appendable = [p for p in promoted if p.get("type") != "soul-suggestion"]
        if appendable:
            new_memory = _append_promotions(memory_text, appendable)
            new_memory = _compact_if_over_cap(new_memory)
            with file_lock(memory_path):
                atomic_write(memory_path, new_memory)
            _log(
                f"  wrote MEMORY.md ({len(new_memory.encode('utf-8'))}B; "
                f"appended {len(appendable)} items)"
            )

    _record_done(yesterday_str)
    _log(f"reflection done; recorded last={yesterday_str}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Daily MEMORY.md reflection")
    parser.add_argument("--dry-run", action="store_true", help="print parsed JSON; skip vault writes and state update")
    args = parser.parse_args(argv[1:])
    return _run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
