"""News digest pipeline: RSS → dedup → Haiku score → Sonnet summarize → vault.

Pipeline:
  1. integrations.rss.new_items()  — unseen items since last run
  2. memory_search.py --path-prefix news-digest  — drop items already covered
  3. Haiku 4.5  — score 0–10 on AI-engineering relevance (rubric inlined)
  4. filter score ≥ 7  — slow news day if <3 survive
  5. Sonnet 4.6  — cluster into 3–5 themes, 2-sentence summaries
  6. atomic_write to BrunOS/Memory/news-digest/YYYY-MM-DD.md (idempotent overwrite)

Models locked: claude-haiku-4-5-20251001 (scoring), claude-sonnet-4-6 (summary).
Recursion guard mandatory — set CLAUDE_INVOKED_BY before SDK import.
"""

from __future__ import annotations

import os

os.environ.setdefault("CLAUDE_INVOKED_BY", "news-digest")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import atomic_write, load_env, now_brt, vault_path, _ts_brt  # noqa: E402

load_env()

from integrations.rss import FeedItem, new_items  # noqa: E402
from sanitize import wrap_external  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
RUBRIC_PATH = SCRIPT_DIR.parent / "references" / "scoring-rubric.md"
DEBUG_DIR = REPO_ROOT / ".claude" / "data" / "state"

DEDUP_SCORE_THRESHOLD = 0.5
SCORE_KEEP_THRESHOLD = 7
MAX_RSS_ITEMS = 200
MIN_SURVIVORS = 3

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"


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


async def _reason(prompt_text: str, *, model: str, system_prompt: str) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        allowed_tools=[],
        setting_sources=None,
        system_prompt=system_prompt,
        max_turns=1,
        model=model,
    )
    parts: list[str] = []
    async for msg in query(prompt=prompt_text, options=options):
        text = _extract_text(msg)
        if text:
            parts.append(text)
    return "".join(parts).strip()


