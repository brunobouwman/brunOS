# Feature: Phase 5 — Skills (brunos-vault, weekly-review, news-digest)

The following plan should be complete, but it's important to validate Phase 4's integration shape and the skill discovery contract before starting implementation. Pay special attention to:

- **Phase 4 is shipping in a parallel session.** This plan blocks on `.claude/scripts/integrations/{rss,clickup,github,gmail,calendar}.py`, `.claude/scripts/query.py`, and `.claude/scripts/integrations/registry.py`. The implementation agent MUST verify those exist and expose the documented public API before writing the digest / weekly-review scripts. If Phase 4 hasn't merged when Phase 5 execution begins, ship `brunos-vault` first (it has zero Phase 4 dependency) and STOP before `weekly-review` / `news-digest`. Surface to Bruno.
- **Recursion guard is mandatory in every Agent SDK script.** `news-digest/scripts/digest.py` (Haiku 4.5) and `weekly-review/scripts/aggregate_week.py` (Opus 4.7) both call `claude_agent_sdk`. Each script MUST set `os.environ["CLAUDE_INVOKED_BY"] = "<purpose>"` BEFORE the `claude_agent_sdk` import — otherwise the SessionEnd hook re-fires `memory_flush.py` and you get an infinite spawn loop. Same pattern as `memory_flush.py:28`.
- **`setting_sources` is not optional.** Every `ClaudeAgentOptions(...)` call in Phase 5 MUST pass `setting_sources` explicitly. Pass `None` for child reasoning calls (no `.claude/`/CLAUDE.md/skills loaded — fast, deterministic, cheap). The default has flipped between SDK 0.1.x releases — don't rely on it. Same rule as Phase 2's `memory_flush.py:115`.
- **Skills, not slash commands, are the primary interface.** Phase 5's three skills register via `.claude/skills/<name>/SKILL.md` with YAML frontmatter `name` + `description`. Claude Code progressive-disclosure loads name+description always, body on description match, scripts on demand. NO slash commands are required by this phase — Phase 9's launchd/systemd wakes the scripts directly. Don't create `.claude/commands/weekly-review.md` etc.; the skill body documents the trigger phrasing.
- **No scheduling in this phase.** Per PRD line 387 + 575 + 583: launchd plists / systemd timers land in Phase 9. Phase 5 ships only standalone, idempotent CLI scripts. Bruno + Phase 6's heartbeat invoke them manually until then.
- **The LLM never sees raw tokens.** Same rule as Phase 4. Scripts import integration modules in-process; tokens load via `os.environ` inside those modules; sanitized dataclasses cross the boundary into the prompt. RSS items + ClickUp tasks + GitHub issues are external content — they are DATA, not instructions. When Phase 8's sanitizer ships, both Phase 5 scripts will be retrofitted to wrap external content in `<external_data>` tags. For Phase 5: comment-flag every prompt-construction site with `# TODO(Phase 8): wrap in <external_data>` so the retrofit is greppable.
- **Phase 3 dependency for news-digest dedup.** `digest.py` calls `memory_search.py --path-prefix news-digest` to filter out items already covered in past digests. Phase 3 is in main (commit `7d622b0`). The `--path-prefix` flag exists; verify before relying on it.
- **Output goes into the vault, not into the repo.** `weekly-review` writes to `BrunOS/Memory/goals/YYYY-Www-review.md`; `news-digest` writes to `BrunOS/Memory/news-digest/YYYY-MM-DD.md`. Both via `shared.atomic_write` so the YAML `updated:` field is stamped automatically. NEVER hardcode the relative `BrunOS/` path — always `shared.vault_path() / "Memory" / ...`.
- **Decisions locked in conversation 2026-05-02:** `brunos-vault` has no scripts (pure SKILL.md context). `weekly-review` uses Opus 4.7 (`claude-opus-4-7`) per PRD §"Stack at a glance" — Sonnet is insufficient for synthesizing a full week. `news-digest` uses Haiku 4.5 (`claude-haiku-4-5-20251001`) for per-item scoring — high volume, cheap, deterministic. `sales-deal-tracker` (PRD §5.4) deferred to post-Phase 6.

## Feature Description

Phase 5 builds the three Anthropic-native "skills" that turn the vault into a workspace the agent actively reasons over. Output: three skill packages under `.claude/skills/`, two of them with backing scripts.

1. **`brunos-vault`** — pure-context skill. SKILL.md teaches the agent the vault folder layout, frontmatter conventions, naming patterns, and routing rules (Portuguese vs English, sources_of_truth, draft lifecycle). Description triggers on most vault-touching prompts. No scripts, no references.
2. **`weekly-review`** — `scripts/aggregate_week.py` pulls the past 7 days of ClickUp completions, GitHub PR/issue activity, calendar density, daily-log themes (via `memory_search.py`), and active goals; passes the bundle to Opus 4.7; writes a draft to `Memory/goals/YYYY-Www-review.md` for Bruno to refine. Never auto-finalizes.
3. **`news-digest`** — `scripts/digest.py` reads new RSS items via `integrations.rss.new_items()`, scores each item with Haiku 4.5 on AI-engineering relevance, drops low-signal, clusters survivors into 3–5 themes, summarizes each in 2 sentences, writes `Memory/news-digest/YYYY-MM-DD.md`. Dedupes against past digests using `memory_search.py --path-prefix news-digest`.

The primary downstream consumers are **Phase 6's heartbeat** (which surfaces the existence of new digest / review files in the daily summary) and **Bruno** (who reads the drafts and either accepts or refines). Phase 7's chat bot will load all three skills via `setting_sources=["project"]`.

## User Story

As Bruno (operator of BrunOS, balancing Vertik contract work + Protostack co-founding + AI-engineering transition learning, with a vault that has finally got integrations as of Phase 4)
I want three skills the agent can invoke — one to navigate the vault correctly, one to compile a weekly review draft from real-world data, and one to filter the AI-news firehose into a curated digest
So that Sunday-evening planning takes 30 minutes instead of 2 hours, the daily 07:30 BRT digest tells me what to read in 10 lines, and every agent interaction respects vault conventions without me re-explaining them.

## Problem Statement

Without Phase 5:

1. The agent reads the vault but doesn't *know* it. It writes drafts to wrong folders, uses inline-array `tags`, drops the `updated:` field, mixes Portuguese into English memory. Bruno has to correct conventions every session.
2. The Sunday weekly review is fully manual: 30 min in ClickUp, 30 min in GitHub, 30 min skimming daily logs, 30 min synthesizing. By the time the review is written, the planning energy is gone.
3. AI news consumption is a noise problem. ~10 curated RSS feeds produce 50–200 items/day. Without scoring + clustering + dedup, Bruno either reads nothing or burns 90 min on it.
4. Phase 6's heartbeat has nowhere to call into for "what should the daily summary actually contain" — the digest + review files give it concrete vault deltas to reference.

