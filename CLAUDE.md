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

# Heartbeat + reflection (Phase 6) — manual CLI; Phase 9 wires the scheduler:
uv run python .claude/scripts/heartbeat.py [--dry-run] [--no-agent] [--force]
uv run python .claude/scripts/memory_reflect.py [--dry-run]

# Phase 7 — Slack chat bot:
uv run python .claude/chat/bot.py --smoke-test     # connect + auth.test, exit 0
uv run python .claude/chat/bot.py                  # foreground daemon (Ctrl+C to stop)
```

Hooks in `.claude/settings.json` invoke scripts via `uv run python ...` so they pick up the project's `.venv` regardless of cwd or whether the venv is activated.

## Memory search (Phase 3)

Embedding model: `BAAI/bge-small-en-v1.5` via FastEmbed (384-dim, asymmetric — `passage_embed` for indexing, `query_embed` for retrieval). Cache: `.claude/data/fastembed_cache/`. DB: `.claude/data/state/memory.db` (SQLite + sqlite-vec + FTS5; same engine on Mac and VPS — each host keeps its own index, rebuilt from the synced vault). Hybrid retrieval merges vector top-k×3 + FTS top-k×3 via RRF (k=60). The indexer excludes `Memory/personal/finance.md` per the SOUL.md no-financial-data boundary.

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
4. Drafts hygiene (`drafts.expire_old_drafts` — moves >24h-old drafts to `drafts/expired/`; `drafts.capture_sent_replies` is a Phase 6.5 stub) + habits prep (`habits.reset_for_today_if_needed` archives yesterday's "## Today" to "## History" and rebuilds a fresh checklist; `habits.detect_signals` computes per-pillar booleans from the snapshot diff).
5. If delta is empty AND no habits-reset AND no drafts expired → fast-path: append a one-line tick to today's daily log + `_notify` "no changes" + exit. Otherwise: build sanitized `delta_text` (every external payload through `sanitize.wrap_external`) → Haiku 4.5 guardrail (`allowed_tools=[]`, `setting_sources=None`, `max_turns=1`, default-deny on parse failure) → on `pass`/`suspicious`, Sonnet 4.6 main agent (`allowed_tools=["Read","Write","Edit","Bash"]`, `setting_sources=["project"]`, `max_turns=15`) → osascript notify.

The main agent's tools include Bash but the system prompt forbids invoking `query.py slack send` or any external curl. Phase 8's `dangerous-bash.py` hardens this; Phase 6 ships honor-system + tools-whitelist.

CLI flags: `--dry-run` (print stages + would-be agent prompt; skip SDK calls + vault writes + notify); `--no-agent` (run deterministic stages, skip SDK calls); `--force` (bypass empty-delta fast-path).

### `memory_reflect.py` (daily 08:00 BRT in Phase 9, before heartbeat)

Single Sonnet 4.6 call (`allowed_tools=[]`, `setting_sources=None`, `max_turns=1`). Reads yesterday's daily log + current MEMORY.md; emits JSON of `[{type, text, promote}]` per item; deterministic Python applies promotions to the right MEMORY.md section (decision → "Key durable decisions", lesson → "Lessons", fact → "Tax & financial structure", status → "Active projects"). If MEMORY.md > 5KB after append, a SECOND Sonnet call compacts older entries first (aborts apply if shrink > 50%). SOUL.md changes go to today's daily log under "## SUGGESTED SOUL CHANGES (REVIEW MANUALLY)" — never directly written. Idempotent via `.claude/data/state/last_reflection.json`.

`protect-soul.py` (PreToolUse `Edit|Write`) is belt-and-suspenders: it blocks `BrunOS/Memory/SOUL.md` edits when `CLAUDE_INVOKED_BY=reflection`. Reflection itself uses no tools, so the hook is defensive against future agent surfaces.

`CLAUDE_INVOKED_BY` values introduced in this phase: `heartbeat`, `reflection`. Each script sets it BEFORE importing `claude_agent_sdk` (recursion-safe).

`sanitize.py` provides `wrap_external`, `clean_external`, `TRUST_BOUNDARY_INSTRUCTION`, regex injection-marker stripping, base64 blob redaction, and markdown/XML escaping outside fenced code blocks.

### Drafts + habits

`drafts.py` handles deterministic lifecycle: `expire_old_drafts(now)` moves >24h-old drafts from `drafts/active/` to `drafts/expired/` (flips `status: expired`). `capture_sent_replies` is a Phase 6.5 stub. Voice corpus retrieval uses `memory_search.py --path-prefix drafts/sent`. Filename: `YYYY-MM-DD_<source>_<recipient-slug>_<short-hash>.md` — same `(source, source_id)` always hashes to the same filename so the same item never produces two drafts.

`habits.py` handles the 08:00 BRT reset (deterministic — archive yesterday's "Today" to History, create fresh checklist) + signal detection (per-pillar boolean from snapshot deltas). The HEARTBEAT AGENT applies HABITS.md check-marks via the Edit tool — `habits.py` only computes signals.

## Slack chat bot (Phase 7)

Long-running daemon at `.claude/chat/bot.py` that turns Bruno's personal Slack workspace into a remote chat surface for BrunOS. Connects via Socket Mode (`AsyncApp` + `AsyncSocketModeHandler` from `slack_bolt`), listens for `message.im` events, and routes each Slack thread to a stateful `ClaudeSDKClient` keyed on the thread root `ts`. Replies post in-thread via Bolt's `say()`.

- **Entry**: `uv run python .claude/chat/bot.py` (foreground); `--smoke-test` validates the bot token + prints `bot_user_id` and exits 0.
- **Recursion guard**: `CLAUDE_INVOKED_BY=chat` is set BEFORE any SDK import (skips SessionEnd flush + PreCompact hooks for child sessions).
- **Tools / setting_sources**: every options block uses `allowed_tools=["Read","Write","Edit","Bash"]` and `setting_sources=["project"]` so each session loads `CLAUDE.md` + the four Phase 5 skills (`brunos-vault`, `memory-search`, `news-digest`, `weekly-review`). The Bash tool lets the bot shell out to `query.py` (Phase 4 dispatcher) and `memory_search.py` (Phase 3) — no integration logic is duplicated in the bot.
- **System prompt**: built ONCE at startup via `chat.system_prompt.build_chat_system_prompt()` — composes a chat-mode preamble (Slack mrkdwn rules, carve-out reminder) plus the canonical vault block (`hooks.session-start-context.build_context()` reused via `importlib`). Vault edits during the daemon's run aren't reflected until restart — acceptable trade-off vs ~6 file reads per message.
- **Slack send carve-out**: this is the **only** autonomous-send surface in BrunOS. SOUL.md prohibits sending elsewhere (email, GitHub/ClickUp comments, X, etc. all stay draft-only).
- **Surfaces**: two Bolt event handlers wired in `slack_adapter.py:register()`:
  - `@app.event("message")` filters to `channel_type=im` for DMs (no @mention needed).
  - `@app.event("app_mention")` handles channel @mentions; the `<@bot_user_id>` self-mention is stripped from `event["text"]` before being sent to the SDK.
  Channel UX: every continuation requires another @mention (Slack does NOT deliver `app_mention` for follow-up replies in the same thread, and we don't subscribe to `message.channels` because it's a fire hose). Bare `<@brunos>` with no instruction posts a "Yes? Mention me with a question or instruction." nudge.
- **Session keying**: `f"{channel_id}:{thread_root_ts}"`, so DMs (`D…:ts`) and channel threads (`C…:ts`) never collide and the same channel can host multiple parallel threads. The Slack `thread_ts` returned to `say()` is just the bare `ts` portion.
- **Slack app config (one-time)**: bot scopes `chat:write` + `app_mentions:read`; Event Subscriptions enabled with **only** `message.im` and `app_mention` under Bot Events; Socket Mode enabled with an App-Level Token (`xapp-...`) holding `connections:write`. Reinstall the app after scope changes. Invite the bot into channels where you want to @mention it (`/invite @brunos`). Documented in `.claude/.env.example`.
- **State**: `bot_user_id` is merged into `.claude/data/state/slack-state.json` (shared with Phase 4 — channels map untouched). Per-thread index lives in `.claude/data/state/chat.db` (SQLite, `chat_threads(thread_key, created_at, last_message_at)`).
- **Shutdown**: SIGINT/SIGTERM trigger `session_manager.close_all()` + `handler.close_async()` so all SDK sessions disconnect cleanly. Daemon restart starts each thread fresh (MVP: no replay-on-resume).
- **Phase 6/7 boundary**: chat bot owns DMs + channel @mentions in real-time (Socket Mode push). `heartbeat._gather()` calls `_split_chat_bot_handled()` after `slack.since_last_run()`: drops messages with `<@bot_uid>` (chat bot's `app_mention` handler owns them) and drops DMs where `slack.get_thread()` shows a bot reply with `ts > message.ts`. Unreplied DMs stay actionable — that's the catch-up safety net for Phase 7 downtime. Snapshot + daily-log counts use `slack_msgs_all` (raw haul) so reflection still sees full activity, even DMs the bot handled. Tick entry format: `Slack: <total> new (<handled by chat bot>, <need attention>)`.

## Security (Phase 8)

Four independent layers guard the long-running agent surfaces:

1. **Credential protection** — `.claude/hooks/block-secrets.py` runs as a PreToolUse hook before file reads/writes/searches and Bash. It blocks credential/private paths (`.env*`, keys, OAuth tokens, `.ssh/`, `.aws/`, `secrets/`, `private/`) and finance/invoice/billing/payment files, plus Bash env-exfil commands like `cat .env`, `printenv`, `env`, `os.environ`, and `process.env`.
2. **Sanitization** — `.claude/scripts/sanitize.py` is the data-boundary source of truth. `wrap_external` strips injection markers via `_INJECTION_PATTERNS`, redacts large base64-looking blobs, escapes XML/markdown control characters outside fenced code, escapes tag attributes, then wraps third-party content in `<external_data ...>`.
3. **Semantic guardrail** — `heartbeat.py` runs the Haiku 4.5 pre-flight check before the main heartbeat agent (`allowed_tools=[]`, `setting_sources=None`, `max_turns=1`). Verdict parse failures default to `fail`.
4. **Command guardrails** — `.claude/hooks/dangerous-bash.py` runs as a Bash-only PreToolUse hook. Patterns live in `DANGEROUS_BASH_PATTERNS` inside `.claude/scripts/shared.py` and cover destructive filesystem commands, privilege escalation, outbound curl/wget/netcat-style exfil, package installs, destructive git commands, and process/system kills.

Hook order in `.claude/settings.json`: `block-secrets.py` first, `dangerous-bash.py` second, `protect-soul.py` last. Hook input is JSON on stdin. `block-secrets.py` and `protect-soul.py` soft-block with `{"decision":"block","reason":"..."}` on stdout; `dangerous-bash.py` hard-blocks with exit 2 and stderr.

## Deployment (Phase 9)

Two-host deployment: a **Hetzner CX21 ARM64 droplet at `49.13.165.23`, shared with Lisa**, hosts the always-on services (heartbeat, reflection, weekly review, news digest, Slack chat bot, vault git-sync) under a `brunoosbrain-*` systemd namespace; Mac keeps the same units installed as launchd plists with `Disabled=true` for one-command failover. Vault becomes its own private GitHub repo with a `concat-both` merge driver so daily-log appends survive bidirectional sync. Storage stays on **SQLite + sqlite-vec on both hosts** — the DB file (`.claude/data/state/memory.db`) is per-host, rebuilt from the synced vault on first run.

### Host shape

- **VPS**: shared with Lisa. Bruno's namespace = user `bruno`, services `brunoosbrain-*`, log dir `/var/log/brunoosbrain/`, repo `/home/bruno/claude-second-brain`, vault `/home/bruno/BrunOS`. Lisa's namespace = `lisa` / `lisaosbrain-*` — never touch.
- **Mac**: failover-ready. Plists live at `~/Library/LaunchAgents/com.bruno.brunos.<svc>.plist`, all `Disabled=true` except `git-sync` (read consumer, dual-run safe).
- **uv path**: `/usr/local/bin/uv` on VPS (system-wide, Lisa's bootstrap); `/Users/brunobouwman/.local/bin/uv` on Mac (per-user). Don't conflate.

### Deploy artifacts (`deploy/`)

```
deploy/
  README.md                          operator runbook (read this first)
  bin/                               idempotent helpers (seed/bootstrap/sync/install/merge-driver)
  launchd/com.bruno.brunos.*.plist   6 Mac plists (Disabled=true except git-sync)
  systemd/brunoosbrain-*             6 services + 5 timers (slackbot has no timer)
  vault/{gitignore,gitattributes}    templates copied to BrunOS/.gitignore + .gitattributes at vault git-init
