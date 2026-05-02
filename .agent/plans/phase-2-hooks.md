# Feature: Phase 2 — Hooks (context persistence + recursion safety)

The following plan should be complete, but it's important to validate documentation, codebase patterns, and task sanity before implementing. Pay special attention to:

- The `claude-agent-sdk` API shape: Phase 0 already verified `ClaudeAgentOptions` exposes `setting_sources`. If a re-probe fails, ABORT and surface — Phase 2 hard-codes that field name.
- **Recursion guard**: every Agent SDK script MUST set `os.environ["CLAUDE_INVOKED_BY"] = "<purpose>"` BEFORE `import claude_agent_sdk`. Without it, SessionEnd → memory_flush → new session → SessionEnd → … infinite loop. Test for this guard explicitly.
- **`setting_sources` policy**: every `ClaudeAgentOptions(...)` call must pass `setting_sources` explicitly. Default has flipped between SDK releases.
- **Std-lib only constraint for `shared.py`**: the SessionStart hook runs with the OS-level `python3` (not `.venv/bin/python`), so `shared.py` cannot depend on `python-dotenv`, `pyyaml`, or any other PyPI package. Parse `.env` manually.
- **Vault path resolution**: every script that touches the vault calls `shared.vault_path()`. Never hardcode `BrunOS/`. Scripts will run from launchd/systemd with arbitrary cwd in Phase 9.
- The vault is **gitignored** by this repo. Do not stage vault files. The hook writes to `BrunOS/Memory/daily/YYYY-MM-DD.md` but those writes stay outside the parent repo's git history (vault becomes its own repo in Phase 9).

## Feature Description

Phase 2 wires the four lifecycle moments where Claude Code can persist or restore BrunOS state:

1. **SessionStart** — when a session starts/resumes, dump the agent's identity (`SOUL.md`), profile (`USER.md`), durable memory (`MEMORY.md`), recent daily logs, and monitoring/habit checklists into the model's context.
2. **PreCompact** (manual + auto) — before the SDK compacts conversation history, snapshot the transcript and fire-and-forget a consolidator that distils durable items into today's daily log.
3. **SessionEnd** — same flush logic on session end; without this, brain-state goes nowhere.

Plus the cross-cutting utilities (`shared.py`) that every later phase reuses: vault path resolution, file locking, atomic writes, frontmatter `updated:` stamping, daily-log append, JSON state, exponential-backoff retry.

The result: at the end of Phase 2, opening a new Claude Code session in this repo loads BrunOS context automatically, and ending a session leaves a memory trail in the daily log without manual intervention.

## User Story

As Bruno (the operator of BrunOS)
I want hooks that load my vault into every Claude Code session and persist a session summary to my daily log on exit
So that I never have to paste vault content manually, and important decisions/lessons from a session aren't lost when context compacts or the session ends.

## Problem Statement

Phase 0 produced scaffolding. Phase 1 produced a populated vault. **There is no path between the two** — Claude Code sessions don't automatically read `BrunOS/Memory/SOUL.md`, and they don't write back to `BrunOS/Memory/daily/`. Every later phase assumes:

- A `shared.vault_path()` helper exists and resolves `BRUNOS_VAULT_PATH`. (Phase 4 integrations all need it; Phase 6 heartbeat is built on it.)
- A `shared.atomic_write()` exists and stamps the `updated:` frontmatter field per CLAUDE.md (the rule is committed in CLAUDE.md but no code enforces it).
- A `shared.append_to_daily_log()` exists with proper file-locking. (Memory flush, heartbeat, and chat all append to the same `daily/YYYY-MM-DD.md` from concurrent processes; without locking, writes interleave.)
- A `shared.with_retry()` wraps every external API call (PRD lines 152, all of Phase 4).
- The `CLAUDE_INVOKED_BY` env-var pattern is established and tested. (Phase 6 heartbeat sets `"heartbeat"`, reflection sets `"reflection"`, chat sets `"chat"`. Phase 8 hooks short-circuit when set.)

Without Phase 2, every later phase has to reinvent these primitives or violate the recursion-guard contract.

## Solution Statement

Build the four hook scripts (`session-start-context.py`, `pre-compact-flush.py`, `session-end-flush.py`) and one consolidator script (`memory_flush.py`), backed by a single std-lib-only `shared.py` module. Register the hooks in `.claude/settings.json` with the matchers the PRD specifies. Validate end-to-end with executable smoke tests that exercise each hook's stdin contract and confirm the recursion guard short-circuits as designed.

Two design decisions, already aligned with the user:

1. **Frontmatter stamping uses regex, not `pyyaml`** — `shared.py` stays std-lib-only so hooks can run with system `python3`. The frontmatter format is fixed and we control all writers; a regex is sufficient.
2. **`memory_flush.py` skips if transcript < 2KB** — short interactive sessions don't justify a Sonnet 4.6 call. The consolidator only fires when there's plausibly something to consolidate.

## Feature Metadata

**Feature Type**: New Capability (foundational — unblocks Phases 3–9)
**Estimated Complexity**: Medium
**Primary Systems Affected**: `.claude/scripts/`, `.claude/hooks/`, `.claude/settings.json`, `CLAUDE.md` (phase status update). Vault writes hit `BrunOS/Memory/daily/YYYY-MM-DD.md` but no other vault files.
**Dependencies**: `claude-agent-sdk` (already pinned in `requirements.txt`, installed via `.venv`); no new deps.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: READ BEFORE IMPLEMENTING

