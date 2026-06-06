---
name: memory-search
description: Hybrid memory-search skill for BrunOS's RAG over BrunOS/Memory/. Use whenever Bruno asks to search, recall, find, dedupe, or match the tone of anything in the vault — daily logs, drafts, projects, clients, goals, content, team, research, news-digest, meetings, personal — or asks "did I", "have I", "when did I", "what did I write about", "is there a prior", "similar to", "tone match", "voice corpus", or runs memory_search.py / memory_index.py directly. Teaches the canonical CLI invocation, asymmetric BGE query phrasing (short natural-language sentences, not keyword soup), the folder→--path-prefix cheat sheet, RRF score interpretation (ordinal, not absolute), the read-after-search workflow (chunks are 400-token slices — Read the file_path of high-RRF hits for full context), the FTS5 operator escape hatch (+required / -excluded / "exact phrase" / prefix*), the no-date-filter limit, the personal/finance.md exclusion, the pending personal buffer (today's extracted-but-not-yet-curated items, auto-appended to unscoped results so intraday knowledge is recallable before nightly curation), and a fallback ladder when results look thin or stale. Pairs with the brunos-vault skill (folder semantics) — this one is for retrieval-by-meaning.
---

# Memory Search Skill

Hybrid retrieval over `BrunOS/Memory/**/*.md`: vector top-k×3 (BGE-small-en-v1.5, 384-dim, asymmetric) + FTS5 top-k×3 (porter unicode61), fused via RRF (k=60). Index lives at `.claude/data/state/memory.db`. Search engine: `.claude/scripts/memory_search.py`. Indexer: `.claude/scripts/memory_index.py`.

## How to invoke

```bash
uv run python .claude/scripts/memory_search.py "<natural-language query>" [--k N] [--path-prefix <folder>]
```

Defaults: `--k 10`, no path filter. Output is JSON to stdout:

```json
[
  {"id": 1234, "file_path": "research/pgvector-indexing-notes.md", "chunk_idx": 0, "content": "...", "score": 0.0317}
]
```

In-process Python API for scripted callers: `from memory_search import search; search(query, k=10, path_prefix=None)`.

## When to use it (vs Read vs Grep)

| Situation | Tool |
|---|---|
| Known file path, want full content | `Read` |
| Known exact string, want every match | `Grep` over `BrunOS/Memory/` |
| Question about meaning ("what did I think about X", "tone match Y", "have I covered Z") | `memory_search.py` |

For folder semantics, frontmatter spec, language routing, and boundaries, defer to the `brunos-vault` skill — that one teaches *where* things live; this one teaches *how to retrieve them by meaning*.

## Pending personal buffer (today's not-yet-curated items)

The hourly reflection inbox pass extracts durable personal items but **buffers** them in `.claude/data/state/personal_pending.json` until the daily curation pass folds them into `MEMORY.md`. Those items aren't in the vault or the index yet — without this, a fact learned at 09:00 would be unrecallable until tomorrow's curation + reindex.

- **Unscoped searches include them automatically.** Up to 3 lexical buffer matches are *appended* to the results, each tagged `"pending": true` with `file_path: ".claude/data/state/personal_pending.json"`. Treat them as fresh, low-friction memory — already extracted, just not yet promoted. Their `score` is query/item token-overlap (0–1), **not** comparable to RRF; they're a supplement after the ranked vault hits, so don't read a high pending score as "more relevant than the vault hits."
- **Scoped searches skip them.** `--path-prefix <folder>` never returns buffer rows (and neither do dedup callers like news-digest / dreaming, which always scope) — a folder-scoped search is asking about the vault, not the buffer.
- **Read the whole buffer directly** when you want today's full set, not just query matches: `Read .claude/data/state/personal_pending.json`. It's drained + cleared each day by the curation pass, so it only ever holds *today's* extractions.
- The same items are also injected into every session's loaded context (the `pending-personal` block, right after MEMORY.md), so you usually already have them — search is for when you need to *retrieve* a specific one mid-task.
- Disable with `--no-pending` / `BRUNOS_SEARCH_NO_PENDING=1`.

## Query phrasing for asymmetric BGE

Queries are encoded with `query_embed`, passages with `passage_embed` — different code paths, model trained for the asymmetry. Phrase queries as short natural-language sentences. The FTS5 leg uses `porter unicode61`, so English stems are folded automatically ("running" matches "ran" matches "runs") — don't enumerate variants.

> **Good**: `"did I write notes on pgvector hnsw vs ivfflat tradeoffs"` — natural-language, full words, one clear topic.
>
> **Bad**: `"pgvector hnsw ivfflat OR ivf"` — keyword soup; defeats BGE recall, and FTS5's default is implicit AND between terms anyway.

## `--path-prefix` cheat sheet

| Folder prefix | Question type | Example query |
|---|---|---|
| `daily` | "what did I do / think / decide last week" | `"how did I feel about the vertik scope creep"` |
| `drafts/sent` | tone matching for new drafts; voice corpus | `"how I usually open replies to marcus"` |
| `drafts/active` | reply drafts in flight | `"what's the open thread with lisa about pricing"` |
| `projects` | project context recall (Vertik, BrunOS, AI mastery) | `"what's the BrunOS phase 9 deployment plan"` |
| `clients` | Protostack labs/clinics context | `"what does clinica X want from us"` |
| `research` | AI-engineering learning notes | `"my notes on agent observability"` |
| `goals` | weekly/monthly/vision context | `"this month's focus areas"` |
| `news-digest` | dedup or recall past digests | `"have I covered the latest claude release"` |
| `meetings` | "what did we decide in that meeting" | `"the protostack pricing kickoff"` |
| `team` | Lisa, contractors, partners context | `"lisa's preferences on async vs sync"` |
| `content` | content ideas + drafts | `"my unposted linkedin draft about evals"` |

