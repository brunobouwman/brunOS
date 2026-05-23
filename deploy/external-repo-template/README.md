# External-repo capture template (Phase A)

Drop the contents of this directory into any project repo to route Claude Code session captures into BrunOS's per-project inbox at `BrunOS/Memory/_inbox/sessions/<project>/` — and to inject that project's accumulated memory back at the start of each new session.

## What you get

- **SessionStart** — at session start (and resume), `session-start-project.py` injects, in order: **SOUL.md + USER.md** (the agent's identity and Bruno's profile — always, so a work session still knows who it is and who it works for), then an optional consolidated context file (`--context-file`), then the most-recent distilled captures from `_inbox/sessions/<project>/`. It deliberately omits MEMORY.md, daily logs, HEARTBEAT.md and HABITS.md — the operational second-brain-self files. (The full-vault dump is `session-start-context.py`, BrunOS-self only.)
- **SessionEnd** — at the end of each session, a Sonnet-distilled bullet list of the durable bits lands in the inbox.
- **PreCompact** — same, fired before context compaction so long sessions don't lose the early thinking.

Capture (SessionEnd/PreCompact) and injection (SessionStart) share the same `<project>` slug, so a session reads back what prior sessions wrote. Captures are NOT written to the daily log (which stays scoped to BrunOS-self work) and NOT auto-promoted to MEMORY.md. They sit in `_inbox/` for reflection (Phase B+) to classify and route.

**Keep the slug consistent across capture surfaces.** If this repo's Codex sessions are also captured (via `codex_watcher.py` / the codex template), use the SAME slug there — otherwise the repo's knowledge splits across two inbox folders and SessionStart only sees one of them. (The watcher derives its slug from the repo directory name, e.g. `vertik-lab-agent`; match that unless you have a reason not to.)

## Choose: settings.json vs settings.local.json

- **`settings.json`** (project-shared, committed to repo) — use only when ALL teammates working on this repo run BrunOS on Bruno's path. In practice, that's never true except for solo repos like the cemetery project before any other contributor exists.
- **`settings.local.json`** (personal, gitignored by default) — use for any team-shared repo (Vertik, future client repos, anything Lisa or other teammates clone). The hardcoded `/Users/brunobouwman/...` path is host-specific; it would break on a teammate's machine.

**Default to `settings.local.json`** unless you have a deliberate reason to share. The template file in this directory is named `settings.json` only because that's what the cemetery solo case will use — for other repos, copy it as `settings.local.json` instead.

## Usage

```bash
# In the new project repo (solo / cemetery — settings.json is fine):
mkdir -p .claude
cp /Users/brunobouwman/Documents/claude-second-brain/deploy/external-repo-template/.claude/settings.json .claude/settings.json
sed -i '' 's/<project>/cemetery/g; s|<context-file>|projects/memorial-colinas.md|g; s/<default-export>/linos-protostack/g' .claude/settings.json

# In a team-shared repo (Vertik, etc — use settings.local.json):
mkdir -p .claude
cp /Users/brunobouwman/Documents/claude-second-brain/deploy/external-repo-template/.claude/settings.json .claude/settings.local.json
sed -i '' 's/<project>/vertik-something/g; s|<context-file>|projects/vertik.md|g; s/<default-export>/personal/g' .claude/settings.local.json
# Verify .claude or settings.local.json is gitignored before committing other changes.
```

`<project>` is a slug like `cemetery`, `vertik-ext`, `client-acme`. It becomes the inbox subfolder name. Lowercase, dashes only — the writer slugs anything else automatically.

`<context-file>` is an OPTIONAL `Memory/`-relative path to a consolidated project file (e.g. `projects/vertik.md`) that SessionStart prepends to the injected captures. If there's no such file, delete the entire `--context-file=<context-file>` flag from the SessionStart command — the hook still injects the recent captures on their own.

`<default-export>` is one of:
- `personal` — capture stays in BrunOS only. Use for Bruno-solo work (Vertik, personal experiments).
- `linos-protostack` — capture is tagged for promotion to LinOS during Phase C reflection. Use for joint Protostack client work.
- `discard` — capture is recorded but flagged for skip in reflection. Use for throwaway prototyping where you want capture-as-safety-net but don't want signal.

The Phase B reflection classifier can override per-item, but this is the session default.

## Path assumptions

The template hardcodes `/Users/brunobouwman/Documents/claude-second-brain/` (Bruno's Mac). For:
- **Lisa's Mac**: replace with her clone path; LisaOS uses the same pattern with its own scripts.
- **VPS** (per Phase 9): `/home/bruno/claude-second-brain/.venv/bin/python /home/bruno/claude-second-brain/.claude/hooks/...`.

The `.venv/bin/python` invocation skips the `uv run` wrapper because `uv` resolves projects relative to cwd — it would try to use the *external* repo's pyproject.toml (which doesn't exist) instead of BrunOS's. Direct venv-python invocation is host-agnostic within a single user's home.

## Secret leakage — what's mitigated and what isn't

The session transcript that memory_flush sends to Sonnet for distillation includes raw tool calls. If a session contains commands with embedded secrets (`PGPASSWORD=... psql ...`, `OPENAI_API_KEY=sk-... npx ...`), those secrets are visible to Sonnet.

**Mitigated:**
- The flush system prompt explicitly forbids credentials, API keys, connection strings, and internal IPs in the distilled output. The inbox file should contain abstracted references ("rotated the prod DB password") rather than literal secrets.

**Not mitigated:**
- The Anthropic API request body contains the raw transcript. Sending sessions through Sonnet means Anthropic sees the raw secrets. If you wouldn't paste a credential into claude.ai, don't enable capture in a repo where sessions handle that credential.
- The `block-secrets.py` PreToolUse hook in BrunOS does NOT run in external repos. External repos need their own copy of that hook if they want pre-tool credential blocking.

**Practical guidance:** for repos with high secret density (Vertik, client deploys), set `default-export=personal` so captures never leak to LinOS, and treat the inbox files as containing potentially-sensitive abstracted references — they should not be casually shared.

## What this does NOT do (yet)

- Does NOT write anything outside BrunOS. LinOS export happens in Phase B+ via reflection, not at capture time.
- Does NOT classify capture content beyond the session-default tag. Per-item LLM classification is Phase B.
- Does NOT register the project's MCP clients for federated query. That's Phase D.