The risk of doing it wrong: skills with vague descriptions never trigger; skills that import wrong dataclass shapes from `integrations/` crash silently in launchd; news-digest with no dedup re-surfaces the same arxiv paper for a week and Bruno mutes it.

## Solution Statement

Three skills, in order of dependency:

```
1. brunos-vault   →  SKILL.md only. Zero deps. Ships first, validates the skill format.
2. news-digest    →  digest.py imports integrations.rss + calls memory_search subprocess
                     + Haiku 4.5 scoring + clustering + Sonnet 4.6 summarization
3. weekly-review  →  aggregate_week.py imports integrations.{clickup,github,calendar}
                     + memory_search subprocess for daily-log themes
                     + Opus 4.7 single-call synthesis
```

Both scripts use the same skeleton: data-gathering (deterministic, no SDK) → context construction → single Agent SDK call (`max_turns=1`, `allowed_tools=[]`, `setting_sources=None`) → write output via `shared.atomic_write`. The skeleton matches `memory_flush.py` proven in Phase 2. Phase 6's heartbeat will later orchestrate these without re-running them; it just notices the output files exist.

## Feature Metadata

**Feature Type**: New Capability (skill layer)
**Estimated Complexity**: Low–Medium (per PRD §5)
**Primary Systems Affected**: `.claude/skills/{brunos-vault,weekly-review,news-digest}/` (3 new packages), `BrunOS/Memory/goals/YYYY-Www-review.md` + `BrunOS/Memory/news-digest/YYYY-MM-DD.md` (created on first run), `CLAUDE.md` (commands appended + Phase 5 marked done).
**Dependencies**:
- Phase 0: deps installed (`claude-agent-sdk`, `feedparser`, `slack_sdk` already in main) — no new pyproject deps in this phase.
- Phase 1: vault folders `goals/` and `news-digest/` exist (verified — `goals/_README.md`, `news-digest/_README.md`).
- Phase 2: `shared.vault_path`, `shared.atomic_write`, `shared.now_brt`, `shared.with_retry` — all in main as of `7d622b0`.
- Phase 3: `memory_search.py` with `--path-prefix` flag — in main (commit `7d622b0`).
- Phase 4: `integrations.rss.new_items()`, `integrations.clickup.{overdue,today,completed_in_range}`, `integrations.github.{merged_prs,issues_opened_closed,recent_commits}`, `integrations.calendar.{events_in_range}`, plus `query.py` dispatcher. **Block weekly-review + news-digest on Phase 4 merge.** brunos-vault is independent.
- External: none (no new SDK installs, no new credentials).

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: READ THESE BEFORE IMPLEMENTING

- `.agent/plans/second-brain-prd.md` (lines 315–349) — Why: source of truth for what each skill does, output paths, model choices, scoring criteria.
- `.agent/plans/second-brain-prd.md` (lines 21–35, "Stack at a glance") — Why: confirms model assignments — Sonnet 4.6 default, Haiku 4.5 for sanitize/guardrail/news-scoring, Opus 4.7 for weekly review. Use these IDs verbatim.
- `.agent/plans/phase-4-integrations.md` (entire file) — Why: defines the integration module API surface this phase consumes. Read the dataclass names + function signatures before writing imports. CRITICAL: if Phase 4 changed any names mid-build (e.g., `since_last_run` vs `new_items`), `digest.py` won't run. Verify against the `.claude/scripts/integrations/` files actually on disk at execution time, not just this plan.
- `.agent/plans/phase-3-memory-search.md` (lines 22–28) — Why: confirms `memory_search.py` accepts `--path-prefix` and emits JSON; Phase 5's news-digest dedup is one of its named consumers.
- `.claude/scripts/memory_flush.py` (entire file) — Why: canonical Agent SDK skeleton. Mirror its structure: recursion guard set BEFORE import, `ClaudeAgentOptions(allowed_tools=[], setting_sources=None, max_turns=1, model=...)`, async `query()` consumer, text extraction via `_extract_text`. Both Phase 5 scripts follow this shape.
- `.claude/scripts/shared.py` (entire file) — Why: `vault_path()`, `atomic_write()` (stamps `updated:` for `.md`), `now_brt()`, `with_retry()`, `_ts_brt()`. Use these — never re-implement.
- `.claude/scripts/memory_search.py` (entire file) — Why: shape of search results (`{id, file_path, chunk_idx, content, score}`). Used by digest dedup (subprocess call) and weekly-review themes (subprocess call). JSON shape contract.
- `.claude/skills/create-second-brain-prd/SKILL.md` (lines 1–10) — Why: the only existing example of skill frontmatter format on disk. Mirror the `name` + `description` + `argument-hint` block structure. Note: `argument-hint` is optional and only meaningful for skills with positional args — `brunos-vault` doesn't need it.
- `BrunOS/Memory/_README.md` (lines 28–64) — Why: vault folder semantics + frontmatter spec the `brunos-vault` skill must teach. The skill body is essentially a distilled, agent-facing version of this README.
- `BrunOS/Memory/USER.md` (lines 56–80) — Why: drafting criteria + language routing the skill must encode. Don't duplicate the whole USER.md — the skill points to it; "rules" inline are the high-leverage routing lines only.
- `BrunOS/Memory/SOUL.md` — Why: the agent's identity + the explicit Slack carve-out + the no-financial-data boundary. The vault skill must NOT contradict SOUL.md; it must reinforce its boundaries (e.g., `personal/finance.md` is OFF-LIMITS).
- `BrunOS/Memory/goals/_README.md` — Why: confirms the weekly-review filename pattern is `YYYY-Www-review.md` (ISO week, e.g., `2026-W18-review.md`). Locked.
- `BrunOS/Memory/sources_of_truth.md` — Why: ClickUp ↔ Obsidian boundary the vault skill restates ("ClickUp = execution; Obsidian = thinking; don't duplicate").
- `CLAUDE.md` (entire file) — Why: project conventions. Recursion-guard + `setting_sources` rules are MANDATORY in this phase. The "Build commands" section is where Phase 5 appends its three commands at the end.

### Existing State (verified 2026-05-02) — DO NOT REGENERATE

- `.claude/skills/create-second-brain-prd/` — pre-existing reference skill from PRD-generation flow. **DO NOT touch.** Its presence proves the skill loader works.
- `BrunOS/Memory/goals/` — exists with `_README.md`, `personal_vision.md`, `this_week.md`, `this_month.md`. Weekly review writes `YYYY-Www-review.md` here.
- `BrunOS/Memory/news-digest/` — exists with `_README.md` only. Digest writes `YYYY-MM-DD.md` here.
- `.claude/scripts/memory_flush.py` — proven Agent SDK skeleton.

**Phase 4 deliverables (NOT YET LANDED at plan-write time, parallel session):**
- `.claude/scripts/integrations/{rss,clickup,github,gmail,calendar,slack}.py`
- `.claude/scripts/integrations/registry.py`
- `.claude/scripts/query.py`
- `.claude/data/state/{slack,rss}-state.json` (created on first integration run)

