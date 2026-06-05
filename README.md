# BrunOS

**A personal second brain that runs itself.**

BrunOS is a long-running [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) process backed by an Obsidian vault. It watches Bruno's Slack, GitHub, ClickUp, Gmail, Calendar, and RSS feeds; drafts replies in his voice; tracks daily habits; consolidates what it learns into durable memory; and is reachable as a chat bot from anywhere via Slack.

It operates at **Assistant proactivity** — it does the safe, reversible work on its own (logging, drafting, organizing) and asks before anything outward-facing or irreversible. The one autonomous send surface is Slack-on-@mention; email and every other external channel stay draft-only.

> This is the **code** repository. The vault it operates on (`BrunOS/`) is a *separate* private git repo synced between the Mac and the VPS — it is gitignored here and never tracked from this repo.

---

## What it does

| Capability | How |
|---|---|
| **Monitors** Slack / GitHub / ClickUp / Gmail / Calendar / RSS | A 30-min heartbeat gathers all sources in parallel, diffs against the last snapshot, and only wakes the reasoning agent when something changed. |
| **Drafts replies** in Bruno's voice | Retrieves tone from a corpus of past sent messages (hybrid RAG), routes language (Portuguese for Brazilian recipients, else English), and writes to a draft lifecycle (`active → sent → expired`). |
| **Remembers** | Nightly reflection distils daily logs + cross-repo session captures into a ≤5 KB durable `MEMORY.md`, per-project continuity notes, and a privacy-gated federation inbox. |
| **Tracks habits** | Five daily pillars, auto-reset each morning, with signals inferred from the day's activity. |
| **Chats** | A Socket-Mode Slack daemon turns any DM or channel @mention into a stateful Claude session with full vault context. |
| **Produces** | Daily AI-news digest and a Sunday weekly-review draft. |

---

## Architecture at a glance

```
                         ┌─────────────────────────────────────────┐
   Slack  GitHub         │  .claude/scripts/query.py  (dispatcher)  │
   ClickUp  Gmail   ───▶ │  → integrations/{slack,github,clickup,   │ ─── deterministic API clients
   Calendar  RSS         │     gmail,calendar,rss}.py               │     (tokens never reach the LLM)
                         └─────────────────────────────────────────┘
                                          │
              ┌───────────────────────────┼───────────────────────────┐
              ▼                            ▼                           ▼
       heartbeat.py                 memory_reflect.py            chat/bot.py
   (every 30 min, 08–22 BRT)      (nightly, 2 stages)        (always-on Slack daemon)
   guardrail → reasoning agent    daily-log + inbox stages    per-thread SDK sessions
              │                            │                           │
              └──────────────┬─────────────┴───────────────────────────┘
                             ▼
                    BrunOS/Memory/  (the Obsidian vault — separate git repo)
                    SOUL · USER · MEMORY · HEARTBEAT · HABITS · daily/ · drafts/ · projects/ …
                             │
                  Hybrid RAG: FastEmbed (BGE-small) + sqlite-vec + FTS5, merged via RRF
```

Everything that touches third-party text passes through `sanitize.py` (`wrap_external`) before entering a prompt, and four security layers guard the long-running surfaces (credential-path blocking, sanitization, a Haiku pre-flight guardrail, and dangerous-bash command filtering).

---

## Repository layout

```
.claude/
  scripts/          integrations, heartbeat, reflection, RAG index/search, sync, sanitize
  skills/           brunos-vault · memory-search · news-digest · weekly-review
  chat/             Slack chat-bot daemon (Socket Mode)
  hooks/            SessionStart/End, secret-blocking, dangerous-bash, soul-protection
  settings.json     hook wiring
  data/             local state + SQLite DBs (gitignored)
deploy/             systemd units (VPS) + launchd plists (Mac) + operator runbook
tests/              pytest suites
BrunOS/             the vault — gitignored here, its own private repo
CLAUDE.md           the canonical, deep technical reference (read this for internals)
AGENTS.md           cross-repo agent conventions
pyproject.toml      deps, managed by uv
```

---

## Setup

Requires **Python 3.13** and [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. Install dependencies (creates .venv from uv.lock)
uv sync

# 2. Configure environment
cp .claude/.env.example .claude/.env
#    then fill in: BRUNOS_VAULT_PATH, Slack/GitHub/ClickUp tokens, Google OAuth, etc.

# 3. One-time Google OAuth (Gmail + Calendar — opens a browser)
uv run python .claude/scripts/bootstrap_google_oauth.py

# 4. Build the memory index from the vault
uv run python .claude/scripts/memory_index.py --full
```

The vault path is set via `BRUNOS_VAULT_PATH` in `.claude/.env` — **never hardcode it**; every script resolves it through `shared.vault_path()` so jobs work under launchd/systemd with arbitrary working directories.

---

## Common commands

```bash
# Query any integration through the single dispatcher
uv run python .claude/scripts/query.py slack mentions
uv run python .claude/scripts/query.py clickup overdue --workspace vertik
uv run python .claude/scripts/query.py calendar today

# Search the vault by meaning (hybrid RAG)
uv run python .claude/scripts/memory_search.py "what did I decide about pricing" --k 10

# Run the proactive loops manually (Phase 9 schedules these)
uv run python .claude/scripts/heartbeat.py --dry-run
uv run python .claude/scripts/memory_reflect.py --dry-run

# Skills
uv run python .claude/skills/news-digest/scripts/digest.py
uv run python .claude/skills/weekly-review/scripts/aggregate_week.py

# Chat bot
uv run python .claude/chat/bot.py --smoke-test   # validate token, exit 0
uv run python .claude/chat/bot.py                # foreground daemon
```

---

## Deployment

Two-host setup: a Hetzner ARM VPS runs the always-on services under a `brunoosbrain-*` systemd namespace (heartbeat, reflection, weekly review, news digest, chat bot, vault + code git-sync); the Mac keeps the same units installed as `Disabled=true` launchd plists for one-command failover. Vault state syncs Mac↔VPS over git with a `concat-both` merge driver so daily-log appends from both hosts survive merges.

The operator runbook — including the failover one-liner and the coexistence checklist for the VPS shared with Lisa — lives in [`deploy/README.md`](deploy/README.md).

---

## Conventions

- **Timezone**: America/São_Paulo (GMT-3). Dates `YYYY-MM-DD`; timestamps RFC3339 with explicit `-03:00`.
- **Every vault file** carries YAML frontmatter (`type / created / updated / tags / status`).
- **Sources of truth**: ClickUp is the execution layer (tasks with status); Obsidian is the thinking layer (decisions, context, lessons). Don't duplicate between them.
- **No secrets in the vault, ever.** `personal/finance.md` is off-limits to the agent.
- **Internal memory is always English**; only outbound drafts are language-routed.

---

## Further reading

- **[`CLAUDE.md`](CLAUDE.md)** — the canonical technical reference: every phase, every script, every gotcha. Start here for internals.
- **[`deploy/README.md`](deploy/README.md)** — operator runbook for the VPS + Mac deployment.
- **`BrunOS/PRD.md`** (vault-resident) — the build PRD.