def _dedup_against_past(items: list[FeedItem]) -> list[FeedItem]:
    """Drop items whose title hits a past digest above DEDUP_SCORE_THRESHOLD."""
    search_script = REPO_ROOT / ".claude" / "scripts" / "memory_search.py"
    survivors: list[FeedItem] = []
    drops = 0
    for item in items:
        query_text = item.title.strip()[:200]
        if not query_text:
            survivors.append(item)
            continue
        try:
            result = subprocess.run(
                [
                    sys.executable, str(search_script), query_text,
                    "--k", "1", "--path-prefix", "news-digest",
                ],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            _log(f"  dedup search failed for {query_text!r}: {type(e).__name__}; keeping item")
            survivors.append(item)
            continue
        if result.returncode != 0 or not result.stdout.strip():
            survivors.append(item)
            continue
        try:
            hits = json.loads(result.stdout)
        except json.JSONDecodeError:
            survivors.append(item)
            continue
        if hits and isinstance(hits, list):
            top_score = hits[0].get("score", 0) if isinstance(hits[0], dict) else 0
            if isinstance(top_score, (int, float)) and top_score > DEDUP_SCORE_THRESHOLD:
                drops += 1
                continue
        survivors.append(item)
    _log(f"  dedup: dropped {drops}, kept {len(survivors)}")
    return survivors


def _build_scoring_prompt(items: list[FeedItem], rubric: str) -> tuple[str, str]:
    system = (
        "You score AI-engineering RSS items for Bruno's daily digest. "
        "Output exactly one JSON array — no preamble, no fenced code blocks, no explanation. "
        "Each element: {\"id\": <int index>, \"score\": <0-10 int>, \"reason\": <short str>}.\n\n"
        "Use this rubric:\n\n" + rubric
    )
    lines = ["Score each item below. Return ONE JSON array, in input order.\n"]
    for idx, item in enumerate(items):
        summary = re.sub(r"\s+", " ", item.summary).strip()[:600]
        content = [f"title: {item.title}", f"feed: {item.feed_url}"]
        if summary:
            content.append(f"summary: {summary}")
        lines.append(f"--- item {idx} ---")
        lines.append(
            wrap_external(
                "\n".join(content),
                "rss",
                id=str(idx),
                feed=item.feed_url,
                title=item.title[:80],
            )
        )
        lines.append("")
    user = "\n".join(lines)
    return system, user


def _parse_scores(raw: str) -> list[dict] | None:
    """Pull the first JSON array out of Haiku's output. Tolerant of fences / preamble."""
    if not raw:
        return None
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
        try:
            score = int(entry.get("score", 0))
        except (TypeError, ValueError):
            continue
        try:
            idx = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        out.append({"id": idx, "score": score, "reason": str(entry.get("reason", ""))})
    return out


def _build_summary_prompt(items: list[FeedItem], scores_by_id: dict[int, dict]) -> tuple[str, str]:
    system = (
        "You write Bruno's daily AI-engineering digest. Cluster the items below into "
        "3 to 5 coherent themes. For each theme: a short H2-style header (## Theme name), "
        "then exactly 2 sentences of summary. Be terse, factual, no marketing. "
        "Skip themes with weak signal rather than padding. Output markdown only — "
        "no preamble, no closing remark."
    )
    lines = ["Items to cluster + summarize (already filtered for relevance):\n"]
    for idx, item in enumerate(items):
        meta = scores_by_id.get(idx, {})
        score = meta.get("score", "?")
        summary = re.sub(r"\s+", " ", item.summary).strip()[:600]
        content = [
            f"title: {item.title}",
            f"link: {item.link}",
            f"score: {score}",
        ]
        if summary:
            content.append(f"summary: {summary}")
        lines.append(f"--- item {idx} (score {score}) ---")
        lines.append(
            wrap_external(
                "\n".join(content),
                "rss",
                id=str(idx),
                feed=item.feed_url,
                title=item.title[:80],
            )
        )
        lines.append("")
    return system, "\n".join(lines)


def _build_appendix(items: list[FeedItem], scores_by_id: dict[int, dict]) -> str:
    parts = ["## Source items", ""]
    for idx, item in enumerate(items):
        meta = scores_by_id.get(idx, {})
        score = meta.get("score", "?")
        parts.append(f"- ({score}/10) [{item.title}]({item.link})")
    return "\n".join(parts) + "\n"


def _build_frontmatter(date_str: str) -> str:
    ts = _ts_brt()
    return (
        "---\n"
        "type: digest\n"
        f"created: {ts}\n"
        f"updated: {ts}\n"
        "tags:\n"
        "  - news\n"
        "  - digest\n"
        "status: active\n"
        f"date: {date_str}\n"
        "---\n\n"
    )


def _slow_news_day(items: list[FeedItem], scores_by_id: dict[int, dict]) -> str:
    survivors = sorted(
        ((idx, scores_by_id.get(idx, {}).get("score", 0)) for idx in scores_by_id),
        key=lambda t: t[1], reverse=True,
    )
    if survivors:
        top_idx, top_score = survivors[0]
        item = items[top_idx]
        return f"Slow news day: {len(scores_by_id)} items below threshold. Top: ({top_score}/10) [{item.title}]({item.link}).\n"
    return f"Slow news day: {len(items)} items pulled, none above threshold.\n"


def _dump_debug(label: str, payload: str) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    debug_path = DEBUG_DIR / f"news-digest-debug-{label}-{now_brt().strftime('%Y%m%dT%H%M%S')}.txt"
    try:
        debug_path.write_text(payload, encoding="utf-8")
        _log(f"  debug dump: {debug_path}")
    except OSError as e:
        _log(f"  debug dump failed: {e}")


def _run(dry_run: bool, max_items: int | None) -> int:
    _log(f"news-digest start ({_ts_brt()})")

    rubric = RUBRIC_PATH.read_text(encoding="utf-8")

    _log("stage 1: pulling RSS new_items")
    try:
        items = new_items()
    except Exception as e:
        _log(f"  rss.new_items failed: {type(e).__name__}: {e}")
        return 1
    if max_items is not None:
        items = items[:max_items]
    else:
        items = items[:MAX_RSS_ITEMS]
    _log(f"  pulled {len(items)} items")
    if not items:
        _log("stage done: no new items; nothing to write")
        return 0

    _log("stage 2: dedup against past digests")
    items = _dedup_against_past(items)
    if not items:
        _log("stage done: all items already covered; nothing to write")
        return 0

    _log(f"stage 3: scoring {len(items)} items via Haiku 4.5")
    sys_prompt, user_prompt = _build_scoring_prompt(items, rubric)
    try:
        raw = asyncio.run(_reason(user_prompt, model=HAIKU_MODEL, system_prompt=sys_prompt))
    except Exception as e:
        _log(f"  Haiku call failed: {type(e).__name__}: {e}")
        return 1
    parsed = _parse_scores(raw)
    if parsed is None:
        _log("  scoring JSON parse failed; dumping debug and exiting")
        _dump_debug("haiku-raw", raw)
        return 0
    scores_by_id = {entry["id"]: entry for entry in parsed if 0 <= entry["id"] < len(items)}
    _log(f"  parsed {len(scores_by_id)} scored items")

    survivor_ids = sorted(
        idx for idx, meta in scores_by_id.items() if meta["score"] >= SCORE_KEEP_THRESHOLD
    )
    survivors = [items[i] for i in survivor_ids]
    _log(f"stage 4: kept {len(survivors)} (score >= {SCORE_KEEP_THRESHOLD})")

    date_str = now_brt().strftime("%Y-%m-%d")
    out_path = vault_path() / "Memory" / "news-digest" / f"{date_str}.md"

    if len(survivors) < MIN_SURVIVORS:
        body = _slow_news_day(items, scores_by_id)
        content = _build_frontmatter(date_str) + f"# AI digest — {date_str}\n\n" + body
        if dry_run:
            sys.stdout.write(content)
            return 0
        atomic_write(out_path, content)
        _log(f"wrote slow-news placeholder → {out_path}")
        return 0

    survivor_scores_by_id = {
        new_idx: scores_by_id[old_idx] for new_idx, old_idx in enumerate(survivor_ids)
    }

    _log(f"stage 5: clustering + summarizing via Sonnet 4.6")
    sys_prompt, user_prompt = _build_summary_prompt(survivors, survivor_scores_by_id)
    try:
        summary_md = asyncio.run(_reason(user_prompt, model=SONNET_MODEL, system_prompt=sys_prompt))
    except Exception as e:
        _log(f"  Sonnet call failed: {type(e).__name__}: {e}")
        return 1
    if not summary_md.strip():
        _log("  Sonnet returned empty output; aborting write")
        return 0

    appendix = _build_appendix(survivors, survivor_scores_by_id)
    content = (
        _build_frontmatter(date_str)
        + f"# AI digest — {date_str}\n\n"
        + summary_md.strip() + "\n\n"
        + appendix
    )

    if dry_run:
        sys.stdout.write(content)
        _log(f"dry-run complete (would have written {out_path})")
        return 0

    atomic_write(out_path, content)
    _log(f"wrote digest → {out_path} ({len(content)} chars)")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Daily AI-engineering news digest")
    parser.add_argument("--dry-run", action="store_true", help="print to stdout, skip vault write")
    parser.add_argument("--max-items", type=int, default=None, help="cap RSS pull at N items")
    args = parser.parse_args(argv[1:])
    return _run(dry_run=args.dry_run, max_items=args.max_items)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
