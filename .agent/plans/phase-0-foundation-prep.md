# Feature: Phase 0 — Foundation Prep (env, deps, repo skeleton)

The following plan should be complete, but it's important to validate the existing vault state and PRD details before starting implementation. Pay special attention to:

- Existing files at the project root and inside the BrunOS vault that should NOT be regenerated.
- The vault is a **CHILD** directory of the code repo (`/Users/brunobouwman/Documents/claude-second-brain/BrunOS/`). The code repo's `.git` currently covers the vault path too, but the vault has not been tracked yet (`git ls-files BrunOS` returns 0). Phase 9 will `git init` inside `BrunOS/` to make it its OWN repo for Mac↔VPS sync; Phase 0 prepares for that by gitignoring `BrunOS/` in the parent repo.
- All code paths that reference the vault must resolve via `BRUNOS_VAULT_PATH` env var (default: the absolute path to `claude-second-brain/BrunOS`). NEVER hardcode `BrunOS/` as a relative path — scripts may be invoked from any cwd (launchd, systemd, ad-hoc shells).
- The PRD's flagged gotcha: `claude-agent-sdk` `setting_sources` default has flipped between releases — the current default (>=0.1.x) is `None` (no filesystem settings loaded). All later phases must pass `setting_sources` **explicitly** even when matching the default, to be release-resilient.

## Feature Description

Phase 0 lays down the runnable scaffolding so every later phase has somewhere to land. This phase produces no application logic — it produces the shape (deps, env vars, ignored files, empty package markers, data dirs) and an initial `CLAUDE.md` that captures every convention later phases must respect. Without Phase 0 done correctly, Phase 1+ can't resolve the vault, can't load deps, and the agent has no project-level guidance file to read.

A secondary goal of this phase: **answer the open vault-structure question** ("should everything be under `Memory/`?"). The verdict is **yes** — the PRD intentionally puts the agent's read/write surface under `BrunOS/Memory/` so the vault root can hold non-memory items (`PRD.md`, `README.md`, `.obsidian/`, future asset folders) without polluting the agent's session-loaded context. We codify this in `CLAUDE.md` so future sessions don't re-litigate it.

## User Story

As Bruno (the operator of BrunOS)
I want a runnable, conventions-locked project scaffold (deps + env + ignored files + data dirs + CLAUDE.md)
So that every later phase has a deterministic landing zone, the vault path is unambiguously resolvable from code, and a fresh Claude Code session reading `CLAUDE.md` immediately knows the timezone, language routing, vault location, and forbidden patterns.

## Problem Statement

The vault has been seeded (Phase 1 deliverables exist under `claude-second-brain/BrunOS/Memory/`), but the **code repo** at `/Users/brunobouwman/Documents/claude-second-brain/` is a near-empty skeleton:

- `CLAUDE.md` exists but is empty (0 bytes) — Claude Code sessions get no project guidance.
- No `requirements.txt` → no way to install Python deps reproducibly.
- No `.env` / `.env.example` → no place to declare API tokens, the vault path, or DB backend.
- No `.gitignore` → risk of committing `.env`, `.venv/`, `__pycache__/`, fastembed model cache, the entire `BrunOS/` vault prematurely (Phase 9 wants vault to be its own repo), or active drafts (which can contain sensitive recipient context).
- No `.claude/scripts/` package skeleton or `.claude/data/` state dirs → Phase 2+ scripts have no parent dirs.
- The vault-vs-code path relationship is not yet codified anywhere — the chosen layout is vault-as-child-of-code-repo, but no env var resolves it and no `.gitignore` rule expresses "vault becomes its own repo in Phase 9".

## Solution Statement

Create the missing scaffolding files and dirs at the code repo root, populate `CLAUDE.md` with the canonical conventions (timezone, language routing, frontmatter spec, vault path resolution, recursion-guard pattern preview, secret rules, command index seed), set up the venv with pinned major versions, and verify each piece with executable validation commands. **No application code is written in this phase.**

We resolve the vault path with `BRUNOS_VAULT_PATH` env var (default in `.env.example`: `/Users/brunobouwman/Documents/claude-second-brain/BrunOS`). Every later script reads it via a single helper (`shared.vault_path()`, deferred to Phase 2 where `shared.py` is built) — Phase 0 only needs the env var declared in `.env.example` and documented in `CLAUDE.md`.

We gitignore `BrunOS/` in the parent repo's `.gitignore` because Phase 9 will `cd BrunOS && git init` to make the vault its own repo. Tracking it in the parent now would force Phase 9 to untangle a `git rm --cached -r BrunOS/` step. The vault's own internal `.gitignore` (active drafts, etc.) is a Phase 9 concern; we just document it in `CLAUDE.md` so it's not forgotten.

## Feature Metadata

**Feature Type**: New Capability (foundational scaffolding)
**Estimated Complexity**: Low
**Primary Systems Affected**: Repo root (`/Users/brunobouwman/Documents/claude-second-brain/`); `CLAUDE.md`; `.claude/` directory tree
**Dependencies**: Python 3.10+ (`claude-agent-sdk` requires it; Bruno has 3.13.3 — verified); `pip`; existing `BrunOS/` vault as child of repo

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: READ THESE BEFORE IMPLEMENTING

