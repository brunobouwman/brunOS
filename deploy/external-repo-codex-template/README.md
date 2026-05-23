# External-repo Codex capture template (Phase A)

Codex sibling of `deploy/external-repo-template/`. Routes Codex session captures into BrunOS's per-project inbox at `BrunOS/Memory/_inbox/sessions/<project>/`, same format the Claude Code template uses — so Phase B reflection processes both uniformly.

## Two capture surfaces — one automatic, one per-repo

| Surface | Coverage | Setup | When it fires |
|---|---|---|---|
| **`codex_watcher.py`** (launchd) | All Codex sessions, all repos | Already running via `com.bruno.brunos.codex-watcher.plist` (300s tick) | ~10 min after a rollout JSONL goes idle (de-facto session end) |
| **Per-repo `.codex/config.toml`** (this template) | Only the repos you install it in | Copy this directory's contents into the repo | Before context compaction (mid-session snapshot) |

The watcher alone gives you full coverage. Use this template only when you want **earlier** capture for repos with long compact-heavy sessions — without it, post-compact work still gets captured at session-end, but pre-compact content is lost from the final transcript window.

## What you get

- **PreCompact** — at compact time (manual or auto), the current rollout up to that point is distilled by Sonnet and lands in `BrunOS/Memory/_inbox/sessions/<project>/<YYYY-MM-DD>-<HHMMSS>-<sid>.md` with frontmatter tagging it for Phase B reflection.

There is no `SessionEnd` hook in Codex (only per-turn `Stop`) — see the watcher path above for that coverage.

## settings file: there's only `.codex/config.toml`

Codex doesn't distinguish project vs. local config the way Claude Code does (no `settings.local.json` equivalent). The single per-repo file is `.codex/config.toml`. If the repo is team-shared, EITHER gitignore the entire `.codex/` directory OR set `<default-export>` carefully so confidential captures stay tagged `personal` (never export to LinOS).

## Usage

```bash
# In the new project repo:
mkdir -p .codex
cp /Users/brunobouwman/Documents/claude-second-brain/deploy/external-repo-codex-template/.codex/config.toml .codex/config.toml
# Use the same slug convention as Claude Code captures so both end up in the
# same per-project inbox folder:
sed -i '' 's/<project>/vertik-lab-agent/g; s/<default-export>/personal/g' .codex/config.toml

# Then start Codex in this repo and run /hooks. Codex will show the new
# PreCompact hook as untrusted — review the exact command string, trust it,
# done.
echo ".codex/" >> .gitignore  # if team-shared and you don't want others to inherit it
```

`<project>` should match the Claude Code template's slug for this repo (e.g. `vertik-lab-agent`, `cemetery`). That way Codex and Claude Code captures from the same project share one inbox folder, and the Phase B reflector treats them uniformly.

`<default-export>` is one of: `personal` (BrunOS-only, safe default), `linos-protostack` (Phase C promotion candidate, joint Protostack work), `discard` (capture-as-safety-net only).

## Trust step (Codex-specific)

Codex blocks non-managed command hooks until you trust the exact command string once. After installing this file:

1. Open Codex in this repo (Desktop or CLI).
2. Type `/hooks` to list registered hooks.
3. Trust the `codex-precompact-flush.py` entry. Codex prints the command verbatim for review — verify the path matches `<repo>/.claude/hooks/codex-precompact-flush.py` (i.e. it points into BrunOS, not somewhere unexpected).
4. Trust persists per command string. If you ever edit the command (add a flag, change a path), Codex re-prompts on next session.

## Why no SessionStart hook here

Codex DOES support a SessionStart hook (`matcher = "startup|resume|clear|compact"`). We're not using it because:

1. The watcher + PreCompact already cover capture — SessionStart is for context **injection**, not capture.
2. Codex hook stdout fields are `continue`/`stopReason`/`systemMessage`/`suppressOutput` — none obviously injects context into the model's prompt the way Claude Code's SessionStart hook does. The closest event is `UserPromptSubmit` (which has a `prompt` field), but that's per-turn, not per-session.
3. Bruno's `AGENTS.md` at the repo root is the static-context channel for Codex; SOUL/USER/MEMORY equivalents would need to be summarized into that file (out of scope for this template).

If we add dynamic context injection later, it'll be a separate template change.

## Recursion / safety

The hook script sets up no `CLAUDE_INVOKED_BY` — instead it short-circuits on its own check at the top. memory_flush.py (which the hook dispatches) sets `CLAUDE_INVOKED_BY=memory_flush` before importing the Anthropic SDK. So Anthropic SDK calls made during distillation can't re-trigger Codex hooks (those only watch Codex's own lifecycle, not Anthropic API behavior).

## Secret leakage

Same caveat as the Claude Code template:

- **Mitigated**: the Sonnet system prompt forbids credentials, API keys, connection strings, internal IPs in the distilled output.
- **Not mitigated**: the Anthropic API request body sees the raw rollout text. Codex rollouts can contain shell commands with embedded secrets (`PGPASSWORD=`, `OPENAI_API_KEY=`, etc.). If a session handles a credential you wouldn't paste into claude.ai, gitignore `.codex/` or set `default-export=discard`.

The Codex rollout parser strips `base_instructions`, `turn_context`, `response_item.reasoning` (encrypted blobs anyway), `token_count` events, and tool-call mechanics before sending to Sonnet — so the input is just `USER:`/`ASSISTANT:` turns. Shell command secrets pasted IN-conversation still flow through; tool-arg secrets in `function_call.arguments` do not (those events are skipped).

## Path assumptions

The template hardcodes `/Users/brunobouwman/Documents/claude-second-brain/` (Bruno's Mac, primary host). For:

- **Lisa's Mac**: replace with her clone path; LisaOS-equivalent.
- **VPS**: Codex isn't installed on the VPS today, so this template isn't relevant there. If that changes, the path is `/home/bruno/claude-second-brain/.venv/bin/python ...`.
