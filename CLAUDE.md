# BrunOS — Project Guide for Claude Code Sessions

Bruno's personal Second Brain. A Claude Agent SDK process backed by the vault at `BRUNOS_VAULT_PATH` (a CHILD of this repo today; becomes its own git repo in Phase 9). The agent monitors Slack/GitHub/ClickUp/Gmail/Calendar/RSS, drafts replies, tracks habits, and supports day/week/month planning. Operates at Assistant proactivity.

## Vault location

Set `BRUNOS_VAULT_PATH` in `.claude/.env`. Default on Mac: `/Users/brunobouwman/Documents/claude-second-brain/BrunOS` (this repo's child dir). On VPS (Phase 9): `/home/bruno/BrunOS`. All scripts resolve via `shared.vault_path()` (Phase 2). Never hardcode the relative `BrunOS/` — scripts run from launchd/systemd with arbitrary cwd.

## Env file location

Phase 4 moved the env file from repo-root `.env` to `.claude/.env`. `shared.load_env()` (lazy import of python-dotenv) loads it; `shared.vault_path()` reads `BRUNOS_VAULT_PATH` from there if not already in the environment. Committed example: `.claude/.env.example`. `.claude/.env` is gitignored.

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

- **Allowed without asking**: append daily log, draft replies, expire/sent draft moves, update HABITS, organize files within vault, edit files outside vault, **send Slack replies when @mentioned** (personal workspace carve-out — see SOUL.md "Slack send carve-out").
- **Ask first**: new ClickUp tasks, GitHub issues/PRs, anything touching `.env`/`*.pem`/`*.key`.
- **NEVER (without explicit Bruno approval)**: send email, post external broadcasts (GitHub/ClickUp comments, X/Twitter, any non-Slack channel), post to social media, access financial data, delete anything, modify SOUL.md from reflection. Slack-on-@mention is the **only** autonomous send surface.

## Recursion guard (Phase 2 detail, preview here)

Every Agent SDK script MUST set `os.environ["CLAUDE_INVOKED_BY"] = "<purpose>"` BEFORE `import claude_agent_sdk`. Without this, SessionEnd-triggered flushes infinite-loop.

## `setting_sources` policy

Every `ClaudeAgentOptions(...)` call MUST pass `setting_sources` explicitly (`None`, `["project"]`, or `["user","project"]`). Never rely on the default — it has flipped between SDK releases. Default in current 0.1.x is `None` (no `.claude/`/CLAUDE.md/skills loaded).

## Build commands

```bash
# Install / sync deps (uv reads pyproject.toml + uv.lock; recreates .venv as needed):
uv sync

# Manually consolidate a transcript into today's daily log (Phase 2):
uv run python .claude/scripts/memory_flush.py <transcript-path>

# Index / search BrunOS/Memory/ (Phase 3):
uv run python .claude/scripts/memory_index.py [--full] [--paths file1.md file2.md] [--dry-run]
uv run python .claude/scripts/memory_search.py "<query>" [--k 10] [--path-prefix drafts/sent]

# Phase 4 integrations — single dispatcher:
uv run python .claude/scripts/query.py slack channels
uv run python .claude/scripts/query.py slack since
uv run python .claude/scripts/query.py slack mentions
uv run python .claude/scripts/query.py slack dms
uv run python .claude/scripts/query.py slack thread <channel> <ts>
uv run python .claude/scripts/query.py slack send <channel> <text>
uv run python .claude/scripts/query.py slack reply <channel> <parent_ts> <text>
uv run python .claude/scripts/query.py github issues [--repo owner/name]
uv run python .claude/scripts/query.py github prs [--repo owner/name]
uv run python .claude/scripts/query.py github recent [--repo owner/name] [--days N]
uv run python .claude/scripts/query.py github open-issue --title <t> --body-file <f> [--repo owner/name]
uv run python .claude/scripts/query.py clickup overdue [--workspace vertik|protostack]
uv run python .claude/scripts/query.py clickup today [--workspace vertik|protostack]
uv run python .claude/scripts/query.py clickup create --workspace <name> --list <id> --name "..."
uv run python .claude/scripts/query.py clickup status <task_id> <new_status>
uv run python .claude/scripts/query.py gmail unread [--max N]
uv run python .claude/scripts/query.py gmail recent <hours> [--max N]
uv run python .claude/scripts/query.py calendar today
uv run python .claude/scripts/query.py calendar week
uv run python .claude/scripts/query.py rss new
uv run python .claude/scripts/query.py rss feeds

# One-time OAuth bootstrap for Gmail + Calendar (Mac, browser consent):
uv run python .claude/scripts/bootstrap_google_oauth.py

# Run skills (Phase 5):
uv run python .claude/skills/news-digest/scripts/digest.py [--dry-run] [--max-items N]
uv run python .claude/skills/weekly-review/scripts/aggregate_week.py [--week YYYY-Www] [--dry-run] [--force]
```

Hooks in `.claude/settings.json` invoke scripts via `uv run python ...` so they pick up the project's `.venv` regardless of cwd or whether the venv is activated.

## Memory search (Phase 3)

Embedding model: `BAAI/bge-small-en-v1.5` via FastEmbed (384-dim, asymmetric — `passage_embed` for indexing, `query_embed` for retrieval). Cache: `.claude/data/fastembed_cache/`. DB: `.claude/data/state/memory.db` (SQLite + sqlite-vec + FTS5; Postgres+pgvector path stubbed for Phase 9 VPS deploy). Hybrid retrieval merges vector top-k×3 + FTS top-k×3 via RRF (k=60). The indexer excludes `Memory/personal/finance.md` per the SOUL.md no-financial-data boundary.

## Integrations (Phase 4)

Single dispatcher (`.claude/scripts/query.py`) fronts six integrations under `.claude/scripts/integrations/`:

- **Slack** — read + send. Autonomous send-on-@mention is the **only** autonomous send surface (SOUL.md "Slack send carve-out", personal-workspace-only). DMs without an @mention go through the standard draft flow. State at `.claude/data/state/slack-state.json` (per-channel last `ts`).
- **GitHub** — read issues/PRs/commits, create issues with `agent-drafted` label, open draft PRs. **FGPAT quirk**: token's repo allowlist is fixed at creation — adding a new repo requires re-issuing the token. **Draft-PR fallback**: private free repos return 422 on `draft=True`; code falls back to a regular PR with `[WIP]` title prefix and `draft` label. Default repo via `GITHUB_DEFAULT_REPO`.
- **ClickUp** — multi-workspace cross-list queries (overdue, due-today). Workspaces configured via `CLICKUP_WORKSPACES=name:id,name:id`. **Date gotcha**: ClickUp uses Unix **milliseconds**, not seconds — `int(dt.timestamp() * 1000)`. Day boundaries computed in BRT.
- **Gmail** — read-only metadata listing (`is:unread`, `newer_than:Nh`). Full message bodies are NEVER fetched in Phase 4; that's Phase 6's draft generator on-demand. **No `gmail.send` scope, ever.**
- **Calendar** — read-only events list (today, week). Times in BRT.
- **RSS** — etag/modified polite polling of curated AI feeds. Per-feed try/except so one dead feed never breaks the tick. Last-seen IDs capped at 200/feed (FIFO).

**OAuth bootstrap** (Gmail + Calendar, one-time on Mac): `uv run python .claude/scripts/bootstrap_google_oauth.py`. Resulting `google_token.json` is portable to VPS via `scp` (Phase 9) — refresh tokens bind to OAuth client_id, not the machine. Required GCP project APIs: **Gmail API + Google Calendar API** must be enabled in the same Cloud project as the OAuth client; otherwise reads return 403 with an actionable enable-API URL.

No Agent SDK calls in Phase 4 code — every integration is deterministic API client. Tokens load at the Python process boundary via `os.environ`; the LLM never sees them. Phase 6's heartbeat is what wraps these reads + Slack send into Sonnet/Haiku reasoning.

## Skills (Phase 5)

Anthropic-style skills under `.claude/skills/`. Discovery is via SKILL.md frontmatter `name` + `description`; Claude Code progressive-disclosure loads name+description always, body on description match.

- `brunos-vault` — pure-context skill teaching folder layout, frontmatter, language routing, draft lifecycle, and boundaries (Slack carve-out, no `personal/finance.md`). No scripts. Triggers on most vault interactions.
- `memory-search` — pure-context skill teaching when to invoke `memory_search.py`, query phrasing for asymmetric BGE, the `--path-prefix` folder cheat sheet, RRF score interpretation, the read-after-search workflow, and the fallback ladder. Triggers on most recall/search/dedup/tone-matching prompts. Pairs with `brunos-vault` (which teaches *where* things live; this one teaches *how to retrieve them by meaning*).
- `news-digest` — daily AI-news digest. Pulls `integrations.rss.new_items()`, dedupes via `memory_search.py --path-prefix news-digest`, scores with Haiku 4.5 (`claude-haiku-4-5-20251001`) using `references/scoring-rubric.md`, clusters survivors with Sonnet 4.6 (`claude-sonnet-4-6`) → `Memory/news-digest/YYYY-MM-DD.md`. Idempotent (overwrites on same day). Slow-news placeholder when fewer than 3 items survive.
- `weekly-review` — Sunday-evening review DRAFT. Bundles `clickup.{overdue,due_today}` + `github.{recent_commits(days=7),open_prs,assigned_to_me}` + `calendar.week()` + daily-log themes (subprocess `memory_search.py --path-prefix daily`) + active-goals files; single Opus 4.7 call (`claude-opus-4-7`) using `references/review-template.md` as system prompt → `Memory/goals/YYYY-Www-review.md`. Refined-protection: aborts unless `--force` if first non-frontmatter line ≠ `_Draft for review — refine before Monday._`. ClickUp completion-history isn't yet exposed by `integrations.clickup`; "what got done" leans on GitHub commits + daily-log themes.

Both scripts set `CLAUDE_INVOKED_BY` before importing `claude_agent_sdk` (recursion-safe) and pass `setting_sources=None` on every `ClaudeAgentOptions(...)` (deterministic + cheap child calls). Both write external content with `# TODO(Phase 8): wrap in <external_data>` comments at prompt-construction sites for the upcoming sanitizer retrofit. Phase 9 schedules them via launchd / systemd; Phase 5 ships only standalone CLIs.

## Phase status

- [x] Phase 0 — Foundation prep (2026-05-02)
- [x] Phase 1 — Memory layer (vault seeded manually 2026-05-01; BOOTSTRAP.md skipped by design)
- [x] Phase 2 — Hooks (2026-05-02)
- [x] Phase 3 — Memory search (hybrid RAG) (2026-05-02)
- [x] Phase 4 — Integrations (Slack → GitHub → ClickUp → Gmail/Calendar → RSS) (2026-05-02)
- [x] Phase 5 — Skills (`brunos-vault`, `memory-search`, `news-digest`, `weekly-review`) (2026-05-02)
- [ ] Phase 6 — Heartbeat + Reflection + Drafts + Habits
- [ ] Phase 7 — Slack chat bot (optional)
- [ ] Phase 8 — Security hardening (4 layers)
- [ ] Phase 9 — Deployment (Mac launchd + VPS systemd + vault git-sync)

## Reference

- Build PRD: `.agent/plans/second-brain-prd.md` (also vault-resident at `BrunOS/PRD.md`).
- Vault README: `$BRUNOS_VAULT_PATH/README.md`.
- Memory layout (canonical): `$BRUNOS_VAULT_PATH/Memory/_README.md`.