- `/Users/brunobouwman/Documents/claude-second-brain/.agent/plans/second-brain-prd.md` (lines 39–58, full Phase 0 spec) — Why: source-of-truth list of every file/dir/dep Phase 0 must produce. Section "Personalization notes" (lines 53–55) names the SDK gotcha.
- `/Users/brunobouwman/Documents/claude-second-brain/.agent/plans/second-brain-prd.md` (line 57, the Phase 0 CLAUDE.md mandate) — Why: spells out exactly what the initial CLAUDE.md must contain.
- `/Users/brunobouwman/Documents/claude-second-brain/.agent/plans/second-brain-prd.md` (lines 59–113, Phase 1) — Why: lists the Memory/ folder layout and YAML frontmatter spec that CLAUDE.md must encode. Already-built — DO NOT regenerate.
- `/Users/brunobouwman/Documents/claude-second-brain/.agent/plans/second-brain-prd.md` (lines 598–631, Phase 9 vault sync) — Why: spells out that the vault becomes its own git repo (`cd BrunOS && git init`). This drives the Phase 0 decision to gitignore `BrunOS/` in the parent repo.
- `/Users/brunobouwman/Documents/claude-second-brain/CLAUDE.md` — Why: file exists but is empty (0 bytes confirmed). We OVERWRITE with the initial content. (Verify still 0 bytes before overwriting; ABORT if non-empty.)
- `/Users/brunobouwman/Documents/claude-second-brain/BrunOS/README.md` — Why: vault-side README already lists the conventions (timezone, language routing, source-of-truth split). The repo-side `CLAUDE.md` should reference but not duplicate it.
- `/Users/brunobouwman/Documents/claude-second-brain/BrunOS/Memory/_README.md` (lines 32–69) — Why: source of truth for the Memory/ folder layout, the YAML frontmatter spec, and the migration notes (BOOTSTRAP.md absent — manually bootstrapped). CLAUDE.md must point here for the canonical layout.
- `/Users/brunobouwman/Documents/claude-second-brain/BrunOS/Memory/SOUL.md` — Why: confirms the agent's identity and security boundaries already exist in the vault. CLAUDE.md should NOT duplicate them; it should point sessions to load the vault.
- `/Users/brunobouwman/Documents/claude-second-brain/.claude/skills/create-second-brain-prd/SKILL.md` — Why: shows the existing `.claude/` structure (`skills/`, `commands/`). Confirms Phase 0 must add `scripts/`, `hooks/`, `data/` siblings, NOT replace the existing dirs.
- `/Users/brunobouwman/Documents/claude-second-brain/.claude/commands/` — Why: existing command files (commit.md, create-prd.md, init-project.md, plus subdirs `content-ideation/`, `core_piv_loop/`, `github_bug_fix/`, `validation/`). Pre-existing — DO NOT touch.

### Existing Vault State (verified 2026-05-02) — DO NOT REGENERATE

These already exist and are well-populated. Phase 0 must NOT touch them.