### New Files to Create

All paths relative to repo root.

- `.claude/skills/brunos-vault/SKILL.md` — pure context skill. ~80 lines distilled from Memory/_README.md + USER.md routing rules + SOUL.md boundaries.

- `.claude/skills/news-digest/SKILL.md` — describes when to invoke + what `scripts/digest.py` does + how to read its output. ~40 lines.
- `.claude/skills/news-digest/scripts/digest.py` — CLI script. Pulls RSS new_items → Haiku 4.5 scoring → cluster → Sonnet 4.6 summarize → write digest. ~250 lines.
- `.claude/skills/news-digest/references/scoring-rubric.md` — the relevance criteria the Haiku call uses. Loaded on demand. ~30 lines.

- `.claude/skills/weekly-review/SKILL.md` — describes when to invoke + how to read review draft. ~40 lines.
- `.claude/skills/weekly-review/scripts/aggregate_week.py` — CLI script. Pulls ClickUp/GitHub/Calendar/themes → Opus 4.7 synthesis → write review. ~280 lines.
- `.claude/skills/weekly-review/references/review-template.md` — the structural prompt template Opus follows. Loaded on demand. ~50 lines.

### Runtime Files Created on First Run (gitignored from this code repo, tracked inside the vault repo from Phase 9)

- `BrunOS/Memory/goals/YYYY-Www-review.md` — weekly review output. Frontmatter `type: goal`, `tags: [weekly-review]`, `status: active`.
- `BrunOS/Memory/news-digest/YYYY-MM-DD.md` — daily digest output. Frontmatter `type: digest`, `tags: [news, digest]`, `status: active`.

### Relevant Documentation — READ BEFORE IMPLEMENTING

