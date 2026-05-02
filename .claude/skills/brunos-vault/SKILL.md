---
name: brunos-vault
description: Vault navigation skill for BrunOS. Use whenever the agent reads from or writes to BrunOS/Memory/ — drafts, daily logs, projects, clients, goals, content, team, research, news-digest, meetings, personal. Teaches folder semantics, the YAML frontmatter spec (type/created/updated/tags/status — tags as block list), checkbox syntax (- [ ] / - [x]), language routing (Brazilian recipient → Portuguese; internal memory → English), draft lifecycle (active → sent → expired), Slack autonomous-send carve-out (@mention only), the ClickUp-vs-Obsidian boundary, and the personal/finance.md off-limits rule. Triggers on "where does X live", "draft a reply", "log this to today", "update HABITS", "weekly review", any task that reads or writes vault paths.
---

# BrunOS Vault Skill

The vault lives at `$BRUNOS_VAULT_PATH` (resolve via `shared.vault_path()` — never hardcode). Below is what every read/write into `Memory/` must respect.

## Folder map

| Path | What lives there |
|---|---|
| `Memory/daily/YYYY-MM-DD.md` | Append-only daily log. Reflection promotes from here into MEMORY/projects/research. |
| `Memory/drafts/active/` | Reply drafts awaiting Bruno. Sensitive recipient context — gitignored from vault repo (Phase 9). |
| `Memory/drafts/sent/` | Voice corpus — Bruno's actual sent replies. RAG source for tone matching. |
| `Memory/drafts/expired/` | Drafts auto-moved after 24h with no action. |
| `Memory/meetings/` | `YYYY-MM-DD-slug.md` meeting notes. |
| `Memory/projects/` | Vertik (contract), Protostack-related personal context, AI mastery course. |
| `Memory/clients/` | Protostack's future labs/clinics (NOT Vertik's clients — those belong to Vertik). |
| `Memory/research/` | AI engineering learning notes. |
| `Memory/goals/` | `this_week.md`, `this_month.md`, `personal_vision.md`, weekly reviews `YYYY-Www-review.md`. |
| `Memory/content/` | Content ideas + drafts (LinkedIn, YouTube, X, Shorts). |
| `Memory/team/` | Lisa, contractors, partners. |
| `Memory/personal/` | PRD extension. **`personal/finance.md` is OFF-LIMITS** (matches SOUL.md no-financial-data boundary). |
| `Memory/news-digest/` | Daily AI-engineering digests, `YYYY-MM-DD.md`. |

## Top-level singletons (loaded every session)

- `SOUL.md` — agent identity. **Write-protected from reflection.** Proposed changes go into the daily log under "SUGGESTED SOUL CHANGES (REVIEW MANUALLY)".
- `USER.md` — Bruno's profile, stack, drafting criteria, voice rules.
- `MEMORY.md` — durable decisions / active state. **Hard cap 5KB.** Growth via reflection only — Bruno doesn't hand-edit.
- `HEARTBEAT.md` — what the heartbeat monitors each tick.
- `HABITS.md` — 5 daily pillars, auto-detection rules, daily reset 08:00 BRT.
- `sources_of_truth.md` — ClickUp ↔ Obsidian convention reference.

`BOOTSTRAP.md` is absent by design — vault was bootstrapped manually 2026-05-01.

## YAML frontmatter spec — every file, no exceptions

```yaml
---
type: meeting | project | client | research | goal | content | team | draft | digest | personal | daily | system | reference
created: 2026-05-02T09:00-03:00
updated: 2026-05-02T09:00-03:00
tags:
  - vertik
  - protostack
status: active | archived | done
---
```

**Rules:**
- `tags` is a YAML **block list**, NOT inline `[tag1, tag2]`. Obsidian rewrites inline arrays to block on save and the diff churn is annoying.
- `updated:` is auto-stamped by `shared.atomic_write()` on every agent write to a `.md` file. Never manually touch it. Hand-edits in Obsidian don't refresh it — treat as "last agent touch", not "last edit of any kind".
- Timestamps: RFC3339 with explicit `-03:00` offset. Never UTC, never naive.

**Type assignments for non-obvious files:**
- `SOUL.md`, `USER.md`, `MEMORY.md`, `HEARTBEAT.md`, `HABITS.md` → `type: system`
- `sources_of_truth.md` → `type: reference`
- `daily/YYYY-MM-DD.md` → `type: daily`, `tags: [daily]`
- Drafts → `type: draft` plus extended fields (`source_id`, `recipient`, `subject`, `context`, `language`)

## Checkbox syntax

`- [ ]` for open, `- [x]` for done. Nothing else (no `[~]`, no `[-]`).

## Language routing

- **Brazilian recipient** (PT-BR speaker or works in BR) → **Portuguese drafts.**
- **Anyone else** → **English drafts.**
- **Internal vault notes, MEMORY.md, daily logs** → **always English**, regardless of recipient.

Tone: same as English (direct, short, confident) but slightly warmer Brazilian register in greetings: "Olá Marcus, tudo bem?" not "Marcus,". Sign-off "Abraço" for casual; "Atenciosamente" for first contact / formal.

## Draft lifecycle

```
drafts/active/   →  drafts/sent/    (when Bruno actually replied — capture his real reply text)
                →  drafts/expired/  (after 24h with no action)
```

`drafts/sent/` is the voice corpus. Query it via `memory_search.py --path-prefix drafts/sent` before drafting a new reply for tone matching.

## Sources of truth — ClickUp vs Obsidian

- **ClickUp = execution.** Anything with a state that changes (todo → doing → done): tasks, statuses, due dates, dependencies.
- **Obsidian (this vault) = thinking.** Why a project exists, decisions, lessons, persistent context.
- **Don't duplicate.** A bug fix is a ClickUp task; the lesson learned from fixing it is an Obsidian note.

Full reference: `Memory/sources_of_truth.md`.

## Boundaries — never cross these

- **`personal/finance.md` is OFF-LIMITS.** No reads, no writes. Matches SOUL.md no-financial-data boundary. Files matching `*finance*`, `*invoice*`, `*billing*`, `*payment*` follow the same rule unless Bruno hands them over in chat.
- **No autonomous send beyond Slack @mention.** Email, GitHub/ClickUp comments, X/Twitter, all other surfaces: drafting only, Bruno sends. Slack carve-out is the **only** autonomous send surface — and only when the bot is @mentioned, in Bruno's private workspace.
- **No deletes.** Soft-delete equivalents (move to `expired/`, archive, `status: done`) are fine.
- **No secrets in vault.** Ever. No `.env` content, no tokens, no PEM keys.
- **SOUL.md is write-protected from reflection.** Propose changes via the daily log.

## Writing files into the vault

Always use `shared.atomic_write(path, content)` from `.claude/scripts/shared.py`:
- Stamps `updated:` automatically for `.md` files.
- Atomic via `os.replace` — safe under crash.
- Resolves paths via `shared.vault_path()` — never hardcode the relative `BrunOS/`.

## What lives here vs LinOS

- **BrunOS (this vault)** — Bruno's personal context: Vertik, personal finance, journaling, personal goals.
- **LinOS** (separate joint vault) — joint with Lisa: Protostack methodology, joint financial plan, city move, joint goals.

Default: personal first. Things only flow to LinOS when genuinely shared.
