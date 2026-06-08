# <BrainName> — Project Guide for Claude Code Sessions

> **This is the ENGINE template for a brain's `CLAUDE.md` (the instance layer).** `CLAUDE.md`
> itself is brain-local + gitignored — generated per brain by `create-second-brain-prd` (from
> the onboarding spec) and placed by `bootstrap-brain`. Fill every `<…>` placeholder; add the
> brain's own sections (projects, phases, deployment specifics) below the universal blocks.
> Universal conventions below apply to EVERY brain (they describe the engine); change them only
> if the engine changes. See `.agent/plans/engine-instance-extraction.md`.

`<BrainName>` is a `<individual personal | company institutional>` second brain — a Claude
Agent SDK process backed by the vault at `<BRAIN_VAULT_PATH>`. `<one-line purpose from the
onboarding spec: who it serves + what it does>`. Operates at `<proactivity level>` proactivity.

## Vault location

Set `<BRAIN>_VAULT_PATH` in `.claude/.env`. All scripts resolve via `shared.vault_path()` —
never hardcode a relative path (scripts run from launchd/systemd with arbitrary cwd).

## Env file location

`.claude/.env` (gitignored). `shared.load_env()` loads it; `shared.vault_path()` reads the
vault path from there. Committed example: `.claude/.env.example`.

## Engine vs instance (read this)

This brain = **engine clone + instance layer.** The **engine** (`.claude/scripts`, `hooks`,
`chat`, shared skills, `tests`, `deploy` templates, `pyproject.toml`) is pulled from the
canonical engine repo (`<engine-channel: main for the canary brain, stable for everyone else>`)
— never edit it to customize this brain. The **instance** (this `CLAUDE.md`, `.claude/.env`,
`brain-config.json`, the vault, the brain-local skills `vault-structure` + `memory-search`) is
this brain's own — generated/owned locally, never pulled.

## Conventions (universal)

- Timezone: `<IANA tz, e.g. America/Sao_Paulo>`. Date: `YYYY-MM-DD`; Timestamp: RFC3339 with
  explicit offset.
- Checkbox: `- [ ]` / `- [x]`.
- Language routing: `<routing rule from spec>`. Internal memory ALWAYS English.
- **No secrets in the vault, ever.**
- Every file under the vault's `Memory/` carries YAML frontmatter
  (`type / created / updated / tags (block list) / status`).

## Proactivity: `<level>`

- **Allowed without asking:** `<from spec — e.g. append daily log, draft replies, organize vault>`.
- **Ask first:** `<from spec — e.g. external task/issue creation, anything touching .env/keys>`.
- **NEVER without explicit approval:** `<from spec security boundaries — e.g. send email, post
  external/social, access financial data, delete anything>`. `<the single autonomous-send
  carve-out, if any>`.

## Recursion guard (universal)

Every Agent SDK script MUST set `os.environ["CLAUDE_INVOKED_BY"] = "<purpose>"` BEFORE
`import claude_agent_sdk`. Without this, SessionEnd-triggered flushes infinite-loop.

## `setting_sources` policy (universal)

Every `ClaudeAgentOptions(...)` MUST pass `setting_sources` explicitly (`None`, `["project"]`,
or `["user","project"]`). Never rely on the default. `["project"]` loads `CLAUDE.md`, the four
`settings.json` security hooks, and the skills.

## Build commands (engine)

```bash
uv sync                                              # install/sync deps
uv run python .claude/scripts/memory_index.py [--full]
uv run python .claude/scripts/memory_search.py "<query>"
uv run python .claude/scripts/query.py <integration> <subcmd>   # slack/github/clickup/gmail/calendar/rss
uv run python .claude/scripts/heartbeat.py [--dry-run]          # individual role
uv run python .claude/scripts/memory_reflect.py [--dry-run]
uv run python .claude/scripts/memory_dream.py [--dry-run]
# Company role adds: company_brain_reflect.py, linos_consumer.py, the company personas.
```
`<add this brain's extra commands as features are enabled>`

## Security (universal — 5 layers, installed by the engine)

`.claude/settings.json` wires four PreToolUse hooks (order: `block-secrets` → `dangerous-bash`
→ `protect-soul` → `path-boundary`) + `sanitize.py` is the data-boundary. For an autonomous
daemon (`CLAUDE_INVOKED_BY ∈ {chat, heartbeat}`) the hooks ARE the enforcement — they fire
before the permission check in any `permission_mode`. Do not weaken these.

## Skills

- **Engine (shared, pulled):** `diagnose-brain`, `bootstrap-brain`, `create-second-brain-prd`,
  `news-digest`, `weekly-review`, `dev-task`, `code-review`, the `company-*` personas (company
  role), `skill-creator`.
- **Brain-local (instance, generated at bootstrap):** `vault-structure` (this brain's folder
  layout / frontmatter / routing) + `memory-search` (this brain's retrieval cheat-sheet).

## `<BrainName>`-specific sections (fill from the spec + as the brain evolves)

- `<vault folder taxonomy / key paths inside the vault>`
- `<role + federation: singleton / producer→<company> / consumer; the federation specifics>`
- `<deployment: hosts, units, cadence, failover>`
- `<integrations enabled + their quirks>`
- `<projects / phase history / anything this brain needs its agent to know>`

## Reference

- Engine architecture + the onboarding/diagnosis/bootstrap loop: the engine repo docs +
  `.agent/plans/engine-instance-extraction.md`.
- Vault layout (canonical): `<BRAIN_VAULT_PATH>/Memory/_README.md`.