- [Anthropic Agent Skills overview](https://docs.claude.com/en/api/agent-sdk/overview) — Why: confirms skill discovery via `name` + `description` frontmatter, progressive-disclosure semantics (body on trigger, scripts/references on demand). The `description` field is the WHOLE trigger contract — write it for retrieval, not for humans.
- [Claude Agent SDK — `ClaudeAgentOptions`](https://github.com/anthropics/claude-agent-sdk-python) — Why: confirms 0.1.x signature. `allowed_tools=[]` for pure reasoning; `setting_sources=None` to skip skills/CLAUDE.md/MCP loading inside child calls; `max_turns=1` for single-shot; `model="claude-haiku-4-5-20251001"` / `"claude-sonnet-4-6"` / `"claude-opus-4-7"` are the current IDs.
- [feedparser docs](https://feedparser.readthedocs.io/) — Why: only relevant if `integrations/rss.py` exposes raw `entry` dicts; if it returns dataclasses, ignore. Verify Phase 4's choice on disk.
- [PyGithub `Repository.get_pulls` / `get_issues`](https://pygithub.readthedocs.io/en/stable/github_objects/Repository.html) — Why: the date-range filtering pattern weekly-review uses. Confirm Phase 4 wraps this.
- [Google Calendar `events.list` `timeMin`/`timeMax`](https://developers.google.com/calendar/api/v3/reference/events/list) — Why: weekly-review needs events for the past 7 days; verify Phase 4 exposes a date-range query, not just `today()`/`week()`.

### Patterns to Follow

**Skill SKILL.md frontmatter**:
```yaml
---
name: <kebab-case>
description: <ONE sentence — agent reads this to decide whether to load the skill. Trigger phrases inline.>
---
```
Mirror the existing `.claude/skills/create-second-brain-prd/SKILL.md`. The `description` field is the entire discovery surface — overload it with trigger keywords (e.g., for news-digest: "Use when Bruno asks for AI news, the day's digest, what's new in the AI feeds, or runs `digest.py`. Triggers on 'news digest', 'AI news', 'what's new today', morning briefings, RSS summaries.").

**Script entry-point shape** (mirror `memory_flush.py:1–48`):
```python
"""<docstring>"""

from __future__ import annotations

import os

os.environ.setdefault("CLAUDE_INVOKED_BY", "<purpose>")  # MUST be before SDK import

import asyncio  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]  # skills/<name>/scripts/<file>.py → repo root
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import vault_path, atomic_write, now_brt, _ts_brt  # noqa: E402
```

**Note**: `parents[3]` from `.claude/skills/<name>/scripts/<file>.py`. Validate with `print(REPO_ROOT)` during dev.

**Agent SDK call shape** (mirror `memory_flush.py:106–121`):
```python
async def _reason(prompt_text: str, *, model: str, system_prompt: str) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        allowed_tools=[],
        setting_sources=None,            # MANDATORY — never default
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
```

The `_extract_text` helper is duplicated verbatim from `memory_flush.py:87–103`. Don't try to import it — duplication is cheaper than a shared `sdk_helpers.py` module for two callsites.

**Output write pattern** (uses `shared.atomic_write` so `updated:` is auto-stamped):
```python
out_path = vault_path() / "Memory" / "news-digest" / f"{now_brt().strftime('%Y-%m-%d')}.md"
content = build_markdown_with_frontmatter(...)
atomic_write(out_path, content)  # stamps updated: automatically for .md files
```

**Frontmatter format** (matches CLAUDE.md spec — `tags` as a YAML block list, NOT inline array; Obsidian rewrites inline arrays to block on save and the diff churn is annoying):
```markdown
---
type: digest
created: 2026-05-02T07:30-03:00
updated: 2026-05-02T07:30-03:00
tags:
  - news
  - digest
status: active
---
```

**Subprocess call to memory_search.py** (digest dedup + weekly-review themes):
```python
import subprocess, json, sys
result = subprocess.run(
    [sys.executable, str(REPO_ROOT / ".claude" / "scripts" / "memory_search.py"),
     query, "--k", "5", "--path-prefix", "news-digest"],
    capture_output=True, text=True, timeout=30, check=False,
)
hits = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else []
```

Use `sys.executable` (not bare `python`) so the subprocess inherits the same `.venv`. `check=False` + post-hoc handling because a search miss is normal (returncode 0, empty list) but other errors should fail-soft for the digest.

**Integration import pattern** (in-process, not via subprocess to query.py):
```python
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))
from integrations.rss import new_items as rss_new_items  # verify name on disk
```

In-process is faster, avoids JSON round-trips, and lets the script handle structured exceptions. Subprocess to `query.py` is the human/CLI surface — Phase 5 scripts go behind the public CLI.

**Logging**: print to stderr via `print(..., file=sys.stderr)`. No `logging` config — Phase 6's heartbeat captures stderr per script, same convention as Phase 3.

**Failure mode**: each script must be **idempotent** — re-running on the same day overwrites the same output file. Don't append. Don't error if the file exists. `atomic_write` handles overwrite via `os.replace`.

**No new dependencies**: pyproject.toml stays unchanged. If a script "needs" something (e.g., `dateutil` for ISO-week math), use stdlib (`datetime.isocalendar()`).

---

## IMPLEMENTATION PLAN

### Phase A: Verify pre-existing state (no writes)

Confirm Phase 4 has merged. ABORT weekly-review + news-digest if not (brunos-vault can still ship).

### Phase B: Build `brunos-vault` skill (no Phase 4 dep, ships first)

Single SKILL.md. Zero scripts. Manual smoke-test: open a Claude Code session in repo root, ask "where do drafts live?" — confirm the skill body activates.

### Phase C: Build `news-digest` skill

Skill package + `scripts/digest.py` + `references/scoring-rubric.md`. Smoke-test against Bruno's curated RSS feeds, write a real digest, sanity-check 3–5 themes + signal/noise ratio.

### Phase D: Build `weekly-review` skill

Skill package + `scripts/aggregate_week.py` + `references/review-template.md`. Smoke-test: run for the current week, confirm output shape, hand to Bruno for refinement.

### Phase E: Update CLAUDE.md

Append three build-command lines, mark Phase 5 done.

---

## STEP-BY-STEP TASKS

Execute every task in order. Each task has a single executable validation. Run from repo root with `uv` available.

### VERIFY Phase 4 has landed (BLOCKS Phases C, D — not B)

- **CHECK**: `.claude/scripts/integrations/{rss,clickup,github,calendar}.py` and `.claude/scripts/query.py` exist; expected public functions importable.
- **GOTCHA**: If only some integrations have landed (e.g., RSS done, ClickUp WIP), ship only the skills whose dependencies are present. Surface to Bruno before partial-shipping anything ambiguous.
- **VALIDATE**:
  ```bash
  uv run python -c "
  import sys; sys.path.insert(0, '.claude/scripts')
  missing = []
  try: from integrations import rss
  except ImportError as e: missing.append(f'rss: {e}')
  try: from integrations import clickup
  except ImportError as e: missing.append(f'clickup: {e}')
  try: from integrations import github
  except ImportError as e: missing.append(f'github: {e}')
  try: from integrations import calendar
  except ImportError as e: missing.append(f'calendar: {e}')
  if missing:
      print('PHASE 4 NOT READY:', missing)
      print('  → Ship brunos-vault now; STOP before news-digest/weekly-review.')
  else:
      print('Phase 4 OK — proceed with all three skills.')
  "
  ```

### CREATE `.claude/skills/brunos-vault/SKILL.md`

- **IMPLEMENT**: Pure-context skill. YAML frontmatter `name: brunos-vault` + a `description` field overloaded with trigger phrases. Body distills `Memory/_README.md` (folder map + frontmatter spec), `USER.md` lines 56–80 (drafting criteria + language routing), `SOUL.md` boundaries (Slack carve-out, no financial data, no auto-send beyond Slack@mention), and `sources_of_truth.md` (ClickUp = execution / Obsidian = thinking).
- **PATTERN**: Match the frontmatter format in `.claude/skills/create-second-brain-prd/SKILL.md:1–5`.
- **DESCRIPTION FIELD** (this is the whole trigger surface — write it for retrieval):
  ```
  description: Vault navigation skill for BrunOS. Use whenever the agent reads from or writes to BrunOS/Memory/ — drafts, daily logs, projects, clients, goals, content, team, research, news-digest, meetings, personal. Teaches folder semantics, the YAML frontmatter spec (type/created/updated/tags/status — tags as block list), checkbox syntax (- [ ] / - [x]), language routing (Brazilian recipient → Portuguese; internal memory → English), draft lifecycle (active → sent → expired), Slack autonomous-send carve-out (@mention only), the ClickUp-vs-Obsidian boundary, and the personal/finance.md off-limits rule. Triggers on "where does X live", "draft a reply", "log this to today", "update HABITS", "weekly review", any task that reads or writes vault paths.
  ```
- **BODY SECTIONS** (markdown, agent-facing — be terse, no marketing prose):
  1. **Folder map** — one-liner per folder under `Memory/`. Mirror `_README.md:28–40`.
  2. **Top-level singletons** — one-liner per file. Reinforce SOUL.md / MEMORY.md / HABITS.md write rules.
  3. **Frontmatter spec** — exact YAML block. State the `tags` block-list rule explicitly. Reference `shared.atomic_write` for the auto-stamp.
  4. **Type assignments** — copy from CLAUDE.md lines 47–53.
  5. **Language routing** — Brazilian recipient → Portuguese drafts; everything else → English; internal vault notes always English.
  6. **Draft lifecycle** — active → sent (when Bruno actually replied; capture his real reply text) → expired (>24h no action). Voice corpus = sent/.
  7. **Boundaries** — no `personal/finance.md` reads; SOUL.md write-protected from reflection; Slack send only on @mention.
  8. **Sources of truth** — ClickUp = execution layer; Obsidian = thinking layer; don't duplicate. Reference `Memory/sources_of_truth.md`.
- **GOTCHA**: Keep the body under ~150 lines. Skill bodies expand the context window every load — bloat is real cost. The skill is a routing tool, not full vault docs (that's `_README.md`).
- **GOTCHA**: Do NOT include any secrets, paths to `.env`, or specific token names. Skills get loaded into the model's window — anything in here is in every prompt that triggers the skill.
- **VALIDATE**:
  ```bash
  uv run python -c "
  from pathlib import Path
  p = Path('.claude/skills/brunos-vault/SKILL.md')
  assert p.exists(), 'SKILL.md missing'
  text = p.read_text()
  assert text.startswith('---\n'), 'no frontmatter'
  assert 'name: brunos-vault' in text, 'name field missing'
  assert 'description:' in text, 'description field missing'
  # description must mention trigger phrases for retrieval
  for phrase in ['BrunOS/Memory', 'frontmatter', 'language routing', 'drafts']:
      assert phrase.lower() in text.lower(), f'missing trigger: {phrase}'
  print('brunos-vault SKILL.md OK; chars:', len(text))
  "
  ```

### CREATE `.claude/skills/news-digest/references/scoring-rubric.md`

- **IMPLEMENT**: A reference doc the Haiku scoring call's system prompt links into. Loaded on demand by the agent if it needs to explain why an item scored low. The script ALSO inlines the rubric into the system prompt — this file is the canonical source the script reads at startup.
- **CONTENT** — the relevance scoring criteria. Distilled from PRD line 340:
  ```markdown
  # News-digest scoring rubric

  Score each RSS item on relevance to Bruno's interests, 0–10:

  HIGH SIGNAL (7–10):
  - Agent frameworks, multi-agent orchestration (LangGraph, CrewAI, etc.)
  - Anthropic / Claude releases, model cards, API changes
  - LLM evaluation methodology (Langfuse, Inspect, eval frameworks)
  - Production AI systems case studies (cost, reliability, observability)
  - Hybrid retrieval / RAG advancements at scale
  - Direct competitors / peers in the AI-engineering-consulting space

  MEDIUM (4–6):
  - General AI news with engineering content (training infra, benchmarks)
  - Adjacent ML research with practical implications
  - Open-source releases relevant to Bruno's stack (Python/TS, Postgres+pgvector)

  LOW SIGNAL (0–3, drop):
  - Hype / "AI is going to..." takes
  - Crypto/web3 unless directly AI-engineering relevant
  - Hardware-only (GPUs, etc.)
  - Pure consumer-product news (ChatGPT mobile UX, etc.)
  - Items already covered in past digests (the script checks before scoring)
  ```
- **VALIDATE**: file exists; first non-frontmatter line is `# News-digest scoring rubric`.
  ```bash
  test -f .claude/skills/news-digest/references/scoring-rubric.md && head -1 .claude/skills/news-digest/references/scoring-rubric.md
  ```

### CREATE `.claude/skills/news-digest/SKILL.md`

- **IMPLEMENT**: YAML frontmatter + ~30-line body explaining when the skill fires, what `scripts/digest.py` does, and where the output lands.
- **DESCRIPTION FIELD**:
  ```
  description: Daily AI-engineering news digest for BrunOS. Use when Bruno asks for the day's AI news, what's new in the feeds, the morning digest, or runs digest.py. Reads new RSS items via integrations.rss, scores via Haiku 4.5 on agent frameworks / Claude / eval / production AI relevance, dedupes against past digests, clusters survivors into 3–5 themes, summarizes each in 2 sentences, writes Memory/news-digest/YYYY-MM-DD.md. Triggers on "AI news", "morning digest", "what's new today", "summarize the feeds", explicit `/news-digest` invocations.
  ```
- **BODY**:
  - When to invoke (morning briefing, ad-hoc Bruno question, Phase 6 heartbeat tick at 07:30 BRT).
  - How to invoke: `uv run python .claude/skills/news-digest/scripts/digest.py`. Optional flags: `--max-items N` (default unlimited), `--dry-run` (skip write).
  - Output location: `BrunOS/Memory/news-digest/YYYY-MM-DD.md`.
  - Re-running on same day: overwrites (idempotent).
  - References: `${CLAUDE_SKILL_DIR}/references/scoring-rubric.md` for scoring criteria.
- **VALIDATE**:
  ```bash
  uv run python -c "
  from pathlib import Path
  text = Path('.claude/skills/news-digest/SKILL.md').read_text()
  assert text.startswith('---\n')
  assert 'name: news-digest' in text
  assert 'description:' in text
  print('news-digest SKILL.md OK')
  "
  ```

### CREATE `.claude/skills/news-digest/scripts/digest.py`

- **IMPLEMENT**: Full pipeline. Imports `integrations.rss`, calls Haiku for scoring, Sonnet for clustering+summary, writes digest with proper frontmatter.
- **STAGES** (in order, each with explicit logging to stderr):
  1. **Load env / set guard**: `os.environ.setdefault("CLAUDE_INVOKED_BY", "news-digest")` BEFORE SDK import.
  2. **Pull new items**: `from integrations.rss import new_items` (verify exact name on disk first); call it. State diff happens inside `rss.py` — don't re-implement. Cap to last 200 items defensively.
  3. **Dedupe against past digests**: for each item title (or first 100 chars of body), call `memory_search.py --path-prefix news-digest --k 1`. If top hit score > a threshold (0.5 RRF; tune empirically), drop the item. Print drop count.
  4. **Score**: bundle remaining items into one Haiku 4.5 prompt (model `claude-haiku-4-5-20251001`). System prompt = scoring rubric inlined from references file. Output expected as JSON `[{"id": "<entry.id>", "score": 0-10, "reason": "..."}, ...]`. Parse defensively — Haiku occasionally drops a comma; on parse failure, log + drop the run (do NOT write a partial digest).
  5. **Filter**: keep only items with score ≥ 7. If fewer than 3 items survive, write a one-line "Slow news day: N items below threshold, top: <title>" digest and exit.
  6. **Cluster + summarize**: pass survivors to Sonnet 4.6 with a prompt like "Cluster these N items into 3–5 themes; for each theme write a 2-sentence summary; format as markdown." Output expected as markdown.
  7. **Write**: assemble frontmatter + Sonnet output + a "Source items" appendix listing each (title, url, score). `atomic_write` to `BrunOS/Memory/news-digest/YYYY-MM-DD.md`.
- **MODEL IDs** (verbatim):
  - Scoring: `claude-haiku-4-5-20251001`
  - Summary: `claude-sonnet-4-6`
- **GOTCHA**: `# TODO(Phase 8): wrap RSS bodies in <external_data>` comment at the prompt-construction site. RSS content is third-party and a known prompt-injection vector — Phase 8's `sanitize.py` retrofits this.
- **GOTCHA**: Haiku JSON output is not guaranteed — wrap parse in try/except and on failure write the raw output to `.claude/data/state/news-digest-debug-<ts>.json` for postmortem, then exit 0 (silent skip beats partial output).
- **GOTCHA**: `next(iter(query_embed([text])))` is a generator pattern from Phase 3; you don't need it here — `memory_search.py` is invoked as a subprocess.
- **GOTCHA**: `parents[3]` for REPO_ROOT — script is at depth 4 from repo root. Off-by-one here means `vault_path()` import fails silently.
- **OUTPUT FRONTMATTER**:
  ```yaml
  ---
  type: digest
  created: <RFC3339 BRT>
  updated: <RFC3339 BRT>
  tags:
    - news
    - digest
  status: active
  ---
  ```
- **CLI FLAGS** (`argparse`):
  - `--dry-run`: print to stdout, skip write.
  - `--max-items N`: cap RSS pull at N (debug).
- **VALIDATE** (smoke test — Phase 4 must be merged):
  ```bash
  # Smoke test: dry-run, don't write
  uv run python .claude/skills/news-digest/scripts/digest.py --dry-run --max-items 20 2>&1 | tail -40
  # Expected: stderr stages logged ("pulled N items", "after dedup: M", "scored", "kept: K"); stdout has frontmatter + markdown.
  ```
- **REAL VALIDATE** (one real run):
  ```bash
  uv run python .claude/skills/news-digest/scripts/digest.py 2>&1 | tail -10
  ls -la "$(uv run python -c "
  import sys; sys.path.insert(0, '.claude/scripts')
  from shared import vault_path, now_brt
  print(vault_path() / 'Memory' / 'news-digest' / f\"{now_brt().strftime('%Y-%m-%d')}.md\")
  ")"
  ```

### CREATE `.claude/skills/weekly-review/references/review-template.md`

- **IMPLEMENT**: The structural prompt template Opus follows. Defines section headers, tone (concise + honest, not motivational), explicit "do not auto-finalize" caveat the agent surfaces in the output's first line.
- **CONTENT**:
  ```markdown
  # Weekly review template

  Synthesize the bundled data into a Sunday-evening review draft for Bruno.
  Output is a DRAFT for Bruno to refine — open with: "_Draft for review — refine before Monday._"

  Sections (skip a section if data is empty rather than padding):

  ## What got done
  - 3–7 bullets across Vertik, Protostack, AI mastery, learning, content
  - Tag each bullet with the source: ClickUp / GitHub / vault edit / calendar

  ## What stalled
  - Tasks overdue, PRs sitting, goals untouched

  ## Themes from daily logs
  - 2–4 recurring themes from the past week's logs (use the search results provided)
  - Quote one short phrase per theme

  ## Time picture
  - Meetings vs heads-down (rough split from calendar density)
  - Any back-to-back days worth flagging

  ## Next week
  - 3 concrete focus areas (NOT a task list — themes)
  - One question worth deciding before Monday

  Tone: direct, honest, no hedging. Bruno reads this once and refines once.
  Length cap: ~600 words.
  ```
- **VALIDATE**:
  ```bash
  test -f .claude/skills/weekly-review/references/review-template.md
  ```

### CREATE `.claude/skills/weekly-review/SKILL.md`

- **IMPLEMENT**: YAML frontmatter + body documenting the script and its trigger.
- **DESCRIPTION FIELD**:
  ```
  description: Sunday-evening weekly review draft generator for BrunOS. Use when Bruno asks for the weekly review, this week's recap, the Sunday review, or runs aggregate_week.py. Pulls past-7-day ClickUp completions, GitHub PR/issue activity, calendar density, daily-log themes (via memory_search), and active goals; passes the bundle to Opus 4.7; writes Memory/goals/YYYY-Www-review.md as a DRAFT for Bruno to refine — never auto-finalizes. Triggers on "weekly review", "this week's recap", "Sunday review", "/weekly-review", planning-cadence prompts.
  ```
- **BODY**:
  - When to invoke (Sunday evening, ad-hoc, Phase 6 heartbeat at 19:00 BRT Sundays).
  - How to invoke: `uv run python .claude/skills/weekly-review/scripts/aggregate_week.py` with optional `--week YYYY-Www` (defaults to current ISO week).
  - Output: `BrunOS/Memory/goals/YYYY-Www-review.md` (e.g., `2026-W18-review.md`).
  - "DRAFT" caveat: Bruno refines this; the agent never marks it final.
  - References: `${CLAUDE_SKILL_DIR}/references/review-template.md` for the section structure.
- **VALIDATE**:
  ```bash
  uv run python -c "
  from pathlib import Path
  text = Path('.claude/skills/weekly-review/SKILL.md').read_text()
  assert 'name: weekly-review' in text and 'description:' in text
  print('weekly-review SKILL.md OK')
  "
  ```

### CREATE `.claude/skills/weekly-review/scripts/aggregate_week.py`

- **IMPLEMENT**: Full pipeline. Imports `integrations.{clickup,github,calendar}`, runs `memory_search.py` subprocess for daily-log themes, calls Opus 4.7, writes review.
- **STAGES**:
  1. **Guard + bootstrap**: `os.environ.setdefault("CLAUDE_INVOKED_BY", "weekly-review")` BEFORE SDK import.
  2. **Resolve week**: `--week YYYY-Www` flag, else current ISO week from `now_brt().isocalendar()`. Compute `start_dt` (Monday 00:00 BRT) and `end_dt` (Sunday 23:59 BRT) — both ms-precision for ClickUp.
  3. **Gather data** in parallel where possible (use `asyncio.gather` if integrations are async, else sequential `with_retry`):
     - **ClickUp**: completed in [start, end], opened in [start, end], due next week — across both workspaces (Vertik + Protostack). The exact function names depend on Phase 4's API — verify on disk. Likely `clickup.completed_in_range(start_ms, end_ms)`, `clickup.opened_in_range(...)`, `clickup.due_in_range(next_start, next_end)`.
     - **GitHub**: PRs merged in [start, end], issues opened/closed in [start, end], commits to learning repos. Verify Phase 4 names.
     - **Calendar**: events in [start, end] across all calendars. Tally rough meeting hours per day.
     - **Daily-log themes**: subprocess `memory_search.py "themes" --path-prefix daily --k 30`. Then post-filter results client-side to those with chunk dates within [start, end]. (memory_search doesn't filter by date — that's a known limitation.)
     - **Active goals**: read `goals/this_week.md`, `goals/this_month.md`, `goals/personal_vision.md` directly (no integration needed — they're vault files).
  4. **Construct prompt**: bundle = headed sections per data category (ClickUp / GitHub / Calendar / Themes / Goals). Wrap in `# TODO(Phase 8): wrap external API content in <external_data>` comment. System prompt = `references/review-template.md` content. User message = the bundle.
  5. **Call Opus 4.7** (`claude-opus-4-7`): `max_turns=1`, `allowed_tools=[]`, `setting_sources=None`. Sole call in the pipeline. Cost-aware: cap input bundle at ~30K characters (truncate themes section first if over).
  6. **Write**: assemble frontmatter (`type: goal`, `tags: [weekly-review]`, `status: active`) + Opus output + a "Source data" appendix (raw counts, no PII). `atomic_write` to `BrunOS/Memory/goals/YYYY-Www-review.md`.
- **MODEL ID** (verbatim): `claude-opus-4-7`
- **CLI FLAGS**:
  - `--week YYYY-Www` (default: current ISO week)
  - `--dry-run`: print to stdout, skip write
- **GOTCHA**: ISO week math — `now_brt().isocalendar()` returns `IsoCalendarDate(year, week, weekday)`. Format as `f"{year}-W{week:02d}"`. Edge case: late-December dates can fall in week 1 of the following year (`isocalendar()` handles this — trust it).
- **GOTCHA**: BRT timezone math — Monday 00:00 BRT in unix-ms is `int(monday_brt.timestamp() * 1000)`. ClickUp wants ms. Confirm Phase 4's clickup module accepts ms (per phase-4-integrations.md it does).
- **GOTCHA**: Idempotent overwrite — if Bruno re-runs Sunday evening after refining, his refined content is gone. Mitigation: if the target file exists AND its first non-frontmatter line is NOT `_Draft for review — refine before Monday._`, abort with stderr message "Refined review exists; pass --force to overwrite". Add `--force` flag.
- **GOTCHA**: ClickUp/GitHub/Calendar may all be empty for the week. Don't crash — Opus prompt should still run with "no data this week in <category>" placeholders. The review template's "skip empty sections" rule handles output.
- **OUTPUT FRONTMATTER**:
  ```yaml
  ---
  type: goal
  created: <RFC3339 BRT>
  updated: <RFC3339 BRT>
  tags:
    - weekly-review
  status: active
  ---
  ```
- **VALIDATE** (dry-run first):
  ```bash
  uv run python .claude/skills/weekly-review/scripts/aggregate_week.py --dry-run 2>&1 | tail -50
  # Expected: stderr stage logs; stdout has the full draft preceded by `_Draft for review — refine before Monday._`.
  ```
- **REAL VALIDATE** (writes the file):
  ```bash
  uv run python .claude/skills/weekly-review/scripts/aggregate_week.py 2>&1 | tail -5
  ls -la "$(uv run python -c "
  import sys; sys.path.insert(0, '.claude/scripts')
  from shared import vault_path, now_brt
  y, w, _ = now_brt().isocalendar()
  print(vault_path() / 'Memory' / 'goals' / f'{y}-W{w:02d}-review.md')
  ")"
  ```

### UPDATE `CLAUDE.md` — append Phase 5 commands + mark phase done

- **IMPLEMENT**:
  1. In the **Build commands** section, after the Phase 3 memory commands and Phase 4's integration commands (Phase 4 will have already added its block), append a Phase 5 block:
     ```bash
     # Run skills (Phase 5):
     uv run python .claude/skills/news-digest/scripts/digest.py [--dry-run] [--max-items N]
     uv run python .claude/skills/weekly-review/scripts/aggregate_week.py [--week YYYY-Www] [--dry-run] [--force]
     ```
  2. Add a new short reference section after "Memory search (Phase 3)":
     ```markdown
     ## Skills (Phase 5)

     Three Anthropic-style skills under `.claude/skills/`:
     - `brunos-vault` — pure-context skill teaching folder layout, frontmatter, language routing, draft lifecycle, boundaries (Slack carve-out, no `personal/finance.md`). Triggers on most vault interactions.
     - `news-digest` — daily AI-news digest. Haiku 4.5 scoring → cluster → Sonnet 4.6 summary → `Memory/news-digest/YYYY-MM-DD.md`. Dedupes via `memory_search.py --path-prefix news-digest`.
     - `weekly-review` — Sunday-evening review DRAFT. Opus 4.7 synthesis of ClickUp + GitHub + Calendar + daily-log themes → `Memory/goals/YYYY-Www-review.md`. Never auto-finalizes; first line is `_Draft for review — refine before Monday._`.

     Both scripts set `CLAUDE_INVOKED_BY` before importing `claude_agent_sdk` (recursion-safe). Both pass `setting_sources=None` to keep child SDK calls fast and deterministic. Phase 9 schedules these via launchd / systemd; Phase 5 ships only the standalone CLIs.
     ```
  3. Update the Phase status checklist:
     ```
     - [x] Phase 5 — Skills (vault skill, weekly-review, news-digest) (2026-05-XX)
     ```
- **VALIDATE**:
  ```bash
  grep -q "Phase 5 — Skills" CLAUDE.md && grep -q "uv run python .claude/skills/news-digest" CLAUDE.md && grep -q "uv run python .claude/skills/weekly-review" CLAUDE.md && echo "CLAUDE.md updated OK"
  ```

---

## TESTING STRATEGY

No formal pytest suite in this project (consistent with Phase 0–3). Validation happens via inline `uv run python -c "..."` smoke checks per task above + one end-to-end run per script.

### Smoke Tests (executed inline)

- **brunos-vault**: file exists, frontmatter parses, description has trigger phrases.
- **news-digest**: dry-run produces stage logs + a digest-shaped stdout. Real run writes a file at the expected path.
- **weekly-review**: dry-run produces a draft with the leading "_Draft for review — refine before Monday._" line. Real run writes the file.

### Manual Validation

1. Open a Claude Code session in repo root, ask "where do drafts live?" — confirm `brunos-vault` skill loads and answers from its body, not from re-reading the vault each time.
2. Run `news-digest` — eyeball the digest. Are 3–5 themes coherent? Did obviously-irrelevant items drop? Is dedup against yesterday working (re-run twice, second run should produce same items but not include yesterday's already-covered).
3. Run `weekly-review` — eyeball the draft. Does it match the template structure? Does it include real ClickUp/GitHub data? Does it preserve the "draft" caveat?

### Edge Cases to Verify

- **brunos-vault**: skill loads with no errors when `BrunOS/` is missing (it's gitignored — should still load; the skill is pure content).
- **news-digest**: zero RSS items (early morning, no new entries) → writes a "Slow news day" line, doesn't crash.
- **news-digest**: Haiku returns malformed JSON → script logs + skips, no partial digest.
- **weekly-review**: empty ClickUp/GitHub/Calendar week → Opus produces a draft skipping empty sections, doesn't crash.
- **weekly-review**: re-run on existing refined file (no `_Draft for review_` marker) → aborts with a clear message; `--force` overrides.
- **weekly-review**: late-December run where ISO week falls into next year → filename year-week is correct (`isocalendar()` handles this).

---

## VALIDATION COMMANDS

Execute every command to ensure zero regressions and 100% feature correctness.

### Level 1: Syntax & file structure

```bash
# All three SKILL.md files have valid frontmatter
for f in .claude/skills/{brunos-vault,news-digest,weekly-review}/SKILL.md; do
  uv run python -c "
import sys
text = open('$f').read()
assert text.startswith('---\n'), '$f: no frontmatter'
end = text.find('\n---\n', 4)
assert end > 0, '$f: frontmatter not closed'
print('$f: OK')
"
done
```

```bash
# Both scripts compile (syntax check)
uv run python -m py_compile .claude/skills/news-digest/scripts/digest.py
uv run python -m py_compile .claude/skills/weekly-review/scripts/aggregate_week.py
echo "scripts compile OK"
```

### Level 2: Recursion-guard sanity

```bash
# Both scripts set CLAUDE_INVOKED_BY before importing claude_agent_sdk
for f in .claude/skills/news-digest/scripts/digest.py .claude/skills/weekly-review/scripts/aggregate_week.py; do
  uv run python -c "
text = open('$f').read()
guard_idx = text.find('CLAUDE_INVOKED_BY')
sdk_idx = text.find('claude_agent_sdk')
assert guard_idx > 0, '$f: missing CLAUDE_INVOKED_BY'
assert sdk_idx > 0, '$f: missing claude_agent_sdk import'
assert guard_idx < sdk_idx, '$f: guard must come before SDK import'
print('$f: guard order OK')
"
done
```

### Level 3: Setting-sources sanity

```bash
# Every ClaudeAgentOptions(...) call must pass setting_sources explicitly
for f in .claude/skills/news-digest/scripts/digest.py .claude/skills/weekly-review/scripts/aggregate_week.py; do
  uv run python -c "
import re
text = open('$f').read()
calls = re.findall(r'ClaudeAgentOptions\([^)]*\)', text, re.DOTALL)
assert calls, '$f: no ClaudeAgentOptions call found'
for c in calls:
    assert 'setting_sources' in c, f'$f: ClaudeAgentOptions without setting_sources: {c[:80]}'
print(f'$f: setting_sources OK ({len(calls)} call(s))')
"
done
```

### Level 4: End-to-end smoke (Phase 4 must be merged)

```bash
uv run python .claude/skills/news-digest/scripts/digest.py --dry-run --max-items 10 2>&1 | tail -20
uv run python .claude/skills/weekly-review/scripts/aggregate_week.py --dry-run 2>&1 | tail -30
```

### Level 5: Full vault write (final validation)

```bash
uv run python .claude/skills/news-digest/scripts/digest.py 2>&1 | tail -5
uv run python .claude/skills/weekly-review/scripts/aggregate_week.py 2>&1 | tail -5

# Confirm files exist with valid frontmatter
uv run python -c "
import sys; sys.path.insert(0, '.claude/scripts')
from shared import vault_path, now_brt
y, w, _ = now_brt().isocalendar()
digest = vault_path() / 'Memory' / 'news-digest' / f\"{now_brt().strftime('%Y-%m-%d')}.md\"
review = vault_path() / 'Memory' / 'goals' / f'{y}-W{w:02d}-review.md'
for p in (digest, review):
    assert p.exists(), f'missing: {p}'
    text = p.read_text()
    assert text.startswith('---\n')
    assert 'updated:' in text.split('---')[1]
    print(f'{p.name}: OK ({len(text)} chars)')
"
```

### Level 6: Re-index after writes (Phase 3 integration)

```bash
# After digest + review run, re-index the vault so memory_search picks them up.
# (Phase 6's heartbeat will do this automatically; for Phase 5 it's manual.)
uv run python .claude/scripts/memory_index.py 2>&1 | tail -5
uv run python .claude/scripts/memory_search.py "weekly review themes" --k 3 --path-prefix goals 2>&1 | head -30
```

---

## ACCEPTANCE CRITERIA

- [ ] `brunos-vault` skill loads when description matches a vault-related prompt; body distills folder map + frontmatter + language routing + boundaries; no scripts.
- [ ] `news-digest` skill ships with `scripts/digest.py` and `references/scoring-rubric.md`. Real run produces a `Memory/news-digest/YYYY-MM-DD.md` with valid frontmatter, 3–5 themes (or "slow news day"), and a Source items appendix.
- [ ] `weekly-review` skill ships with `scripts/aggregate_week.py` and `references/review-template.md`. Real run produces a `Memory/goals/YYYY-Www-review.md` with valid frontmatter and the leading `_Draft for review — refine before Monday._` caveat.
- [ ] Both scripts set `CLAUDE_INVOKED_BY` BEFORE importing `claude_agent_sdk`.
- [ ] Both scripts pass `setting_sources=None` on every `ClaudeAgentOptions(...)`.
- [ ] Both scripts use `shared.vault_path()`, `shared.atomic_write()`, `shared.now_brt()`. No hardcoded `BrunOS/` paths.
- [ ] Both scripts are idempotent on re-run (same-day digest overwrites; weekly-review aborts on refined file unless `--force`).
- [ ] News-digest dedupes via `memory_search.py --path-prefix news-digest`.
- [ ] Weekly-review pulls daily-log themes via `memory_search.py --path-prefix daily`.
- [ ] All `# TODO(Phase 8): wrap in <external_data>` comments are present at every external-content prompt-construction site.
- [ ] CLAUDE.md updated with three Phase 5 build commands + skills reference section + Phase 5 marked `[x]`.
- [ ] No new dependencies added to `pyproject.toml`.
- [ ] No `setting_sources=["project"]` calls anywhere — child SDK calls stay deterministic and cheap.
- [ ] No new slash commands under `.claude/commands/` — skills are the interface.

---

## COMPLETION CHECKLIST

- [ ] Phase 4 merge confirmed (or scope reduced to brunos-vault only with explicit Bruno sign-off).
- [ ] All seven new files created (3 SKILL.md + 2 scripts + 2 references).
- [ ] All Level 1–3 sanity checks pass.
- [ ] Level 4 dry-runs produce sensible output.
- [ ] Level 5 real runs write valid vault files.
- [ ] Level 6 re-index picks up new files; search returns them.
- [ ] CLAUDE.md updated and committed.
- [ ] Phase 5 marked done in the Phase status checklist.

---

## NOTES

### Why Opus 4.7 for weekly-review (not Sonnet)?

Per PRD §"Stack at a glance" line 29. The weekly review is the highest-context, highest-stakes synthesis in the system: it shapes Bruno's plan for the next 7 days. Sonnet 4.6 is fine for daily reasoning; Opus 4.7's stronger long-context synthesis materially improves theme detection across a noisy 7-day window. Cost is bounded: one call per week, ~30K-char input, single-shot — under $1/run.

### Why Haiku 4.5 for news-digest scoring (not Sonnet)?

Volume + determinism. ~50–200 RSS items/day to score; cost adds up fast on Sonnet. Haiku handles "is this AI-engineering relevant" with 95%+ agreement on a calibration set per the PRD §"Stack at a glance" line 29 + standard model-card guidance. Sonnet is reserved for the cluster+summarize step where reasoning quality matters more than throughput.

### Why no slash commands?

Skills + their `description` field are the right primitive for Phase 5. Slash commands are user-typed shortcuts; skills are agent-discovered capabilities. Phase 6's heartbeat invokes the scripts directly via `subprocess`, not via slash commands. Phase 9's launchd/systemd wakes them via `uv run python ...`. No human-in-the-loop slash-typing surface needed for these specific scripts. Bruno can still type `/news-digest` if he adds a slash command later — that's a Phase 6+ concern.

### Why subprocess to memory_search.py instead of imports?

`memory_search.py` is the public API. Importing its `search()` function directly works but couples Phase 5 scripts to its internals. Subprocess + JSON keeps the contract narrow. The 50–100ms subprocess overhead is negligible compared to the seconds-long Agent SDK calls dominating each script.

### Why the `_Draft for review — refine before Monday._` marker?

It's the in-band signal the script uses to detect "Bruno hasn't refined this yet" on a re-run. After Bruno edits the file (removing or rewording the marker), `--force` is required to overwrite. Prevents accidentally clobbering Bruno's refined version with a fresh re-aggregation.

### What's deferred to later phases

- **Sales-deal-tracker skill (PRD §5.4)** — defer to post-Phase 6. Needs `clients/` folder populated with real deals; needs heartbeat-driven cadence; not a Phase 5 priority.
- **Scheduling** (launchd / systemd) — Phase 9.
- **`<external_data>` wrapping** — Phase 8 sanitizer. Phase 5 marks the retrofit sites with `# TODO(Phase 8)`.
- **Slash command surfaces** — only if Bruno explicitly wants them after living with the skills for a week.

### Open questions for Bruno (block code generation)

- **Phase 4 integration API names** — `rss.new_items()`, `clickup.completed_in_range(start_ms, end_ms)`, `github.merged_prs(start, end)`, `calendar.events_in_range(start, end)`: confirm these (or near-equivalents) exist in Phase 4's deliverables before writing the imports. If Phase 4 picked different names, this plan adjusts at the import site only — no structural change.
- **News-digest dedup threshold** — RRF score 0.5 is a starting guess. Tune empirically after the first 3 days of real digests.
- **Weekly-review on a quiet week** — Bruno's preference: skip the run entirely (no file written), or write a "quiet week — nothing to review" placeholder? Plan currently writes the placeholder.
