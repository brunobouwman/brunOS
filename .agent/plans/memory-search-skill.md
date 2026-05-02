# Feature: `memory-search` skill — agent-facing guide for the hybrid RAG over `BrunOS/Memory/`

The following plan should be complete, but it's important to validate documentation and codebase patterns and task sanity before you start implementing. Pay special attention to:

- **This is a pure-context skill, no scripts.** Mirrors the `brunos-vault` precedent (`.claude/skills/brunos-vault/SKILL.md` — 113 lines, zero `scripts/`, zero `references/`). Phase 5's plan deliberately split scripted skills (news-digest, weekly-review) from pure-context ones (brunos-vault). This one is in the second bucket. **Do not** add `scripts/` or `references/` subdirs unless the user asks for them in a follow-up.
- **The skill teaches `memory_search.py` — it does NOT modify it.** `memory_search.py` (60 lines, shipped 2026-05-02 in `7d622b0`) is the public API and the contract is locked: `python memory_search.py <query> [--k N] [--path-prefix PFX]` → JSON list of `{id, file_path, chunk_idx, content, score}`. If the skill author finds an ergonomic gap (e.g., wants markdown output), surface to Bruno before changing the script.
- **Skill discovery is the description field.** Claude Code's progressive disclosure loads the SKILL.md frontmatter `name` + `description` always; body only on description match. Overload `description` with retrieval-friendly trigger phrases — that field is the entire dispatch surface. See `.claude/skills/brunos-vault/SKILL.md:3` for the canonical overloaded-description example (one paragraph of trigger phrases, written for retrieval not for humans).
- **No `setting_sources` / `CLAUDE_INVOKED_BY` concerns.** Those rules apply to scripts that invoke `claude_agent_sdk`. This skill ships zero scripts. Both rules are out of scope. (Mention this explicitly to the implementing agent so they don't pad the SKILL.md with irrelevant boilerplate.)
- **The skill's body must reflect the search engine as it actually exists on disk, not as it's described in plan docs.** Asymmetric BGE (`embed_query` for queries, `passage_embed` for indexing — `embeddings.py:29-36`), RRF k=60 (`memory_search.py:16`), inner_k = `max(k*3, 30)` for both vector and FTS legs (`memory_search.py:41`), `path_prefix` matched as `c.file_path LIKE ? || '/%'` (so it's a *folder* prefix, not a file prefix — `db.py:130, 150`), FTS5 tokenizer is `porter unicode61` (`db.py:40`), `personal/finance.md` is excluded at index time (`memory_index.py:33`). Verify each of these against the files before quoting them.
- **Path-prefix gotcha (critical for the skill body).** `c.file_path LIKE ? || '/%'` means the prefix MUST be a folder name without trailing slash. `--path-prefix drafts/sent` works; `--path-prefix drafts/sent/` returns nothing; `--path-prefix daily/2026` also returns nothing because there's no `daily/2026/` folder (daily files are flat — `daily/2026-05-02.md`). For year/month filtering, fall back to FTS5 prefix-matching the date in the query string itself, or grep + read.
- **No date filter exists.** Phase 5's plan flagged this at line 504 (`memory_search doesn't filter by date — that's a known limitation`). The skill must document this limitation explicitly so the agent doesn't try to construct date filters that don't work.
- **FTS5 syntax pass-through.** The query string is passed raw to `chunk_fts MATCH ?` (`db.py:147`). The agent CAN use FTS5 operators (`+required`, `-excluded`, `"exact phrase"`, `term*` prefix) when keyword precision matters. But the *vector* leg of hybrid search interprets the same string as natural language, so over-engineering FTS syntax can degrade vector recall. The skill should give one clear "when to reach for FTS operators" rule + an example.
- **Stale-index risk.** `memory_search.py` reads from `.claude/data/state/memory.db`, indexed by `memory_index.py`. If Bruno just edited a file in Obsidian and immediately asks the agent to search it, the new content won't appear until reindex. The skill must teach: "if results look stale, run `uv run python .claude/scripts/memory_index.py` (incremental — only reindexes mtime-changed files; takes a few seconds)". Phase 6's heartbeat will eventually run this on a cadence; until then it's manual.
- **No `setting_sources=["project"]` in any future caller.** Even if a Phase 5+ script calls `memory_search.search()` directly (in-process), that script is the one bound by the rule, not this skill. Out of scope here.

## Feature Description

A pure-context Claude Code skill — `.claude/skills/memory-search/SKILL.md` — that teaches the BrunOS agent **when** to invoke `memory_search.py`, **how** to phrase queries for the asymmetric BGE+FTS5 hybrid index, **which `--path-prefix`** values map to which question types, **how** to interpret RRF scores, and the **read-after-search** workflow that converts ranked chunks into useful context. Scoped to pair with the existing `brunos-vault` skill: brunos-vault teaches *where* things live; memory-search teaches *how to retrieve them by meaning*.

The skill body is a distilled, agent-facing decision guide — folder→prefix cheat sheet, query-phrasing rules, RRF interpretation, fallback ladder (search → re-search without prefix → grep → ls), and the canonical workflows already named in CLAUDE.md and the Phase 5 plan: tone matching from `drafts/sent/`, theme extraction from `daily/`, news-digest dedup against `news-digest/`, project-context recall from `projects/`/`clients/`/`research/`. No scripts, no schema changes, no new dependencies.

## User Story

As Bruno (operator of BrunOS, with a 2026-05-02-shipped hybrid memory search that the agent technically *can* call but routinely under-uses — falling back to `Read` of obvious files, missing the right `--path-prefix`, or phrasing queries as keyword soup that defeats the asymmetric BGE embeddings)

I want a skill that triggers whenever I ask the agent to recall, search, summarize, dedupe, or match-the-tone-of anything in my vault, and that gives the agent a tight folder→prefix cheat sheet, query-phrasing rules, and a fallback ladder

So that the agent reaches for `memory_search.py` reflexively (not for `Read` of the first matching filename), uses the right `--path-prefix` on the first try, phrases queries as natural-language sentences (not `agent OR framework OR rag`), and the chunks-to-files post-processing step happens consistently — turning the RAG layer from "exists but underused" into "the agent's primary recall mechanism."

## Problem Statement

`memory_search.py` shipped 2026-05-02 (Phase 3, commit `7d622b0`) and is documented in `CLAUDE.md` lines 75–76 + 99 ("Memory search (Phase 3)" section). Three current consumers exist or are planned:

1. **Phase 5's `news-digest/scripts/digest.py`** — dedup against past digests via `--path-prefix news-digest`.
2. **Phase 5's `weekly-review/scripts/aggregate_week.py`** — daily-log themes via `--path-prefix daily`.
3. **The agent itself, in conversation** — but with no skill-level guidance, the agent today either (a) doesn't know the tool exists when a memory-recall question arrives, (b) invokes it without `--path-prefix` and gets noisy multi-folder results, (c) phrases queries as keyword soup which hurts the BGE leg, or (d) takes the JSON output and stops, instead of `Read`-ing the top-hit `file_path` for full context.

The PRD names the vault as "the agent's primary recall mechanism" (`second-brain-prd.md`, see "Phases 3 + 5"). Without a `memory-search` skill, that recall is bottlenecked on the agent guessing the right invocation each session.

Side problem: `brunos-vault` (the existing skill) is correctly scoped to *folder semantics + frontmatter + boundaries* and explicitly excludes search guidance. There's no other skill carrying that load. Memory-search is a clean separation-of-concerns split from brunos-vault.

The risk of doing it wrong: an over-broad skill (e.g., "use memory-search for anything vault-related") would over-trigger and conflict with `brunos-vault`. An over-narrow one ("use only when Bruno says 'search'") would under-trigger. The description-field engineering is the whole game.

## Solution Statement

One file: `.claude/skills/memory-search/SKILL.md`. ~120–150 lines. Body sections (in this order — the agent reads top-to-bottom):

1. **What it does + how to invoke** — one-paragraph overview + the canonical `uv run python .claude/scripts/memory_search.py "<query>" [--k N] [--path-prefix PFX]` line + JSON output shape.
2. **When to use it (vs `Read` vs `Grep`)** — three-row decision table.
3. **Query phrasing for asymmetric BGE** — short natural-language sentences; one good example, one bad example with the diff explained.
4. **`--path-prefix` cheat sheet** — folder → typical question type → example query. Covers all 11 `Memory/` subfolders.
5. **RRF score interpretation** — RRF scores are *relative*, not absolute; the gap between rank 1 and rank 3 matters more than the absolute value; threshold for "this is probably a hit" comes from calibration on the specific vault.
6. **Result post-processing (the read-after-search pattern)** — chunks are 400-token slices with 50-token overlap (per `memory_index.py:29-31`); for any high-RRF hit you actually use, `Read` the full `file_path` to get frontmatter + surrounding context.
7. **Common workflows** — five named patterns the agent will hit repeatedly: tone matching, theme extraction, dedup, project-context recall, "did I already discuss X".
8. **FTS5 operator escape hatch** — when keyword precision matters (`+required`, `-excluded`, exact phrases), pass them raw; warn that vector recall degrades on heavily-operatored queries.
9. **Limits** — no date filter; `personal/finance.md` is excluded at index time; index can be stale (run `memory_index.py`); chunks ignore frontmatter (it's part of the `.md` text, gets embedded along with body).
10. **Fallback ladder** — search → search-without-prefix → `Grep` over `BrunOS/Memory/` → `ls` the relevant folder + `Read` the obvious file.

Trigger surface (the `description` field) overloads keywords for retrieval: search, recall, find, "did I", "have I", "when did I", tone matching, voice corpus, theme, dedup, prior, similar, context, "what did I write about", explicit `memory_search.py` invocations.

## Feature Metadata

**Feature Type**: New Capability (skill layer; pairs with existing `brunos-vault`)
**Estimated Complexity**: Low (one ~140-line markdown file; no code; no dependencies; no scheduling)
**Primary Systems Affected**:
- `.claude/skills/memory-search/SKILL.md` (new file — the directory `.claude/skills/memory-search/` already exists empty, ready to receive the file)
- `CLAUDE.md` — append a one-line skill mention to the "Skills (Phase 5)" section (Phase 5 plan line 558 left this section open for additions)

**Dependencies**:
- Phase 3 (memory search) — landed on `main` in commit `7d622b0` (2026-05-02). `memory_search.py`, `memory_index.py`, `db.py`, `embeddings.py` are all in tree.
- Phase 5 `brunos-vault` skill — landed in working tree (untracked but present at `.claude/skills/brunos-vault/SKILL.md`). Provides the canonical pure-context skill format to mirror.
- No external libs, no new pyproject deps.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: READ THESE BEFORE IMPLEMENTING

- `.claude/skills/brunos-vault/SKILL.md` (entire file, 113 lines) — Why: **the** template to mirror. Same skill class (pure-context, no scripts). Same frontmatter shape, same body density, same tone (terse, agent-facing, no marketing). Especially line 3 (the overloaded `description` field) and the section structure (folder map, rules, boundaries — translate that pattern into search guidance).
- `.claude/scripts/memory_search.py` (entire file, 60 lines) — Why: the public CLI surface this skill teaches. Quote the invocation form verbatim. Note `RRF_K = 60` (line 16), `inner_k = max(k * 3, 30)` (line 41), JSON output keys `{id, file_path, chunk_idx, content, score}`.
- `.claude/scripts/db.py` (lines 109–158) — Why: the `path_prefix` matching is `c.file_path LIKE ? || '/%'` — this is the entire reason `--path-prefix` must be a folder, not a file or year-prefix. Quote this gotcha. Also line 36–41: FTS5 uses `porter unicode61` tokenizer (English stemming + unicode-normalized lowercase). Skill body should mention stemming so the agent doesn't write `running` AND `runs` AND `ran` in queries.
- `.claude/scripts/embeddings.py` (lines 29–36) — Why: confirms asymmetric BGE — `passage_embed` for indexing (`embed_passages`) vs `query_embed` for retrieval (`embed_query`). The skill body's "query phrasing" section is grounded in this asymmetry: queries should look like natural-language questions / short statements, not bag-of-keywords, because the model was *trained* with that asymmetry.
- `.claude/scripts/memory_index.py` (lines 28–33, 65–120) — Why: documents chunk size (400 tokens, 50-token overlap, line 29–31) and the `personal/finance.md` exclusion (line 33). Both feed into the skill body.
- `.claude/skills/create-second-brain-prd/SKILL.md` (lines 1–10) — Why: confirms the skill frontmatter format; secondary reference after `brunos-vault`. Single source of truth: SKILL.md starts with a `---`-fenced YAML block containing `name:` + `description:`, optional `argument-hint:`. No other fields are part of the discovery contract.
- `CLAUDE.md` (lines 75–76 + the "Memory search (Phase 3)" section starting around line 99) — Why: the existing project-level documentation of `memory_search.py`. The skill body is consistent with what's there but goes deeper. Don't contradict — extend.
- `BrunOS/Memory/_README.md` — Why: ground-truth of what folders exist under `Memory/`. The folder→prefix cheat sheet must match this layout. Open this and verify each prefix in the cheat sheet maps to a real folder.
- `.agent/plans/phase-3-memory-search.md` — Why: original Phase 3 plan; lines 22–28 confirm `--path-prefix` and JSON output contract (cited from Phase 5 plan line 80). Cross-check the skill body against this contract.
- `.agent/plans/phase-5-skills.md` (lines 207–215) — Why: shows the canonical subprocess-call pattern that `news-digest`/`weekly-review` will use to invoke `memory_search.py`. The skill body's "scripted callers" note (one short bullet) points here so the agent knows the in-process Python API exists if it's ever inside a script context.

### New Files to Create

- `.claude/skills/memory-search/SKILL.md` — the entire deliverable. ~140 lines, pure markdown, YAML frontmatter at top.

### Existing State (verified 2026-05-02) — DO NOT REGENERATE

- `.claude/skills/memory-search/` — directory exists, empty. Confirmed via `ls -la .claude/skills/memory-search/` (returns the dir with `.` and `..` only, no children). Just drop SKILL.md into it.
- `.claude/skills/brunos-vault/SKILL.md` — exists, 113 lines. Reference template.
- `.claude/scripts/memory_search.py` — committed in `7d622b0`. CLI contract locked.

### Files Touched (small edits)

- `CLAUDE.md` — append one bullet to the existing "Skills (Phase 5)" section (currently scoped to brunos-vault + planned news-digest + planned weekly-review). The bullet documents the new skill in one line, matching the existing entry style.

### Relevant Documentation — READ BEFORE IMPLEMENTING

- [Anthropic Agent Skills overview](https://docs.claude.com/en/api/agent-sdk/overview) — Why: confirms skill discovery via `name` + `description` frontmatter, progressive-disclosure loading semantics. The `description` field is the trigger contract; write it overloaded with retrieval keywords, not for human readers.
- [BGE-small-en-v1.5 model card (HuggingFace)](https://huggingface.co/BAAI/bge-small-en-v1.5) — Why: confirms the asymmetric encoding contract — `passage_embed` vs `query_embed` are *different* code paths and the model card explicitly documents query phrasing recommendations (short natural-language statements). Cite to ground the "query phrasing" section.
- [SQLite FTS5 query syntax](https://sqlite.org/fts5.html#full_text_query_syntax) — Why: the operator escape-hatch section needs to be precise. Document `+required`, `-excluded`, `"exact phrase"`, `prefix*`, `{col1 col2}: ...` only insofar as they actually work against the schema in `db.py`.
- [Reciprocal Rank Fusion (Cormack 2009)](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) — Why: one-line citation in the "RRF score interpretation" section. RRF scores are sums of `1/(k+rank)` terms; absolute values are not bounded, only ordinal. Skill body explains this in plain English; the citation just exists for the curious reader.

### Patterns to Follow

**Skill frontmatter** (mirror `.claude/skills/brunos-vault/SKILL.md:1-4`):

```yaml
---
name: memory-search
description: <ONE long sentence overloaded with trigger phrases — see brunos-vault for length and density. Mention: search, recall, find, "did I", "have I", tone matching, voice corpus, theme extraction, dedup, prior context, similar, "what did I write about", explicit memory_search.py invocations. Mention each Memory/ folder by name (drafts, daily, projects, clients, research, content, goals, news-digest, meetings, team, personal). Make the field at least as keyword-dense as brunos-vault's — that field is the entire dispatch surface.>
---
```

**Body density** (mirror `brunos-vault/SKILL.md:6-113`): terse, agent-facing, no marketing prose, no "introduction" paragraphs, no "we" or "our". Tables where they're clearer than prose. Code fences for invocation examples. Section headings as `##`. Body length cap ~150 lines (skill bodies expand the model's context window every load — bloat is real cost).

**Invocation example** (quote verbatim from `memory_search.py` and `db.py`):

```bash
uv run python .claude/scripts/memory_search.py "what did I learn about pgvector indexing" --k 5 --path-prefix research
```

**Output shape** (one block, document the JSON keys exactly):

```json
[
  {
    "id": 1234,
    "file_path": "research/pgvector-indexing-notes.md",
    "chunk_idx": 0,
    "content": "...",
    "score": 0.0317
  }
]
```

**Query-phrasing example pair** (the body shows both):

> Good: `"did I write notes on pgvector hnsw vs ivfflat tradeoffs"` — natural-language, full words, one clear topic.
>
> Bad: `"pgvector hnsw ivfflat OR ivf"` — keyword soup; wastes the BGE leg's recall, and FTS5's `OR` is implicit between terms anyway (no need to write it).

**`--path-prefix` cheat sheet** (one row per Memory/ folder; verify against `BrunOS/Memory/_README.md` before quoting):

| Folder prefix | Question type | Example query |
|---|---|---|
| `daily` | "what did I do / think / decide last week" | `"how did I feel about the vertik scope creep"` |
| `drafts/sent` | tone matching for new drafts; voice corpus | `"how I usually open replies to marcus"` |
| `drafts/active` | reply drafts in flight | `"what's the open thread with lisa about pricing"` |
| `projects` | project context recall | `"what's the BrunOS phase 9 deployment plan"` |
| `clients` | Protostack labs/clinics context | `"what does clinica X want from us"` |
| `research` | AI-engineering learning notes | `"my notes on agent observability"` |
| `goals` | weekly/monthly/vision context | `"this month's focus areas"` |
| `news-digest` | dedup or recall past digests | `"have I covered the latest claude release"` |
| `meetings` | "what did we decide in that meeting" | `"the protostack pricing kickoff"` |
| `team` | Lisa, contractors, partners context | `"lisa's preferences on async vs sync"` |
| `content` | content ideas + drafts | `"my unposted linkedin draft about evals"` |

**Workflow patterns** (named, one paragraph each):

1. **Tone matching** — query `drafts/sent/` with a natural-language description of the new reply's purpose; `Read` the top 2–3 hits to mimic structure/sign-off.
2. **Theme extraction** — query `daily/` with a thematic phrase (e.g., `"recurring frustration with"`); cluster top 10–20 hits manually by file_path.
3. **News-digest dedup** — query `news-digest/` with the candidate item's title or first 100 chars of body; if top RRF score is high, drop the item. (See `phase-5-skills.md` lines 389 + 397 for threshold tuning notes.)
4. **Project-context recall** — query `projects/` (or `clients/`, depending on which workspace owns the project) with a short statement of what you need; top hits → `Read` for full files.
5. **"Did I already discuss X"** — search without `--path-prefix` first; if top hit is high-confidence, you have your answer. If results are spread across folders, narrow with a `--path-prefix` and re-run.

**Fallback ladder** (numbered list, verbatim language for the skill body):

1. `memory_search.py "<query>" --path-prefix <best-guess-folder>` — first try.
2. If results look thin: re-run without `--path-prefix`. The hybrid search may surface relevant content from an adjacent folder.
3. If still thin: `Grep` over `BrunOS/Memory/` for the exact strings you'd expect. Catches things stale in the index.
4. If still empty: `ls BrunOS/Memory/<best-guess-folder>/` and `Read` the obvious file. Some content is structurally findable without search.
5. If you suspect the index is stale (recent Obsidian edit): `uv run python .claude/scripts/memory_index.py` (incremental — fast). Then re-run step 1.

**RRF score interpretation** (plain-English version for the body):

> RRF scores are *ordinal*, not absolute. They sum `1/(60 + rank)` contributions across the vector and FTS rankings; the maximum possible is `1/61 + 1/61 ≈ 0.033` (a chunk that ranked #1 in both legs). Most useful hits land in the 0.005–0.025 range. **Use the gap, not the value.** If rank-1 is 0.025 and rank-2 is 0.005, rank-1 is probably the answer. If the top 5 hits are all 0.010–0.012, treat them as a *set* and `Read` more files to disambiguate.

**FTS5 escape-hatch rule** (one paragraph, terse):

> Pass FTS5 operators (`+required`, `-excluded`, `"exact phrase"`, `prefix*`) directly in the query string when keyword precision matters more than semantic recall — e.g., searching for a specific person's name (`"+Marcus +Aurelius"`) or excluding a noisy term (`agent -agent_smith`). The same string is passed to the BGE leg as natural language, so heavily-operatored queries hurt vector recall. Use sparingly.

**CLAUDE.md edit** (append to existing "Skills (Phase 5)" section, keep entry style consistent with the existing `brunos-vault` line):

```markdown
- `memory-search` — pure-context skill teaching when to invoke `memory_search.py`, query phrasing for asymmetric BGE, the `--path-prefix` folder cheat sheet, RRF score interpretation, the read-after-search workflow, and the fallback ladder. Triggers on most recall/search/dedup/tone-matching prompts. Pairs with `brunos-vault` (which teaches *where* things live; this one teaches *how to retrieve them by meaning*).
```

---

## IMPLEMENTATION PLAN

### Phase 1: Verify pre-existing state

No writes. Confirm the empty skill directory exists, the brunos-vault template exists, `memory_search.py` is on disk and committed, `BrunOS/Memory/` has the folder layout the cheat sheet will reference. If any of these are missing, surface to Bruno.

### Phase 2: Author SKILL.md

Single file write. Mirror brunos-vault's section density. Get the description field right first (it's the highest-leverage line in the file). Body sections in the order specified in the Solution Statement above. Verify every quoted detail (chunk size, RRF k, path_prefix matching shape, FTS tokenizer, asymmetric BGE) against the source files cited.

### Phase 3: Update CLAUDE.md

Append one bullet to the Skills (Phase 5) section. No other edits.

### Phase 4: Smoke test

Open a fresh Claude Code session in repo root. Ask three questions designed to trigger the skill: a tone-matching prompt, a theme-extraction prompt, an explicit "search my vault" prompt. Confirm the skill body loads (visible via `/skills` or by Claude's reasoning citing the cheat sheet). Optional: verify that the skill body changes the agent's behavior on a side-by-side comparison vs a session where the skill is renamed/disabled. (Heavy for a one-line change; mark optional.)

---

## STEP-BY-STEP TASKS

Execute every task in order. Each task has a single executable validation.

### VERIFY pre-existing state (no writes)

- **CHECK**: directory `.claude/skills/memory-search/` exists and is empty; `.claude/skills/brunos-vault/SKILL.md` exists; `.claude/scripts/memory_search.py` exists; `BrunOS/Memory/` exists with the expected subfolders.
- **VALIDATE**:
  ```bash
  test -d .claude/skills/memory-search && \
  test -z "$(ls -A .claude/skills/memory-search/)" && \
  test -f .claude/skills/brunos-vault/SKILL.md && \
  test -f .claude/scripts/memory_search.py && \
  ls .claude/scripts/db.py .claude/scripts/embeddings.py .claude/scripts/memory_index.py >/dev/null && \
  uv run python -c "
  import sys; sys.path.insert(0, '.claude/scripts')
  from shared import vault_path
  from pathlib import Path
  v = vault_path() / 'Memory'
  expected = ['daily','drafts','projects','clients','research','goals','content','team','meetings','news-digest','personal']
  missing = [f for f in expected if not (v / f).exists()]
  assert not missing, f'missing folders: {missing}'
  print('preconditions OK')
  "
  ```

### CREATE `.claude/skills/memory-search/SKILL.md`

- **IMPLEMENT**: Pure-context skill. YAML frontmatter (`name`, `description`) followed by ~120–150 lines of body in the section order: (1) what + how to invoke, (2) when vs Read vs Grep, (3) query phrasing for asymmetric BGE with one good/one bad example, (4) `--path-prefix` cheat-sheet table covering all 11 Memory/ folders, (5) RRF score interpretation, (6) result post-processing / read-after-search, (7) five named workflow patterns, (8) FTS5 operator escape hatch, (9) limits (no date filter, finance excluded, index staleness, frontmatter-included-in-chunks), (10) fallback ladder.

- **PATTERN**: Mirror frontmatter format from `.claude/skills/brunos-vault/SKILL.md:1-4`. Mirror body density and tone (terse, no marketing, agent-facing, tables where prose would be longer).

- **DESCRIPTION FIELD** (this is the dispatch surface — write it for retrieval, not for humans; keep it one paragraph, similar density to brunos-vault's):
  ```
  description: Hybrid memory-search skill for BrunOS's RAG over BrunOS/Memory/. Use whenever Bruno asks to search, recall, find, dedupe, or match the tone of anything in the vault — daily logs, drafts, projects, clients, goals, content, team, research, news-digest, meetings, personal — or asks "did I", "have I", "when did I", "what did I write about", "is there a prior", "similar to", "tone match", or runs memory_search.py / memory_index.py directly. Teaches the canonical CLI invocation, asymmetric BGE query phrasing (short natural-language sentences, not keyword soup), the folder→--path-prefix cheat sheet, RRF score interpretation (ordinal not absolute), the read-after-search workflow (chunks are 400-token slices — Read the file_path of high-RRF hits for full context), the FTS5 operator escape hatch (+required / -excluded / "exact phrase" / prefix*), the no-date-filter limit, the personal/finance.md exclusion, and a fallback ladder when results look thin or stale. Pairs with the brunos-vault skill (folder semantics) — this one is for retrieval-by-meaning.
  ```

- **BODY SECTIONS** (in order):

  **1. What it does + how to invoke**

  Two-sentence overview: hybrid search (vector top-k×3 + FTS5 top-k×3, fused via RRF k=60) over `BrunOS/Memory/**/*.md`. Then the canonical CLI line:
  ```bash
  uv run python .claude/scripts/memory_search.py "<natural-language query>" [--k N=10] [--path-prefix <folder>]
  ```
  Then the JSON output shape (one fenced block) — keys `{id, file_path, chunk_idx, content, score}`. Then a one-line note: in-process Python API at `.claude/scripts/memory_search.search(query, k, path_prefix)` for scripted callers (e.g., Phase 5's `news-digest/scripts/digest.py`).

  **2. When to use it (vs Read vs Grep)** — three-row table:

  | Situation | Tool |
  |---|---|
  | Known file path, want full content | `Read` |
  | Known exact string, want every match | `Grep` over `BrunOS/Memory/` |
  | Question about meaning ("what did I think about X", "tone match Y", "have I covered Z") | `memory_search.py` |

  **3. Query phrasing for asymmetric BGE**

  One-paragraph rule: queries are encoded with `query_embed` (per `embeddings.py:34`), passages with `passage_embed` — *different code paths*, model trained for the asymmetry. Phrase queries as short natural-language sentences ("did I write notes on pgvector hnsw vs ivfflat tradeoffs"), not as bag-of-keywords ("pgvector hnsw ivfflat OR ivf"). FTS5 layer is forgiving (porter unicode61 stems automatically — "running" matches "ran" matches "runs"); the BGE layer is what rewards natural phrasing.

  **4. `--path-prefix` cheat sheet** — the 11-row table from "Patterns to Follow" above. Plus a one-line gotcha at the bottom: "Path prefix is matched as `c.file_path LIKE '<prefix>/%'` — pass a folder name without trailing slash. `drafts/sent` works; `drafts/sent/` returns nothing; `daily/2026` returns nothing because daily files are flat."

  **5. RRF score interpretation** — the plain-English block from "Patterns to Follow" above.

  **6. Result post-processing (read-after-search)**

  Chunks are 400 tokens with 50-token overlap (per `memory_index.py:29-30`). For any high-RRF hit you actually want to use, `Read` the full `file_path` to pull frontmatter + surrounding context. The `chunk_idx` is informational — it's the chunk's ordinal position within the file (0-indexed); you don't need it for reading the file, but it's useful when explaining provenance ("found in chunk 3 of `projects/brunos.md`").

  **7. Common workflows** — the five named patterns from "Patterns to Follow" above (tone matching, theme extraction, news-digest dedup, project-context recall, "did I already discuss X"). One short paragraph per pattern.

  **8. FTS5 operator escape hatch** — the one-paragraph rule from "Patterns to Follow" above.

  **9. Limits**
  - No date filter — work around via FTS5 (`"2026-04"` if you know the format) or by reading after retrieval.
  - `personal/finance.md` is excluded at index time (`memory_index.py:33`). It will never appear in results regardless of query.
  - Index can be stale if Bruno just edited a file in Obsidian. Run `uv run python .claude/scripts/memory_index.py` (incremental — only reindexes mtime-changed files; usually a few seconds).
  - Chunks include frontmatter as part of the text (the chunker doesn't strip it). Tags and types are searchable but they show up in `content` snippets.
  - `--path-prefix` filters to a single folder. Use multiple searches if you need cross-folder.

  **10. Fallback ladder** — the 5-step list from "Patterns to Follow" above.

- **GOTCHA**: Do NOT include any secrets, paths to `.env`, or token names. Skills load into the model's window — anything in here is in every prompt that triggers the skill.

- **GOTCHA**: Don't pad. brunos-vault is 113 lines; this one should land in the 120–150 range. If you're approaching 200, you're rephrasing rather than informing.

- **GOTCHA**: Do not contradict `brunos-vault`. brunos-vault explicitly owns folder semantics + frontmatter + boundaries; this skill owns retrieval. They will both load on most vault-touching prompts; their bodies must read as complementary, not overlapping. If something is in brunos-vault, link to it, don't restate.

- **GOTCHA**: The `description` field is the entire dispatch surface — write it for retrieval, not for humans. Overload trigger phrases. brunos-vault's description (line 3 of its SKILL.md) is the calibration point.

- **VALIDATE**:
  ```bash
  uv run python -c "
  from pathlib import Path
  p = Path('.claude/skills/memory-search/SKILL.md')
  assert p.exists(), 'SKILL.md missing'
  text = p.read_text()
  assert text.startswith('---\n'), 'no frontmatter'
  end = text.find('\n---\n', 4)
  assert end > 0, 'frontmatter not closed'
  fm = text[4:end]
  assert 'name: memory-search' in fm, 'name field missing or wrong'
  assert 'description:' in fm, 'description field missing'
  body = text[end+5:]
  # the description must mention key trigger phrases for retrieval
  for phrase in ['memory_search', '--path-prefix', 'BGE', 'RRF', 'FTS5', 'drafts/sent', 'daily', 'tone']:
      assert phrase.lower() in text.lower(), f'missing trigger/keyword: {phrase}'
  # body density check — between ~80 and ~200 lines
  lines = len(body.splitlines())
  assert 80 <= lines <= 200, f'body length {lines} lines outside expected 80–200 range'
  print(f'memory-search SKILL.md OK; body={lines} lines, total={len(text)} chars')
  "
  ```

### UPDATE `CLAUDE.md` — append memory-search bullet to the Skills (Phase 5) section

- **IMPLEMENT**: Append the one-line entry from "Patterns to Follow → CLAUDE.md edit" above. Insert it after the existing `brunos-vault` bullet in the Skills (Phase 5) section so the agent reads vault-then-search in the natural order.

- **PATTERN**: Match the existing bullet style (single backtick'd skill name, em-dash, sentence describing what it does + its trigger surface). Do NOT touch the Phase status checklist — this skill is a Phase 5 addendum, not a new phase.

- **GOTCHA**: Don't re-list the skill in the "Build commands" section. There are no build commands — it's a pure-context skill with no scripts.

- **VALIDATE**:
  ```bash
  grep -q "memory-search" CLAUDE.md && \
  grep -q "memory_search.py" CLAUDE.md && \
  echo "CLAUDE.md updated OK"
  ```

### SMOKE TEST: cold session, three questions

- **IMPLEMENT**: Open a fresh Claude Code session in the repo root. Ask three questions, each picked to land the skill in the model's window via the description match:
  1. *"Tone-match this for me — I want to draft a reply to Marcus that sounds like the way I usually open."* (Should trigger memory-search → `drafts/sent` workflow.)
  2. *"What themes have come up in my daily logs over the past two weeks?"* (Should trigger memory-search → `daily` + theme-extraction workflow.)
  3. *"Have I already written notes on pgvector hnsw vs ivfflat tradeoffs?"* (Should trigger memory-search → `research` + the "did I already discuss X" workflow.)

  For each, observe whether the agent (a) cites the cheat sheet's `--path-prefix`, (b) phrases the query as natural language, (c) `Read`s the top hit after the search call. If 2/3 land cleanly, ship; if 0–1 land, iterate on the description field.

- **VALIDATE** (manual; no automated check possible — skill activation is judgment):
  ```bash
  # Confirm the skill is discoverable by listing the loaded skills in a Claude Code session.
  # (Manually open a session, type /skills, and verify memory-search is listed.)
  echo "Open a Claude Code session in repo root, run /skills, confirm memory-search appears."
  ```

---

## TESTING STRATEGY

No formal pytest suite (consistent with Phases 0–5). Validation is inline `uv run python -c "..."` smoke checks per task above + the cold-session smoke test.

### File-level checks

- SKILL.md frontmatter is well-formed and contains `name` + `description`.
- Body length lands in the 80–200 line band.
- Body mentions the keys the description field promises (`memory_search`, `--path-prefix`, BGE, RRF, FTS5, the named folder prefixes, the workflow names).
- CLAUDE.md gained one bullet under "Skills (Phase 5)", no other diffs.

### Skill-activation checks

The skill's description field is the dispatch surface. The smoke test above verifies that real, paraphrased recall prompts trigger it. There's no automated way to check skill triggering — it's inherent to Claude Code's progressive-disclosure loader.

### Edge cases

- **Skill loaded but `BrunOS/Memory/` empty (fresh-clone scenario)** — the skill body still loads (it's pure markdown); the agent may invoke `memory_search.py` and get an empty result. The skill's fallback ladder handles this (step 4: `ls` the folder).
- **Stale index** — the skill explicitly tells the agent to re-index when results look thin or wrong. If the agent never tries this on stale-index symptoms, that's a description-field-tuning bug, not a body bug.
- **`--path-prefix` typo / nonexistent folder** — the script returns `[]` (no error). The skill's fallback ladder (re-run without prefix) is the recovery path.
- **Query in Portuguese** — BGE-small-en is English-only. Queries against Bruno's Portuguese drafts will degrade in vector recall but FTS5 (porter unicode61) tokenizes fine. The skill should mention this in the limits section so the agent doesn't over-trust hits on Portuguese content.

---

## VALIDATION COMMANDS

### Level 1: Skill file structure

```bash
uv run python -c "
from pathlib import Path
p = Path('.claude/skills/memory-search/SKILL.md')
assert p.exists()
text = p.read_text()
assert text.startswith('---\n')
end = text.find('\n---\n', 4)
assert end > 0
print(f'SKILL.md OK: {len(text)} chars, body starts at line {text[:end].count(chr(10))+2}')
"
```

### Level 2: Description field has retrieval-friendly trigger keywords

```bash
uv run python -c "
text = open('.claude/skills/memory-search/SKILL.md').read()
fm_end = text.find('\n---\n', 4)
fm = text[4:fm_end]
# description must overload trigger phrases
required = ['search', 'recall', 'find', 'tone', 'dedup', 'memory_search', 'path-prefix', 'BGE', 'RRF', 'FTS5']
missing = [k for k in required if k.lower() not in fm.lower()]
assert not missing, f'description missing keywords: {missing}'
# all 11 Memory/ folders must appear in description for the dispatcher
folders = ['daily', 'drafts', 'projects', 'clients', 'research', 'goals', 'content', 'team', 'meetings', 'news-digest', 'personal']
missing_folders = [f for f in folders if f not in fm.lower()]
assert not missing_folders, f'description missing folder mentions: {missing_folders}'
print('description field OK')
"
```

### Level 3: Body covers all 10 sections

```bash
uv run python -c "
text = open('.claude/skills/memory-search/SKILL.md').read()
body = text.split('---\n', 2)[-1]
required_sections = [
    'invoke',                # section 1
    'when',                  # section 2 (vs Read / Grep)
    'phrasing',              # section 3 (BGE)
    'path-prefix',           # section 4 (cheat sheet)
    'RRF',                   # section 5
    'read-after-search',     # section 6 (or 'post-processing')
    'workflow',              # section 7
    'FTS5',                  # section 8
    'limits',                # section 9
    'fallback',              # section 10
]
missing = [s for s in required_sections if s.lower() not in body.lower()]
assert not missing, f'body missing sections covering: {missing}'
print(f'body section coverage OK; lines={len(body.splitlines())}')
"
```

### Level 4: CLAUDE.md gained the bullet

```bash
grep -q "memory-search" CLAUDE.md && \
grep -q "memory_search.py" CLAUDE.md && \
echo "CLAUDE.md ref OK"
```

### Level 5: No regressions in existing skills

```bash
# brunos-vault must still parse cleanly (we shouldn't have touched it)
uv run python -c "
text = open('.claude/skills/brunos-vault/SKILL.md').read()
assert text.startswith('---\n')
assert 'name: brunos-vault' in text
print('brunos-vault SKILL.md still OK')
"
```

### Level 6: Manual — cold-session activation smoke (judgment call)

Open a Claude Code session in repo root. Run `/skills` and confirm `memory-search` appears in the list with the description rendering correctly. Then ask one of the three smoke prompts from the SMOKE TEST task and observe whether the agent's answer reflects the skill body (cites a `--path-prefix`, phrases the query as natural language, follows up the search with a `Read`).

---

## ACCEPTANCE CRITERIA

- [ ] `.claude/skills/memory-search/SKILL.md` exists with `name: memory-search` + a description field overloaded with retrieval keywords (covers all 11 `Memory/` subfolders by name + the verbs search/recall/find/dedup/tone-match + `memory_search.py` + `--path-prefix`).
- [ ] Body covers all 10 sections in the specified order: invoke / when-vs-Read-vs-Grep / BGE phrasing / `--path-prefix` cheat sheet / RRF interpretation / read-after-search / workflows / FTS5 escape hatch / limits / fallback ladder.
- [ ] Body length lands in 80–200 lines (matches brunos-vault density).
- [ ] All 11 `Memory/` folders are represented in the `--path-prefix` cheat sheet table with one example query each.
- [ ] No script files added under `.claude/skills/memory-search/` (pure-context skill).
- [ ] No `references/` subdirectory added (out of scope; resurface to Bruno if needed).
- [ ] No edits to `memory_search.py`, `memory_index.py`, `db.py`, `embeddings.py`. The skill teaches the existing surface; it does not extend it.
- [ ] CLAUDE.md gains exactly one bullet under "Skills (Phase 5)" pointing to `memory-search`. No other CLAUDE.md edits.
- [ ] No new dependencies in `pyproject.toml`.
- [ ] No new slash commands.
- [ ] All Level 1–5 validation commands pass.
- [ ] Cold-session smoke test (Level 6) lands at least 2/3 prompts cleanly.

---

## COMPLETION CHECKLIST

- [ ] Pre-existing state verified (empty skill dir, brunos-vault template, memory_search.py, vault folder layout).
- [ ] SKILL.md authored, frontmatter validated, body covers all 10 sections.
- [ ] CLAUDE.md bullet appended.
- [ ] Levels 1–5 validation pass.
- [ ] Level 6 cold-session smoke landed (manual judgment).
- [ ] Diff reviewed — no accidental edits to `memory_search.py` / `memory_index.py` / `db.py` / `embeddings.py` / `brunos-vault/SKILL.md`.

---

## NOTES

### Why pure-context (no scripts)?

The memory-search **engine** is already shipped (`memory_search.py`, 60 lines, Phase 3). The gap is *agent dispatch* — the model frequently doesn't know to reach for it, doesn't pick the right `--path-prefix`, phrases queries badly, or stops at JSON without doing the read-after-search. All four are pure-knowledge problems that fit Anthropic's progressive-disclosure skill model perfectly: load the body when the description matches, and the agent has the routing knowledge it needs. Adding scripts would conflate "tool" with "guide". The brunos-vault precedent is the same shape and same reasoning.

### Why pair this with `brunos-vault` instead of merging?

Two skills with sharp scopes load and cache better than one big skill. brunos-vault's description triggers on "where does X live", "draft a reply", "log to today" — structural and write-side prompts. memory-search's description triggers on "search", "recall", "tone match", "did I" — retrieval and read-side prompts. There's overlap (most vault interactions), but the bodies are complementary, not duplicative. Splitting also means the description fields can each be densely keyword-loaded for their respective surfaces without diluting either.

### What's deferred / out of scope

- **Markdown output mode for `memory_search.py`** — would be ergonomic for direct prompt injection, but it's a script change and breaks the JSON contract that Phase 5's `news-digest`/`weekly-review` rely on. Resurface to Bruno after Phase 5 lands; could be a `--format markdown` flag added non-breakingly.
- **Date-range filter** — Phase 5 plan flagged this at line 504. Real solution is a `--since` flag on `memory_search.py` that joins on `chunks.mtime`. Out of scope here; this skill teaches the workaround (FTS5 date string in query, or post-filter after retrieval).
- **Rerank stage** — RRF is good enough for ~10K-chunk vaults; rerank (cross-encoder) becomes worthwhile around 100K+ chunks. Bruno's vault is in the low thousands. Defer.
- **Skill-driven query expansion** — could teach the agent to issue 2–3 paraphrased queries and merge results. Real win, but adds complexity. Defer to a v2 after observing real usage.
- **Portuguese-aware search** — BGE-small-en is English-only; Portuguese drafts in `drafts/sent/` lose vector recall. Real fix is multilingual embeddings (e.g., `BAAI/bge-m3`) or a separate per-language index. Out of scope; the skill mentions this as a limit so the agent doesn't over-trust Portuguese hits.

### Open questions for Bruno (NOT blocking — defaults documented)

- **Should the skill mention `query.py` integration commands as adjacent recall surfaces?** Phase 4 ships `query.py slack`, `query.py github`, etc. — these are *external-system* recall, vs `memory_search.py`'s *vault* recall. Default: skill mentions the boundary in one line ("`query.py` retrieves from external systems; `memory_search.py` retrieves from the vault — pick by source") and stops. If Bruno wants deeper integration cross-pollination ("search Slack threads via query.py and vault drafts via memory_search and merge"), that's a follow-up skill.
- **Cheat-sheet example queries — English vs Portuguese?** Default: English (matches brunos-vault's "internal vault notes ALWAYS English" convention). If Bruno wants the cheat sheet to model Portuguese drafts queries explicitly, add one row.
- **Should the skill body include a "common pitfalls" section?** Default: no — the limits + gotchas are inlined per section. If usage reveals a recurring failure mode (e.g., agent keeps using `--path-prefix drafts/sent/` with the trailing slash), promote to a dedicated section. Iterate after one week of use.