setup.sh                             repo-root idempotent venv bootstrap (uv sync)
```

### Vault git-sync + concat-both merge driver

Vault is a separate private GitHub repo (`brunobouwman/brunos-vault`). VPS+Mac both run `git-sync` (simonthum) every 2 min. `Memory/daily/*.md` and `Memory/HABITS.md` use the `concat-both` merge driver (`deploy/bin/git-merge-concat`) so simultaneous appends from both hosts survive merge — at the cost of line order (driver sorts to compute the diff). The driver registration is per-clone (`deploy/bin/install-merge-driver.sh` runs `git config merge.concat-both.driver` inside the vault repo on each host). Sensitive paths excluded from the vault repo: `Memory/drafts/active/*` (recipient context), `Memory/personal/finance.md` (SOUL.md no-financial-data boundary), `.DS_Store`, `.obsidian/workspace*`, `.obsidian/cache`.

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
- [ ] Phase 9 — Deployment (Mac launchd + VPS systemd + vault git-sync)

## Reference

- Build PRD: `.agent/plans/second-brain-prd.md` (also vault-resident at `BrunOS/PRD.md`).
- Vault README: `$BRUNOS_VAULT_PATH/README.md`.
- Memory layout (canonical): `$BRUNOS_VAULT_PATH/Memory/_README.md`.