- `.agent/plans/second-brain-prd.md` (lines 116–173, full Phase 2 spec) — Why: source-of-truth list of every file, every script's behavior, every hook matcher. The PRD is canonical; this plan elaborates with task-level decisions and validation.
- `.agent/plans/second-brain-prd.md` (lines 359–387, Phase 6 heartbeat) — Why: shows how Phase 6 will *consume* `shared.py`. `dispatch_flush`, `append_to_daily_log`, `with_retry`, `vault_path` all get reused there. Implementing them with that consumer in mind avoids redesign.
- `.agent/plans/second-brain-prd.md` (lines 481–559, Phase 8 security) — Why: Phase 8 adds `protect-soul.py`, `block-secrets.py`, `dangerous-bash.py`. **Phase 2 must NOT add these** — only the three lifecycle hooks. `DANGEROUS_BASH_PATTERNS` is declared as an empty constant in `shared.py` for Phase 8 to populate.
- `CLAUDE.md` (whole file, currently working-tree-modified) — Why: Phase 2 will append `python .claude/scripts/memory_flush.py <transcript-path>` to Build commands and mark Phase 2 done in the phase status checklist. The frontmatter spec at lines 49–63 commits us to stamping `updated:` on every agent write — `atomic_write` enforces this.
- `.env.example` and `.env` (root) — Why: `BRUNOS_VAULT_PATH=/Users/brunobouwman/Documents/claude-second-brain/BrunOS` is set; `vault_path()` resolves through it. `.env` is gitignored — never `cat` it from a script that might log to a public surface.
- `BrunOS/Memory/_README.md` — Why: confirms `BOOTSTRAP.md` is absent by design. The SessionStart hook checks for it and falls through to the standard concatenation when missing. A re-introduced `BOOTSTRAP.md` would change behavior — flag if discovered.
- `BrunOS/Memory/SOUL.md`, `USER.md`, `MEMORY.md`, `HEARTBEAT.md`, `HABITS.md` — Why: the SessionStart hook reads these. Confirm they all open without permission errors before relying on them.
- `BrunOS/Memory/daily/2026-04-27.md`, `2026-04-28.md` — Why: existing daily logs to confirm the frontmatter pattern (`type: daily`, `tags: [daily]`, `created`, `updated`, `status`). The `append_to_daily_log` helper must produce files that match this shape so Obsidian Properties keeps working.
- `requirements.txt` — Why: `claude-agent-sdk>=0.1,<0.2` confirms the pinned major. The plan's expected `query()` and `ClaudeAgentOptions(...)` call shape is bound to that range.
- `.claude/data/state/.gitkeep` — Why: this dir already exists and is gitignored. Transcripts (`flush-{session_id}.json`), `last_flush.json`, and `locks/*.lock` all land here.

### Existing Pre-Phase-2 State (verified)

- `.claude/scripts/__init__.py` — exists, empty. (Phase 0)
- `.claude/scripts/integrations/__init__.py` — exists, empty. (Phase 0)
- `.claude/hooks/.gitkeep` — exists.
- `.claude/data/state/` — exists, gitignored content.
- `.claude/data/fastembed_cache/` — exists, gitignored content (used in Phase 3).
- `.claude/settings.json` — **does not exist**. Phase 2 creates it.
- `.claude/scripts/shared.py` — **does not exist**. Phase 2 creates it.
- `.claude/scripts/memory_flush.py` — **does not exist**. Phase 2 creates it.
- `.claude/hooks/*.py` — **none exist**. Phase 2 creates the three lifecycle hooks.
- The user has a working venv at `.venv/` with `claude-agent-sdk` installed; verified by Phase 0 import probe.

### New Files to Create (Phase 2 deliverables)

All paths relative to `/Users/brunobouwman/Documents/claude-second-brain/`.

1. `.claude/scripts/shared.py` — std-lib-only utilities: `vault_path`, `now_brt`, `file_lock`, `atomic_write` (with frontmatter stamping), `append_to_daily_log`, `save_state`, `load_state`, `with_retry`, `dispatch_flush`, plus the empty `DANGEROUS_BASH_PATTERNS` constant.
2. `.claude/scripts/memory_flush.py` — the consolidator. Sets `CLAUDE_INVOKED_BY=memory_flush` at the top of the file, then imports the SDK. Reads transcript JSON, skips if <2KB, dedups via `last_flush.json`, calls Sonnet 4.6 with `allowed_tools=[]` and `setting_sources=None`, appends bullet summary to today's daily log via `append_to_daily_log`.
3. `.claude/hooks/session-start-context.py` — emits `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}` on stdout. Concatenates SOUL + USER + MEMORY + last 3 daily logs + HEARTBEAT + HABITS. Falls through to `BOOTSTRAP.md` content if that file (re-)appears.
4. `.claude/hooks/pre-compact-flush.py` — recursion-guarded; on stdin transcript, persists to `.claude/data/state/flush-{session_id}.json` and `Popen`s `memory_flush.py` detached.
5. `.claude/hooks/session-end-flush.py` — same logic as PreCompact, registered against the SessionEnd event.
6. `.claude/settings.json` — registers the three hooks with the matchers from the PRD.

### Modified Files

- `CLAUDE.md` — append `python .claude/scripts/memory_flush.py <transcript-path>` to Build commands; mark Phase 2 as `[x]` in Phase status. The pre-existing working-tree edit (frontmatter spec tightening — `type/created/updated/tags/status` for every vault file) stays unchanged.

### Relevant Documentation — READ BEFORE IMPLEMENTING