> **Gotcha**: prefix is matched as `c.file_path LIKE '<prefix>/%'` — pass a folder name with **no trailing slash**. `drafts/sent` works; `drafts/sent/` returns nothing. `daily/2026` also returns nothing — daily files are flat (`daily/2026-05-02.md`), no year subfolder. For year/month filtering, fall back to FTS5 in the query string (e.g., include `"2026-04"` as a phrase) or `Grep` after.

## RRF score interpretation

Scores are *ordinal*, not absolute. They sum `1/(60 + rank)` contributions across the vector and FTS rankings; max possible is `1/61 + 1/61 ≈ 0.033` (a chunk that ranked #1 in both legs). Most useful hits land in 0.005–0.025.

**Use the gap, not the value.** If rank-1 is 0.025 and rank-2 is 0.005, rank-1 is probably the answer. If the top 5 hits are all in 0.010–0.012, treat them as a *set* and `Read` more files to disambiguate.

## Result post-processing (read-after-search)

Chunks are 400 tokens with 50-token overlap (`memory_index.py:CHUNK_TOKENS`/`OVERLAP_TOKENS`). The JSON content is *just that slice* — frontmatter or surrounding context may be in adjacent chunks or simply outside the window.

**For any high-RRF hit you actually plan to use, `Read` the full `file_path`.** The `chunk_idx` is informational (0-indexed ordinal position within the file) — useful for citing provenance ("chunk 3 of `projects/brunos.md`"), not required for the read.

## Common workflows

1. **Tone matching** — query `drafts/sent/` with a natural-language description of the new reply's purpose. `Read` the top 2–3 hits to mimic structure, register, sign-off.
2. **Theme extraction** — query `daily/` with a thematic phrase ("recurring frustration with", "decisions about hiring"). Cluster top 10–20 hits by `file_path` to surface dates.
3. **News-digest dedup** — query `news-digest/` with the candidate item's title or first ~100 chars of body. If top RRF score is high and the gap to rank-2 is wide, drop the item.
4. **Project-context recall** — query `projects/` (or `clients/` for Protostack work) with a short statement of what you need. Top hits → `Read` for the full file.
5. **"Did I already discuss X"** — search **without** `--path-prefix` first. If top hit is high-confidence, you have your answer. If results spread across folders, narrow with the most likely prefix and re-run.

## FTS5 operator escape hatch

Pass FTS5 operators directly in the query string when keyword precision matters more than semantic recall:

- `+Marcus +Aurelius` — both terms required.
- `agent -agent_smith` — exclude noisy term.
- `"exact phrase here"` — phrase match.
- `pgvect*` — prefix.

The same string is also encoded by BGE as natural language, so heavily-operatored queries hurt vector recall. Use sparingly — reach for operators when searching for a specific person's name, an exact code symbol, or to exclude a known-noisy term. Otherwise prefer plain prose.

## Limits

- **No date filter.** Index has no `--since` flag. Workarounds: include the date format in the query as an FTS phrase (e.g., `"2026-04"`), or post-filter results after retrieval. Year/month subfolder prefix filtering does not work — daily files are flat.
- **`personal/finance.md` is excluded at index time** (`memory_index.py:EXCLUDE_RELATIVE`). It will never appear in results regardless of query — matches the SOUL.md no-financial-data boundary.
- **Index can be stale.** If Bruno just edited a file in Obsidian, the new content won't appear until reindex. Run `uv run python .claude/scripts/memory_index.py` (incremental — only reindexes mtime-changed files; usually a few seconds). Phase 6's heartbeat will eventually run this on a cadence.
- **Frontmatter is part of the chunked text.** The chunker does not strip the YAML block, so tags / type / status are searchable but they also show up as noise in `content` snippets.
- **Single-folder filter.** `--path-prefix` is one folder. For cross-folder, run multiple searches or omit the prefix and post-filter `file_path` after.
- **English-only embeddings.** BGE-small-en is English-trained. Portuguese drafts (in `drafts/sent/`, `drafts/active/`) lose vector recall; FTS5 still tokenizes fine. Don't over-trust scores on Portuguese content — verify with a `Read`.

## Fallback ladder

1. `memory_search.py "<query>" --path-prefix <best-guess-folder>` — first try.
2. If results look thin: re-run **without** `--path-prefix`. Hybrid search may surface relevant content from an adjacent folder.
3. If still thin: `Grep` over `BrunOS/Memory/` for the exact strings you'd expect. Catches things the index missed (recent edits, exotic tokenization).
4. If still empty: `ls BrunOS/Memory/<best-guess-folder>/` and `Read` the obvious filename. Some content is structurally findable without search.
5. If you suspect the index is stale (recent Obsidian edit, or you just wrote a file via `shared.atomic_write`): `uv run python .claude/scripts/memory_index.py` (incremental, fast). Then re-run step 1.

## Boundary with `query.py`

`query.py` retrieves from external systems (Slack, GitHub, ClickUp, Gmail, Calendar, RSS — Phase 4). `memory_search.py` retrieves from the vault. Pick by source: if the answer lives in `BrunOS/Memory/`, this skill; if it lives in an external API, `query.py`.
