# BrunOS — Project Guide for Claude Code Sessions

Bruno's personal Second Brain. A Claude Agent SDK process backed by the vault at `BRUNOS_VAULT_PATH` (a CHILD of this repo today; becomes its own git repo in Phase 9). The agent monitors Slack/GitHub/ClickUp/Gmail/Calendar/RSS, drafts replies, tracks habits, and supports day/week/month planning. Operates at Assistant proactivity.

## Vault location

Set `BRUNOS_VAULT_PATH` in `.env`. Default on Mac: `/Users/brunobouwman/Documents/claude-second-brain/BrunOS` (this repo's child dir). On VPS (Phase 9): `/home/bruno/BrunOS`. All scripts resolve via `shared.vault_path()` (Phase 2). Never hardcode the relative `BrunOS/` — scripts run from launchd/systemd with arbitrary cwd.

## Vault repo separation

`BrunOS/` is **gitignored** by this code repo. Phase 9 will `cd BrunOS && git init` to make the vault its own repo for Mac↔VPS git-sync. Vault-internal ignores (e.g. `Memory/drafts/active/*`, which contains sensitive recipient context) live in `BrunOS/.gitignore` once Phase 9 runs. Do NOT track vault files from this code repo.

## Key paths inside the vault

- `Memory/SOUL.md` — agent identity (write-protected from reflection per Phase 6).
- `Memory/USER.md` — Bruno's profile.
- `Memory/MEMORY.md` — durable memory, ≤5KB hard cap, growth via reflection only.
- `Memory/HEARTBEAT.md` — what to monitor each tick.
- `Memory/HABITS.md` — 5 daily pillars.
- `Memory/sources_of_truth.md` — ClickUp ↔ Obsidian convention reference.
- `Memory/daily/YYYY-MM-DD.md` — append-only daily logs.
- `Memory/drafts/{active,sent,expired}/` — draft lifecycle (`sent/` is the voice corpus).
- `Memory/{meetings,projects,clients,research,goals,content,team,personal,news-digest}/`
- `Memory/personal/` — PRD extension. `personal/finance.md` is OFF-LIMITS to the agent (matches SOUL.md "no financial data" boundary).
- **Note**: `Memory/BOOTSTRAP.md` is absent by design — vault was bootstrapped manually 2026-05-01 (per `Memory/_README.md` line 41). Phase 2's SessionStart hook will not see it.

## Conventions

- Timezone: America/Sao_Paulo (GMT-3).
- Date: `YYYY-MM-DD`; Timestamp: RFC3339 with explicit `-03:00`.
- Checkbox: `- [ ]` / `- [x]`.
- Language routing: Brazilian recipient → Portuguese drafts; otherwise English. Internal memory ALWAYS English.
- **No secrets in vault**, ever.
- Sources of truth: ClickUp = execution layer (tasks with status). Obsidian = thinking layer (decisions, context, lessons). Don't duplicate.

## YAML frontmatter (every vault file)

Every file in `BrunOS/Memory/` carries this block — no exceptions, including the top-level singletons. Bruno uses Obsidian Properties to scan the vault, and inconsistent frontmatter breaks the filter.

```yaml
---
type: meeting | project | client | research | goal | content | team | draft | digest | personal | daily | system | reference
created: 2026-05-02T09:00-03:00
updated: 2026-05-02T09:00-03:00
tags: [...]
status: active | archived | done
---
```

Type assignments for non-obvious files:
- `SOUL.md`, `USER.md`, `MEMORY.md`, `HEARTBEAT.md`, `HABITS.md` → `type: system`
- `sources_of_truth.md` → `type: reference`
- `daily/YYYY-MM-DD.md` → `type: daily`, `tags: [daily]`
- Drafts → `type: draft` plus extended fields (`source_id`, `recipient`, `subject`, `context`, `language`)

`updated` is stamped by `shared.atomic_write` (Phase 2) on every agent write. Hand-edits in Obsidian do NOT refresh it — treat the field as "last agent touch", not "last edit of any kind".

## Proactivity: Assistant level

- **Allowed without asking**: append daily log, draft replies, expire/sent draft moves, update HABITS, organize files within vault, edit files outside vault.
- **Ask first**: new ClickUp tasks, GitHub issues/PRs, anything touching `.env`/`*.pem`/`*.key`.
- **NEVER**: send messages, post to social, access financial data, delete anything, modify SOUL.md from reflection.

## Recursion guard (Phase 2 detail, preview here)

Every Agent SDK script MUST set `os.environ["CLAUDE_INVOKED_BY"] = "<purpose>"` BEFORE `import claude_agent_sdk`. Without this, SessionEnd-triggered flushes infinite-loop.

## `setting_sources` policy

Every `ClaudeAgentOptions(...)` call MUST pass `setting_sources` explicitly (`None`, `["project"]`, or `["user","project"]`). Never rely on the default — it has flipped between SDK releases. Default in current 0.1.x is `None` (no `.claude/`/CLAUDE.md/skills loaded).

## Build commands

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# Manually consolidate a transcript into today's daily log (Phase 2):
python .claude/scripts/memory_flush.py <transcript-path>

# Index / search BrunOS/Memory/ (Phase 3):
python .claude/scripts/memory_index.py [--full] [--paths file1.md file2.md] [--dry-run]
python .claude/scripts/memory_search.py "<query>" [--k 10] [--path-prefix drafts/sent]
```

## Memory search (Phase 3)

Embedding model: `BAAI/bge-small-en-v1.5` via FastEmbed (384-dim, asymmetric — `passage_embed` for indexing, `query_embed` for retrieval). Cache: `.claude/data/fastembed_cache/`. DB: `.claude/data/state/memory.db` (SQLite + sqlite-vec + FTS5; Postgres+pgvector path stubbed for Phase 9 VPS deploy). Hybrid retrieval merges vector top-k×3 + FTS top-k×3 via RRF (k=60). The indexer excludes `Memory/personal/finance.md` per the SOUL.md no-financial-data boundary.

## Phase status

- [x] Phase 0 — Foundation prep (2026-05-02)
- [x] Phase 1 — Memory layer (vault seeded manually 2026-05-01; BOOTSTRAP.md skipped by design)
- [x] Phase 2 — Hooks (2026-05-02)
- [x] Phase 3 — Memory search (hybrid RAG) (2026-05-02)
- [ ] Phase 4 — Integrations (Slack → GitHub → ClickUp → Gmail/Calendar → RSS)
- [ ] Phase 5 — Skills (vault skill, weekly-review, news-digest)
- [ ] Phase 6 — Heartbeat + Reflection + Drafts + Habits
- [ ] Phase 7 — Slack chat bot (optional)
- [ ] Phase 8 — Security hardening (4 layers)
- [ ] Phase 9 — Deployment (Mac launchd + VPS systemd + vault git-sync)

## Reference

- Build PRD: `.agent/plans/second-brain-prd.md` (also vault-resident at `BrunOS/PRD.md`).
- Vault README: `$BRUNOS_VAULT_PATH/README.md`.
- Memory layout (canonical): `$BRUNOS_VAULT_PATH/Memory/_README.md`.