- `BrunOS/PRD.md` (30 KB) — copy of build PRD (vault-resident for cross-machine access)
- `BrunOS/README.md` (~2 KB) — vault overview (lists conventions, status: Phase 1 done, Phase 0 next)
- `BrunOS/.obsidian/` — Obsidian config + `obsidian-local-rest-api` plugin (do NOT delete; user uses Obsidian as viewer)
- `BrunOS/.DS_Store` — macOS junk; can be removed but not Phase 0's concern
- `BrunOS/Memory/SOUL.md` (~6 KB, populated; note: line 7 contains a `bruno-os/Memory/` typo — vault content owned by Bruno, do NOT silently fix)
- `BrunOS/Memory/USER.md` (~6 KB, populated; `Integration config` section has placeholders for Phase 4)
- `BrunOS/Memory/MEMORY.md` (~3.6 KB, populated; under 5 KB cap)
- `BrunOS/Memory/HEARTBEAT.md` (~2.4 KB, populated)
- `BrunOS/Memory/HABITS.md` (~1.6 KB, populated)
- `BrunOS/Memory/_README.md` (~5 KB, includes a "Weekend fill-in" placeholder list — user-action, not Phase 0's concern)
- `BrunOS/Memory/sources_of_truth.md` (~4 KB, extension to PRD — keep)
- `BrunOS/Memory/personal/finance.md` + `personal/_README.md` (PRD extension for personal finance — keep; CLAUDE.md must include `personal/` in the canonical folder list AND note `finance.md` is OFF-LIMITS per SOUL.md boundary)
- `BrunOS/Memory/projects/` — populated with `vertik.md`, `vertik_orcamento.md`, `vertik_architecture.md`, `vertik_clean_architecture.md`, `vertik_ideas.md`, `vertik_modular_addons_strategy.md`, `ai_mastery_course.md`
- `BrunOS/Memory/team/lisa.md` — populated
- `BrunOS/Memory/goals/` — populated with `personal_vision.md`, `this_week.md`, `this_month.md`, `_README.md`
- `BrunOS/Memory/daily/` — `2026-04-27.md`, `2026-04-28.md`, `_README.md`
- `BrunOS/Memory/drafts/{active,sent,expired}/_README.md` — empty subfolders with marker files
- `BrunOS/Memory/{clients,content,research,meetings,news-digest}/_README.md` — empty subfolders with marker files
- `BrunOS/Memory/BOOTSTRAP.md` — **absent by design** (vault was bootstrapped manually pre-migration; SessionStart hook in Phase 2 will not see it; documented in `Memory/_README.md` line 41). CLAUDE.md notes this so Phase 2 doesn't re-introduce it.

### New Files to Create (Phase 0 deliverables)

All paths relative to `/Users/brunobouwman/Documents/claude-second-brain/`.

- `requirements.txt` — pinned major versions of every Python dep used in Phases 2–7.
- `.env.example` — template (committed) with every env var name + a comment, no real secrets.
- `.env` — actual values (gitignored). Created from `.env.example`. Filled progressively as integrations are wired (Phase 4). Phase 0 only needs `BRUNOS_VAULT_PATH` and `DB_BACKEND=sqlite` populated to be testable.
- `.gitignore` — `.env`, `.env.local`, `.venv/`, `__pycache__/`, `*.pyc`, `.claude/data/`, `BrunOS/` (whole vault — see solution statement), `.DS_Store`, `*.egg-info/`, `.pytest_cache/`, `.ruff_cache/`.
- `.claude/scripts/__init__.py` — empty package marker (so `from .claude.scripts.shared import ...` resolves in Phase 2).
- `.claude/scripts/integrations/__init__.py` — empty package marker.
- `.claude/data/state/.gitkeep` — preserves the dir in git (contents are gitignored).
- `.claude/data/fastembed_cache/.gitkeep` — preserves the dir in git (contents are gitignored).
- `.claude/hooks/.gitkeep` — preserves the (empty) hooks dir for Phase 2 to populate.
- `CLAUDE.md` — initial content (currently empty 0-byte file, will be overwritten).

### New Directories to Create

- `.claude/scripts/` and `.claude/scripts/integrations/`
- `.claude/data/state/` and `.claude/data/fastembed_cache/`
- `.claude/hooks/`
- `.venv/` — Python 3.10+ virtualenv (gitignored)

### Relevant Documentation — READ BEFORE IMPLEMENTING

- [claude-agent-sdk Python on PyPI](https://pypi.org/project/claude-agent-sdk/) — install requirements + breaking changes. Why: Confirms Python 3.10+ requirement, that the CLI is bundled, and that `setting_sources` default is `None` in current releases. Verify the exact latest version at install time and pin major.
- [Claude Agent SDK overview](https://docs.claude.com/en/api/agent-sdk/overview) — filesystem settings & `setting_sources`. Why: Confirms `setting_sources=None` (default) loads NO `.claude/`/`~/.claude/` settings — for skills + CLAUDE.md to load, must pass `setting_sources=["project"]` (or include `"user"`).
- [Claude Agent SDK Python reference](https://docs.claude.com/en/api/agent-sdk/python) — `ClaudeAgentOptions` parameter list. Why: Phase 0 verification step probes the actual installed shape rather than trusting docs.
- [sqlite-vec on PyPI](https://pypi.org/project/sqlite-vec/) — Why: Phase 3 dep; verify it builds on macOS arm64 before pinning.
- [FastEmbed docs](https://qdrant.github.io/fastembed/) — Why: Phase 3 embedding dep; confirm `BAAI/bge-small-en-v1.5` is supported and the ONNX model size (~130 MB) for budgeting `.claude/data/fastembed_cache/`.
- [git-sync README](https://github.com/simonthum/git-sync) — Why: Phase 9 dep (vault sync); not installed in Phase 0, but referenced in `CLAUDE.md`'s "deferred to Phase 9" note.

### Patterns to Follow

The repo currently has only the `.agent/plans/` and `.claude/{commands,skills}/` patterns. Phase 0 establishes the patterns later phases will mirror.

**File-naming patterns:**

- Python modules: `snake_case` (`memory_flush.py`, `block_secrets.py`).
- Hooks: `kebab-case` (`session-start-context.py`, `block-secrets.py`) — matches Claude Code's hook convention used elsewhere in the ecosystem.
- Markdown vault notes: `YYYY-MM-DD.md` for daily, `YYYY-MM-DD-slug.md` for meetings, `<slug>.md` for projects/clients.
- Scripts that wrap Claude Agent SDK calls: must set `os.environ["CLAUDE_INVOKED_BY"] = "<purpose>"` BEFORE `import claude_agent_sdk` (Phase 2 detail; preview in CLAUDE.md so future readers don't forget).

**Env-var naming pattern:**

- Use uppercase `SCREAMING_SNAKE_CASE`.
- Group by integration (`SLACK_*`, `GITHUB_*`, `CLICKUP_*`, `GOOGLE_*`).
- Path-like vars end with `_PATH`; count-like vars end with `_LIMIT`.

**Pinning policy for `requirements.txt`:**

- Major-pin (`>=X,<X+1`) for 1.x+ libs.
- Major-and-minor pin (`>=X.Y.Z,<X.(Y+1)`) for pre-1.0 SDKs (`claude-agent-sdk`, `sqlite-vec`, `fastembed`) — 0.x is not semver-stable; minor bumps frequently break.
- Rationale per PRD line 46: "Pin major versions" — pre-1.0 SDKs need tighter constraints because breaking changes between minors are common.

**Other Relevant Patterns:**

- `.claude/data/` is the per-machine, never-committed state dir. Anything written by hooks or scripts at runtime lives here. Synced vault content (daily logs, drafts) lives in `BrunOS/Memory/`, NOT here.
- `BRUNOS_VAULT_PATH` must be settable per-machine so the VPS deploy (Phase 9) can point at e.g. `/home/bruno/BrunOS`.
- Vault's own `.gitignore` (Phase 9 concern, NOT Phase 0): inside `BrunOS/.gitignore` will live `Memory/drafts/active/*` (active drafts contain sensitive recipient context per PRD line 48). Phase 0 must NOT try to express this from the parent repo's `.gitignore` — once `BrunOS/` is whole-gitignored at the parent level, the parent has no opinion about its internals.

---

## IMPLEMENTATION PLAN

### Phase 1: Verify pre-existing state (no writes)

We confirm the vault state matches what this plan assumes — that the seeded `Memory/` files are present, that `CLAUDE.md` is empty (so overwriting is safe), and that no `BOOTSTRAP.md` exists. If any assumption is wrong, the implementation agent must STOP and ask.

**Tasks:**

- Confirm `claude-second-brain/BrunOS/Memory/` exists and contains SOUL.md, USER.md, MEMORY.md, HEARTBEAT.md, HABITS.md.
- Confirm `claude-second-brain/CLAUDE.md` is exactly 0 bytes (empty) — if it has content, ABORT and surface to user.
- Confirm `BrunOS/Memory/BOOTSTRAP.md` is ABSENT.
- Confirm Python 3.10+ is on PATH (`python3 --version` ≥ 3.10).

### Phase 2: Create gitignore and env templates

Order matters — `.gitignore` first so subsequent file creations don't accidentally stage secrets, and so `BrunOS/` doesn't pollute `git status` in subsequent steps.

**Tasks:**

- CREATE `.gitignore` with the full pattern list (includes `BrunOS/` to keep the vault out of the parent repo per Phase 9 sync model).
- CREATE `.env.example` (committed) — every env var name with an inline comment.
- CREATE `.env` (gitignored) — copy of `.env.example` with `BRUNOS_VAULT_PATH=/Users/brunobouwman/Documents/claude-second-brain/BrunOS` and `DB_BACKEND=sqlite` set; all other secrets left as empty placeholders for Phase 4.

### Phase 3: Create directory skeleton

**Tasks:**

- CREATE `.claude/scripts/` and `.claude/scripts/__init__.py` (empty).
- CREATE `.claude/scripts/integrations/` and `.claude/scripts/integrations/__init__.py` (empty).
- CREATE `.claude/data/state/.gitkeep`.
- CREATE `.claude/data/fastembed_cache/.gitkeep`.
- CREATE `.claude/hooks/.gitkeep`.

### Phase 4: Pin dependencies and bootstrap venv

**Tasks:**

- CREATE `requirements.txt` with every dep + version pin. Group by phase (foundation, integrations, vector, vps-only).
- CREATE `.venv/` via `python3 -m venv .venv`.
- INSTALL via `source .venv/bin/activate && pip install -U pip && pip install -r requirements.txt` (do NOT install `psycopg[binary]` on Mac; it's VPS-only — PRD line 54).
- VERIFY SDK shape: probe `ClaudeAgentOptions` to confirm `setting_sources` field exists; if not, the install is wrong and the agent must surface it before later phases assume the field name.

### Phase 5: Initialize CLAUDE.md (overwrite the 0-byte placeholder)

**Tasks:**

- OVERWRITE `claude-second-brain/CLAUDE.md` with the canonical project guidance file. Sections required (per PRD line 57 + Phase 0 mandate):
  1. **Project description** — 2 sentences max. Names BrunOS, what it does, that the vault lives at `BRUNOS_VAULT_PATH`.
  2. **Vault path resolution** — `BRUNOS_VAULT_PATH` env var, default `<repo>/BrunOS`, with the helper-deferred-to-Phase-2 note. Document that the vault is a CHILD of this repo today and becomes its own git repo in Phase 9 (so the parent `.gitignore` ignores `BrunOS/`).
  3. **Key paths** — `Memory/` subfolder list with one-line purpose for each (mirrors `BrunOS/Memory/_README.md` lines 32–55 but condensed). Includes `personal/` (PRD extension) with note that `personal/finance.md` is OFF-LIMITS per SOUL.md. Notes `BOOTSTRAP.md` is absent by design.
  4. **Conventions** — timezone (America/Sao_Paulo / GMT-3), date format (YYYY-MM-DD), timestamp format (RFC3339 with `-03:00`), checkbox syntax (`- [ ]`), language routing (Brazilian recipient → PT, else EN, internal memory always EN), no-secrets-in-vault rule.
  5. **YAML frontmatter spec** — copied from `BrunOS/Memory/_README.md` lines 60–68. Single source of truth: this CLAUDE.md.
  6. **Proactivity level** — Assistant (act on low-risk: log/draft/organize; ask for high-risk: send/post/delete/finance/new tasks).
  7. **Build commands** — seeded with: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`. Each later phase's CLAUDE.md update appends here.
  8. **Recursion guard preview** — one paragraph: every Agent SDK script must set `CLAUDE_INVOKED_BY=<purpose>` BEFORE importing `claude_agent_sdk`. Without it, SessionEnd-triggered flushes infinite-loop. (Full pattern in Phase 2.)
  9. **`setting_sources` policy** — every `ClaudeAgentOptions(...)` call must pass `setting_sources` EXPLICITLY (None, ["project"], or ["user","project"]) — never rely on the default, which has shifted between releases.
  10. **Vault-repo split (Phase 9 preview)** — note that `BrunOS/` is gitignored at the parent level; Phase 9 runs `cd BrunOS && git init` and adds `Memory/drafts/active/*` to the vault's own `.gitignore`. This is documented now so Phase 9 doesn't re-discover the constraint.
  11. **Completed phases** — `- [x] Phase 0 — Foundation prep (2026-05-02)`, `- [x] Phase 1 — Memory layer (vault seeded manually 2026-05-01; BOOTSTRAP.md skipped by design)`, then `- [ ] Phase 2 — Hooks` and the rest as TODO. Phase 1 is checked because the vault is already populated; this is recorded so future sessions know not to re-bootstrap.

### Phase 6: Validate

**Tasks:**

- Run every validation command in the VALIDATION COMMANDS section.
- Confirm `git status` shows the expected new files staged-able and `.env`, `.venv/`, `BrunOS/` UNTRACKED-and-IGNORED (proving `.gitignore` is correct).

---

## STEP-BY-STEP TASKS

Execute every task in order. Each task has a single executable validation. Run from `/Users/brunobouwman/Documents/claude-second-brain/`.

### VERIFY pre-existing state

- **CHECK**: `BrunOS/Memory/SOUL.md`, `USER.md`, `MEMORY.md`, `HEARTBEAT.md`, `HABITS.md` all exist and are non-empty.
- **CHECK**: `claude-second-brain/CLAUDE.md` is exactly 0 bytes.
- **CHECK**: `BrunOS/Memory/BOOTSTRAP.md` does NOT exist (absent by design).
- **CHECK**: `python3 --version` reports 3.10 or higher.
- **GOTCHA**: If `CLAUDE.md` is non-empty, ABORT and surface to Bruno — overwriting could destroy his prior content. If Python is < 3.10, ABORT and surface — `claude-agent-sdk` requires it. If `BOOTSTRAP.md` exists, surface to Bruno — the vault was supposed to be manually bootstrapped; an unexpected BOOTSTRAP.md means migration state is inconsistent.
- **VALIDATE**:
  ```bash
  test -s BrunOS/Memory/SOUL.md && \
  test -s BrunOS/Memory/USER.md && \
  test -s BrunOS/Memory/MEMORY.md && \
  test -s BrunOS/Memory/HEARTBEAT.md && \
  test -s BrunOS/Memory/HABITS.md && \
  test ! -s CLAUDE.md && \
  test ! -e BrunOS/Memory/BOOTSTRAP.md && \
  python3 -c "import sys; assert sys.version_info >= (3, 10), sys.version" && \
  echo OK
  ```

### CREATE `.gitignore`

- **IMPLEMENT**: Single `.gitignore` at the repo root with patterns for venvs, Python bytecode, env files, runtime data, the entire vault dir (per Phase 9 sync model), and OS files.
- **PATTERN**: Standard Python `.gitignore` + project-specific runtime dirs from PRD line 48 + vault-as-separate-repo decision.
- **CONTENT** (exact):
  ```gitignore
  # Python
  __pycache__/
  *.pyc
  *.pyo
  *.egg-info/
  .pytest_cache/
  .ruff_cache/
  .mypy_cache/

  # Virtualenv
  .venv/
  venv/

  # Env / secrets
  .env
  .env.local
  .env.*.local

  # Runtime data (per-machine state, never commit)
  .claude/data/

  # Vault (BrunOS/) is its own concern.
  # Phase 9 will run `cd BrunOS && git init` so the vault syncs Mac<->VPS as its own repo.
  # Until then, ignore from this (code) repo so it doesn't get half-tracked.
  # Vault-internal ignores (e.g. Memory/drafts/active/*) live in BrunOS/.gitignore (Phase 9).
  BrunOS/

  # OS / editors
  .DS_Store
  Thumbs.db
  *.swp
  .idea/
  .vscode/
  ```
- **GOTCHA**: Do NOT write `BrunOS/Memory/drafts/active/*` here — once `BrunOS/` is whole-ignored, more-specific rules under it have no effect. The active-drafts ignore lives inside the vault's OWN `.gitignore` in Phase 9.
- **GOTCHA**: If a future change wants to track *some* vault file from the parent repo (e.g., a top-level README cross-referencing both), use `!BrunOS/<file>` un-ignore patterns. Don't remove the `BrunOS/` ignore wholesale — it would re-create the Phase 9 untangle problem.
- **VALIDATE**:
  ```bash
  git check-ignore -q .env && \
  git check-ignore -q .venv/foo && \
  git check-ignore -q .claude/data/state/x && \
  git check-ignore -q BrunOS/Memory/SOUL.md && \
  git check-ignore -q .DS_Store && \
  echo "all expected paths ignored"
  ```

### CREATE `.env.example`

- **IMPLEMENT**: Template with every env var Phase 4+ will eventually need. Phase 0 doesn't fill them — it just declares the shape.
- **CONTENT** (exact):
  ```bash
  # === BrunOS environment ===
  # Copy to .env and fill in values. .env is gitignored; .env.example is committed.

  # --- Vault path ---
  # Absolute path to the BrunOS vault. On Mac this is the in-repo child dir;
  # on VPS (Phase 9) it lives at /home/bruno/BrunOS. Always absolute, no trailing slash.
  BRUNOS_VAULT_PATH=/Users/brunobouwman/Documents/claude-second-brain/BrunOS

  # --- DB backend (Phase 3) ---
  # sqlite on Mac, postgres on VPS
  DB_BACKEND=sqlite
  POSTGRES_URL=

  # --- Anthropic (Phases 2, 6, 7) ---
  ANTHROPIC_API_KEY=

  # --- Slack (Phase 4.1) ---
  SLACK_BOT_TOKEN=
  SLACK_APP_TOKEN=

  # --- GitHub (Phase 4.2) ---
  # Fine-grained PAT with per-repo Contents/Issues/PRs read+write, Metadata read
  GITHUB_TOKEN=

  # --- ClickUp (Phase 4.3) ---
  CLICKUP_API_TOKEN=
  # Cached after first GET /api/v2/team
  CLICKUP_TEAM_ID=

  # --- Google (Phase 4.4) ---
  # Path to the OAuth client_secrets.json downloaded from Google Cloud Console
  GOOGLE_OAUTH_CLIENT_SECRETS_PATH=
  ```
- **GOTCHA**: Do NOT include real tokens. Even commented-out real tokens leak via git history.
- **VALIDATE**: `grep -c '^[A-Z_][A-Z_0-9]*=' .env.example` should return 9.

### CREATE `.env`

- **IMPLEMENT**: Copy `.env.example` to `.env`, then ensure `BRUNOS_VAULT_PATH` is set to the actual absolute in-repo vault path and `DB_BACKEND=sqlite`. All other vars stay empty placeholders for Phase 4.
- **GOTCHA**: Confirm `.env` is gitignored AFTER creating it: `git check-ignore .env` must succeed (exit 0). If it doesn't, the `.gitignore` step failed.
- **VALIDATE**:
  ```bash
  set -a && source .env && set +a && \
  test "$BRUNOS_VAULT_PATH" = "/Users/brunobouwman/Documents/claude-second-brain/BrunOS" && \
  test "$DB_BACKEND" = "sqlite" && \
  test -d "$BRUNOS_VAULT_PATH/Memory" && \
  git check-ignore -q .env && \
  echo OK
  ```

### CREATE `.claude/scripts/` package skeleton

- **IMPLEMENT**: Create the dir tree and empty `__init__.py` files so Phase 2's `shared.py` can be imported from any cwd inside the repo.
- **STRUCTURE**:
  ```
  .claude/
    scripts/
      __init__.py             # empty
      integrations/
        __init__.py           # empty
  ```
- **VALIDATE**:
  ```bash
  test -f .claude/scripts/__init__.py && \
  test -f .claude/scripts/integrations/__init__.py && \
  echo OK
  ```

### CREATE `.claude/data/` runtime dirs

- **IMPLEMENT**: Create state and fastembed cache dirs. Both contents are gitignored, but the dirs themselves are preserved with `.gitkeep`.
- **STRUCTURE**:
  ```
  .claude/
    data/
      state/.gitkeep
      fastembed_cache/.gitkeep
  ```
- **GOTCHA**: `.gitignore` ignores `.claude/data/` entirely, so `.gitkeep` files must be force-added: `git add -f .claude/data/state/.gitkeep .claude/data/fastembed_cache/.gitkeep`.
- **VALIDATE**:
  ```bash
  test -f .claude/data/state/.gitkeep && \
  test -f .claude/data/fastembed_cache/.gitkeep && \
  echo OK
  ```

### CREATE `.claude/hooks/` placeholder

- **IMPLEMENT**: `.claude/hooks/.gitkeep` so Phase 2's hook scripts have a parent dir.
- **VALIDATE**: `test -d .claude/hooks && test -f .claude/hooks/.gitkeep && echo OK`

### CREATE `requirements.txt`

- **IMPLEMENT**: Pinned deps. Group by phase via comment headers. Use major-pins (`>=X.Y,<X.(Y+1)`) for pre-1.0 SDKs and major-pins (`>=X,<X+1`) for 1.x+ libs.
- **PATTERN**: Per PRD line 46. Verify each version exists on PyPI before pinning (latest known good is fine; `pip install --dry-run` will catch typos).
- **CONTENT** (exact):
  ```text
  # === BrunOS Python dependencies ===
  # Pin major (and minor for pre-1.0) per PRD Phase 0.
  # Mac install: pip install -r requirements.txt
  # VPS install: pip install -r requirements.txt && pip install "psycopg[binary]>=3.2,<4"

  # --- Foundation (Phases 2, 6, 7) ---
  claude-agent-sdk>=0.1,<0.2
  python-dotenv>=1.0,<2

  # --- Vector / RAG (Phase 3) ---
  fastembed>=0.4,<0.5
  sqlite-vec>=0.1.6,<0.2
  numpy>=1.26,<3

  # --- Slack (Phase 4.1) ---
  slack_sdk>=3.27,<4
  slack_bolt>=1.20,<2

  # --- GitHub (Phase 4.2) ---
  PyGithub>=2.3,<3

  # --- Google: Gmail + Calendar (Phase 4.4) ---
  google-api-python-client>=2.130,<3
  google-auth-oauthlib>=1.2,<2
  google-auth-httplib2>=0.2,<0.3

  # --- HTTP / RSS (Phase 4 + 4.5) ---
  requests>=2.31,<3
  feedparser>=6.0,<7

  # --- VPS only: Postgres + pgvector ---
  # Install separately on VPS: pip install "psycopg[binary]>=3.2,<4"
  # Do NOT add here to keep Mac install clean.
  ```
- **GOTCHA**: Do NOT pin `claude-agent-sdk` to an exact patch (`==X.Y.Z`). The PRD's note about `setting_sources` defaults flipping between releases means a patch upgrade may shift behavior; use a major-pin (`<0.2`) and verify the field shape post-install via the SDK shape probe step below.
- **GOTCHA**: `psycopg[binary]` is VPS-only per PRD line 54. Including it on Mac forces a libpq build that often fails on Apple Silicon. Comment it out in this file; install ad-hoc on the VPS in Phase 9.
- **GOTCHA**: If `fastembed` install fails on Apple Silicon with "missing Xcode CLT", surface to Bruno — the fix is `xcode-select --install`, which is interactive.
- **GOTCHA**: If `sqlite-vec` wheel isn't available for Bruno's Python version (3.13), the install will try to build from source. If that fails, surface to Bruno BEFORE proceeding to CLAUDE.md — Phase 3 needs it. Possible mitigations: pin to a Python version with prebuilt wheels, or defer the dep with a comment.
- **VALIDATE** (after venv created): `pip install --dry-run -r requirements.txt` must resolve without conflicts.

### CREATE venv and install deps

- **IMPLEMENT**: `python3 -m venv .venv && source .venv/bin/activate && pip install -U pip && pip install -r requirements.txt`
- **GOTCHA**: On macOS, fastembed downloads ONNX runtime — first install can take 2–4 min. Don't kill it.
- **GOTCHA**: If the venv was created by a different Python (e.g., 3.9), recreate it. Bruno's `python3` is 3.13.3 — this should be fine.
- **VALIDATE**:
  ```bash
  source .venv/bin/activate && \
  python -c "import claude_agent_sdk, fastembed, sqlite_vec, slack_sdk, github, googleapiclient, feedparser, dotenv; print('imports OK')"
  ```

### VERIFY `claude-agent-sdk` shape

- **IMPLEMENT**: One-shot probe to confirm the install exposes `setting_sources` (or whatever the equivalent param is named in the installed version).
- **VALIDATE** (must run inside the activated venv):
  ```bash
  source .venv/bin/activate && python -c "
  from claude_agent_sdk import ClaudeAgentOptions
  fields = [f for f in dir(ClaudeAgentOptions) if not f.startswith('_')]
  print('Fields:', fields)
  assert 'setting_sources' in fields or any('setting' in f.lower() for f in fields), \
      'setting_sources field missing — SDK API may have changed; check CHANGELOG'
  print('OK: setting_sources field present')
  "
  ```
- **GOTCHA**: If this validation fails, STOP and surface to Bruno before proceeding to overwrite CLAUDE.md — Phase 2+ scripts depend on the field name being `setting_sources`. Don't write CLAUDE.md against an assumption that's no longer true.

### OVERWRITE `CLAUDE.md`

- **IMPLEMENT**: Replace the 0-byte file with the canonical project guidance. Sections in order (per Phase 5 of this plan's IMPLEMENTATION PLAN). Aim for ~150–250 lines max — CLAUDE.md is loaded into every session and bloat costs tokens.
- **PATTERN**: Mirror the section structure of `BrunOS/Memory/_README.md` for the Memory/ folder list, but de-duplicate. CLAUDE.md is the project-side guide; vault-side `_README.md` covers vault internals.
- **CONTENT outline** (write the file with this structure; copy convention text from the cited vault files):
  ````markdown
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

  ## YAML frontmatter (every agent-written note)

  ```yaml
  ---
  type: meeting | project | client | research | goal | content | team | draft | digest | personal
  created: 2026-05-02T09:00-03:00
  tags: [...]
  status: active | archived | done
  ---
  ```

  Drafts have an extended frontmatter (`source_id`, `recipient`, `subject`, `context`, `language`, `status`).

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
  ```

  ## Phase status

  - [x] Phase 0 — Foundation prep (2026-05-02)
  - [x] Phase 1 — Memory layer (vault seeded manually 2026-05-01; BOOTSTRAP.md skipped by design)
  - [ ] Phase 2 — Hooks
  - [ ] Phase 3 — Memory search (hybrid RAG)
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
  ````
- **GOTCHA**: Do NOT duplicate SOUL.md content here. CLAUDE.md is the *project* guide; agent identity lives in the vault. Sessions load both (once Phase 2 SessionStart hook is wired).
- **GOTCHA**: Keep total under ~250 lines. Every line costs tokens on every session start.
- **VALIDATE**:
  ```bash
  test -s CLAUDE.md && \
  grep -q "BRUNOS_VAULT_PATH" CLAUDE.md && \
  grep -q "CLAUDE_INVOKED_BY" CLAUDE.md && \
  grep -q "setting_sources" CLAUDE.md && \
  grep -q "America/Sao_Paulo" CLAUDE.md && \
  grep -q "Phase 0" CLAUDE.md && \
  LINES=$(wc -l < CLAUDE.md) && \
  [ "$LINES" -lt 300 ] && \
  echo "CLAUDE.md $LINES lines — OK"
  ```

### COMMIT (Bruno-controlled — agent does NOT auto-commit)

- **IMPLEMENT**: After all validations pass, surface to Bruno: "Phase 0 scaffolding ready. Suggested commit message: `feat: Phase 0 scaffolding (deps, env, CLAUDE.md, .claude/ skeleton)`. Want me to commit now or do you want to review first?"
- **GOTCHA**: Per the global Claude Code rules, never auto-commit unless explicitly asked.

---

## TESTING STRATEGY

Phase 0 produces no application logic, so there are no unit tests. Validation is a sequence of file-existence and import checks plus one shape probe of `claude-agent-sdk`.

### Unit Tests

N/A for Phase 0.

### Integration Tests

N/A for Phase 0.

### Edge Cases

- **Python version mismatch.** If a globally-installed Python is < 3.10, the venv may inherit it. Validation must check the venv's Python, not just `python3 --version`: `source .venv/bin/activate && python --version`. Bruno has 3.13.3 globally, so this should pass.
- **Existing CLAUDE.md non-empty.** If the file is unexpectedly populated, ABORT before overwriting. Pre-flight check covers this.
- **Re-running Phase 0.** Should be idempotent — re-running must not duplicate `.gitignore` lines, must not overwrite a now-non-empty `.env` with an empty template, and must NOT overwrite a now-non-empty CLAUDE.md.
- **Vault path missing.** If `BRUNOS_VAULT_PATH` resolves to a non-existent dir, `.env` validation fails fast.
- **`.env` accidentally tracked.** After creation, run `git check-ignore .env` to confirm it's ignored. If exit code ≠ 0, the `.gitignore` is wrong.
- **`BrunOS/` half-tracked from a prior run.** If Bruno previously ran `git add BrunOS/`, the new `.gitignore` won't un-track already-staged files. Validation must check `git ls-files BrunOS/ | wc -l` returns 0. If non-zero, surface and recommend `git rm --cached -r BrunOS/`.
- **`fastembed` first-install slowness.** Don't time out the install at < 5 min.
- **`sqlite-vec` no wheel for Python 3.13.** If install fails, this is a release-resilience question — surface to Bruno before deferring.

---

## VALIDATION COMMANDS

Run from `/Users/brunobouwman/Documents/claude-second-brain/`. Each level must pass before the next.

### Level 1: Pre-flight

```bash
# Confirm Python ≥ 3.10
python3 -c "import sys; assert sys.version_info >= (3, 10), f'Python {sys.version} too old'; print('Python', sys.version_info[:2])"

# Confirm vault is reachable
test -d BrunOS/Memory && echo "vault OK"

# Confirm CLAUDE.md is empty (safe to overwrite)
test ! -s CLAUDE.md && echo "CLAUDE.md empty — safe to overwrite" || echo "ABORT — CLAUDE.md has content"

# Confirm BOOTSTRAP.md is absent (per migration design)
test ! -e BrunOS/Memory/BOOTSTRAP.md && echo "BOOTSTRAP.md absent — expected" || echo "ABORT — unexpected BOOTSTRAP.md"
```

### Level 2: Files & dirs created

```bash
test -f .gitignore && \
test -f .env.example && \
test -f .env && \
test -f requirements.txt && \
test -d .venv && \
test -f .claude/scripts/__init__.py && \
test -f .claude/scripts/integrations/__init__.py && \
test -f .claude/data/state/.gitkeep && \
test -f .claude/data/fastembed_cache/.gitkeep && \
test -d .claude/hooks && \
test -f .claude/hooks/.gitkeep && \
test -s CLAUDE.md && \
echo "all Phase 0 deliverables present"
```

### Level 3: Gitignore correctness

```bash
# These must all be ignored (exit 0)
git check-ignore -q .env && echo ".env ignored OK"
git check-ignore -q .venv/foo && echo ".venv ignored OK"
git check-ignore -q .claude/data/state/foo.json && echo ".claude/data ignored OK"
git check-ignore -q BrunOS/Memory/SOUL.md && echo "BrunOS/ ignored OK"

# These must NOT be ignored (exit 1)
! git check-ignore -q CLAUDE.md 2>/dev/null && echo "CLAUDE.md tracked OK"
! git check-ignore -q requirements.txt 2>/dev/null && echo "requirements.txt tracked OK"
! git check-ignore -q .gitignore 2>/dev/null && echo ".gitignore tracked OK"

# Vault must NOT have any tracked files in the parent repo
[ "$(git ls-files BrunOS/ 2>/dev/null | wc -l)" -eq 0 ] && echo "BrunOS/ untracked OK" || echo "WARN: BrunOS/ has tracked files — run 'git rm --cached -r BrunOS/'"
```

### Level 4: Dependency install

```bash
source .venv/bin/activate
python -c "import claude_agent_sdk, fastembed, sqlite_vec, slack_sdk, github, googleapiclient, feedparser, dotenv; print('all imports OK')"
```

### Level 5: SDK shape probe

```bash
source .venv/bin/activate
python -c "
from claude_agent_sdk import ClaudeAgentOptions
fields = [f for f in dir(ClaudeAgentOptions) if not f.startswith('_')]
assert 'setting_sources' in fields or any('setting' in f.lower() for f in fields), 'setting_sources field missing'
print('SDK OK — setting_sources field present')
"
```

### Level 6: CLAUDE.md content sanity

```bash
grep -q "BRUNOS_VAULT_PATH" CLAUDE.md && \
grep -q "CLAUDE_INVOKED_BY" CLAUDE.md && \
grep -q "setting_sources" CLAUDE.md && \
grep -q "America/Sao_Paulo" CLAUDE.md && \
grep -q "Phase 0" CLAUDE.md && \
grep -q "BOOTSTRAP.md" CLAUDE.md && \
echo "CLAUDE.md contains required sections"

# Token budget check — keep CLAUDE.md tight
LINES=$(wc -l < CLAUDE.md)
[ "$LINES" -lt 300 ] && echo "CLAUDE.md $LINES lines — OK" || echo "WARN: CLAUDE.md $LINES lines — consider trimming"
```

### Level 7: Env loads cleanly

```bash
set -a && source .env && set +a
test "$BRUNOS_VAULT_PATH" = "/Users/brunobouwman/Documents/claude-second-brain/BrunOS" && \
test "$DB_BACKEND" = "sqlite" && \
test -d "$BRUNOS_VAULT_PATH/Memory" && \
echo "env OK"
```

---

## ACCEPTANCE CRITERIA

- [ ] All Phase 0 deliverables exist (`.gitignore`, `.env.example`, `.env`, `requirements.txt`, `.venv/`, `.claude/scripts/{,integrations/}__init__.py`, `.claude/data/{state,fastembed_cache}/.gitkeep`, `.claude/hooks/.gitkeep`, populated `CLAUDE.md`)
- [ ] `git check-ignore` confirms `.env`, `.venv/`, `.claude/data/`, and `BrunOS/` are ignored
- [ ] `git ls-files BrunOS/` returns 0 (vault not tracked by parent repo)
- [ ] `pip install -r requirements.txt` completes without conflicts on Apple Silicon macOS
- [ ] `python -c "from claude_agent_sdk import ClaudeAgentOptions"` succeeds in the venv
- [ ] `setting_sources` field exists on `ClaudeAgentOptions` (or doc-flagged equivalent)
- [ ] `CLAUDE.md` contains: vault path resolution, vault-repo split note, key paths, conventions, frontmatter spec, proactivity policy, recursion guard, `setting_sources` policy, build commands, phase status. Length under 300 lines.
- [ ] Vault state UNTOUCHED — no edits to `BrunOS/Memory/*` during Phase 0
- [ ] No regressions in `.claude/commands/` or `.claude/skills/` (pre-existing)
- [ ] CLAUDE.md `Phase status` section marks Phase 0 and Phase 1 as `[x]`

---

## COMPLETION CHECKLIST

- [ ] All tasks executed top-to-bottom in order
- [ ] Each task's validation passed immediately
- [ ] Levels 1–7 of VALIDATION COMMANDS all pass
- [ ] `pip install -r requirements.txt` clean run
- [ ] `python -c "import claude_agent_sdk, fastembed, sqlite_vec, slack_sdk, github, googleapiclient, feedparser, dotenv"` clean
- [ ] CLAUDE.md content reviewed for accuracy against vault state
- [ ] Bruno asked before committing (NOT auto-committed)
- [ ] Phase 0 mark in CLAUDE.md `Phase status` section is `[x]`

---

## NOTES

### Answer to "should it be like that?" (re: Memory/ subfolder)

Yes — confirmed correct. The PRD intentionally puts the agent's memory under `BrunOS/Memory/` rather than at the vault root because:

1. **Vault root holds non-memory files** (`PRD.md`, `README.md`, `.obsidian/`, future asset folders, the `.gitattributes` for the `concat-both` merge driver from Phase 9). Putting `SOUL.md` etc. at the root would mix agent context with vault-management files and force more careful path filtering in the SessionStart hook.
2. **Sync surface is the whole vault, but loaded surface is `Memory/`**. Phase 9 syncs `BrunOS/` between Mac and VPS as one git repo. The SessionStart hook only reads `Memory/` — clean separation between "what gets synced" and "what gets loaded into context every session".
3. **Future-proofs** for things like `BrunOS/archive/` (cold storage), `BrunOS/scripts/` (vault-side helpers separate from `.claude/scripts/`), or media folders that shouldn't be loaded into agent context.

The structure as built matches the PRD. No restructuring needed.

### Code repo vs. vault repo (current layout)

The chosen layout: vault is a CHILD of the code repo, but treated as a separate git concern.

```
~/Documents/claude-second-brain/    # CODE REPO (this .git)
├── .claude/                        # hooks, scripts, skills, settings
│   ├── commands/                   # pre-existing (commit, create-prd, etc.)
│   ├── skills/                     # pre-existing (create-second-brain-prd)
│   ├── scripts/                    # NEW (Phase 0)
│   ├── hooks/                      # NEW (Phase 0)
│   └── data/                       # NEW (Phase 0, gitignored)
├── .agent/plans/                   # planning docs (PRD, this plan)
├── CLAUDE.md                       # NEW (Phase 0)
├── requirements.txt                # NEW (Phase 0)
├── .env / .env.example             # NEW (Phase 0)
├── .gitignore                      # NEW (Phase 0; ignores BrunOS/)
├── .venv/                          # NEW (Phase 0, gitignored)
└── BrunOS/                         # VAULT (gitignored from parent;
    ├── PRD.md                      #   becomes own .git in Phase 9)
    ├── README.md
    ├── .obsidian/
    └── Memory/
```

Why not sibling? The user already has the vault at this path with seeded content; moving it would break the existing Obsidian config and the `BrunOS/PRD.md` cross-link. Treating the vault as a gitignored child achieves the same separation Phase 9 needs without forcing a move.

The implication for Phase 2's `shared.py`: every script that touches the vault must call `vault_path()` instead of hardcoding `BrunOS/Memory/` — because launchd/systemd will invoke scripts from arbitrary cwd, and on the VPS the vault lives at `/home/bruno/BrunOS`, not `<repo>/BrunOS`. Phase 2 builds that helper. Phase 0 only declares the env var.

### Personal/ folder (PRD extension)

The user added `BrunOS/Memory/personal/` with `personal/finance.md` as an extension to the PRD's folder list. CLAUDE.md must include it in the canonical folder list AND document that `personal/finance.md` is OFF-LIMITS to the agent (matches SOUL.md "no financial data" boundary, which is already in place; CLAUDE.md need not duplicate the boundary, only point).

### SOUL.md typo (NOT Phase 0's job)

`BrunOS/Memory/SOUL.md` line 7 references `bruno-os/Memory/` (lowercase, hyphenated) instead of `BrunOS/Memory/`. This is vault content owned by Bruno; do NOT silently fix as part of Phase 0. If surfacing during validation, mention as an FYI and let Bruno decide.

### Confidence Score

**8.5/10** that execution succeeds in one pass. The 1.5 points of risk:

- `claude-agent-sdk` API may have shifted (covered by the SDK shape probe — abort if shape is wrong).
- `fastembed` install on Apple Silicon occasionally fails on first run when Xcode CLT is missing; if it fails, `xcode-select --install` resolves it but is a manual step.
- `sqlite-vec` Python wheel availability for Python 3.13 fluctuates — if it fails to install, the fallback is to either pin to a Python with prebuilt wheels or defer to Phase 3 with a comment in `requirements.txt`. Surface to Bruno before deferring.

Mitigations are in the GOTCHAs of the relevant tasks. The plan covers each known risk with an abort/surface step.