- [Claude Code hooks reference](https://docs.claude.com/en/docs/claude-code/hooks) — Why: the canonical event names (`SessionStart`, `PreCompact`, `SessionEnd`), their stdin payload shape, the `hookSpecificOutput.additionalContext` contract, and the `matcher` field semantics. **Critical**: confirm the `SessionStart` matcher syntax (`startup|resume`) and the `PreCompact` matcher syntax (`manual|auto`) at implementation time — these are pipe-delimited and case-sensitive.
- [Claude Agent SDK Python — `ClaudeAgentOptions`](https://docs.claude.com/en/api/agent-sdk/python) — Why: confirms `allowed_tools`, `setting_sources`, `system_prompt`, `max_turns`, `model` are the field names. Phase 0 verified `setting_sources` exists; this plan assumes the rest are unchanged. If `query()` or any field has been renamed in the installed version, surface and update the plan.
- [Python `fcntl.flock`](https://docs.python.org/3/library/fcntl.html#fcntl.flock) — Why: `file_lock` uses `LOCK_EX`. macOS POSIX `flock` releases on `close()`; we wrap in a context manager to make this explicit and exception-safe.
- [Python `os.replace`](https://docs.python.org/3/library/os.html#os.replace) — Why: atomic on POSIX same-filesystem; Mac and Linux behave the same. We rely on this for `atomic_write`.

### Patterns to Follow

**Naming Conventions:**

- Python modules: `snake_case` (`memory_flush.py`).
- Hooks: `kebab-case` (`session-start-context.py`, `pre-compact-flush.py`, `session-end-flush.py`).
- All Agent SDK scripts set `os.environ["CLAUDE_INVOKED_BY"] = "<purpose>"` before `import claude_agent_sdk`. The `<purpose>` string conventions: `memory_flush`, `heartbeat` (Phase 6), `reflection` (Phase 6), `chat` (Phase 7), `guardrail` (Phase 6), `news_digest` (Phase 5), `weekly_review` (Phase 5).

**Frontmatter Pattern (CLAUDE.md authoritative):**

Every vault file under `BrunOS/Memory/` carries:

```yaml
---
type: meeting | project | client | research | goal | content | team | draft | digest | personal | daily | system | reference
created: 2026-05-02T09:00-03:00
updated: 2026-05-02T09:00-03:00
tags:
  - daily
status: active | archived | done
---
```

`atomic_write` patches `updated:` for `.md` files when frontmatter is present; no-ops on files without frontmatter. **Block-list YAML for tags** (memory feedback: Obsidian rewrites inline arrays on save) — `append_to_daily_log` uses `tags:\n  - daily` form.

**Error-handling Pattern:**

- Hooks **fail open**: any unexpected exception → write to stderr, exit 0. Never block session start or compaction.
- `memory_flush.py` **fails silently**: any SDK error or transcript parse error → exit 0 with a stderr note. The next flush will try again.
- Library callers catching network errors wrap with `with_retry` from `shared.py`.

**Logging Pattern:**

- Hooks: stderr only. Stdout is reserved for the structured JSON payload (SessionStart) or empty (PreCompact / SessionEnd).
- `memory_flush.py`: stderr-only. The hook spawned it detached — anything on stdout/stderr goes to `/dev/null` per `dispatch_flush`. Log-to-daily-log via `append_to_daily_log` for any user-visible signal.

**Subprocess Pattern (`dispatch_flush`):**

```python
subprocess.Popen(
    [python_bin, str(flush_script), str(transcript_path)],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    stdin=subprocess.DEVNULL,
    start_new_session=True,    # detach from the controlling tty
)
```

`start_new_session=True` (POSIX `setsid`) is what makes the spawn truly fire-and-forget. Without it, the parent waits on the child's controlling terminal in some shells.

**Venv Detection in `dispatch_flush`:**

```python
venv_python = REPO_ROOT / ".venv" / "bin" / "python"
python_bin = str(venv_python) if venv_python.exists() else sys.executable
```

The hooks themselves run with system `python3` (per the std-lib-only `shared.py` design). But `memory_flush.py` needs `claude_agent_sdk`, which lives in `.venv/`. So `dispatch_flush` explicitly invokes the venv python.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation — `shared.py`

Build the cross-cutting utilities first because every other Phase 2 file imports from this module.

**Tasks:**

- Implement `vault_path()` with manual `.env` parsing (no `python-dotenv` dep).
- Implement `now_brt()` returning a `ZoneInfo("America/Sao_Paulo")` datetime.
- Implement `file_lock(path)` as a `fcntl.flock` context manager keyed by an MD5 of the absolute path; lock files in `.claude/data/state/locks/`.
- Implement `atomic_write(path, content, *, stamp_updated=None)` with regex-based frontmatter `updated:` stamping for `.md` files.
- Implement `append_to_daily_log(line, dt=None)` using `file_lock` + `atomic_write`. Creates the daily file with proper frontmatter (`type: daily`, `tags:\n  - daily`, `status: active`) if missing.
- Implement `save_state(path, obj)` and `load_state(path, default=None)` over JSON via `atomic_write` (with `stamp_updated=False`).
- Implement `with_retry(fn, *, max_retries=3, backoff_base=1.0, retry_on=(429, 500, 502, 503))` with exponential backoff. Detect status from `e.status_code`, `e.response.status_code`, or `e.code`.
- Implement `dispatch_flush(stdin_data, source)` that persists transcript JSON and `Popen`s `memory_flush.py` detached using the venv python.
- Declare `DANGEROUS_BASH_PATTERNS: list[str] = []` (populated in Phase 8).

### Phase 2: Lifecycle Hooks

Build the three hooks. Each is a thin shim around `shared.py`. Each is `chmod +x` and starts with `#!/usr/bin/env python3`.

**Tasks:**

- `session-start-context.py`: read stdin (drain to avoid SIGPIPE), check for `BOOTSTRAP.md`, otherwise concatenate the canonical context, emit `hookSpecificOutput.additionalContext` JSON.
- `pre-compact-flush.py`: recursion-guard via `CLAUDE_INVOKED_BY`, parse stdin JSON, call `shared.dispatch_flush(data, source="pre-compact")`.
- `session-end-flush.py`: same logic, `source="session-end"`.

### Phase 3: Memory Flush — the consolidator

The only file that imports `claude_agent_sdk`. The recursion guard MUST be set before that import.

**Tasks:**

- Set `os.environ["CLAUDE_INVOKED_BY"] = "memory_flush"` as the FIRST executable line.
- Import `claude_agent_sdk.{ClaudeAgentOptions, query}` after the env-var set.
- Skip if transcript path missing or <2KB.
- Dedup via `last_flush.json` (skip if same `session_id` flushed <60s ago).
- Async run an SDK query with: `allowed_tools=[]`, `setting_sources=None`, `system_prompt=SYSTEM_PROMPT`, `max_turns=1`, `model="claude-sonnet-4-6"`.
- Emit a `## Memory flush (HH:MM)` header + the SDK's bullet output to today's daily log via `append_to_daily_log`.
- Skip writing if SDK output is empty or exactly `FLUSH_OK`.
- Unlink the transcript file after successful processing.

### Phase 4: Hook Registration & Integration

**Tasks:**

- Create `.claude/settings.json` with the three `hooks` registrations (matchers: `startup|resume` for SessionStart, `manual|auto` for PreCompact, none for SessionEnd).
- `chmod +x` all three hook scripts.

### Phase 5: Validation & Documentation

**Tasks:**

- Smoke test each hook (stdin → expected stdout/exit-code).
- Smoke test `shared.py` utilities (write/read frontmatter; daily-log append; vault_path resolution).
- Verify recursion guard short-circuits both flush hooks when `CLAUDE_INVOKED_BY=test` is set in the environment.
- Update `CLAUDE.md` Build commands and Phase status.
- Surface to Bruno for commit decision (do NOT auto-commit).

---

## STEP-BY-STEP TASKS

Run from `/Users/brunobouwman/Documents/claude-second-brain/`.

### CREATE `.claude/scripts/shared.py`

- **IMPLEMENT**: Standard-library-only module with `vault_path`, `now_brt`, `file_lock`, `atomic_write`, `append_to_daily_log`, `save_state`, `load_state`, `with_retry`, `dispatch_flush`, and the empty `DANGEROUS_BASH_PATTERNS` constant. Imports limited to: `fcntl, hashlib, json, os, re, time, contextlib, datetime, functools, pathlib, zoneinfo`. (Plus `subprocess`, `sys`, `uuid` inside `dispatch_flush` since they're only needed there.)
- **PATTERN**: Mirror module structure of any std-lib utility module: top-level constants → simple helpers → core functions. No classes needed.
- **IMPORTS**: `from __future__ import annotations` at top so `os.PathLike` and `dict | None` syntax works on Python 3.10+.
- **GOTCHA — frontmatter regex**: the pattern `\A---\n(.*?)\n---\n` with `re.DOTALL` handles only LF line endings; vault files are LF (verified). If a future Windows write produces CRLF, the regex no-ops gracefully (no stamping) but the file isn't corrupted.
- **GOTCHA — vault_path caching**: `@lru_cache(maxsize=1)` is fine because `BRUNOS_VAULT_PATH` doesn't change mid-process. But it means tests that mock the env var must call `vault_path.cache_clear()`.
- **GOTCHA — file_lock**: don't try to lock the target file directly — it may not exist yet. Lock a sibling in `.claude/data/state/locks/<md5>.lock`. The lock dir is gitignored.
- **GOTCHA — atomic_write `updated:` stamping**: `created:` may not be present in some files; in that case append `updated:` at end of frontmatter. If the frontmatter regex doesn't match at all (no `---` block), return content unchanged — never inject frontmatter where none existed.
- **GOTCHA — `with_retry` status detection**: different libraries expose status differently. `requests` → `e.response.status_code`. `slack_sdk.errors.SlackApiError` → `e.response.status_code` (their response is a dict-ish). `googleapiclient.errors.HttpError` → `e.resp.status` (string). Fall through to `e.code` as a final attempt; otherwise re-raise immediately.
- **VALIDATE** (creates a fresh test file, exercises stamping, verifies daily-log creation):

  ```bash
  source .venv/bin/activate
  python -c "
  from pathlib import Path
  import sys; sys.path.insert(0, '.claude/scripts')
  from shared import vault_path, now_brt, atomic_write, append_to_daily_log, save_state, load_state, _stamp_updated, _ts_brt

  # vault_path
  vp = vault_path()
  assert vp.exists(), f'vault not found at {vp}'
  print('vault_path OK:', vp)

  # frontmatter stamping
  src = '---\ntype: system\ncreated: 2026-05-01T00:00:00-03:00\nupdated: 2026-05-01T00:00:00-03:00\ntags:\n  - test\nstatus: active\n---\n\nbody'
  out = _stamp_updated(src, '2099-01-01T00:00:00-03:00')
  assert 'updated: 2099-01-01T00:00:00-03:00' in out, out
  print('stamp OK')

  # save/load
  save_state(Path('.claude/data/state/_phase2_smoke.json'), {'a': 1})
  assert load_state(Path('.claude/data/state/_phase2_smoke.json')) == {'a': 1}
  Path('.claude/data/state/_phase2_smoke.json').unlink()
  print('state OK')

  # daily log append (uses real vault — adds a line then we manually clean)
  append_to_daily_log('<!-- phase2 smoke test (delete) -->')
  daily = vp / 'Memory' / 'daily' / (now_brt().strftime('%Y-%m-%d') + '.md')
  assert daily.exists(), daily
  txt = daily.read_text()
  assert 'phase2 smoke test' in txt
  print('append_to_daily_log OK at', daily)
  print('NOTE: smoke marker left in daily log — remove it manually before committing.')
  "
  ```

### CREATE `.claude/hooks/session-start-context.py`

- **IMPLEMENT**: Drain stdin (so the SDK doesn't get SIGPIPE on its write). Resolve `vault_path()`. If `Memory/BOOTSTRAP.md` exists, dump its content as `additionalContext`. Otherwise concatenate (in order): `SOUL.md`, `USER.md`, `MEMORY.md`, last 3 `daily/*.md` (excluding `_README.md`), `HEARTBEAT.md`, `HABITS.md`. Each section gets a `<!-- filename -->` HTML comment header so the model can locate its source. Emit a single JSON object: `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}` on stdout.
- **PATTERN**: Shebang `#!/usr/bin/env python3`. Add `.claude/scripts/` to `sys.path` so `from shared import vault_path` resolves regardless of cwd.
- **GOTCHA — stdin handling**: Claude Code may send a JSON payload on stdin (event metadata). We don't need it for SessionStart, but consume it (`sys.stdin.read()`) so the SDK doesn't block on a full pipe. Drain before doing any work.
- **GOTCHA — fail open**: catch every exception around `build_context()`, write to stderr, exit 0. A broken hook must NOT block the user from starting a session.
- **GOTCHA — daily file glob**: `daily/*.md` includes `_README.md`. Filter on `not p.stem.startswith('_')` to skip placeholder files.
- **GOTCHA — token budget**: SOUL.md (~6KB) + USER.md (~12KB) + MEMORY.md (~3.6KB) + 3×daily logs (varies, currently ~few KB) + HEARTBEAT.md (~2.4KB) + HABITS.md (~1.6KB) ≈ ~30–40KB ≈ ~10K tokens. Fine for a Sonnet context window. If `daily/` ever grows large per-file, may need to truncate; not Phase 2's concern yet.
- **VALIDATE** (no real session, just exercise the script):

  ```bash
  echo '{}' | python3 .claude/hooks/session-start-context.py | python3 -c "
  import json, sys
  d = json.load(sys.stdin)
  assert d['hookSpecificOutput']['hookEventName'] == 'SessionStart', d
  ctx = d['hookSpecificOutput']['additionalContext']
  assert '<!-- SOUL.md -->' in ctx
  assert '<!-- USER.md -->' in ctx
  assert '<!-- MEMORY.md -->' in ctx
  assert '<!-- HEARTBEAT.md -->' in ctx
  assert '<!-- HABITS.md -->' in ctx
  print(f'SessionStart OK: {len(ctx)} chars of context')
  "
  ```

### CREATE `.claude/hooks/pre-compact-flush.py`

- **IMPLEMENT**: First check `os.environ.get("CLAUDE_INVOKED_BY")` — if set, exit 0 immediately. Otherwise read JSON from stdin, call `shared.dispatch_flush(data, source="pre-compact")`. Wrap dispatch in try/except; never re-raise.
- **PATTERN**: Same shebang + sys.path setup as the SessionStart hook.
- **GOTCHA — recursion**: this hook is the LESS dangerous of the two flush hooks — PreCompact only fires during compaction. SessionEnd is the bigger recursion risk. But we apply the same guard to both for symmetry and defense in depth.
- **GOTCHA — empty stdin**: if Claude Code happens to invoke the hook with no stdin (manual trigger, edge cases), `json.load(sys.stdin)` raises. Catch and exit 0.
- **VALIDATE** (recursion guard exits cleanly):

  ```bash
  echo '{"session_id": "test-recursion"}' | CLAUDE_INVOKED_BY=test python3 .claude/hooks/pre-compact-flush.py
  echo "exit code: $?"
  # Must be 0 and must NOT have created flush-test-recursion.json
  test ! -f .claude/data/state/flush-test-recursion.json && echo "guard OK: no transcript persisted"
  ```

### CREATE `.claude/hooks/session-end-flush.py`

- **IMPLEMENT**: Same as `pre-compact-flush.py` but with `source="session-end"`. Exact duplication of the script body except for the source label is acceptable here — both files are 20 lines and clarity matters more than DRY.
- **PATTERN**: Same shebang + sys.path setup.
- **GOTCHA — this is THE infinite-loop risk**: `memory_flush.py` ends → SDK fires SessionEnd → this hook spawns another flush → loop. The `CLAUDE_INVOKED_BY` guard prevents it. Verify in test.
- **VALIDATE**:

  ```bash
  echo '{"session_id": "test-recursion-2"}' | CLAUDE_INVOKED_BY=test python3 .claude/hooks/session-end-flush.py
  echo "exit code: $?"
  test ! -f .claude/data/state/flush-test-recursion-2.json && echo "guard OK"
  ```

### CREATE `.claude/scripts/memory_flush.py`

- **IMPLEMENT**: First two non-comment lines: `import os` then `os.environ["CLAUDE_INVOKED_BY"] = "memory_flush"`. THEN `import claude_agent_sdk` (or specifically `from claude_agent_sdk import ClaudeAgentOptions, query`). Read transcript JSON from `sys.argv[1]`. Skip cases: file missing, file <2KB, JSON parse fails, dedup hit (same `session_id` within 60s). On a real run, build the SDK options as PRD spec'd and consume the async iterator into a single text blob. If output is empty or exactly `FLUSH_OK`, exit 0 silently. Otherwise prefix with `## Memory flush (HH:MM)` and `append_to_daily_log`. Unlink the transcript file on success.
- **PATTERN**: Async function `_consolidate(transcript_text)` that runs the SDK query; called via `asyncio.run`. Iterate the async generator and string-concat any `text` attributes from messages or content blocks. (PRD line 146: `async for msg in query(prompt=transcript, options=options): ...`.)
- **GOTCHA — env var ordering**: setting `CLAUDE_INVOKED_BY` AFTER importing the SDK is too late — the SDK may inspect the env at import time, and any spawned hook runs see the parent env at fork time. Set it FIRST.
- **GOTCHA — message shape**: the SDK returns mixed message types (assistant messages, tool_use messages, etc.). Only assistant `text` is interesting. Use `getattr(msg, 'text', None)` first, then fall back to iterating `msg.content` blocks. Don't crash on unknown shapes — skip and continue.
- **GOTCHA — input cap**: transcripts can be huge (compaction fires on long sessions). Cap input at ~200K chars to avoid blowing the model's context. The consolidator only needs the gist.
- **GOTCHA — dedup file growth**: `last_flush.json` accumulates entries. Trim entries older than 1 day on every write.
- **VALIDATE** (smoke test that import succeeds and the env-var guard is set; no real SDK call without an `ANTHROPIC_API_KEY`):

  ```bash
  source .venv/bin/activate
  python -c "
  import os
  # Set guard ahead of time so the import in memory_flush.py is a no-op env-wise
  os.environ['CLAUDE_INVOKED_BY'] = 'test-import'
  import importlib.util, pathlib
  spec = importlib.util.spec_from_file_location('memory_flush', '.claude/scripts/memory_flush.py')
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  assert os.environ.get('CLAUDE_INVOKED_BY') in ('memory_flush', 'test-import'), os.environ.get('CLAUDE_INVOKED_BY')
  print('memory_flush imports OK; env guard intact:', os.environ['CLAUDE_INVOKED_BY'])
  "
  ```

  And a no-op skip test (transcript too small):

  ```bash
  source .venv/bin/activate
  echo '{"session_id":"smoke","messages":[]}' > /tmp/_brunos_tiny.json
  python .claude/scripts/memory_flush.py /tmp/_brunos_tiny.json
  echo "exit: $?"
  rm /tmp/_brunos_tiny.json
  ```

  Expected: exit 0, no daily-log entry added (file is tiny, hits the <2KB skip).

### CREATE `.claude/settings.json`

- **IMPLEMENT**: A single JSON object registering the three hooks. The PRD specifies the exact structure (lines 161–168).
- **PATTERN**:

  ```json
  {
    "hooks": {
      "SessionStart": [
        {
          "matcher": "startup|resume",
          "hooks": [{"type": "command", "command": ".claude/hooks/session-start-context.py"}]
        }
      ],
      "PreCompact": [
        {
          "matcher": "manual|auto",
          "hooks": [{"type": "command", "command": ".claude/hooks/pre-compact-flush.py"}]
        }
      ],
      "SessionEnd": [
        {
          "hooks": [{"type": "command", "command": ".claude/hooks/session-end-flush.py"}]
        }
      ]
    }
  }
  ```

- **GOTCHA — relative paths**: hook commands are interpreted relative to the project root (where `.claude/settings.json` lives). Don't include leading `./`.
- **GOTCHA — settings vs settings.local**: `.claude/settings.json` is committed; `.claude/settings.local.json` is gitignored and per-user. Phase 2 hooks are project-level (everyone working in this repo gets them) — use `settings.json`.
- **GOTCHA — Phase 8 will EXTEND this file**: Phase 8 adds `PreToolUse` entries for `block-secrets.py`, `dangerous-bash.py`, `protect-soul.py`. Don't add them now. The shape we ship in Phase 2 must accept additive merging without restructuring.
- **VALIDATE**:

  ```bash
  python3 -c "import json; d = json.load(open('.claude/settings.json')); assert set(d['hooks'].keys()) == {'SessionStart','PreCompact','SessionEnd'}; print('settings.json OK')"
  ```

### CHMOD hooks executable

- **IMPLEMENT**: `chmod +x .claude/hooks/session-start-context.py .claude/hooks/pre-compact-flush.py .claude/hooks/session-end-flush.py`
- **GOTCHA**: without the executable bit, Claude Code's hook runner falls back to `/bin/sh` interpretation of the file, which fails on Python source. The shebang is necessary but not sufficient — the bit must be set.
- **VALIDATE**:

  ```bash
  test -x .claude/hooks/session-start-context.py && \
  test -x .claude/hooks/pre-compact-flush.py && \
  test -x .claude/hooks/session-end-flush.py && \
  echo "all hooks executable"
  ```

### UPDATE `CLAUDE.md`

- **IMPLEMENT**: Append `python .claude/scripts/memory_flush.py <transcript-path>` to the Build commands section (or add a "Hooks (Phase 2)" subsection if cleaner). Mark `Phase 2 — Hooks` as `[x]` with the date `2026-05-02` in the Phase status checklist. Do NOT touch the existing working-tree edit (the frontmatter spec tightening) — that's an independent change Bruno can stage separately or together at his discretion.
- **PATTERN**: Match the existing CLAUDE.md formatting: code blocks with `bash` highlighter, checkbox list with date in parens.
- **GOTCHA**: keep CLAUDE.md under ~250 lines per Phase 0's note (every line costs tokens on session start). Phase 2's additions are minimal.
- **VALIDATE**:

  ```bash
  grep -q 'python .claude/scripts/memory_flush.py' CLAUDE.md && \
  grep -q '\[x\] Phase 2' CLAUDE.md && \
  echo "CLAUDE.md updated"
  ```

### END-TO-END SMOKE TEST

- **IMPLEMENT**: Run all three hooks back-to-back to confirm they don't interfere with each other or leave bad state behind.
- **VALIDATE**:

  ```bash
  # 1. SessionStart produces valid context
  echo '{}' | python3 .claude/hooks/session-start-context.py >/tmp/_brunos_ss.json
  python3 -c "import json; d=json.load(open('/tmp/_brunos_ss.json')); assert d['hookSpecificOutput']['hookEventName']=='SessionStart'; print('SessionStart end-to-end OK')"

  # 2. PreCompact under guard exits clean
  echo '{"session_id":"smoke-pc"}' | CLAUDE_INVOKED_BY=test python3 .claude/hooks/pre-compact-flush.py
  test ! -f .claude/data/state/flush-smoke-pc.json && echo "PreCompact guard OK"

  # 3. SessionEnd under guard exits clean
  echo '{"session_id":"smoke-se"}' | CLAUDE_INVOKED_BY=test python3 .claude/hooks/session-end-flush.py
  test ! -f .claude/data/state/flush-smoke-se.json && echo "SessionEnd guard OK"

  # 4. PreCompact WITHOUT guard does persist + spawn (we won't wait for the SDK call to complete; just check the transcript landed)
  echo '{"session_id":"smoke-pc2"}' | python3 .claude/hooks/pre-compact-flush.py
  sleep 1
  test -f .claude/data/state/flush-smoke-pc2.json && echo "PreCompact spawn OK" && rm .claude/data/state/flush-smoke-pc2.json

  rm -f /tmp/_brunos_ss.json
  ```

  Note: step 4 spawns `memory_flush.py` detached. Without `ANTHROPIC_API_KEY`, the SDK call inside it will fail and exit silently (per the script's fail-silently contract). The transcript file landing on disk is sufficient evidence the hook worked.

### SURFACE TO BRUNO (no auto-commit)

- **IMPLEMENT**: Print a status summary listing the new files, the existing working-tree CLAUDE.md change, and propose a commit message: `feat: Phase 2 hooks (session-start context, flush hooks, shared utilities)`. Ask whether to commit now or leave the work staged for Bruno's review.
- **PATTERN**: Per the global Claude Code rules, never auto-commit unless explicitly asked. Surface and wait.

---

## TESTING STRATEGY

Phase 2 is infrastructure — it has no business logic to unit-test in the conventional sense. Validation is a sequence of executable smoke tests bound to each task. There's no `pytest` suite yet (none of the Phase 0 / Phase 1 deliverables shipped tests either), and adding one for Phase 2 alone would be premature — Phase 3+ may need a different test rig (mocking the SDK, mocking external APIs).

### Unit Tests

N/A for Phase 2. Each `shared.py` function has an inline smoke test in the validation step for that task.

### Integration Tests

The end-to-end smoke test exercises all three hooks plus their interaction with `shared.py` and (indirectly, via transcript persistence) `memory_flush.py`. That's the integration surface for Phase 2.

### Edge Cases

- **Empty stdin to a hook.** Catch and exit 0. Validated by `pre-compact-flush.py` and `session-end-flush.py` validation steps.
- **Missing `BRUNOS_VAULT_PATH`.** `vault_path()` raises; the SessionStart hook's outer try/except catches, writes to stderr, exits 0 (fail open).
- **Vault path resolves to nonexistent dir.** Same fail-open path. The smoke test would catch this — it asserts `vp.exists()`.
- **Daily log file already exists with valid frontmatter.** `append_to_daily_log` appends to body; `atomic_write` re-stamps `updated:`. Verified by running the daily-log smoke twice and inspecting the file.
- **Daily log file has no frontmatter (legacy / hand-edited).** `atomic_write` no-ops on stamping; appends a line at end. Acceptable degradation — Bruno can fix the frontmatter manually.
- **`memory_flush.py` invoked with garbage transcript.** Transcript parse fails → exit 0 silently.
- **Recursion under SessionEnd.** Confirmed by the `CLAUDE_INVOKED_BY=test` smoke test.
- **Concurrent writes to today's daily log** (heartbeat + memory_flush in Phase 6). Covered by `file_lock`. Smoke test only exercises the single-writer case; full concurrency exercise lands in Phase 6.
- **Transcript file accumulation.** `dispatch_flush` writes one transcript per session_id; `memory_flush.py` unlinks it after processing. If `memory_flush.py` crashes pre-unlink, the transcript stays. Acceptable — `.claude/data/state/` is gitignored and Bruno can `rm flush-*.json` manually if needed. Phase 6 may add a janitor.

---

## VALIDATION COMMANDS

Run from `/Users/brunobouwman/Documents/claude-second-brain/`. Each level must pass before the next.

### Level 1: Files exist

```bash
test -f .claude/scripts/shared.py && \
test -f .claude/scripts/memory_flush.py && \
test -f .claude/hooks/session-start-context.py && \
test -f .claude/hooks/pre-compact-flush.py && \
test -f .claude/hooks/session-end-flush.py && \
test -f .claude/settings.json && \
test -x .claude/hooks/session-start-context.py && \
test -x .claude/hooks/pre-compact-flush.py && \
test -x .claude/hooks/session-end-flush.py && \
echo "Phase 2 files present and executable"
```

### Level 2: `shared.py` smoke

```bash
source .venv/bin/activate
python -c "
import sys; sys.path.insert(0, '.claude/scripts')
from shared import vault_path, now_brt, atomic_write, append_to_daily_log, save_state, load_state, with_retry, file_lock, _stamp_updated
assert callable(vault_path) and callable(append_to_daily_log) and callable(with_retry)
assert vault_path().exists()
assert now_brt().tzinfo is not None
print('shared.py smoke OK')
"
```

### Level 3: SessionStart end-to-end

```bash
echo '{}' | python3 .claude/hooks/session-start-context.py | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['hookSpecificOutput']['hookEventName'] == 'SessionStart'
ctx = d['hookSpecificOutput']['additionalContext']
for marker in ('SOUL.md', 'USER.md', 'MEMORY.md', 'HEARTBEAT.md', 'HABITS.md'):
    assert f'<!-- {marker} -->' in ctx, f'missing {marker}'
print(f'SessionStart OK: {len(ctx)} chars')
"
```

### Level 4: Recursion guards

```bash
echo '{"session_id":"l4-pc"}' | CLAUDE_INVOKED_BY=test python3 .claude/hooks/pre-compact-flush.py && \
test ! -f .claude/data/state/flush-l4-pc.json && \
echo "PreCompact guard OK"

echo '{"session_id":"l4-se"}' | CLAUDE_INVOKED_BY=test python3 .claude/hooks/session-end-flush.py && \
test ! -f .claude/data/state/flush-l4-se.json && \
echo "SessionEnd guard OK"
```

### Level 5: Flush hook spawn (no guard)

```bash
echo '{"session_id":"l5-spawn"}' | python3 .claude/hooks/pre-compact-flush.py
sleep 1
test -f .claude/data/state/flush-l5-spawn.json && echo "spawn OK"
rm -f .claude/data/state/flush-l5-spawn.json
```

### Level 6: `memory_flush.py` skip-small

```bash
source .venv/bin/activate
echo '{"session_id":"l6","messages":[]}' > /tmp/_brunos_l6.json
python .claude/scripts/memory_flush.py /tmp/_brunos_l6.json
echo "exit: $?"
rm /tmp/_brunos_l6.json
# Expected: exit 0, no new entry in today's daily log
```

### Level 7: `settings.json` valid

```bash
python3 -c "
import json
d = json.load(open('.claude/settings.json'))
assert set(d['hooks'].keys()) == {'SessionStart','PreCompact','SessionEnd'}
for k, lst in d['hooks'].items():
    assert isinstance(lst, list) and lst, k
    for entry in lst:
        assert 'hooks' in entry and entry['hooks'], entry
        for h in entry['hooks']:
            assert h['type'] == 'command' and h['command'].startswith('.claude/hooks/'), h
print('settings.json OK')
"
```

### Level 8: CLAUDE.md updated

```bash
grep -q 'python .claude/scripts/memory_flush.py' CLAUDE.md && \
grep -qE '\[x\] Phase 2' CLAUDE.md && \
echo "CLAUDE.md OK"
```

### Level 9: Cleanup smoke marker

If Level 2 of the per-task smoke tests left a `<!-- phase2 smoke test (delete) -->` marker in today's daily log, remove it now (Obsidian-side or via `sed -i '' '/phase2 smoke test/d' BrunOS/Memory/daily/$(date +%Y-%m-%d).md`). The validation step's `NOTE` line warns about this.

---

## ACCEPTANCE CRITERIA

- [ ] `.claude/scripts/shared.py` exists; std-lib only; `vault_path`, `now_brt`, `file_lock`, `atomic_write` (with frontmatter stamping), `append_to_daily_log`, `save_state`, `load_state`, `with_retry`, `dispatch_flush`, `DANGEROUS_BASH_PATTERNS` are defined.
- [ ] `.claude/scripts/memory_flush.py` exists; sets `CLAUDE_INVOKED_BY=memory_flush` BEFORE importing `claude_agent_sdk`; skips transcripts <2KB; dedups on `session_id` within 60s; calls Sonnet 4.6 with `allowed_tools=[]` and `setting_sources=None`; appends to daily log via `append_to_daily_log` on non-empty non-`FLUSH_OK` output.
- [ ] All three hooks (`session-start-context.py`, `pre-compact-flush.py`, `session-end-flush.py`) exist, are `chmod +x`, and have shebang `#!/usr/bin/env python3`.
- [ ] Both flush hooks short-circuit on `CLAUDE_INVOKED_BY` set (recursion guard).
- [ ] `.claude/settings.json` registers the three hooks with the PRD-specified matchers (`startup|resume`, `manual|auto`, none for SessionEnd).
- [ ] Validation Levels 1–9 all pass.
- [ ] `CLAUDE.md` Build commands list `python .claude/scripts/memory_flush.py <transcript-path>`; Phase status marks Phase 2 as `[x]` with date `2026-05-02`.
- [ ] No vault-side files were modified except today's `daily/YYYY-MM-DD.md` (touched by smoke tests; markers cleaned post-validation).
- [ ] Pre-existing pre-Phase-2 deliverables (Phase 0 scaffolding, Phase 1 vault) are unchanged.
- [ ] Bruno surfaced for commit decision; no auto-commit.

---

## COMPLETION CHECKLIST

- [ ] All step-by-step tasks executed in order
- [ ] Each task's validation passed immediately after creation
- [ ] Levels 1–9 of VALIDATION COMMANDS all pass
- [ ] `python -c "from claude_agent_sdk import ClaudeAgentOptions, query"` works in `.venv`
- [ ] No SDK shape mismatch (Phase 0 probe still passes; if it doesn't, ABORT)
- [ ] No new git-tracked files in `BrunOS/` (vault stays gitignored)
- [ ] `git status` shows the expected new files staged-able
- [ ] CLAUDE.md content reviewed for accuracy against vault state
- [ ] Bruno asked before committing (NOT auto-committed)

---

## NOTES

### Frontmatter stamping is regex-based — locked in

Per the in-conversation decision: `atomic_write` patches the `updated:` field in YAML frontmatter via `re.sub`, no `pyyaml`. Three reasons:

1. `shared.py` stays std-lib-only, so hooks can run with system `python3` (the venv may not be activated when launchd/systemd fires a heartbeat in Phase 9).
2. The frontmatter format is fixed and we control all writers — there is no surprise YAML to parse.
3. Regex no-ops on files without frontmatter (legacy hand-edits, `_README.md` files), which is the desired graceful degradation.

If a future need arises (e.g., `tags:` mutation from reflection in Phase 6), revisit and decide whether to add `pyyaml` then. For Phase 2, regex is correct.

### Memory flush skip threshold — locked in

Transcripts under 2KB skip the SDK call entirely. The threshold catches "I just opened a session, asked a 1-line question, closed it" — those don't warrant a Sonnet 4.6 call. Real working sessions blow past 2KB after a single tool round-trip. If the threshold turns out to be too aggressive (decisions in short sessions getting dropped), tune in Phase 6 when heartbeat data is available.

### Why `dispatch_flush` lives in `shared.py`, not a hooks helper

The PRD has both flush hooks doing identical work. Putting `dispatch_flush` in `shared.py` instead of `hooks/_common.py` keeps the hooks' import path uniform with every other Phase 2+ script (everything imports from `shared`), and avoids creating a one-purpose helper module.

### `CLAUDE_INVOKED_BY` value taxonomy

Establishing the convention now so Phase 6 follows it:

| Script | Value |
|---|---|
| `.claude/scripts/memory_flush.py` (Phase 2) | `memory_flush` |
| `.claude/scripts/heartbeat.py` (Phase 6) | `heartbeat` |
| `.claude/scripts/memory_reflect.py` (Phase 6) | `reflection` |
| guardrail agent inside heartbeat (Phase 6) | `guardrail` |
| `.claude/skills/news-digest/scripts/digest.py` (Phase 5) | `news_digest` |
| `.claude/skills/weekly-review/scripts/aggregate_week.py` (Phase 5) | `weekly_review` |
| `.claude/chat/bot.py` (Phase 7) | `chat` |

The hooks check `if os.environ.get("CLAUDE_INVOKED_BY"):` (truthy) — they don't care about the specific value. Phase 8's `protect-soul.py` will check for the specific value `"reflection"` to block SOUL.md edits from reflection only.

### `.claude/settings.json` lifecycle

This file ships in Phase 2 with three SessionStart/PreCompact/SessionEnd entries. Phase 8 EXTENDS it with three PreToolUse entries. Plan a clean additive merge — don't reformat or restructure the file in Phase 8. (The PRD's settings.json snippet at lines 542–555 is already in the additive shape.)

### Confidence Score

**8.5/10** that execution succeeds in one pass. Risk:

- **SDK import shape may have shifted** since Phase 0's probe (1 point). Mitigated by re-probing at the start of Level 2 validation; abort + surface if `query` or `ClaudeAgentOptions` aren't where expected.
- **Hook event-name spelling** in `settings.json` (0.5 points). The PRD says `SessionStart`, `PreCompact`, `SessionEnd`. Confirm against current Claude Code docs at implementation time — these are case-sensitive and have been renamed across major versions of Claude Code in the past.

Mitigations are in the GOTCHAs of the relevant tasks.
