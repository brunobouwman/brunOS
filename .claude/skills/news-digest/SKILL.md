---
name: news-digest
description: Daily AI-engineering news digest for BrunOS. Use when Bruno asks for the day's AI news, what's new in the feeds, the morning digest, or runs digest.py. Reads new RSS items via integrations.rss, scores via Haiku 4.5 on agent frameworks / Claude / eval / production AI relevance, dedupes against past digests, clusters survivors into 3–5 themes, summarizes each in 2 sentences, writes Memory/news-digest/YYYY-MM-DD.md. Triggers on "AI news", "morning digest", "what's new today", "summarize the feeds", explicit /news-digest invocations.
---

# News Digest Skill

A daily AI-engineering digest. Filters Bruno's curated RSS firehose down to 3–5 themed clusters with 2-sentence summaries.

## When to invoke

- Morning briefing (Phase 6's heartbeat will fire this at 07:30 BRT — until then, manual or ad-hoc).
- Bruno explicitly asks for "today's news", "morning digest", "what's new in the feeds".
- Before a planning block, when context on the week's AI landscape matters.

## How to invoke

```bash
uv run python .claude/skills/news-digest/scripts/digest.py
```

Optional flags:
- `--dry-run` — print to stdout, skip vault write.
- `--max-items N` — cap RSS pull at N items (debug / smoke tests).

## Output

`BrunOS/Memory/news-digest/YYYY-MM-DD.md`. Frontmatter `type: digest`, `tags: [news, digest]`. Idempotent — re-running on the same day overwrites.

If fewer than 3 items survive scoring + dedup, the digest is a one-line "Slow news day" placeholder rather than padded prose.

## Pipeline (what the script does)

1. `integrations.rss.new_items()` — pulls unseen items since last run (state in `.claude/data/state/rss-state.json`).
2. Dedup against past digests via `memory_search.py --path-prefix news-digest`. Items previously covered are dropped.
3. **Haiku 4.5** scores each item 0–10 on AI-engineering relevance using `references/scoring-rubric.md`.
4. Items with score ≥ 7 survive.
5. **Sonnet 4.6** clusters survivors into 3–5 themes and writes 2-sentence summaries each.
6. Write digest + appendix of source items (title, url, score) via `shared.atomic_write`.

## References

- `${CLAUDE_SKILL_DIR}/references/scoring-rubric.md` — the relevance criteria. Loaded by the script at startup; readable by the agent if asked to explain why an item scored low.
