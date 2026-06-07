# BrunOS — Project Guide for Claude Code Sessions

Bruno's personal Second Brain. A Claude Agent SDK process backed by the vault at `BRUNOS_VAULT_PATH` (a CHILD of this repo today; becomes its own git repo in Phase 9). The agent monitors Slack/GitHub/ClickUp/Gmail/Calendar/RSS, drafts replies, tracks habits, and supports day/week/month planning. Operates at Assistant proactivity.

## Vault location

Set `BRUNOS_VAULT_PATH` in `.claude/.env`. Default on Mac: `/Users/brunobouwman/Documents/brunOS-brain/BrunOS` (this repo's child dir). On VPS (Phase 9): `/home/bruno/BrunOS`. All scripts resolve via `shared.vault_path()` (Phase 2). Never hardcode the relative `BrunOS/` — scripts run from launchd/systemd with arbitrary cwd.

## Env file location

Phase 4 moved the env file from repo-root `.env` to `.claude/.env`. `shared.load_env()` (lazy import of python-dotenv) loads it; `shared.vault_path()` reads `BRUNOS_VAULT_PATH` from there if not already in the environment. Committed example: `.claude/.env.example`. `.claude/.env` is gitignored.

## Vault repo separation

`BrunOS/` is **gitignored** by this code repo. Phase 9 will `cd BrunOS && git init` to make the vault its own repo for Mac↔VPS git-sync. Vault-internal ignores (e.g. `Memory/drafts/active/*`, which contains sensitive recipient context) live in `BrunOS/.gitignore` once Phase 9 runs. Do NOT track vault files from this code repo.

## Key paths inside the vault

- `Memory/SOUL.md` — agent identity (write-protected from reflection per Phase 6).
- `Memory/USER.md` — Bruno's profile.
- `Memory/MEMORY.md` — durable memory, ≤8KB hard cap, growth via reflection's daily curation only; over-cap bullets evict (lossless) to `_archive/MEMORY-archive.md`.
- `Memory/_archive/MEMORY-archive.md` — durable items evicted from MEMORY.md (oldest-first, searchable; eviction is move-not-delete).
- `Memory/playbook/` — dreaming output: reusable processes/patterns/prompts + decisions (`category: process|pattern|prompt|decision`); see `playbook/_README.md`.
- `Memory/Brain/brain-config.template.json` — per-brain cadence + behavior template (copy to `.claude/data/state/brain-config.json` to override defaults).
- `Memory/HEARTBEAT.md` — what to monitor each tick.
- `Memory/HABITS.md` — 5 daily pillars.
- `Memory/sources_of_truth.md` — ClickUp ↔ Obsidian convention reference.
- `Memory/daily/YYYY-MM-DD.md` — append-only daily logs.
- `Memory/drafts/{active,sent,expired}/` — draft lifecycle (`sent/` is the voice corpus).
- `Memory/{meetings,projects,clients,research,goals,content,team,personal,news-digest}/`
- `Memory/personal/` — PRD extension. `personal/finance.md` is OFF-LIMITS to the agent (matches SOUL.md "no financial data" boundary).
- **Note**: `Memory/BOOTSTRAP.md` is absent by design — vault was bootstrapped manually 2026-05-01 (per `Memory/_README.md` § Migration status). Phase 2's SessionStart hook will not see it.

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
uv run python .claude/scripts/memory_search.py "<query>" [--k 10] [--path-prefix drafts/sent] [--no-graph]

# Retrieval eval (C1 BrainBench-lite — graph OFF vs ON, P@5/Recall/MRR):
uv run python eval/eval.py [--k 10] [--queries eval/queries.jsonl]

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

# Heartbeat + reflection (Phase 6) — manual CLI; Phase 9 wires the scheduler:
uv run python .claude/scripts/heartbeat.py [--dry-run] [--no-agent] [--force]
# Reflection (Phase B: three stages — daily-log distill → inbox pass → memory curation):
uv run python .claude/scripts/memory_reflect.py [--dry-run] [--inbox-only | --curate-only | --skip-inbox] [--project <slug>]

# Dreaming (Phase B) — procedure + decision extraction → playbook/ (Haiku, adaptive):
uv run python .claude/scripts/memory_dream.py [--dry-run] [--since-days N]
uv run python .claude/scripts/memory_dream.py --deliver-questions [--dry-run]   # ask low-confidence decisions via notify adapter
uv run python .claude/scripts/memory_dream.py --reconcile [--dry-run]           # fold tagged replies back into entries

# Modular cadence (Phase B) — generate split timer units from brain-config.json:
uv run python .claude/scripts/gen_schedules.py [--platform mac|vps|both] [--dry-run]

# Comms-capture feeder (BaaS) — distil high-signal knowledge from comms channels → inbox:
uv run python .claude/scripts/comms_capture.py [--dry-run] [--since-hours N]

# Knowledge-gap scan (BaaS C1 — stale ongoing-entity detector):
uv run python .claude/scripts/gap_analysis.py            # human table
uv run python .claude/scripts/gap_analysis.py --json     # machine-readable
uv run python .claude/scripts/gap_analysis.py --days 21  # uniform threshold override
uv run python .claude/scripts/gap_analysis.py --folders projects,goals

# Privacy gate canary test (CI gate — must pass before any BaaS pilot):
uv run python tests/test_privacy_gate.py

# Federation-doctor — per-inbox observability:
uv run python .claude/scripts/federation_doctor.py
uv run python .claude/scripts/federation_doctor.py --inbox <slug>
uv run python .claude/scripts/federation_doctor.py --canary    # also runs canary tests
uv run python .claude/scripts/federation_doctor.py --json      # machine-readable

# Federation read-side (BaaS Track A, code-complete 2026-06-02 — in testing):
uv run python .claude/scripts/linos_consumer.py [--dry-run] [--slug <slug>]   # LinOS consumer loop
uv run python deploy/bin/sync_cleared_inbox.py [--dry-run]                    # cleared+in-scope push to LinOS inbox
uv run python deploy/bin/retire_local_inbox.py [--apply] [--min-age-hours N]  # Mac-side capture retirement (dry-run default)
uv run python deploy/bin/consolidate_inbox_slugs.py [--dry-run]               # one-time slug-split migration (run on VPS)
# Reusable company-brain reflection/dreaming (LinOS is the first profile):
uv run python .claude/scripts/company_brain_reflect.py reflect --profile linos [--dry-run]
uv run python .claude/scripts/company_brain_reflect.py dream --profile linos [--dry-run]

# Phase 7 — Slack chat bot:
uv run python .claude/chat/bot.py --smoke-test     # connect + auth.test, exit 0
uv run python .claude/chat/bot.py                  # foreground daemon (Ctrl+C to stop)
# LinOS company chat profile (stage 0 founder-only; registry enforced by default):
CHAT_BRAIN_PROFILE=linos CHAT_FLUSH_ENABLED=0 uv run python .claude/chat/bot.py --smoke-test

# Track D Phase 1 — monitoring probes (both support --dry-run = no reporting):
uv run python .claude/scripts/slackbot_watchdog.py [--dry-run] [--skip-smoke] [--unit NAME]
uv run python .claude/scripts/memory_doctor.py [--dry-run] [--skip-canary] [--staleness-hours N]

# Track D Phase 2 — provision healthchecks.io checks for a brain (idempotent upsert):
HEALTHCHECKS_API_KEY=<project-rw-key> uv run python .claude/scripts/provision_healthchecks.py \
    --brain <id> --host <label> [--services a,b,c] [--dry-run] [--json]
```

Hooks in `.claude/settings.json` invoke scripts via `uv run python ...` so they pick up the project's `.venv` regardless of cwd or whether the venv is activated.

## Memory search (Phase 3)

Embedding model: `BAAI/bge-small-en-v1.5` via FastEmbed (384-dim, asymmetric — `passage_embed` for indexing, `query_embed` for retrieval). Cache: `.claude/data/fastembed_cache/`. DB: `.claude/data/state/memory.db` (SQLite + sqlite-vec + FTS5; same engine on Mac and VPS — each host keeps its own index, rebuilt from the synced vault). Hybrid retrieval merges vector top-k×3 + FTS top-k×3 via RRF (k=60). The indexer excludes `Memory/personal/finance.md` per the SOUL.md no-financial-data boundary. An **unscoped** search also appends up to 3 lexical matches from the Phase-B pending-personal buffer (`personal_pending.json`, today's not-yet-curated items — not in the index) tagged `pending:true`; scoped (`--path-prefix`) searches skip it (see Phase B).

### Graph traversal over wikilinks (C1, BaaS retrieval-v2)

The indexer extracts `[[wikilink]]` edges (zero-LLM regex) into an `edges` table (`db.py`); resolution is by basename with Obsidian's "shortest path wins" tiebreak, dangling links skipped. `memory_search` graph-augments **after RRF**: top files by RRF seed a one-hop bidirectional neighbor expansion; a neighbor inherits `GRAPH_BETA × best-chunk score of its strongest connecting seed` (max, not sum — avoids hub domination). **Default is inject-only** (surface linked-but-missed docs into the candidate pool; never reorder existing RRF hits → provably can't regress precision/MRR); `BRUNOS_GRAPH_RERANK=1` opts into lifting already-retrieved siblings. All knobs are env-overridable (`BRUNOS_GRAPH_BETA` default 0.05 = eval-measured sweet spot, `_SEED_FILES`, `_MAX_NEIGHBORS`, `_MAX_CHUNKS`); `--no-graph` / `BRUNOS_SEARCH_NO_GRAPH` disables; graph is skipped when `--path-prefix` scopes to a folder. **`edges` is populated only by a full pass — run `memory_index.py --full` once after deploy** (incremental indexing only refreshes edges for changed files; empty `edges` → graph is a no-op).

Eval harness (`eval/`): `eval.py` runs `eval/queries.jsonl` (labelled, starter set — refine the gold) through search graph-OFF vs ON, reporting file-level P@5 / Recall@k / MRR. On the current 159-file vault the hybrid baseline is near-ceiling (MRR ≈ 0.78, Recall@10 ≈ 0.90), so graph is ~neutral (re-rank helps marginally only at low BETA); it's the gate before any reranker work and a published-benchmark artifact.

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

Both scripts set `CLAUDE_INVOKED_BY` before importing `claude_agent_sdk` (recursion-safe) and pass `setting_sources=None` on every `ClaudeAgentOptions(...)` (deterministic + cheap child calls). External RSS / ClickUp / GitHub / Calendar content is wrapped with `sanitize.wrap_external` before entering prompts. Phase 9 schedules them via launchd / systemd; Phase 5 ships only standalone CLIs.

## Heartbeat + Reflection (Phase 6)

Two manually-runnable proactive scripts. Phase 9 wires launchd / systemd schedules.

### `heartbeat.py` (every 30 min during 08:00–22:00 BRT in Phase 9)

5-stage flow:
1. Re-index vault (subprocess `memory_index.py`).
2. Gather Slack/GitHub/ClickUp/Gmail/Calendar/RSS in parallel via `asyncio.gather` (each integration call wrapped in `asyncio.to_thread`; `return_exceptions=True` so one failure doesn't abort the tick).
3. Build snapshot (`heartbeat_snapshot.build_snapshot`); diff against previous (`heartbeat_snapshot.diff_snapshot`); persist current snapshot to `.claude/data/state/heartbeat-state.json` BEFORE the agent runs (a crash-during-agent doesn't replay the same delta on next tick).
4. Drafts hygiene (`drafts.expire_old_drafts` — moves >24h-old drafts to `drafts/expired/`; `drafts.capture_sent_replies` is a Phase 6.5 stub) + habits prep (`habits.reset_for_today_if_needed` archives yesterday's "## Today" to "## History" and rebuilds a fresh checklist; `habits.detect_signals` computes per-pillar booleans from the snapshot diff) + **knowledge-gap scan** (`gap_analysis.gaps_to_surface` — see below) + **decision-rationale loop** (Phase B stage 4c, `_decision_rationale_loop` — deliver queued low-confidence decision questions + reconcile answers; see the Phase B section).
5. If delta is empty AND no habits-reset AND no drafts expired → fast-path: append a one-line tick to today's daily log + `_notify` "no changes" + exit. Otherwise: build sanitized `delta_text` (every external payload through `sanitize.wrap_external`) → Haiku 4.5 guardrail (`allowed_tools=[]`, `setting_sources=None`, `max_turns=1`, default-deny on parse failure) → on `pass`/`suspicious`, Sonnet 4.6 main agent (`allowed_tools=["Read","Write","Edit","Bash"]`, `setting_sources=["project"]`, `max_turns=15`) → osascript notify.

The main agent's tools include Bash but the system prompt forbids invoking `query.py slack send` or any external curl. Phase 8's `dangerous-bash.py` hardens this; Phase 6 ships honor-system + tools-whitelist.

CLI flags: `--dry-run` (print stages + would-be agent prompt; skip SDK calls + vault writes + notify); `--no-agent` (run deterministic stages, skip SDK calls); `--force` (bypass empty-delta fast-path).

### `memory_reflect.py` (daily 08:00 BRT in Phase 9, before heartbeat)

> **Superseded in part by Phase B** (see "## Phase B" below). The MEMORY.md write
> moved out of the daily-log + inbox stages into a new **memory-curation** stage:
> both now BUFFER personal items (`personal_pending.json`); curation drains the
> buffer + evicts-to-archive ONCE per day. The per-batch `_compact_if_over_cap`
> squeeze on MEMORY.md is gone. The inbox stage's strip/clear/continuity behavior
> below is unchanged.

`memory_reflect.py` runs **three independent, idempotent stages** orchestrated by `_run()` (daily-log distill → inbox pass → memory curation); each has its own state file, so a later stage runs even when an earlier one short-circuits.

**Daily-log stage** (`_run_daily_stage`): single Sonnet 4.6 call (`allowed_tools=[]`, `setting_sources=None`, `max_turns=1`). Reads yesterday's daily log + current MEMORY.md; emits JSON of `[{type, text, promote}]` per item; deterministic Python applies promotions to the right MEMORY.md section (decision → "Key durable decisions", lesson → "Lessons", fact → "Tax & financial structure", status → "Active projects"). If MEMORY.md > 8KB after append, a SECOND Sonnet call compacts older entries first (floor-guarded against truncated/garbage returns). SOUL.md changes go to today's daily log under "## SUGGESTED SOUL CHANGES (REVIEW MANUALLY)" — never directly written. Idempotent via `.claude/data/state/last_reflection.json`.

**Inbox stage / federation write-side** (`_run_inbox_stage`): drains the per-project session inboxes at `Memory/_inbox/sessions/<slug>/` (populated by the Phase A external-repo capture hooks). **One Sonnet call per project** with new captures (bounded by *projects touched*, not total projects), producing **three outputs per project**:
1. **Personal consolidation** → durable personal items appended to MEMORY.md (same `_append_promotions` path + 8KB cap-guard as the daily stage).
2. **Per-project continuity** → distilled bullets inserted under a machine-managed `## Auto-consolidated continuity` section in `projects/<slug>.md` (created with full frontmatter if absent; hand-written header preserved; capped to 8KB via the generalized `_compact_if_over_cap`). The `session-start-project.py` hook already loads `projects/<slug>.md` via `--context-file`, so this enriches the next session in that repo.
3. **Strip-in-place + `share_status: cleared`** → each capture is rewritten with personal-life asides removed and stamped `share_status: cleared` in frontmatter (work/technical content preserved verbatim). This is the **privacy boundary as a flag** — a downstream company brain (LinOS now, VertikOS later) reading the same gitignored, per-company inbox sees only work-scoped, cleared content. Captures are **never deleted or moved by reflection**; the Mac producer retires its local copies separately once the VPS holds them terminal (see below). External capture bodies enter the prompt via `sanitize.wrap_external`.

**Inbox-stage hardening (2026-06-02, PRs #5/#7/#8):** captures are processed in **batches of 8** (`INBOX_CAPTURES_PER_BATCH`) with the per-project watermark saved after each batch, so a systemd timeout makes forward progress instead of death-spiraling on a growing backlog (unit `TimeoutStartSec` bumped 600→1800). The watermark advances **only over the leading run of terminal captures** (`_leading_terminal_watermark`) — never past a still-open one — fixing the under-clearing bug where an LLM-omitted capture got stuck uncleared below the watermark forever. A capture that fails to clear after `MAX_CLEAR_ATTEMPTS` (3, tracked via `clear_attempts` in frontmatter) is force-**quarantined** (`share_status: quarantined` — fail-safe: never shared, surfaces for manual review); `cleared` and `quarantined` are both terminal. Separately, `shared.write_inbox_capture` now applies `canonicalize_slug` at the single write chokepoint, so no caller (explicit `--project` flag, precompact hook, watcher) can split one repo across multiple inbox folders again; `deploy/bin/consolidate_inbox_slugs.py` is the one-time idempotent migration for pre-fix splits (**run on the VPS** — it owns the `cleared` truth).

Idempotent via a **per-project watermark** in `.claude/data/state/inbox_reflection.json` (`{"<slug>": "<newest created processed>"}`); only captures with `created > watermark` AND `share_status != cleared` are processed, so re-run is a no-op. CLI: `--inbox-only` (skip daily stage), `--skip-inbox` (legacy daily-only), `--project <slug>` (limit to one inbox); `--dry-run` prints per-project parsed JSON (personal items + continuity + would-clear captures) and writes nothing / advances no watermark.

**Federation model — no `_shared/` staging.** This supersedes the earlier curated-shared-folder design: with per-company inboxes (LinOS reads only `linos-protostack`-tagged inboxes like `colinas/`; VertikOS would read only `vertik`; neither touches personal-only inboxes) plus strip-in-place, the capture *is* the shared artifact and `share_status: cleared` is the gate. `default_export` is preserved as metadata, never used to route — the inbox stage has **no write path outside the BrunOS vault**; the company brain reads the inbox itself. **Mac→VPS inbox transport — LIVE (2026-05-26)** via `deploy/bin/sync_inbox.py` + the `com.bruno.brunos.inbox-rsync` launchd unit: `_inbox/` is gitignored so git-sync never carries it; captures originate on the Mac (external-repo hooks) and rsync (`-a --update`, never `--delete`) one-way to the VPS, where the **VPS-side** reflection inbox stage processes them — reflection stays VPS-only. `--update` means a capture the VPS has already stripped + `cleared` (newer mtime) is never clobbered by the Mac's older original. Refined outputs (MEMORY.md personal items + `projects/<slug>.md` continuity) flow BACK to the Mac via the normal vault git-sync. No brain writes/deletes inside another.

**Federation read-side — code-complete 2026-06-02, in testing (PRs #3/#7/#9):**
- **Cleared-inbox transport** (`deploy/bin/sync_cleared_inbox.py`) — Bruno-side push mirroring **only** captures passing the LinOS gate (`shared.validate_consumer_read`: `default_export == linos-protostack` **AND** `share_status == cleared`) into a LinOS-readable inbox, since `/home/bruno` is `0700`. A Python pre-pass builds an rsync `--files-from` manifest (`-a --update --no-implied-dirs`, never `--delete`). **Both gates matter**: `cleared` alone is stripping, not authorization — most `default_export: personal` captures are also cleared.
- **LinOS consumer** (`.claude/scripts/linos_consumer.py`) — reads eligible captures (read-only), Haiku 4.5 extracts bullets + joint facts, writes joint docs to `LinOS/Memory/joint/<slug>/`, appends to LINMEMORY.md, publishes ack manifests at `LinOS/Memory/_acks/brunos/<id>.json`. Per-slug watermark at `.claude/data/state/consumer_watermark.json`; `CLAUDE_INVOKED_BY=linos-consumer`.
- **Ack return leg** (`deploy/bin/sync_acks.py` + `linosbrain-ack-sync.{service,timer}`, 09:30 BRT) — the mirror of the cleared-push: the consumer writes acks inside linos's `0700` home, so this runs as `linos` and rsyncs them (`-a --ignore-existing`, no `--delete`) into a bruno-owned drop (`/home/bruno/linos-acks/brunos/`, the default `BRUNOS_LINOS_ACK_DIR` that `retire_vps_inbox.py` reads). A one-time least-privilege ACL grants linos traverse+write to just that drop (runbook: `deploy/README.md` § Ack return). **This closes the F2 loop** — without it the consumer's acks are invisible to bruno and every `linos-protostack` capture stays `awaiting-ack` forever.
- **Reusable company-brain reflection/dreaming** (`.claude/scripts/company_brain_reflect.py`) — profile-agnostic routine for LinOS and future client brains. Reads company vault sources (`Memory/LINMEMORY.md`/`COMPANY.md`, decisions, standards, projects, clients, recent joint/digest notes), excludes `_imports`, `_inbox`, `_acks`, and writes reviewable artifacts only: leadership digests, gap digests, and proposed playbooks. LinOS systemd runs this as `COMPANY_BRAIN_PROFILE=linos` after the 09:00 consumer.
- **Mac producer-side retirement** (`deploy/bin/retire_local_inbox.py` + `com.bruno.brunos.inbox-retire` launchd unit, installed **disabled** — review dry-runs first; 11:30 BRT, after reflect → cleared-push → consumer). Deletes a local capture only when the VPS holds the same canonical-slug+filename capture in a **terminal** status (`cleared`/`quarantined`); dry-run unless `--apply`, 48h `--min-age-hours` grace, aborts if VPS unreachable or its terminal set is empty. Closes the failover hazard of a Mac reflection run reprocessing stale `active` originals.

**Phase C.5 live state (2026-06-06):** the LinOS Unix node is provisioned on the shared VPS (`linos` user, `/home/linos/claude-second-brain`, `/home/linos/LinOS`, `/var/log/linosbrain`), LinOS vault git-sync is live against `protostack-linos/linos-brain`, the company-brain identity seed is committed in the LinOS vault, and Bruno-side `brunoosbrain-linos-inbox-sync.{service,timer}` is installed/enabled (~08:45 BRT). Consumer dogfood imported 9 scoped+cleared Colinas captures into distinct `Memory/joint/colinas/*.md` notes, with 9 ack manifests and a watermark at `2026-05-24T19:49:59-03:00`; `linosbrain-consumer.timer` is enabled for 09:00 BRT. Stage-0 LinOS Slack chat is enabled through `linosbrain-slackbot.service`: `CHAT_BRAIN_PROFILE=linos`, `CHAT_FLUSH_ENABLED=0`, deterministic channel registry enforced by default, exact channel `slack:C0B8BCDHM5M`, Bruno/Lisa allowed only, ask-only, no write targets. Company-brain reflection/dreaming now uses the reusable `company_brain_reflect.py` routine: `linosbrain-reflect.timer` runs at 09:15 BRT and `linosbrain-dream.timer` runs at 09:25 BRT, after the 09:00 consumer, producing reviewable leadership/gap/playbook artifacts. The product target remains channel-scoped company chat plus scoped brain-to-brain RPC. The old Obsidian Sync vault was migrated into `Memory/_imports/linos-obsidian-sync-2026-06-06/` in LinOS vault commit `91bf765`, excluding `.obsidian/**`, personal/shared-life notes, generated PDFs, and credential-bearing editor state. The **F2 federation loop is now closed** (2026-06-06): the ack RETURN leg (`deploy/bin/sync_acks.py` + `linosbrain-ack-sync`, 09:30 BRT) pushes the consumer's acks out of linos's `0700` home into bruno's readable `/home/bruno/linos-acks/brunos/` via a one-time least-privilege ACL, so the VPS-side F2 retirement (`retire_vps_inbox.py` + `brunoosbrain-inbox-retire-vps`, 10:30 BRT — `BrunOS-processed AND LinOS-acked ELSE 15-day fallback`) can see them. Verified live: the 9 stuck Colinas captures flipped `awaiting-ack → ready` with `0 fallback`, leaving enabling the retire timer as the final one-time flip. **Still deferred:** Mac inbox-retire enablement after dry-run review, broad multi-channel company chat, company chat ingestion/write pipeline, and monitoring healthchecks.

`protect-soul.py` (PreToolUse `Edit|Write`) is belt-and-suspenders: it blocks `BrunOS/Memory/SOUL.md` edits when `CLAUDE_INVOKED_BY=reflection`. Reflection itself uses no tools, so the hook is defensive against future agent surfaces.

`CLAUDE_INVOKED_BY` values introduced in this phase: `heartbeat`, `reflection`, `company-brain-reflect`. Each script sets it BEFORE importing `claude_agent_sdk` (recursion-safe).

`sanitize.py` provides `wrap_external`, `clean_external`, `TRUST_BOUNDARY_INSTRUCTION`, regex injection-marker stripping, base64 blob redaction, and markdown/XML escaping outside fenced code blocks.

### Drafts + habits

`drafts.py` handles deterministic lifecycle: `expire_old_drafts(now)` moves >24h-old drafts from `drafts/active/` to `drafts/expired/` (flips `status: expired`). `capture_sent_replies` is a Phase 6.5 stub. Voice corpus retrieval uses `memory_search.py --path-prefix drafts/sent`. Filename: `YYYY-MM-DD_<source>_<recipient-slug>_<short-hash>.md` — same `(source, source_id)` always hashes to the same filename so the same item never produces two drafts.

`habits.py` handles the 08:00 BRT reset (deterministic — archive yesterday's "Today" to History, create fresh checklist) + signal detection (per-pillar boolean from snapshot deltas). The HEARTBEAT AGENT applies HABITS.md check-marks via the Edit tool — `habits.py` only computes signals.

### Knowledge-gap scan (`gap_analysis.py`, BaaS C1)

Deterministic, zero-LLM "nothing filed about X in N+ days" detector — gbrain's most distinctive UX, our demo trust-builder. A filesystem-**mtime** recency scan over an ongoing-entity folder allowlist (`projects`, `clients`, `goals`; env `BRUNOS_GAP_FOLDERS`), with **per-folder thresholds** (`projects`/`clients`/`goals` = 14/21/10 days; `BRUNOS_GAP_STALE_DAYS`/`--days` override). mtime (not the frontmatter `updated:` field) is the signal: it also catches Obsidian hand-edits and is the conservative "has *anything* happened to this entity" measure. Skips: closed entities (`status: archived|done|completed|cancelled`), `_`-prefixed meta files, dated point-in-time artifacts (`YYYY-Www-review.md`, date-stamped snapshots — a past week's review is *meant* to be stale), and `personal/finance.md`.

Two surfaces: a **standalone CLI** (`--json`/`--days`/`--folders`, pure — no writes) for demos/on-demand, and the **heartbeat** (`gaps_to_surface` in stage 4b) which surfaces **at most once/day** (guard in `gap-analysis-state.json`, `last_surfaced_date`) — appends a `## Knowledge gaps` block to the daily log + folds a one-liner into the tick notification, so it can't spam the 30-min ticks. No SyncReporter: it runs inside the already-monitored heartbeat. Scope note: re-fires only on a *new day*, not when the gap set changes intra-day (acceptable v1). Standalone tests: `tests/test_gap_analysis.py`.

## Slack chat bot (Phase 7)

Long-running daemon at `.claude/chat/bot.py` that turns Bruno's personal Slack workspace into a remote chat surface for BrunOS. Connects via Socket Mode (`AsyncApp` + `AsyncSocketModeHandler` from `slack_bolt`), listens for `message.im` events, and routes each Slack thread to a stateful `ClaudeSDKClient` keyed on the thread root `ts`. Replies post in-thread via Bolt's `say()`.

- **Entry**: `uv run python .claude/chat/bot.py` (foreground); `--smoke-test` validates the bot token + prints `bot_user_id` and exits 0.
- **Recursion guard**: `CLAUDE_INVOKED_BY=chat` is set BEFORE any SDK import (skips SessionEnd flush + PreCompact hooks for child sessions).
- **Tools / setting_sources**: every options block uses `allowed_tools=["Read","Write","Edit","Bash"]` and `setting_sources=["project"]` so each session loads `CLAUDE.md` + the four Phase 5 skills (`brunos-vault`, `memory-search`, `news-digest`, `weekly-review`). The Bash tool lets the bot shell out to `query.py` (Phase 4 dispatcher) and `memory_search.py` (Phase 3) — no integration logic is duplicated in the bot.
- **System prompt**: REBUILT fresh per session via `chat.system_prompt.build_chat_system_prompt()` (the options factory calls it on each `get_or_create`) — composes a chat-mode preamble (Slack mrkdwn rules, carve-out reminder) plus the canonical vault block (`hooks.session-start-context.build_context()` reused via `importlib`, module cached). Per-session rebuild (~6 cheap vault reads, only on thread creation/resume — not per message) means a week-long daemon never serves stale MEMORY/daily-log context. **`chat` is in the SessionStart hook's `_SKIP_FOR` set**: the hook would otherwise inject the same >cap vault dump a second time, and in the SDK runtime that dump spills to a file (`<persisted-output>`), so the injected copy is just a truncation notice telling the bot to Read a file it already has in its system prompt. The system-prompt bake is therefore the single, always-fresh source of vault context for chat sessions.
- **Slack send carve-out**: this is the **only** autonomous-send surface in BrunOS. SOUL.md prohibits sending elsewhere (email, GitHub/ClickUp comments, X, etc. all stay draft-only).
- **Surfaces**: two Bolt event handlers wired in `slack_adapter.py:register()`:
  - `@app.event("message")` filters to `channel_type=im` for DMs (no @mention needed).
  - `@app.event("app_mention")` handles channel @mentions; the `<@bot_user_id>` self-mention is stripped from `event["text"]` before being sent to the SDK.
  Channel UX: every continuation requires another @mention (Slack does NOT deliver `app_mention` for follow-up replies in the same thread, and we don't subscribe to `message.channels` because it's a fire hose). Bare `<@brunos>` with no instruction posts a "Yes? Mention me with a question or instruction." nudge.
- **Session keying**: `f"{channel_id}:{thread_root_ts}"`, so DMs (`D…:ts`) and channel threads (`C…:ts`) never collide and the same channel can host multiple parallel threads. The Slack `thread_ts` returned to `say()` is just the bare `ts` portion.
- **Slack app config (one-time)**: bot scopes `chat:write` + `app_mentions:read`; Event Subscriptions enabled with **only** `message.im` and `app_mention` under Bot Events; Socket Mode enabled with an App-Level Token (`xapp-...`) holding `connections:write`. Reinstall the app after scope changes. Invite the bot into channels where you want to @mention it (`/invite @brunos`). Documented in `.claude/.env.example`.
- **State**: `bot_user_id` is merged into `.claude/data/state/slack-state.json` (shared with Phase 4 — channels map untouched). Per-thread index lives in `.claude/data/state/chat.db` (SQLite, `chat_threads(thread_key, created_at, last_message_at, sdk_session_id)`).
- **Session lifecycle (reap + resume + flush)**: each thread's SDK `session_id` is captured from the response (`ResultMessage.session_id`) and persisted to `chat.db`. The `SessionManager` runs a background reaper (`run_reaper`, scans every 5 min): a thread idle ≥60 min is reaped, and a hard **LRU cap of 4** live clients evicts the least-recently-active beyond that — both bound memory on the shared, swap-light box (each live SDK subprocess pins ~200MB; an unbounded daemon previously bloated to 2.6G and silently dropped Socket Mode). Reaping is non-destructive: the next message in the thread recreates the client with `resume=<session_id>` (`fork_session=False`, so same transcript continues) — a reaped/restarted thread continues seamlessly; first reply after a gap pays a transcript-reload cost. Resume falls back to a fresh session if the transcript was pruned (Claude Code's 30-day default). **Before** any client is closed (idle reap, LRU evict, or shutdown) `SessionManager._flush_thread` hands the `<session_id>.jsonl` transcript to `dispatch_flush(source="chat-session", _incremental=True)` — the same memory_flush pipeline the SE/PC hooks use — so chat knowledge lands in the daily log (gated by memory_flush's 2KB floor + `FLUSH_OK`) and reflection promotes durable bullets to MEMORY.md. The bot's own SE/PC hooks remain no-ops under `CLAUDE_INVOKED_BY=chat`; the bot-driven flush is what captures chat knowledge. **Incremental flush**: a resumed thread grows one transcript across many reaps, so the `_incremental` flag makes memory_flush distil only lines past a per-session watermark (`flush_offsets.json`) — no duplicate bullets in the daily log on the 2nd+ reap. The watermark advances only on a real flush/FLUSH_OK, so a sub-2KB tail accumulates rather than being lost. (Watermark is claude-code-origin only; codex stays whole-file.)
- **Memory backstops (Phase 9)**: a 2GB swapfile (`deploy/bin/setup-swap.sh`, `vm.swappiness=10`) absorbs transient spikes instead of dropping the WebSocket, and `brunoosbrain-slackbot-restart.timer` (Sunday 04:00 BRT) `try-restart`s the daemon weekly as a final backstop (`try-restart` = no-op when stopped, so it never resurrects a unit stopped for failover).
- **Shutdown**: SIGINT/SIGTERM trigger `session_manager.close_all()` (flush-then-disconnect per thread) + `handler.close_async()` so all SDK sessions flush their knowledge and disconnect cleanly.
- **Phase 6/7 boundary**: chat bot owns DMs + channel @mentions in real-time (Socket Mode push). `heartbeat._gather()` calls `_split_chat_bot_handled()` after `slack.since_last_run()`: drops messages with `<@bot_uid>` (chat bot's `app_mention` handler owns them) and drops DMs where `slack.get_thread()` shows a bot reply with `ts > message.ts`. Unreplied DMs stay actionable — that's the catch-up safety net for Phase 7 downtime. Snapshot + daily-log counts use `slack_msgs_all` (raw haul) so reflection still sees full activity, even DMs the bot handled. Tick entry format: `Slack: <total> new (<handled by chat bot>, <need attention>)`.

## Security (Phase 8)

Four independent layers guard the long-running agent surfaces:

1. **Credential protection** — `.claude/hooks/block-secrets.py` runs as a PreToolUse hook before file reads/writes/searches and Bash. It blocks credential/private paths (`.env*`, keys, OAuth tokens, `.ssh/`, `.aws/`, `secrets/`, `private/`) and finance/invoice/billing/payment files, plus Bash env-exfil commands like `cat .env`, `printenv`, `env`, `os.environ`, and `process.env`.
2. **Sanitization** — `.claude/scripts/sanitize.py` is the data-boundary source of truth. `wrap_external` strips injection markers via `_INJECTION_PATTERNS`, redacts large base64-looking blobs, escapes XML/markdown control characters outside fenced code, escapes tag attributes, then wraps third-party content in `<external_data ...>`.
3. **Semantic guardrail** — `heartbeat.py` runs the Haiku 4.5 pre-flight check before the main heartbeat agent (`allowed_tools=[]`, `setting_sources=None`, `max_turns=1`). Verdict parse failures default to `fail`.
4. **Command guardrails** — `.claude/hooks/dangerous-bash.py` runs as a Bash-only PreToolUse hook. Patterns live in `DANGEROUS_BASH_PATTERNS` inside `.claude/scripts/shared.py` and cover destructive filesystem commands, privilege escalation, outbound curl/wget/netcat-style exfil, package installs, destructive git commands, and process/system kills.

Hook order in `.claude/settings.json`: `block-secrets.py` first, `dangerous-bash.py` second, `protect-soul.py` last. Hook input is JSON on stdin. `block-secrets.py` and `protect-soul.py` soft-block with `{"decision":"block","reason":"..."}` on stdout; `dangerous-bash.py` hard-blocks with exit 2 and stderr.

## Monitoring (BaaS Track D — Phase 1, 2026-06-03)

PRD: `BrunOS/Memory/projects/Brain/monitoring-observability-prd.md`. Backbone decision: **healthchecks.io-centric** — every service pushes a dead-man ping with its status.json **POSTed as the ping body** (healthchecks.io stores the last body, readable via API → the future fleet dashboard reads rich state from the same API that serves alive/dead). Check naming `<brain>-<svc>-<host>`; env var pattern `<BRAIN>_<SVC>_HEALTHCHECK_URL`.

**The pattern** is `sync_common.SyncReporter` (status file `<svc>-state.json` + rate-limited Slack alert to `BRUNOS_ALERT_CHANNEL` + healthcheck ping + systemd `OnFailure=…-alert@%n` backstop). Phase 1 extended it from the original four (vault-sync, code-sync, reflect, federation-doctor) to **every service**:

- **heartbeat** — per-tick outcome via `_record_tick`: ok/fast-path/no-agent → success; guardrail-blocked/agent-error/gather-error/crash → failure + alert (a blocked injection now ALERTS instead of osascript-only). Status file is `heartbeat-monitor-state.json` (`heartbeat-state.json` is the snapshot). Unit gained `OnFailure=`.
- **linos_consumer** — run stats (`eligible/integrated/failed`); any failed capture → alert (retried next run; watermark only advances past successes).
- **transports** — `sync_inbox` (`inbox-rsync`), `sync_cleared_inbox` (`linos-inbox-sync`), `retire_local_inbox` (`inbox-retire`; reports in dry-run mode too — the dead-man proves the review-period job runs).
- **slackbot_watchdog.py** (NEW, 15-min timer) — unit-down / restart-storm (NRestarts delta ≥3) / duplicate-instance (Socket Mode broadcast!) / auth.test token check. **Failover: stop the watchdog timer with the bot** or set `BRUNOS_SLACKBOT_WATCHDOG_DISABLED=1`.
- **memory_doctor.py** (NEW, daily 09:15 BRT) — sqlite quick_check + index freshness (newest vault .md mtime vs memory.db mtime, 3h threshold) + end-to-end search canary (known query must return ≥1 result). Catches "brain can't do memory search", previously invisible.

Conventions: reporting lives at the CLI boundary (`main()`), never in library functions; dry-runs never report; `BRUNOS_DISABLE_REPORTING=1` disables everything (tests); reporting failures never break the job they observe. `make_reporter(service, env)` / `report_outcome(...)` in `sync_common.py` are the one-call helpers.

**Phase 2 — onboarding provisioning** (`provision_healthchecks.py`): one command per brain×host upserts the checks via the healthchecks.io v3 API (`unique:["name"]` → idempotent), applies the naming/tag/grace conventions from `SERVICE_CATALOG`, and emits the env block for the instance's `.env`. Model: one Protostack healthchecks account, **one project per brain** (API keys are project-scoped — the key selects the brain), alerts via project integrations (`channels:"*"`) into the shared Protostack ops channel. **Provision only instrumented services** (a check nothing pings = permanent red): probes-first starter is `memory-doctor,slackbot-watchdog` (external probes, zero changes to a brain's existing scripts). First dogfood: LisaOS — runbook at `projects/Brain/lisaos-monitoring-onboarding.md` (vault); the BrunOS↔LisaOS boundary holds: Bruno provisions, Lisa instruments her side. Phase 3 (thin fleet page over the healthchecks API) is ClickUp-tracked, TBD.

## Phase B — Dreaming + Reflect finalization (modular cadence, 2026-06-06)

Two passes read the same session captures and extract orthogonal things:
**reflection** curates KNOWLEDGE into MEMORY.md; **dreaming** curates PROCEDURE +
DECISIONS into `playbook/`. Plus a config store makes every cadence + behavior
per-brain with working defaults. Plan: `.agent/plans/phase-b-dreaming-reflect-finalization.md`.

### brain-config.json — the per-brain config store

`.claude/scripts/brain_config.py` exposes `get("dotted.path")` returning `DEFAULTS`
deep-merged with `.claude/data/state/brain-config.json` (an **absent file → pure
defaults**, so a fresh brain needs zero config). Behavior toggles are read at
**runtime** by reflect/dream; cadence strings are consumed only by
`gen_schedules.py`. Template (with the documented defaults) ships at
`Memory/Brain/brain-config.template.json`. Schema:

```jsonc
{
  "role": "individual",                       // "individual" | "company"
  "reflection": {
    "inbox_pass":      { "enabled": true, "cadence": "hourly", "hours": "08-20" },
    "memory_curation": { "enabled": true, "cadence": "daily@08:00" },
    "federation":      true                    // strip+clear+forward (false = solo brain)
  },
  "dreaming": {
    "enabled": true, "cadence": "nightly@03:00",
    "trigger_min_captures": 5,                 // adaptive: skip the sweep below this
    "extract": ["processes", "decisions"],
    "decision_prompts": { "enabled": true, "max_per_day": 3, "confidence_threshold": 0.6 }
  },
  "notify": { "adapter": "slack", "target": null }   // adapter ∈ {slack, none}; null = default DM
}
```

### Reflect finalization — buffer → curate → evict

The hourly inbox pass and the daily-log distill no longer write MEMORY.md; they
**buffer** promotable personal items to `personal_pending.json`. The new
**memory-curation** stage (`_run_memory_curation_stage`, daily) drains that buffer
into MEMORY.md **once** via `_append_promotions`, then runs
`_evict_to_archive_if_over_cap` **once**. Result: MEMORY.md is written/compacted at
most once per day (no per-batch churn) and its byte size is stable.

Eviction is **deterministic, zero-LLM, and lossless**: while over the 8KB cap, peel
the **oldest dated bullet** (`- **YYYY-MM-DD** —`) from the **largest section** and
append it verbatim (+ provenance) to `_archive/MEMORY-archive.md`. Undated context
bullets (links, aliases) are never touched; if no dated bullet remains to peel, the
doc is left intact and `still_over_cap` is surfaced to monitoring
(`curate_memory_over_cap`). The old LLM squeeze (`_compact_if_over_cap`) stays only
as the project-doc compactor. Stage flags: `--inbox-only` (hourly), `--curate-only`
(drain+evict), `--skip-inbox` (daily-log distill + curate). Each stage gated by its
brain-config `enabled` toggle; `federation:false` makes the inbox pass extract-only
(no strip/clear). Tests: `tests/test_reflect_eviction.py`.

### Dreaming — `memory_dream.py` (Haiku, adaptive)

`CLAUDE_INVOKED_BY=dream`. Gathers captures under `Memory/_inbox/sessions/`
(recursively, incl. per-project `_archive/`) created > the `dream.json` watermark.
**Adaptive gate**: fewer than `trigger_min_captures` new → log + exit 0 (no model
call). Otherwise one Haiku 4.5 call per batch → JSON entries
`{kind: process|pattern|prompt|decision}`. Each candidate is **deduped** against the
existing playbook via `memory_search.py --path-prefix playbook` (mirrors digest.py;
fail-open). **Confidentiality**: the prompt instructs the model to generalize away
project identifiers, and every entry body runs through `scrub_excluded_entities` +
`scrub_secrets` before write — a Vertik-derived pattern lands as reusable craft, never
a leak. Entries are written as `playbook/<slug>.md` (schema in `playbook/_README.md`);
the watermark advances to the newest processed capture (re-run = no-op via watermark +
dedup). `--since-days N` widens the window for manual dry-run inspection. Tests:
`tests/test_dream.py`.

### Decision-rationale feedback loop

A decision extracted with `confidence < confidence_threshold` is written
**provisionally** (`confidence: low` + an open-question note) AND enqueues a question
in `decision_questions.json`. `notify_adapter.py` is the pluggable "ask the person"
seam: `get_adapter()` returns a `SlackAdapter` (default — DMs via the Phase-4 bot,
target = `notify.target` / `$BRUNOS_NOTIFY_TARGET` / `$BRUNOS_ALERT_CHANNEL`, tagging
`[ref:<id>]`) or a `NoneAdapter` (no-op; unknown adapter falls back here). Delivery
(`--deliver-questions`) is rate-limited to `decision_prompts.max_per_day` and marks a
question `asked` only on **confirmed** send (NoneAdapter confirms nothing → fail-safe).
Reconciliation (`--reconcile`) scans tagged Slack replies, matches by ref-id (v1
simple match — the noted tuning risk), patches the playbook entry (confidence low→high
+ confirmed rationale), and marks the question answered. Tests:
`tests/test_decision_loop.py`.

**Wired into the heartbeat** (stage 4c, `_decision_rationale_loop`): every tick shells
out `--deliver-questions` + `--reconcile`, BEFORE the empty-delta fast-path so a quiet
day still asks. Both are no-ops with no subprocess when the queue is empty; reconcile
further skips the Slack read unless a question is asked-but-unanswered, and reads a
bounded history window directly (NOT `dms_since_last_run`) so it never advances the
shared slack-state watermark.

### Modular cadence — `gen_schedules.py` + split units

`gen_schedules.py` reads brain-config cadence strings and emits the platform's timer
units (`--platform mac|vps|both`, default = host OS; `--dry-run` prints them;
idempotent). It **splits** the single `brunoosbrain-reflect` / `com.bruno.brunos.reflection`
unit into three:

| key | runs | default cadence |
|-----|------|-----------------|
| `reflect-inbox` | `memory_reflect.py --inbox-only` | hourly 08–20 (`OnCalendar=*-*-* 08..20:00:00`) |
| `reflect-curate` | `memory_reflect.py --skip-inbox` | daily 08:00 |
| `dream` | `memory_dream.py` | nightly 03:00 |

Generated systemd timers pin `America/Sao_Paulo` in `OnCalendar` (the VPS runs UTC —
a bare time would fire 3h off); launchd plists ship `Disabled=true` (failover; Mac
isn't dual-run safe for MEMORY/playbook writes).

**Migration — DONE (2026-06-06).** VPS migrated: legacy `brunoosbrain-reflect.timer`
disabled, the three split timers enabled (verified BRT-aligned next-runs). Mac:
`install-mac-launchd.sh` installed the three plists disabled (failover). The legacy
`brunoosbrain-reflect.{service,timer}` + `com.bruno.brunos.reflection.plist` were
removed from `deploy/`. delivery/reconcile are wired into the heartbeat (stage 4c,
above) — so this is the full Phase B activation, not just unit files.

### New state files (`.claude/data/state/`, gitignored runtime)

- `brain-config.json` — this brain's cadence + behavior (absent → defaults).
- `personal_pending.json` — buffered personal items awaiting daily curation. **Surfaced intraday** (it isn't in the vault/index until curation): `build_context()` injects a `pending-personal` block right after MEMORY.md (every session: interactive, chat, heartbeat), and an **unscoped** `memory_search` appends up to 3 lexical buffer matches tagged `pending:true` (scoped `--path-prefix` searches — incl. dedup callers — skip it; `--no-pending`/`BRUNOS_SEARCH_NO_PENDING` disables). Helpers: `shared.load_personal_pending` / `shared.format_personal_pending`.
- `dream.json` — dream watermark + processed capture ids.
- `decision_questions.json` — rationale-prompt queue.

`CLAUDE_INVOKED_BY` value added this phase: `dream` (set before the SDK import).

## Comms-capture feeder (BaaS — knowledge extraction from comms channels)

PRD/scope: `BrunOS/Memory/projects/Brain/comms_capture_feeders.md` (+ the access/routing primitive in `company_brain_channel_registry.md`). ClickUp: `86ca5bgak`.

**The insight:** code sessions capture knowledge ambiently (SessionEnd/PreCompact → `_inbox/`); people whose work *is* chat have no sessions. `comms_capture.py` (`CLAUDE_INVOKED_BY=comms-capture`) is the non-tech equivalent: a cadence-driven feeder that reads configured comms channels and Haiku-distils **HIGH-SIGNAL ONLY** (decisions / commitments / client+project facts / open questions — never chatter) into the **same** `_inbox/sessions/<project>/` captures code sessions write (`shared.write_inbox_capture`). So reflection (strip → clear → federate) + dreaming (playbook) process comms knowledge **unchanged**, and it reaches the company brain via the existing federation path. **The heartbeat stays reactive** (notify/draft) — capture is this separate, cadence-configurable feeder, not a heartbeat extension.

- **Channel selection = the shared `channels` registry** in brain-config (`company_brain_channel_registry.md` — the same access+routing primitive the company-brain chat skills, ClickUp `86ca5c6nz`, will read). Keyed `"<surface>:<id>"` (e.g. `"slack:C012345"`). The feeder ingests only entries that are `surface: slack`, `status: enabled`, `ingestion_mode ∈ {ingest-and-answer, digest-only}`, and that declare a `capture: {project, default_export}` routing block. Everything else (`disabled`/`ask-only`, unsupported surface, missing/invalid `capture`, surface/key mismatch, bad export target) is **fail-closed** (skipped + logged). The feeder reads only the *ingestion* subset of the registry; governance fields (`allowed_users`/`required_tier`/personas) belong to the chat-skills task.
- **Own watermark, never the heartbeat's.** `slack.since_last_run()` mutates the shared `slack-state.json` cursors the heartbeat owns — so the feeder uses a **stateless** `slack.fetch_channel_history()` (no state writes) + its own per-channel cursor in `.claude/data/state/comms-capture-state.json`. The two readers never consume each other's messages (the scope doc's "share the fetch later" stays deferred). The cursor advances over *everything scanned* (incl. NONE / sub-threshold / filtered-noise windows) so re-runs are no-ops; a distillation **failure holds the cursor** (retried next run).
- **Privacy:** scoping = the in-scope channel allowlist (personal/family channels simply aren't in the registry → never read) + `redaction.exclude_people` (default **true** → `scrub_excluded_entities`) + always-on `scrub_secrets`, all before the capture hits disk. Reflection re-applies the authoritative strip before any federation clear; the feeder's scrub is defense-in-depth (fail-open only on a missing `_excluded-people.md`). External message content enters the Haiku prompt via `sanitize.wrap_external`.
- **Source-dispatch seam:** `SOURCE_READERS` maps a surface → reader; **Slack is implemented**, and Gmail / WhatsApp / Telegram / meeting-transcript (Otter/Meet) become a reader + a `SUPPORTED_SURFACES` entry, no refactor. NB the heartbeat's Gmail read is **metadata-only** (no bodies) and reactive — extracting email knowledge needs its own body-fetching feeder on this seam, not the Slack feeder.
- **Config** (`brain_config.DEFAULTS`): `comms_capture.{enabled, cadence (default daily@22:00), hours, lookback_hours, min_messages}` are the feeder knobs; top-level `channels` is the shared registry (empty by default → the feeder is a clean no-op that never constructs a Slack client, so `enabled:true` is safe even with no token). `gen_schedules.py` emits `brunoosbrain-comms-capture.{service,timer}` (VPS, `OnCalendar` pinned to America/Sao_Paulo, `OnFailure=…-alert@%n`) + `com.bruno.brunos.comms-capture.plist` (Mac, `Disabled=true` for failover) as a 4th unit.
- **Monitoring (Track D):** `main()` wires `sync_common.make_reporter("comms-capture", …)` + `report_outcome` at the CLI boundary — status file `comms-capture-state.json`, healthcheck `BRUNOS_COMMS_CAPTURE_HEALTHCHECK_URL`, `OnFailure` backstop, and a `comms-capture` entry in `provision_healthchecks.SERVICE_CATALOG` (cron `0 22 * * *`). Dry-runs never report; `BRUNOS_DISABLE_REPORTING=1` disables; reporting never breaks the feeder. A success ping carries `{channels_selected, captures, channel_errors}`; it pings `/fail` + alerts **only when every configured channel failed** (missing token / total model outage) — a single transient channel error is logged with the cursor held, not alerted. NB the feeder's own per-channel **cursors** live in `comms-capture-cursors.json` (separate from the reporter's `-state.json`).
- **Tests:** `tests/test_comms_capture.py` (standalone; Haiku + Slack + reporter stubbed).

`CLAUDE_INVOKED_BY` value added: `comms-capture` (set before the SDK import).

## Deployment (Phase 9)

Two-host deployment: a **Hetzner CX21 ARM64 droplet at `49.13.165.23`, shared with Lisa**, hosts the always-on services (heartbeat, reflection, weekly review, news digest, Slack chat bot, vault git-sync, code git-sync) under a `brunoosbrain-*` systemd namespace; Mac keeps the same units installed as launchd plists with `Disabled=true` for one-command failover. Vault becomes its own private GitHub repo with a `concat-both` merge driver so daily-log appends survive bidirectional sync. Storage stays on **SQLite + sqlite-vec on both hosts** — the DB file (`.claude/data/state/memory.db`) is per-host, rebuilt from the synced vault on first run.

### Code-sync (pull-only, every 30 min)

`brunoosbrain-code-sync.timer` runs `.claude/scripts/code_sync.py` (`OnUnitActiveSec=30min`) in `/home/bruno/claude-second-brain` (via `uv run python`; `OnFailure=brunoosbrain-alert@%n.service`). It does a pull-only `git merge --ff-only origin/main` then **auto-recycles the slackbot iff the pull changed its in-process code** — paths matching `.claude/chat/`, `.claude/scripts/{shared,sanitize}.py`, or `.claude/hooks/session-start-context.py` (the daemon imports these at startup and Python doesn't hot-reload). Other changes need no restart: oneshot timers (heartbeat/reflect/etc.) re-exec each tick, and the bot's subprocess helpers (`query.py`, `memory_search.py`, `memory_flush.py`) are spawned fresh per call. The recycle uses `systemctl try-restart` (no-op if the unit is stopped for failover) via a scoped sudoers entry (`deploy/sudoers/brunoosbrain-codesync` → `/etc/sudoers.d/`, `bruno NOPASSWD: try-restart brunoosbrain-slackbot.service` only) and is best-effort (a missing sudoers logs a WARN, never fails the pull). The bot's graceful SIGTERM (flush + disconnect) + thread resume (`chat.db` session_ids) make the recycle lossless — worst-case bot-code staleness is now ≤30 min instead of "until manual restart". Pull-only (Mac is sole writer; VPS read-only). Log: `/var/log/brunoosbrain/code-sync.log`. Mac doesn't run this unit (it's the writer). **Remaining caveat:** `.env` is gitignored — new env vars introduced by code changes still need a manual append to VPS `.env`.

**Reliability + observability (2026-05-31):** `code_sync.py` replaced the old `deploy/bin/code-sync.sh`, sharing the `.claude/scripts/sync_common.py` runtime with `vault_sync.py` (one `SyncReporter` implementation so the two syncs' observability can't drift). Three failure layers, identical to vault-sync: a status file (`.claude/data/state/code-sync-state.json`), a rate-limited Slack alert to `#bruno_ops` (`BRUNOS_ALERT_CHANNEL`, ≤hourly while failing / on new error signature), and a healthchecks.io dead-man's-switch (`BRUNOS_CODESYNC_HEALTHCHECK_URL`, separate VPS-only check `brunos-code-sync-vps`; pinged on every success, `/fail` on failure) — plus the `OnFailure` alert unit as a backstop when the script dies before its own alert path runs. A non-blocking run-lock keeps 30-min ticks from stacking. Failure semantics never wedge the repo: **diverged** (consumer somehow has local commits → ff-only impossible) → alert + exit non-zero, NO auto-`reset --hard`, tree left intact, retry next tick; **dirty tree** → `git stash -u` aside + alert once, then ff proceeds (nothing discarded). Standalone tests in `tests/test_code_sync.py`. NB the installed `/etc/systemd/system/brunoosbrain-code-sync.service` is a symlink to the repo file, so unit edits go live on `git pull` — but a `.service` change still needs `sudo systemctl daemon-reload`.

### Host shape

- **VPS**: shared with Lisa. Bruno's namespace = user `bruno`, services `brunoosbrain-*`, log dir `/var/log/brunoosbrain/`, repo `/home/bruno/claude-second-brain`, vault `/home/bruno/BrunOS`. Lisa's namespace = `lisa` / `lisaosbrain-*` — never touch.
- **Mac**: failover-ready. Plists live at `~/Library/LaunchAgents/com.bruno.brunos.<svc>.plist`, all `Disabled=true` except `git-sync` and `inbox-rsync` (both dual-run safe). **macOS TCC gotcha:** the repo+vault sit under `~/Documents`, which a launchd agent can't read when it execs `/bin/bash` or a binary directly (`Operation not permitted`, exit 126). Both enabled units therefore run via `uv run python <shim>` (`git_sync.py`, `sync_inbox.py`); `~/.local/bin/uv` holds Full Disk Access (granted for codex-watcher) and it inherits to the git/rsync children. Any new Mac launchd unit must do the same.
- **uv path**: `/usr/local/bin/uv` on VPS (system-wide, Lisa's bootstrap); `/Users/brunobouwman/.local/bin/uv` on Mac (per-user). Don't conflate.

### Deploy artifacts (`deploy/`)

```
deploy/
  README.md                          operator runbook (read this first)
  bin/                               idempotent helpers (seed/bootstrap/sync/install/merge-driver) + git_sync.py / sync_inbox.py (uv launchd shims, TCC workaround) + sync_cleared_inbox.py / retire_local_inbox.py / consolidate_inbox_slugs.py (federation read-side)
  launchd/com.bruno.brunos.*.plist   Mac plists (Disabled=true except git-sync, inbox-rsync + codex-watcher; enabled units run via uv shims; inbox-retire ships disabled until dry-runs reviewed). Phase B added reflect-inbox / reflect-curate / dream (generated by gen_schedules.py; supersede the legacy reflection.plist).
  systemd/brunoosbrain-*             services + timers (slackbot daemon has no timer; slackbot-restart weekly recycle; federation-doctor + memory-doctor daily; slackbot-watchdog every 15 min). Phase B added brunoosbrain-{reflect-inbox,reflect-curate,dream}.{service,timer} (generated; supersede the legacy brunoosbrain-reflect unit).
  systemd/linosbrain-*               consumer/reflect/vault-sync + company chat units for the LinOS node
  systemd/brunoosbrain-linos-inbox-sync.{service,timer}
                                      Bruno-side cleared+scoped push into LinOS inbox mirror
  vault/{gitignore,gitattributes}    templates copied to BrunOS/.gitignore + .gitattributes at vault git-init
setup.sh                             repo-root idempotent venv bootstrap (uv sync)
```

### Vault git-sync + concat-both merge driver

Vault is a separate private GitHub repo (`brunobouwman/brunos-vault`). VPS+Mac both run `git-sync` (simonthum) every 2 min (the Mac via the `git_sync.py` uv shim — see the Host shape TCC note; Mac git-sync went live 2026-05-26, previously never ran). `Memory/daily/*.md` and `Memory/HABITS.md` use the `concat-both` merge driver (`deploy/bin/git-merge-concat`) so simultaneous appends from both hosts survive merge — at the cost of line order (driver sorts to compute the diff). The driver registration is per-clone (`deploy/bin/install-merge-driver.sh` runs `git config merge.concat-both.driver` inside the vault repo on each host). Sensitive paths excluded from the vault repo: `Memory/drafts/active/*` (recipient context), `Memory/personal/finance.md` (SOUL.md no-financial-data boundary), `.DS_Store`, `.obsidian/workspace*`, `.obsidian/cache`.

### Single-instance policy

Slack chat bot is **mandatory single-instance** — Slack Socket Mode is a fan-out broadcast, so duplicate clients post duplicate replies. Failover protocol: stop the VPS slackbot (`ssh brunoos sudo systemctl stop brunoosbrain-slackbot`) BEFORE bootstrapping the Mac plist. Heartbeat / reflect / weekly-review / news-digest are also **strongly recommended** single-instance: concat-both protects daily logs but `MEMORY.md` and `HABITS.md` writes can race.

### Snapshot cold-start on failover

`heartbeat-state.json` (snapshot for `_diff_snapshot`) lives in the code repo's `.claude/data/state/`, not in the vault git repo. On failover the new host has no prior snapshot, so the first tick treats everything as new and produces a noisy first-run delta. One-time cost; ignore.

### Logs

| Where | Path | View |
|-------|------|------|
| VPS (file) | `/var/log/brunoosbrain/<svc>.log` | `tail -f /var/log/brunoosbrain/<svc>.log` |
| VPS (journal) | systemd journal | `journalctl -u brunoosbrain-<svc> -f` |
| Mac | `~/Library/Logs/com.bruno.brunos.<svc>.log` | `tail -f ~/Library/Logs/com.bruno.brunos.<svc>.log` |

### OAuth refresh-token portability

Google refresh tokens bind to OAuth `client_id`, not the machine. `bootstrap_google_oauth.py` runs once on Mac (browser consent); resulting `google_token.json` is `scp`'d to VPS by `deploy/bin/sync-secrets.sh`. **Only the runtime token** is needed on VPS — `google_client_secrets.json` stays Mac-only. If the consent screen is in **Testing** mode, refresh tokens expire after 7 days; switch to **In Production / Self-Published** to make them durable.

### Coexistence with Lisa

Never `systemctl stop lisaosbrain-*`, never `DROP ROLE lisaosbrain`, never edit `/home/lisa/`. If memory pressure shows up on the shared CX21 (2 vCPU / 4 GB) at simultaneous :00/:30 ticks, stagger Bruno's heartbeat with `OnCalendar=*-*-* 08..22:15/30 America/Sao_Paulo`. Operator runbook in `deploy/README.md` has the full coexistence checklist + failover one-liner.

## Phase status

- [x] Phase 0 — Foundation prep (2026-05-02)
- [x] Phase 1 — Memory layer (vault seeded manually 2026-05-01; BOOTSTRAP.md skipped by design)
- [x] Phase 2 — Hooks (2026-05-02)
- [x] Phase 3 — Memory search (hybrid RAG) (2026-05-02)
- [x] Phase 4 — Integrations (Slack → GitHub → ClickUp → Gmail/Calendar → RSS) (2026-05-02)
- [x] Phase 5 — Skills (`brunos-vault`, `memory-search`, `news-digest`, `weekly-review`) (2026-05-02)
- [x] Phase 6 — Heartbeat + Reflection + Drafts + Habits (2026-05-03)
- [x] Phase 7 — Slack chat bot (2026-05-03)
- [x] Phase 8 — Security hardening (4 layers) (2026-05-03)
- [x] Phase 9 — Deployment (VPS systemd primary on `LinOS`/49.13.165.23; Mac launchd installed-but-disabled for failover; vault git-sync to `brunobouwman/brunOS-Vault`) (2026-05-19)
- [x] BaaS Track C — Org/onboarding layer (access policy, excluded-entities gate, `validate_consumer_read`) (2026-05-31)
- [x] BaaS Track B — Deterministic security gate (L1 structural separation, L2 secret/PII scrub + fail-closed, L4 canary CI gate, L6 federation-doctor) (2026-05-31)
- [x] BaaS Track A — LinOS consumer loop, CODE-COMPLETE (2026-06-02, PRs #3/#5/#7/#8/#9;
  ClickUp: testing): `linos_consumer.py` + ack manifest + `linosbrain-*` systemd units,
  cleared-inbox transport (`sync_cleared_inbox.py`), reflect inbox batching +
  watermark/quarantine fixes, slug canonicalization at the write boundary (+ migration),
  Mac producer-side retirement (`retire_local_inbox.py`, launchd installed disabled).
- [ ] Phase C.5 — LinOS node DEPLOY + end-to-end verification: node provisioned,
  identity seeded, vault sync live, Bruno-side cleared push live, consumer
  dogfood/import complete, `linosbrain-consumer.timer` live, and stage-0
  founder-only Slack chat live behind the deterministic channel registry.
  Company-brain reflection/dreaming is wired via the reusable
  `company_brain_reflect.py` routine for LinOS dogfood.
  The VPS-side F2 retirement job + its ack RETURN leg
  (`retire_vps_inbox.py`, `sync_acks.py`/`linosbrain-ack-sync`) are built and the
  loop is verified closed (acks flow → retire dry-run clean: `0 fallback`).
  Remaining: enable `brunoosbrain-inbox-retire-vps` (final flip), run any
  remaining slug migration review, enable Mac inbox-retire after dry-run review,
  and productize broad multi-channel company chat + company ingestion/write
  pipeline.

## Reference

- Build PRD: `.agent/plans/second-brain-prd.md` (also vault-resident at `BrunOS/PRD.md`).
- Vault README: `$BRUNOS_VAULT_PATH/README.md`.
- Memory layout (canonical): `$BRUNOS_VAULT_PATH/Memory/_README.md`.
