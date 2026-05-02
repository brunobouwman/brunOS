# Feature: Phase 4 — Integrations (execution-ready plan)

The following plan should be complete, but it's important to validate documentation and codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils, types, and models. Import from the right files etc.

**Companion doc**: `.agent/plans/phase-4-integrations.md` is the design/decision doc (Slack-autonomy carve-out, build order, Phase 4 scope rules). This file is the execution roadmap — every file, every function, every CLI command, every smoke test, in dependency order. Read both before coding.

## Feature Description

Phase 4 builds the integration layer that feeds Phase 6's heartbeat. Five external platforms wired behind one uniform pattern (`dataclass + auth + query + format + CLI`), one CLI dispatcher, one registry, one template. The agent never sees raw tokens — they load at the Python process boundary via `os.environ`. All integrations are **deterministic API client code** — no Agent SDK calls in this phase, no `CLAUDE_INVOKED_BY` anywhere.

Deliverables:

1. Foundation rewrite: env file moved to `.claude/.env`, gitignore patched, `shared.load_env()` helper, `.claude/.env.example` published.
2. `.claude/scripts/integrations/registry.py` — central enable-check registry.
3. `.claude/scripts/query.py` — single CLI dispatcher (`uv run python .claude/scripts/query.py <integration> <subcmd>`).
4. `.claude/scripts/integrations/integration_template.py` — copy-rename starting point with the contract documented.
5. `.claude/scripts/integrations/slack.py` — read + send (autonomous-on-@mention carve-out) + state.
6. `.claude/scripts/integrations/github.py` — read + create issues + create draft PRs (with `[WIP]` fallback).
7. `.claude/scripts/integrations/clickup.py` — cross-list overdue/due-today across multiple workspaces, status updates.
8. `.claude/scripts/integrations/gmail.py` + `calendar.py` — read-only via persisted OAuth token.
9. `.claude/scripts/integrations/rss.py` — etag/modified polite polling, per-feed try/except, last-seen ID dedup.
10. CLAUDE.md updated with all CLI commands; Phase 4 marked done.

## User Story

As Bruno (operator of BrunOS, with workdays spanning Slack, GitHub, ClickUp, Gmail, Calendar, and curated AI news)
I want a uniform Python interface that pulls fresh data from each platform incrementally (only what's new since last run) and lets the agent send Slack replies on @mention without leaving keystrokes
So that Phase 6's heartbeat can cheaply summarize the world every 30 minutes, draft replies in my voice, and respond in Slack the moment I tag the bot — while keeping email and broadcast surfaces firmly draft-and-confirm.

## Problem Statement

The vault is rich on context but isolated from the world. Without integrations:

1. The "Slack-while-away" digest (top task #1) cannot exist — no way to read what changed in channels and DMs.
2. "Open Issues and PRs for supervisor review" (top task #5) fails — no GitHub API client.
3. ClickUp task tracking (top task #2) is invisible — overdue/due-today queries can't be answered.
4. Gmail / Calendar context for draft generation (Phase 6) is missing — drafts can't reference the actual message they're replying to.
5. AI news (top task #3) goes unfiltered — no way to dedupe or score new RSS items.

Risks of doing it wrong: tokens leaking via accidental `print()`/log lines, prompt-injection in fetched content reaching the heartbeat without the (not-yet-built) Phase 8 guardrail, state files corrupting and re-surfacing items every tick, OAuth refresh tokens lost forcing weekly re-consent.

## Solution Statement

One pattern, applied six times:

```
dataclass model     →  what the rest of the codebase consumes (Channel, Message, Issue, Task, Event, FeedItem)
auth fn             →  load token from os.environ; explicit RuntimeError if missing
query fns           →  cursor-based paging; respect rate limits; retry via shared.with_retry; return dataclasses
state diff helpers  →  shared.load_state / save_state for "since last run" (per integration where applicable)
context formatter   →  format_for_context() returning a sanitized markdown block (Phase 8 will wrap in <external_data>)
CLI subcommand      →  registered in query.py dispatcher; smoke-test path before any heartbeat wiring
```

The dispatcher is the single entrypoint Bruno (and later, the heartbeat) calls. Each integration registers its own `argparse` subparser; `query.py` glues them together. The dispatcher does NOT load a token unless the requested integration needs it — `query.py rss new` must not fail because `SLACK_BOT_TOKEN` is unset.

## Feature Metadata

**Feature Type**: New Capability (foundational integration layer)
**Estimated Complexity**: Medium per integration; High in aggregate
**Primary Systems Affected**:
- `.claude/scripts/integrations/` (~7 new modules)
- `.claude/scripts/query.py` (new)
- `.claude/scripts/shared.py` (small additions: `load_env`, dotenv-path migration)
- `.claude/data/state/{slack,rss}-state.json` (created on first run)
- `.claude/.env` (already populated by Bruno; see security note below)
- `.gitignore` (must add `.claude/.env*`)
- `CLAUDE.md` (commands appended, Phase status flipped)

**Dependencies**:
- Phase 0: deps installed in `pyproject.toml` already (`slack_sdk`, `slack_bolt`, `PyGithub`, `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2`, `requests`, `feedparser`, `python-dotenv`).
- Phase 2: `shared.with_retry`, `shared.atomic_write`, `shared.save_state`, `shared.load_state`, `shared.now_brt`. All in main as of `7d622b0`.
- Phase 3: `memory_search.py --path-prefix drafts/sent` will be called by Phase 6's heartbeat (NOT by Phase 4 directly). Search interface stable.
- External: Slack workspace + app registered with the scopes below; GitHub fine-grained PAT scoped to allowlisted repos; ClickUp `pk_…` token; Google Cloud project with Gmail + Calendar APIs enabled (OAuth bootstrap already complete — `google_token.json` present at `.claude/data/state/`).

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: YOU MUST READ THESE BEFORE IMPLEMENTING

- `.claude/scripts/shared.py` (full file, ~302 lines) — **Why**: every integration uses `with_retry`, `atomic_write`, `save_state`, `load_state`, `now_brt`, `vault_path`, `STATE_DIR`. Note `_parse_dotenv` (line 38) and the `vault_path` callsite (line 64) — both must migrate from `REPO_ROOT/.env` to `REPO_ROOT/.claude/.env`. Note `with_retry` retry_on tuple (line 208) — only retries on 429/500/502/503; integration auth errors must NOT be passed through `with_retry`.
- `.claude/scripts/db.py` (full file) — **Why**: shows the SQLite pattern for state modules that use a DB. Phase 4 only uses JSON state files, but follow the same `STATE_DIR` placement convention (line 17).
- `.claude/scripts/memory_flush.py` (lines 26–47) — **Why**: shows the canonical `from __future__ import annotations` + `os` early-import + repo-root path injection pattern. Mirror in every integration module.
- `.claude/scripts/memory_index.py` (lines 14–27) — **Why**: shows the `sys.path.insert(0, str(Path(__file__).parent))` pattern for sibling-import — used in `db.py`/`embeddings.py`. Integrations import from `shared` the same way: `sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))` then `from shared import ...`.
- `.claude/scripts/bootstrap_google_oauth.py` (full file) — **Why**: OAuth flow already shipped. Phase 4's `gmail.py`/`calendar.py` consume the resulting `google_token.json`; do NOT re-run consent. **GOTCHA**: line 23 still reads `REPO_ROOT/.env` — patch in foundation phase.
- `.agent/plans/second-brain-prd.md` §4 (lines 215–311) — **Why**: PRD-level spec for each integration. Read the per-platform "gotcha" lines especially — they are not duplicated in this plan.
- `.agent/plans/phase-4-integrations.md` (full file) — **Why**: design decisions doc. Locks in: Slack autonomy carve-out (chat:write + app_mentions:read), two-workspace ClickUp config, Google token at `.claude/data/state/google_token.json`, no Agent SDK calls in this phase, no CLAUDE_INVOKED_BY.
- `BrunOS/Memory/SOUL.md` (read in entirety) — **Why**: confirms the autonomy boundaries enforced by code (no email send, no social broadcast, no financial data, Slack-on-@mention is the only autonomous send surface).
- `BrunOS/Memory/USER.md` (read in entirety) — **Why**: drafting criteria + workspace IDs + Bruno's GitHub handle (look for it; Phase 6 needs it).
- `pyproject.toml` (full file) — **Why**: deps and pinned ranges. If a library API changed between minor versions, the pin tells you what's locked.
- `.claude/.env` — **DO NOT READ.** User stated this explicitly. Tokens are present; just trust they exist. Use the variable names listed in the env-var reference below.
- `.gitignore` (full file, 24 lines) — **Why**: `.env*` matches root only. `.claude/.env` is NOT currently ignored. Patch in foundation phase.

### New Files to Create

Foundation:
- `.claude/.env.example` — copy of the existing root `.env.example` content; mirrors the new conventional location. Existing root `.env.example` is moved/deleted in foundation phase.

Integrations package (all under `.claude/scripts/integrations/`):
- `integrations/registry.py` — `INTEGRATIONS: list[IntegrationSpec]` with `(name, enabled_check, module_dotted_path)`.
- `integrations/integration_template.py` — copy-rename starting point. Documents the contract.
- `integrations/slack.py` — Slack client + read + send + state.
- `integrations/github.py` — GitHub client + read + create-issue + create-draft-pr.
- `integrations/clickup.py` — ClickUp client + multi-workspace cross-list query + status update.
- `integrations/gmail.py` — Gmail client + unread/recent reads (no send).
- `integrations/calendar.py` — Calendar client + today/week reads.
- `integrations/rss.py` — feedparser polling + last-seen-IDs dedup + per-feed isolation.

CLI:
- `.claude/scripts/query.py` — single dispatcher.

Tests: **none** — repo has no test framework. Validation is CLI smoke testing (see VALIDATION COMMANDS).

### Relevant Documentation — YOU SHOULD READ THESE BEFORE IMPLEMENTING

- [slack_sdk WebClient](https://slack.dev/python-slack-sdk/web/index.html)
  - Specific section: "Calling Web API methods" + "Handling Rate Limited Requests"
  - Why: `RateLimitErrorRetryHandler` is the official retry pattern; we layer it ON TOP of `shared.with_retry` (which doesn't know about Slack's `Retry-After` header).
- [Slack OAuth scopes](https://api.slack.com/scopes)
  - Specific scopes needed (bot): `channels:history`, `groups:history`, `im:history`, `mpim:history`, `channels:read`, `groups:read`, `im:read`, `mpim:read`, `users:read`, `users:read.email`, `team:read`, `chat:write`, `app_mentions:read`.
  - Why: the autonomy carve-out adds `chat:write` + `app_mentions:read` beyond the PRD.
- [Slack rate limits](https://api.slack.com/apis/rate-limits)
  - Tiers: `conversations.history` Tier 3 (~50/min); `users.conversations` / `users.info` / `conversations.list` Tier 2 (~20/min); `chat.postMessage` Tier 4 (~100/min); `auth.test` Tier 4.
  - Why: stagger reads and prefer `users.conversations` over `conversations.list` (server-side `is_member` filter, fewer calls).
- [PyGithub Auth + Repo](https://pygithub.readthedocs.io/en/latest/examples/Authentication.html)
  - Specific section: token auth + `Github(auth=Auth.Token(...))`
  - Why: `Github(login_or_token=...)` is deprecated in 2.x.
- [PyGithub: creating commits + branches + PRs](https://pygithub.readthedocs.io/en/latest/examples/Repository.html)
  - Why: draft PR creation flow needs `repo.get_branch`, `repo.create_git_ref`, `repo.create_file`, `repo.create_pull(draft=True)` — and the 422 fallback for private free repos.
- [ClickUp API: GetFilteredTeamTasks](https://clickup.com/api/clickupreference/operation/GetFilteredTeamTasks/)
  - Specific endpoint: `GET /api/v2/team/{team_id}/task`
  - Why: cross-list query is the workhorse for "overdue + due today across all my lists". Pay attention to `due_date_lt` / `due_date_gt` (Unix **milliseconds**, not seconds).
- [ClickUp API: Create Task](https://clickup.com/api/clickupreference/operation/CreateTask/)
  - Why: needs `due_date_time: true` when including a time-of-day component.
- [google-api-python-client: Gmail](https://googleapis.github.io/google-api-python-client/docs/dyn/gmail_v1.html)
  - Specific methods: `users().messages().list(q=..., maxResults=...)`, `users().messages().get(format='metadata'|'full')`.
  - Why: search syntax goes in `q` (`is:unread newer_than:1h`); `metadata` format keeps list calls cheap.
- [google-api-python-client: Calendar events.list](https://googleapis.github.io/google-api-python-client/docs/dyn/calendar_v3.events.html#list)
  - Why: `singleEvents=True`, `orderBy='startTime'`, ISO 8601 `timeMin`/`timeMax`.
- [google-auth: refreshing credentials](https://googleapis.dev/python/google-auth/latest/user-guide.html#refresh)
  - Why: load saved token; on `creds.expired and creds.refresh_token` call `creds.refresh(Request())` and persist.
- [feedparser docs: HTTP etag and modified](https://feedparser.readthedocs.io/en/latest/http-etag.html)
  - Why: pass `etag=...` and `modified=...` from prior parse to be polite; check `feed.bozo` to detect malformed feeds without crashing.
- [PEP 8 + project style](https://peps.python.org/pep-0008/)
  - Why: codebase style is implicit (no ruff/black config in repo); follow PEP 8, `from __future__ import annotations` everywhere, snake_case modules.

### Patterns to Follow

**Module header** (every integration module):

```python
"""Integration: <Platform>.

<one-paragraph description: what it does, what state it persists, what it does NOT do>
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import (  # noqa: E402
    STATE_DIR,
    load_state,
    now_brt,
    save_state,
    with_retry,
)
```

**Naming conventions** (mirroring existing scripts):

- Module names: snake_case singular (`slack.py`, not `slacks.py`).
- Dataclass models: PascalCase singular (`Channel`, `Message`, `Issue`, `PullRequest`, `Task`, `Event`, `FeedItem`, `Mention`).
- Auth functions: `_client()` (private; one per module). Returns the authenticated client. Caches via module-level `_CLIENT` variable.
- Query functions: verb-noun, return `list[Dataclass]`. Examples: `since_last_run`, `list_channels`, `get_thread`, `mentions_since_last_run`, `dms_since_last_run`, `assigned_to_me`, `overdue`, `due_today`, `recent_commits`, `unread`, `recent`, `today`, `week`, `new_items`.
- Send/write functions (Slack only in this phase): `send_message`, `reply_in_thread`. Explicit name, no overloads.
- Context formatters: `format_for_context(items: list[X]) -> str`. Always returns markdown.
- CLI registration: `def add_subparser(sub: argparse._SubParsersAction) -> None` and `def cli(args: argparse.Namespace) -> int`.

**Error handling**:

- Missing env var → `raise RuntimeError(f"<NAME> not set in environment")` from `_client()`. Do NOT default-empty-string.
- Network error → let `with_retry` handle 429/500/502/503; everything else propagates.
- Single-feed/single-channel failure (RSS, batch reads) → catch `Exception`, log to stderr (`print(..., file=sys.stderr)`), return empty list for that source. **One bad feed must not break a tick.**
- Auth/permission errors (401/403) → propagate; CLI prints actionable message at top level.

**Logging** (no logger configured in repo):
- Errors and warnings: `print(f"[<integration>] <message>", file=sys.stderr)`.
- Info: `print(...)` to stdout (CLI only — never inside library functions).
- **NEVER** print tokens, full message bodies of unread items, OAuth refresh tokens, or any field whose name contains `token`/`secret`/`key`. Tokens never enter logs.

**State persistence**:
- File path: `STATE_DIR / "<integration>-state.json"`. Always JSON.
- Read with `shared.load_state(path, default={...})`; `default` is a typed empty dict, not `None`.
- Write with `shared.save_state(path, obj)` (atomic via `os.replace`). Both helpers are concurrency-safe under `file_lock` if needed; for Phase 4's single-process polling there's no contention.
- Schema lives in a top-of-module comment. Bump a `_schema_version` field if you change shape.

**Retry pattern**:

```python
result = with_retry(
    lambda: client.conversations_history(channel=ch_id, oldest=oldest, limit=200),
    max_retries=3,
    backoff_base=1.0,
    retry_on=(429, 500, 502, 503),
)
```

**Argparse pattern** (from `memory_index.py:124–129`):

```python
ap = argparse.ArgumentParser()
sub = ap.add_subparsers(dest="integration", required=True)
slack.add_subparser(sub)  # each integration registers its own
github.add_subparser(sub)
# ...
args = ap.parse_args()
sys.exit(dispatch(args))
```

**Dataclass pattern** (Phase 6 contract — these field names are load-bearing):

```python
@dataclass(frozen=True)
class Message:
    channel_id: str
    ts: str            # Slack timestamp string (the message ID)
    user_id: str | None
    text: str          # plain-text body, surrogateescape-safe
    thread_ts: str | None  # the parent ts if this is a thread reply
    permalink: str | None  # populated lazily
```

Use `frozen=True` to make objects hashable for set-based dedup. Use `str | None` (PEP 604) — repo is Python 3.13.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation (env relocation, dispatcher skeleton)

Foundational work that every integration depends on. Must complete and validate before touching any platform module.

**Tasks:**
- Move env file convention to `.claude/.env`; patch `.gitignore`, `shared.py`, `bootstrap_google_oauth.py`.
- Add `shared.load_env()` helper using `python-dotenv`.
- Create `.claude/.env.example` mirroring the existing root `.env.example`; delete the root copy.
- Create `integrations/registry.py` with one `IntegrationSpec` dataclass.
- Create `integrations/integration_template.py` documenting the contract.
- Create `query.py` skeleton: parses `<integration> <subcmd> ...`, dispatches to module's `cli()`.

### Phase 2: Slack integration (priority #1, autonomous send carve-out)

**Tasks:**
- Implement `_client()` with `RateLimitErrorRetryHandler`.
- Implement read functions: `list_channels`, `since_last_run`, `get_thread`, `mentions_since_last_run`, `dms_since_last_run`. State is per-channel last-`ts`.
- Implement send functions: `send_message`, `reply_in_thread`.
- Implement echo-loop guard: cache `auth.test` `user_id` at module load, filter out messages where `event["user"] == bot_user_id` or `event.get("bot_id")` is set or `event.get("subtype")` is non-None.
- Register CLI subparser with subcommands: `since`, `channels`, `thread`, `mentions`, `dms`, `send`, `reply`.
- State file: `.claude/data/state/slack-state.json`.
- Smoke-test every subcommand against Bruno's workspace; confirm send arrives in the target channel.

### Phase 3: GitHub integration

**Tasks:**
- Implement `_client()` using `Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"]))`.
- Resolve repo: CLI `--repo owner/name` overrides `os.environ["GITHUB_DEFAULT_REPO"]`.
- Implement read functions: `assigned_to_me` (issues where `assignee == me`, exclude PRs), `open_prs`, `recent_commits(days=N)`.
- Implement write functions: `open_issue(repo, title, body, labels)` — labels always include `agent-drafted`. `open_draft_pr(repo, branch_slug, title, body, files)` — creates branch, commits files, opens PR with `draft=True`; on `GithubException(status=422)` retry with regular PR + `[WIP]` title prefix + `draft` label.
- Implement rate-limit pre-check: `g.get_rate_limit().core.remaining` — abort batch ops if <50 remaining.
- Register CLI subparser: `issues`, `prs`, `recent`, `open-issue`.
- Smoke-test reads against the configured `GITHUB_DEFAULT_REPO`. Smoke-test `open-issue` once with a clearly-marked test title; close immediately after to verify.

### Phase 4: ClickUp integration (multi-workspace)

**Tasks:**
- Parse `os.environ["CLICKUP_WORKSPACES"]` from `name:id,name:id,...` format into `dict[str, str]`. Empty/malformed → `RuntimeError`.
- Implement `_get(path, **params)` and `_post(path, json=...)` with `with_retry`. Inject `Authorization: <pk_...>` header. Read `X-RateLimit-Remaining`; if <5, sleep 5s before next call.
- Implement queries: `overdue(workspace=None)` — if `workspace` is None, iterate all configured workspaces and tag each result. `due_today(workspace=None)` — same. Use `now_brt()` start-of-day and end-of-day for the window; convert to **Unix milliseconds** (`int(dt.timestamp() * 1000)`).
- Implement `update_status(task_id, new_status)` — fetch the task's list config first via `GET /api/v2/list/{list_id}` to validate the status string against the list's configured statuses. Reject unknown statuses with a clear error.
- Implement `create_task(workspace, list_id, name, ...)` — explicit `--workspace` required. Pass `due_date_time: true` if a time component is included.
- Register CLI subparser: `overdue [--workspace]`, `today [--workspace]`, `create --workspace --list --name`, `status <task_id> <new_status>`.
- Smoke-test `overdue` and `today` against both workspaces. Smoke-test `status` with a low-stakes task.

### Phase 5: Gmail + Calendar (read-only)

**Tasks:**
- Implement shared `_creds()` helper in a new `integrations/_google.py` (private; both `gmail.py` and `calendar.py` import it). Loads `google_token.json` from `os.environ["GOOGLE_OAUTH_TOKEN_PATH"]`; if `creds.expired and creds.refresh_token`, call `creds.refresh(Request())` and persist via `atomic_write`. If no refresh token or refresh fails → `RuntimeError` with a pointer to `bootstrap_google_oauth.py`.
- `gmail.py`: `unread(max_results=50)` and `recent(hours=N, max_results=50)` using `messages().list(q=...)` then `messages().get(format='metadata', metadataHeaders=['From','Subject','Date'])`. Return `Message` dataclass. **Do not call `format='full'` in Phase 4** — Phase 6's draft generator will call it on-demand for messages it intends to reply to.
- `calendar.py`: `today()` and `week()` using `events().list(calendarId='primary', singleEvents=True, orderBy='startTime', timeMin=..., timeMax=...)`. Return `Event` dataclass. Times in BRT.
- Register CLI subparsers: `gmail unread`, `gmail recent <hours>`, `calendar today`, `calendar week`.
- Smoke-test all four. Confirm token auto-refreshes (delete `google_token.json` access_token field, re-run, verify silent refresh).

### Phase 6: RSS

**Tasks:**
- Hardcode the curated feed list in `integrations/rss.py` as `DEFAULT_FEEDS: list[str]` per PRD §4.5. Validate at module load — `feedparser.parse(url).bozo` warning on malformed.
- Implement `new_items()` — for each feed: load prior `etag`/`modified` from state, parse with HTTP cache headers, dedup against last-seen IDs (cap at 200/feed, FIFO), update state, return new items only. Wrap each feed in `try/except` — one failure must not abort the others.
- Implement `list_feeds()` — print configured feeds with last-poll-at and item-count.
- Register CLI subparsers: `rss new`, `rss feeds`.
- State file: `.claude/data/state/rss-state.json` with `{"feeds": {"<url>": {"etag": "...", "modified": "...", "seen_ids": [...]}}}`.
- Smoke-test against 2–3 feeds; confirm second `rss new` returns `[]` (etag working).

### Phase 7: Wiring + docs

**Tasks:**
- Update `CLAUDE.md`: append all CLI commands under "Build commands"; add a "Phase 4 — Integrations" section documenting the env-var location (`.claude/.env`), the OAuth bootstrap pointer, the FGPAT repo allowlist quirk, the ClickUp ms-not-seconds gotcha, and the Slack autonomy carve-out (with link to `SOUL.md` "Slack send carve-out"); flip Phase 4 to `[x]` in Phase status.
- Run all CLI smoke tests one final time; confirm zero regressions in Phase 2/3 commands.

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom. Each task is atomic and independently testable.

### F1. VERIFY `.gitignore` covers `.claude/.env*`

- **IMPLEMENT**: Already done at line 21 (`.claude/.env`). Verify the pattern also catches dot-suffixed variants (`.claude/.env.local`, `.claude/.env.production`). If not, add `.claude/.env.*` on the next line.
- **PATTERN**: existing `.gitignore` line 14–17 (`.env`, `.env.local`, `.env.*.local`) — root patterns. Line 21 added `.claude/.env` for the new convention.
- **GOTCHA**: `.env*` at root does NOT match `.claude/.env`. The line-21 entry covers the bare file but not variants. Confirm coverage you actually need.
- **VALIDATE**:
  - `git check-ignore -v .claude/.env && echo OK` (already passes).
  - `touch .claude/.env.local && git check-ignore -v .claude/.env.local; rm .claude/.env.local` — if not ignored, append `.claude/.env.*` to .gitignore.

### F2. CREATE `.claude/.env.example`

- **IMPLEMENT**: Move the existing root `.env.example` to `.claude/.env.example` (`git mv .env.example .claude/.env.example` if tracked, otherwise `mv`). Update the comment header to reflect the new path. Then verify the root `.env.example` no longer exists.
- **PATTERN**: existing root `.env.example` (the modified file in working tree).
- **GOTCHA**: the working tree has uncommitted changes to root `.env.example` (`CLICKUP_WORKSPACES`, etc.). Move the modified version, not the committed-baseline version.
- **VALIDATE**: `test -f .claude/.env.example && ! test -f .env.example && echo OK`

### F3. UPDATE `.claude/scripts/shared.py` (env path migration + `load_env`)

- **IMPLEMENT**:
  - Change line 64 from `env = _parse_dotenv(REPO_ROOT / ".env")` to `env = _parse_dotenv(REPO_ROOT / ".claude" / ".env")`.
  - Add `load_env()` function that wraps `dotenv.load_dotenv(REPO_ROOT / ".claude" / ".env", override=False)`. Import `load_dotenv` lazily inside the function so hooks running on system-python (no .venv) still work — they don't need `.env` loading because they read from stdin.
- **PATTERN**: see `bootstrap_google_oauth.py:19,23` for the `dotenv` import pattern.
- **IMPORTS**: `from dotenv import load_dotenv` (lazy, inside `load_env`).
- **GOTCHA**: `vault_path()` is `@lru_cache(maxsize=1)`. The path migration happens before any cached call (cache is fresh on every interpreter start), so no invalidation is needed.
- **VALIDATE**: `uv run python -c "from sys import path; path.insert(0, '.claude/scripts'); from shared import vault_path, load_env; load_env(); print(vault_path())"` prints the BrunOS path.

### F4. UPDATE `.claude/scripts/bootstrap_google_oauth.py` (env path)

- **IMPLEMENT**: Change line 23 from `load_dotenv(REPO_ROOT / ".env")` to `load_dotenv(REPO_ROOT / ".claude" / ".env")`.
- **PATTERN**: identical change to F3 line 64.
- **GOTCHA**: this script is already-shipped (untracked but functional — token already generated). Don't break it. Only the dotenv path changes.
- **VALIDATE**: `uv run python .claude/scripts/bootstrap_google_oauth.py --help 2>&1 | head -5` doesn't error. (Note: the script doesn't have `--help`, but importing it via Python should not raise: `uv run python -c "import sys; sys.path.insert(0, '.claude/scripts'); import bootstrap_google_oauth; print('OK')"`.)

### F5. CREATE `.claude/scripts/integrations/registry.py`

- **IMPLEMENT**: a frozen dataclass `IntegrationSpec(name: str, env_var: str, module: str)` and a module-level `INTEGRATIONS: list[IntegrationSpec]` listing all six integrations. `enabled(spec) -> bool` returns `os.environ.get(spec.env_var, "") != ""`. Used by `query.py` to error early with a helpful message ("SLACK_BOT_TOKEN unset; configure in .claude/.env").
- **IMPORTS**: `import os`, `from dataclasses import dataclass`.
- **GOTCHA**: the registry is the **only** place new integrations get listed. Future Discord/Linear etc. add a single line.
- **VALIDATE**: `uv run python -c "import sys; sys.path.insert(0, '.claude/scripts'); from integrations.registry import INTEGRATIONS; print([s.name for s in INTEGRATIONS])"` prints `['slack', 'github', 'clickup', 'gmail', 'calendar', 'rss']`.

### F6. CREATE `.claude/scripts/integrations/integration_template.py`

- **IMPLEMENT**: a working but no-op module documenting the contract. Includes:
  - Module-level docstring stating the pattern (dataclass + auth + query + format + CLI).
  - `@dataclass(frozen=True) class Item: ...` — placeholder.
  - `def _client(): raise NotImplementedError(...)`
  - `def example_query(client) -> list[Item]: ...`
  - `def format_for_context(items: list[Item]) -> str: ...`
  - `def add_subparser(sub: argparse._SubParsersAction) -> None: ...`
  - `def cli(args: argparse.Namespace) -> int: ...`
  - "Copy-rename to `<platform>.py`; replace `Item` with your dataclass; replace `_client()` body" comment block at top.
- **PATTERN**: this IS the pattern. All other integration files mirror this.
- **GOTCHA**: don't import `slack_sdk`/`PyGithub`/etc. here — the template stays dependency-free so it's importable without any platform creds set.
- **VALIDATE**: `uv run python -c "import sys; sys.path.insert(0, '.claude/scripts'); from integrations import integration_template as t; print(t.__doc__[:60])"` prints the docstring.

### F7. CREATE `.claude/scripts/query.py`

- **IMPLEMENT**:
  - Calls `shared.load_env()` first thing in `main()`.
  - Builds top-level `argparse.ArgumentParser`. Adds one subparser per integration: `slack.add_subparser(sub)`, `github.add_subparser(sub)`, etc. The integration modules are imported **lazily** — inside `add_subparser` registration is fine because module-level imports of `slack_sdk` etc. are deferred until the registration call. To avoid that import-time cost when the user only wants `rss`, import each integration module inside its own registration helper. Simplest pattern: a small `_lazy_register(name, fn)` wrapper.
  - On dispatch, calls the integration's `cli(args)` and returns its exit code.
- **PATTERN**: `memory_index.py:123–130` for the argparse skeleton; extend with `add_subparsers(dest="integration", required=True)`.
- **IMPORTS**: `import argparse`, `import sys`, `from pathlib import Path`. Add path bootstrap to `.claude/scripts`. Then `from shared import load_env`. Integration imports happen inside subparser registration.
- **GOTCHA**: `argparse` `subparsers.required=True` is needed in Python 3.7+ — without it, a no-arg invocation prints help silently and returns 0. We want `query.py` with no args to fail loudly.
- **VALIDATE**:
  - `uv run python .claude/scripts/query.py --help` lists all six integrations.
  - `uv run python .claude/scripts/query.py 2>&1 | grep -i "required\|usage"` (exits non-zero, prints usage).
  - `uv run python .claude/scripts/query.py rss --help` prints rss subcommands.

---

### S1. CREATE `.claude/scripts/integrations/slack.py`

- **IMPLEMENT**:
  - Module-level constants: `STATE_PATH = STATE_DIR / "slack-state.json"`. Default state schema: `{"_schema_version": 1, "channels": {<channel_id>: <last_ts>}, "bot_user_id": null}`.
  - Dataclasses (frozen): `Channel(id, name, is_im, is_member)`, `Message(channel_id, ts, user_id, text, thread_ts, permalink)`, `Mention(channel_id, ts, user_id, text, thread_ts)`. Subclass-or-not: keep `Mention` separate from `Message` even though shape is similar — readers should see at the type level "this is a mention, not a generic message".
  - `_client() -> WebClient`: read `os.environ["SLACK_BOT_TOKEN"]`; missing → `RuntimeError`. Build `WebClient(token=...)`; append `RateLimitErrorRetryHandler(max_retry_count=3)`. Cache in module global `_CLIENT`.
  - `_bot_user_id(client) -> str`: cache `client.auth_test()["user_id"]` in state via `save_state` so we don't `auth.test` every run.
  - `list_channels(client)` — `client.users_conversations(types="public_channel,private_channel,im,mpim", limit=200)`, paginate `next_cursor`. Returns `list[Channel]`.
  - `since_last_run(client) -> list[Message]`: load state; for each channel where bot is a member, call `conversations_history(channel=ch, oldest=last_ts, limit=200)`, paginate. Filter out own bot user, `bot_id`, `subtype`. Update state per-channel. Return aggregated.
  - `get_thread(client, channel, ts) -> list[Message]`: `conversations_replies(channel=channel, ts=ts)`. Paginate.
  - `mentions_since_last_run(client) -> list[Mention]`: requires `app_mentions:read`. Use `search.messages` with query `<@BOT_USER_ID> after:<date>` IF `search:read` were granted — but it's not, so derive from `since_last_run` results filtering on `f"<@{bot_user_id}>"` substring in `text`.
  - `dms_since_last_run(client) -> list[Message]`: subset of `since_last_run` filtered to channels where `is_im=True`.
  - `send_message(client, channel, text, thread_ts=None) -> dict`: `client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)`. Return the API response dict; do not mutate state (heartbeat handles its own bookkeeping in Phase 6).
  - `reply_in_thread(client, channel, parent_ts, text)`: thin wrapper passing `thread_ts=parent_ts`.
  - `format_for_context(messages: list[Message]) -> str`: markdown like `**#channel-name** (@user, HH:MM): text` lines, grouped by channel, time in BRT. Use `_user_cache` (lazy `users_info`) for display names.
  - `add_subparser(sub)`: subcommands `since [--minutes N]`, `channels`, `thread <channel> <ts>`, `mentions [--since 1h]`, `dms [--since 1h]`, `send <channel> <text>`, `reply <channel> <ts> <text>`.
- **PATTERN**: `memory_search.py:49–56` for the CLI dispatch shape; PRD §4.1 (lines 226–239) for the API method choices; phase-4-integrations.md §"Slack — expanded behavior" for the autonomy carve-out.
- **IMPORTS**:
  ```python
  from slack_sdk import WebClient
  from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler
  from slack_sdk.errors import SlackApiError
  ```
- **GOTCHA**:
  - `users.conversations` returns IMs without a `name` field — handle gracefully.
  - `conversations.history` does NOT include thread replies; you must call `conversations.replies(parent_ts)` per thread for full coverage. In Phase 4, only fetch thread replies when the heartbeat asks for one specific thread (CLI `thread <ch> <ts>`).
  - `Message.text` may contain Slack mrkdwn (`<@U123>`, `<#C456|name>`, `<https://...|label>`). Don't try to render — leave as-is for Phase 8 sanitize layer.
  - `auth.test` is Tier 4 but cheap; still cache the bot_user_id in state to avoid hammering on every poll.
  - State file may not exist on first run — `load_state(STATE_PATH, default={"_schema_version": 1, "channels": {}, "bot_user_id": None})`.
  - First-run cold-start: with no `last_ts`, set `oldest = (now_brt() - timedelta(hours=1)).timestamp()` to avoid pulling the entire channel history.
- **VALIDATE**:
  - `uv run python .claude/scripts/query.py slack channels` lists DMs + channels (Bruno workspace = Bruno + Lisa, expect ≤10 entries).
  - `uv run python .claude/scripts/query.py slack since` returns markdown of recent messages (or empty on cold/quiet workspace).
  - `uv run python .claude/scripts/query.py slack send <DM_with_self> "BrunOS smoke test"` — Bruno confirms message arrives.
  - `cat .claude/data/state/slack-state.json | jq .channels | head` shows per-channel last_ts entries.

---

### G1. CREATE `.claude/scripts/integrations/github.py`

- **IMPLEMENT**:
  - Dataclasses (frozen): `Issue(repo, number, title, url, assignee, labels, updated_at)`, `PullRequest(repo, number, title, url, draft, base, head, updated_at)`, `Commit(repo, sha, message, author, url, committed_at)`.
  - `_client() -> Github`: `Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"]))`. Cache.
  - `_resolve_repo(repo_arg: str | None) -> str`: `repo_arg or os.environ["GITHUB_DEFAULT_REPO"]`. Missing → `RuntimeError`.
  - `_check_rate(g)` → if `g.get_rate_limit().core.remaining < 50`, `RuntimeError("GitHub rate limit nearly exhausted; aborting batch op")`.
  - `assigned_to_me(g, repo_full_name) -> list[Issue]`: `me = g.get_user().login`. `repo.get_issues(state="open", assignee=me)`. Filter `i.pull_request is None` (PyGithub returns PRs through this endpoint too).
  - `open_prs(g, repo_full_name) -> list[PullRequest]`: `repo.get_pulls(state="open", sort="updated", direction="desc")`.
  - `recent_commits(g, repo_full_name, days=7) -> list[Commit]`: `repo.get_commits(since=now_brt() - timedelta(days=days))`.
  - `open_issue(g, repo_full_name, title, body, labels=None) -> Issue`: labels = `[*(labels or []), "agent-drafted"]`. `repo.create_issue(title=title, body=body, labels=list(set(labels)))`. Return wrapped dataclass.
  - `open_draft_pr(g, repo_full_name, branch_slug, title, body, files: dict[str, str]) -> PullRequest`:
    1. `base = repo.get_branch("main")` (fall back to `repo.default_branch`).
    2. `repo.create_git_ref(ref=f"refs/heads/agent/{branch_slug}", sha=base.commit.sha)`.
    3. For each `(path, content)` in `files`: `repo.create_file(path=path, message=f"Draft: {title}", content=content, branch=f"agent/{branch_slug}")`.
    4. Try `repo.create_pull(title=f"Draft: {title}", body=body, head=f"agent/{branch_slug}", base=repo.default_branch, draft=True)`.
    5. On `GithubException` with `status == 422` AND error msg contains "draft" → fallback: `create_pull(... draft=False)` with title `f"[WIP] {title}"`, then `pr.add_to_labels("draft")`.
  - `format_for_context(...)`: terse markdown. One section per type (Issues, PRs, Commits).
  - `add_subparser(sub)`: subcommands `issues [--repo R]`, `prs [--repo R]`, `recent [--repo R] [--days N]`, `open-issue --title --body-file [--repo R]`.
- **PATTERN**: PRD §4.2 lines 244–257.
- **IMPORTS**:
  ```python
  from github import Github, Auth, GithubException
  ```
- **GOTCHA**:
  - PyGithub 2.x deprecates `Github(login_or_token=...)` — must use `Auth.Token`.
  - `repo.get_issues(assignee=me)` requires the assignee to be a `NamedUser` or string — passing the login string works.
  - `repo.create_file` requires the file NOT to exist on the branch yet; if it does, use `update_file`. For agent-drafted PRs we always start fresh from `main`, so `create_file` is correct.
  - Draft PRs require either a public repo OR paid plan on private. The 422 fallback is mandatory.
  - **Do not** open an `open-issue` against a real repo during smoke testing without `[BrunOS smoke test - delete]` in the title — close it immediately after.
- **VALIDATE**:
  - `uv run python .claude/scripts/query.py github issues` returns issues for `GITHUB_DEFAULT_REPO`.
  - `uv run python .claude/scripts/query.py github prs` returns PRs.
  - `uv run python .claude/scripts/query.py github recent --days 14` returns commits.
  - `echo "Smoke test body" > /tmp/smoke.md && uv run python .claude/scripts/query.py github open-issue --title "[smoke test] delete me" --body-file /tmp/smoke.md` — Bruno verifies issue appears with `agent-drafted` label, then closes it.

---

### C1. CREATE `.claude/scripts/integrations/clickup.py`

- **IMPLEMENT**:
  - Module-level: `BASE_URL = "https://api.clickup.com/api/v2"`. `Workspaces = dict[str, str]` parsed once from `os.environ["CLICKUP_WORKSPACES"]` (`name:id,name:id`). Empty → `RuntimeError`.
  - Dataclass: `Task(workspace, list_id, list_name, id, name, status, due_date, url, assignees)`.
  - `_get(path, **params)`: builds URL, sets `Authorization` header, calls `requests.get` wrapped in `with_retry`. Reads `X-RateLimit-Remaining`; if `< 5`, `time.sleep(5)` before returning.
  - `_post(path, json=None)`: same pattern.
  - `_brt_day_window() -> tuple[int, int]`: today 00:00 BRT and tomorrow 00:00 BRT, in **Unix milliseconds**. `int(dt.timestamp() * 1000)`.
  - `_workspaces_filter(workspace: str | None) -> dict[str, str]`: returns subset of configured workspaces.
  - `overdue(workspace=None) -> list[Task]`: for each workspace in scope: `_get(f"team/{team_id}/task", due_date_lt=int(now_brt().timestamp() * 1000), include_closed="false", subtasks="true")`. Tag results with `workspace` name. Concatenate.
  - `due_today(workspace=None) -> list[Task]`: same endpoint with `due_date_gt=<today_00:00_ms>` and `due_date_lt=<tomorrow_00:00_ms>`.
  - `update_status(task_id, new_status) -> Task`: `_get(f"task/{task_id}")` to find `list.id`. `_get(f"list/{list_id}")` to fetch valid statuses. Validate `new_status` is among them (case-insensitive). `_put(f"task/{task_id}", json={"status": new_status})`.
  - `create_task(workspace, list_id, name, description=None, due_date_ms=None, due_date_time=False)`: `_post(f"list/{list_id}/task", json={"name": name, "description": description, "due_date": due_date_ms, "due_date_time": due_date_time})`. Strip `None` keys.
  - `format_for_context(tasks: list[Task]) -> str`: grouped by workspace, then list. Each line `- [ ] [Name](url) (due: <date>) — workspace/list`.
  - `add_subparser(sub)`: subcommands `overdue [--workspace W]`, `today [--workspace W]`, `create --workspace --list --name [--description] [--due-iso ISO8601]`, `status <task_id> <new_status>`.
- **PATTERN**: PRD §4.3 lines 261–268; `with_retry` from `shared.py`.
- **IMPORTS**: `import requests`, `import time`. **No SDK** — ClickUp doesn't have an official Python SDK; raw HTTP is canonical.
- **GOTCHA**:
  - **Unix milliseconds, not seconds.** `int(dt.timestamp() * 1000)`. Forgetting this returns `[]` (everything looks 50 years in the future).
  - `due_date_time: true` is required when creating with a time-of-day component, otherwise ClickUp normalizes to 04:00 UTC.
  - Status strings are case-sensitive on the server but list-config lookup should normalize to lowercase for comparison. Pass back the canonical-case from the list config.
  - 100 req/min rate limit on Personal/Free. Two workspaces × overdue+today = 4 calls per heartbeat tick — well within budget. The pre-emptive sleep on `<5 remaining` is belt-and-suspenders.
  - Token is user-scoped (one `pk_...` works across all workspaces Bruno belongs to). No per-workspace token juggling.
- **VALIDATE**:
  - `uv run python .claude/scripts/query.py clickup overdue` lists overdue across both workspaces, each tagged.
  - `uv run python .claude/scripts/query.py clickup today --workspace vertik` lists vertik-only.
  - `uv run python .claude/scripts/query.py clickup status <known_task_id> "in progress"` flips status; verify in ClickUp UI; flip back manually.

---

### M1. CREATE `.claude/scripts/integrations/_google.py`

- **IMPLEMENT**:
  - `_creds() -> google.oauth2.credentials.Credentials`: load JSON from `os.environ.get("GOOGLE_OAUTH_TOKEN_PATH", ".claude/data/state/google_token.json")`. Path is relative to `REPO_ROOT` if not absolute. If missing → `RuntimeError("google_token.json missing — run uv run python .claude/scripts/bootstrap_google_oauth.py first")`. If `creds.expired and creds.refresh_token` → `creds.refresh(Request())` and persist via `atomic_write` (with `stamp_updated=False`). Cache in module global.
  - `_service(api_name: str, version: str)`: `build(api_name, version, credentials=_creds(), cache_discovery=False)`.
- **PATTERN**: `bootstrap_google_oauth.py` for the credentials shape and JSON serialization (`creds.to_json()`).
- **IMPORTS**:
  ```python
  from google.oauth2.credentials import Credentials
  from google.auth.transport.requests import Request
  from googleapiclient.discovery import build
  ```
- **GOTCHA**:
  - `cache_discovery=False` suppresses noisy "file_cache is unavailable when using oauth2client" warnings under modern auth.
  - Refresh tokens DO expire if the OAuth consent screen is in Testing mode (7 days). If `creds.refresh(Request())` raises, surface a clear "re-run bootstrap_google_oauth.py" message.
- **VALIDATE**: `uv run python -c "import sys; sys.path.insert(0, '.claude/scripts'); from integrations._google import _creds; c = _creds(); print('valid:', c.valid, 'has_refresh:', bool(c.refresh_token))"` prints `valid: True has_refresh: True`.

### M2. CREATE `.claude/scripts/integrations/gmail.py`

- **IMPLEMENT**:
  - Dataclass: `EmailHeader(id, thread_id, from_addr, subject, date_iso, snippet)`.
  - `_svc()`: `_service("gmail", "v1")` from `_google.py`.
  - `unread(max_results=50) -> list[EmailHeader]`: `users().messages().list(userId='me', q='is:unread', maxResults=max_results).execute()` → for each `id`, `users().messages().get(userId='me', id=id, format='metadata', metadataHeaders=['From','Subject','Date'])`.
  - `recent(hours: int, max_results=50) -> list[EmailHeader]`: same with `q=f"newer_than:{hours}h"`.
  - `format_for_context(headers: list[EmailHeader]) -> str`: bullet list, terse: `- From: <from>\n  Subject: <subject>\n  Snippet: <snippet[:120]>`.
  - **No `format='full'` calls in Phase 4.** That's Phase 6's job when generating drafts.
  - `add_subparser(sub)`: subcommands `unread`, `recent <hours>`.
- **IMPORTS**:
  ```python
  sys.path.insert(0, str(Path(__file__).parent))
  from _google import _service
  ```
  (Use leading-underscore module name to mark private; sibling import via path injection.)
- **GOTCHA**:
  - `users().messages().list` returns `id`+`threadId` only — must follow with `get` per message. Batch with `googleapiclient.http.BatchHttpRequest` for >10 messages.
  - `q='is:unread'` matches Gmail's web search syntax. `newer_than:1h` is hours; `newer_than:1d` is days.
  - Some headers may be missing (`From` on drafts, `Date` on misformed mails) — always `.get(name, "")`.
- **VALIDATE**:
  - `uv run python .claude/scripts/query.py gmail unread` returns up to 50 unread.
  - `uv run python .claude/scripts/query.py gmail recent 24` returns mails from last 24h.

### M3. CREATE `.claude/scripts/integrations/calendar.py`

- **IMPLEMENT**:
  - Dataclass: `Event(id, summary, start_iso, end_iso, attendees, location, htmlLink)`.
  - `_svc()`: `_service("calendar", "v3")`.
  - `today() -> list[Event]`: `events().list(calendarId='primary', timeMin=<today_00:00_BRT_iso>, timeMax=<tomorrow_00:00_BRT_iso>, singleEvents=True, orderBy='startTime', maxResults=50).execute()`.
  - `week() -> list[Event]`: same with 7-day window.
  - `format_for_context(...)`: markdown, BRT times. `- HH:MM-HH:MM: <summary> (loc, N attendees)`.
  - `add_subparser(sub)`: subcommands `today`, `week`.
- **GOTCHA**: `events().list` `timeMin`/`timeMax` must be RFC3339 with offset. `dt.isoformat()` produces this for BRT-aware datetimes (`2026-05-02T00:00:00-03:00`). All-day events have `start.date` (no time); handle both `start.dateTime` and `start.date`.
- **VALIDATE**:
  - `uv run python .claude/scripts/query.py calendar today` returns today's events.
  - `uv run python .claude/scripts/query.py calendar week` returns the next 7 days.

---

### R1. CREATE `.claude/scripts/integrations/rss.py`

- **IMPLEMENT**:
  - Module constant: `DEFAULT_FEEDS: list[str]` per PRD §4.5 (arxiv cs.AI/cs.LG/cs.CL, anthropic, openai, deepmind, research.google, news.smol.ai, simonwillison, hnrss AI search, hf-papers community mirror).
  - State path: `STATE_DIR / "rss-state.json"`. Schema: `{"_schema_version": 1, "feeds": {"<url>": {"etag": "", "modified": "", "seen_ids": []}}}`.
  - Dataclass: `FeedItem(feed_url, item_id, title, link, summary, published_iso)`.
  - `new_items(feeds: list[str] | None = None) -> list[FeedItem]`: feeds = feeds or DEFAULT_FEEDS. For each feed, wrapped in `try/except Exception as e: print(f"[rss] {feed_url}: {e}", file=sys.stderr)`:
    1. Load prior state for this feed.
    2. `parsed = feedparser.parse(feed_url, etag=prior.etag, modified=prior.modified)`.
    3. If `parsed.status == 304` (or `parsed.bozo` with bozo_exception that's a 304-equivalent), skip — no new items.
    4. For each entry: `item_id = entry.get("id") or entry.get("link")`. Skip if in `seen_ids`.
    5. Append new ids; cap `seen_ids` at 200 (FIFO drop).
    6. Update `etag = parsed.get("etag", "")`, `modified = parsed.get("modified", "")`.
    7. Save state once (atomic) at end of full run, not per-feed.
  - `list_feeds() -> str`: print each configured feed with last-poll-at and seen-id count.
  - `format_for_context(items: list[FeedItem]) -> str`: grouped by feed source, terse `- [Title](link) — published HH:MM`.
  - `add_subparser(sub)`: subcommands `new`, `feeds`.
- **PATTERN**: PRD §4.5.
- **IMPORTS**: `import feedparser`.
- **GOTCHA**:
  - `feedparser.parse` does NOT raise on HTTP errors — check `parsed.status` and `parsed.bozo`.
  - Some feeds return entries with no `id` — falling back to `link` is safe.
  - HuggingFace community mirror URL drift: confirm the URL is alive at integration time. If it 404s, drop it from `DEFAULT_FEEDS` with a comment.
  - HN RSS search query: `https://hnrss.org/newest?q=AI+OR+LLM&points=50` filters to ≥50 points.
- **VALIDATE**:
  - First run: `uv run python .claude/scripts/query.py rss new` returns several items.
  - Second run within 30s: `uv run python .claude/scripts/query.py rss new` returns `[]` (etag working) OR 1–2 if a feed updates between calls.
  - `uv run python .claude/scripts/query.py rss feeds` lists all configured feeds with state.
  - Force a single dead feed: temporarily prepend `https://example.invalid/rss.xml` to `DEFAULT_FEEDS`. Confirm `rss new` still succeeds and stderr shows the one error. Revert.

---

### W1. UPDATE `query.py` to register all six integrations

- **IMPLEMENT**: add the six `<integration>.add_subparser(sub)` calls in `query.py main()` (lazy-imported as discussed in F7).
- **VALIDATE**: `uv run python .claude/scripts/query.py --help` lists `slack`, `github`, `clickup`, `gmail`, `calendar`, `rss`.

### W2. UPDATE `CLAUDE.md`

- **IMPLEMENT**:
  - Add a "Phase 4 — Integrations" section after "Memory search (Phase 3)". Include:
    - Env-file location: `.claude/.env` (NOT root). Reference `.claude/.env.example`.
    - OAuth bootstrap pointer: `uv run python .claude/scripts/bootstrap_google_oauth.py` (one-time, Mac).
    - FGPAT repo allowlist quirk (re-generate token to add a new repo).
    - ClickUp ms-not-seconds gotcha + BRT day-boundary calculation.
    - Slack autonomy carve-out: link to `BrunOS/Memory/SOUL.md` "Slack send carve-out". Reiterate: `chat:write` is autonomous on @mention only; DMs without @mention go through the standard draft flow.
  - Append all CLI commands under "Build commands" (one block; copy from `.agent/plans/phase-4-integrations.md` lines 107–130).
  - Flip Phase 4 to `[x]` in "Phase status" list with date `2026-05-02`.
- **VALIDATE**: `grep -c "uv run python .claude/scripts/query.py" CLAUDE.md` returns ≥18 (one per subcommand).

---

## TESTING STRATEGY

**Repo has no test framework.** No `pytest`, no test directory, no fixtures. Validation is **manual CLI smoke testing** — every subcommand exercised against the real platform once, expected output confirmed by Bruno.

If a future phase adds a test framework (likely Phase 8 or 9), each integration would gain a `test_<integration>.py` mocking the API client. **Do not add pytest in Phase 4** — out of scope.

### "Unit tests" (manual smoke, per integration)

Each integration's CLI subcommands get exercised in order. Pass criteria:
- Exit code 0.
- Stdout is well-formed markdown (or JSON where applicable).
- No tokens, no full email bodies, no OAuth refresh tokens in stdout/stderr.
- State file at `.claude/data/state/<integration>-state.json` is valid JSON.

### "Integration tests" (manual end-to-end)

After all six integrations land, run them all back-to-back and confirm no cross-talk. Specifically:
- `query.py rss new` works without `SLACK_BOT_TOKEN` set (test by temporarily renaming `.claude/.env`).
- `query.py slack since && query.py github prs && query.py clickup overdue && query.py gmail unread && query.py calendar today && query.py rss new` runs in sequence; total wall-clock <30s on a quiet day.

### Edge cases

- **First run** (no state file): each integration gracefully creates state with sane defaults. Slack falls back to "1 hour ago" oldest. RSS pulls all feeds fresh.
- **Empty result**: each `since`/`overdue`/`today`/`unread`/`new` returns `[]` (empty markdown / empty JSON list) without error.
- **Network failure** (test by disconnecting wifi for one): each integration retries 3× via `with_retry`, then propagates a clear error. Other integrations in the same `query.py` invocation NOT affected (each `query.py` call is one integration).
- **Auth failure** (test by appending `xxx` to a token in `.claude/.env`): integration fails at `_client()` with a 401 → propagates. Restore the token; verify it works again.
- **Rate limit** (Slack): `RateLimitErrorRetryHandler` should sleep `Retry-After` seconds and resume. Confirm by hammering `slack since` 30× in a tight loop and watching for at least one retry log line.
- **OAuth refresh**: simulated by editing `google_token.json` to have an expired `expiry` field. Next `gmail unread` call should silently refresh.
- **ClickUp ms vs seconds**: forget the `* 1000` → `overdue` returns `[]` because everything is "due in the year 1970". Catch this in code review.
- **Slack echo loop**: if `since_last_run` returned bot's own `chat.postMessage` echoes, Phase 6 would reply to itself. Confirm by sending a message via `slack send <ch>`, then immediately `slack since` — the message MUST NOT appear in the output.
- **Draft PR on private free repo**: `open_draft_pr` against a private repo on Bruno's personal Free plan should fall back to `[WIP]`-prefixed regular PR with `draft` label.

---

## VALIDATION COMMANDS

Execute every command to ensure zero regressions and 100% feature correctness.

### Level 1: Syntax & style

```bash
# Python syntax check on all new files
uv run python -m py_compile .claude/scripts/query.py
uv run python -m py_compile .claude/scripts/integrations/*.py

# Import-time check (catches missing imports, top-level errors)
for f in .claude/scripts/integrations/*.py; do
  uv run python -c "import sys; sys.path.insert(0, '.claude/scripts'); import importlib.util; spec = importlib.util.spec_from_file_location('m', '$f'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('OK: $f')"
done
```

(No ruff/black config in repo — skip linting.)

### Level 2: Foundation validation

```bash
# Env file in correct location, ignored, example shipped
git check-ignore -v .claude/.env && echo "OK: .claude/.env ignored"
test -f .claude/.env.example && echo "OK: example shipped"
test ! -f .env.example && echo "OK: root example removed"
test ! -f .env && echo "OK: root .env removed"

# load_env() works
uv run python -c "
import sys; sys.path.insert(0, '.claude/scripts')
from shared import load_env, vault_path
load_env()
print('vault:', vault_path())
"

# query.py wired
uv run python .claude/scripts/query.py --help | grep -E "slack|github|clickup|gmail|calendar|rss" | wc -l   # expect 6
```

### Level 3: Per-integration smoke tests

```bash
# Slack
uv run python .claude/scripts/query.py slack channels
uv run python .claude/scripts/query.py slack since
uv run python .claude/scripts/query.py slack send <DM_with_self> "BrunOS smoke test $(date +%H:%M)"

# GitHub
uv run python .claude/scripts/query.py github issues
uv run python .claude/scripts/query.py github prs
uv run python .claude/scripts/query.py github recent --days 14

# ClickUp (each workspace)
uv run python .claude/scripts/query.py clickup overdue
uv run python .claude/scripts/query.py clickup today --workspace vertik
uv run python .claude/scripts/query.py clickup today --workspace protostack

# Gmail + Calendar
uv run python .claude/scripts/query.py gmail unread
uv run python .claude/scripts/query.py gmail recent 24
uv run python .claude/scripts/query.py calendar today
uv run python .claude/scripts/query.py calendar week

# RSS
uv run python .claude/scripts/query.py rss feeds
uv run python .claude/scripts/query.py rss new
uv run python .claude/scripts/query.py rss new   # second run; expect ≤ first run's count
```

### Level 4: State + idempotency

```bash
# State files exist and are valid JSON
ls -la .claude/data/state/{slack,rss}-state.json
uv run python -c "import json; print(json.load(open('.claude/data/state/slack-state.json')).keys())"
uv run python -c "import json; print(json.load(open('.claude/data/state/rss-state.json')).keys())"

# Slack incremental: a second 'since' call right after the first should return ≤ first run's count
N1=$(uv run python .claude/scripts/query.py slack since | wc -l)
sleep 2
N2=$(uv run python .claude/scripts/query.py slack since | wc -l)
test "$N2" -le "$N1" && echo "OK: slack incremental"
```

### Level 5: Regression — confirm Phase 2/3 still work

```bash
# memory_index runs
uv run python .claude/scripts/memory_index.py --dry-run

# memory_search returns results
uv run python .claude/scripts/memory_search.py "vault frontmatter" --k 3 | head -20

# Hook scripts importable
for f in .claude/hooks/*.py; do
  uv run python -m py_compile "$f" && echo "OK: $f"
done
```

---

## ACCEPTANCE CRITERIA

- [ ] `.claude/.env` is gitignored; root `.env`/`.env.example` removed; `.claude/.env.example` published.
- [ ] `shared.load_env()` exists and is called by `query.py` and (lazily) `bootstrap_google_oauth.py`.
- [ ] `shared.vault_path()` reads from `.claude/.env`.
- [ ] `.claude/scripts/query.py` exists, dispatches to all six integrations, fails loudly with no args.
- [ ] All six integration modules exist under `.claude/scripts/integrations/` and follow the pattern (dataclass + `_client` + queries + `format_for_context` + `add_subparser`/`cli`).
- [ ] Each CLI subcommand listed in `.agent/plans/phase-4-integrations.md` lines 107–130 works against real platforms.
- [ ] No tokens, no OAuth refresh tokens, no full email bodies appear in stdout/stderr of any subcommand.
- [ ] State files at `.claude/data/state/{slack,rss}-state.json` exist after first run, are valid JSON, and have monotonic last-ts / last-seen-id growth.
- [ ] Slack send to a DM with self arrives.
- [ ] GitHub `open-issue` creates an issue with the `agent-drafted` label (smoke test issue closed by Bruno post-verification).
- [ ] ClickUp `overdue` and `today` return tagged-by-workspace results across vertik AND protostack.
- [ ] Gmail/Calendar reads work without re-running OAuth consent.
- [ ] RSS second-run within 30s returns ≤ first run's count.
- [ ] One bad feed in `DEFAULT_FEEDS` does NOT abort the whole `rss new` run (confirmed via temporary bad-feed test, then reverted).
- [ ] Phase 2/3 commands (`memory_index`, `memory_search`, hooks) still work — no regressions.
- [ ] `CLAUDE.md` updated with all CLI commands + Phase 4 section + Phase 4 marked `[x]`.
- [ ] No Agent SDK calls anywhere in Phase 4 code; no `CLAUDE_INVOKED_BY` set in any new file.
- [ ] `git diff --stat` confirms no untracked tokens or `.env` files about to be committed.

---

## COMPLETION CHECKLIST

- [ ] All foundation tasks (F1–F7) completed and validated.
- [ ] All Slack tasks (S1) completed; CLI smoke tested by Bruno.
- [ ] All GitHub tasks (G1) completed; open-issue smoke test confirmed by Bruno.
- [ ] All ClickUp tasks (C1) completed; status update smoke test confirmed by Bruno.
- [ ] All Google tasks (M1, M2, M3) completed; Gmail and Calendar reads confirmed.
- [ ] All RSS tasks (R1) completed; etag dedup confirmed.
- [ ] Wiring tasks (W1, W2) completed.
- [ ] All Level 1–5 validation commands pass.
- [ ] CLAUDE.md updated.
- [ ] Acceptance criteria all met.
- [ ] One full pass of `query.py <integration> <subcmd>` for every documented subcommand, observed by Bruno.
- [ ] Decision: do we squash the foundation rewrite + each integration into separate commits, or one big "feat: Phase 4 integrations" commit? Recommend separate commits per integration for clean revert paths.

---

## NOTES

**Design decisions locked in:**

- **No tests.** Manual CLI smoke per the existing repo's "no test framework" stance. Adding pytest is a Phase 8/9 concern.
- **No Agent SDK calls.** Phase 4 is pure deterministic API client code. Phase 6's heartbeat is what stitches these into Sonnet/Haiku reasoning.
- **No `CLAUDE_INVOKED_BY`.** This env var only matters for SDK-invoking scripts; Phase 4 has none.
- **Lazy imports in `query.py`.** Importing all integrations at the top would force the user to have every token configured even if they only want `rss`. Lazy registration keeps each subcommand independent.
- **`.claude/.env` over root `.env`.** Bruno's existing convention. Aligns secrets with the rest of `.claude/`.
- **Slack `chat:write` is the only autonomous send surface.** Confirmed in `phase-4-integrations.md` and `SOUL.md`. All other writes (GitHub issues, ClickUp tasks, Gmail) require explicit Bruno-initiated CLI invocation OR Phase 6 heartbeat with explicit user confirmation flow.
- **No `gmail.send` scope, ever.** OAuth scopes hardcoded in `bootstrap_google_oauth.py:25–29` — `readonly`, `modify` (for labels/marking-read), `calendar.events.readonly`. Adding `gmail.send` would require re-running consent AND breaking the SOUL.md boundary.
- **Two ClickUp workspaces.** `CLICKUP_WORKSPACES=name:id,name:id` parsed once. Reads default to "all configured workspaces"; writes require explicit `--workspace`.
- **Phase 6 contract: dataclass field names.** `Channel.id`, `Message.channel_id`/`ts`/`text`/`thread_ts`, `Issue.repo`/`number`/`title`/`url`, `Task.workspace`/`list_id`/`status`/`due_date`, `Event.start_iso`/`end_iso`, `FeedItem.item_id`/`link`. Phase 6's heartbeat reads these directly. Renaming any of them later is a breaking change.
- **Repository-relative paths.** Every integration computes `REPO_ROOT = Path(__file__).resolve().parents[3]` (3 levels up from `integrations/<name>.py` → repo root). Don't break this when reorganizing.

**Risks / open questions for Bruno:**

- **GitHub PAT repo scope** — confirm the FGPAT covers `GITHUB_DEFAULT_REPO` AND any other repo we'd want `assigned_to_me` to scan. If multi-repo coverage is wanted, Phase 4's `assigned_to_me` should iterate a configured `GITHUB_REPOS` list (not just `GITHUB_DEFAULT_REPO`). For now, plan assumes single-repo scope.
- **Bruno's GitHub login** — `g.get_user().login` will fetch it on first call, but USER.md should be populated with this for Phase 6's draft generator.
- **HuggingFace daily papers feed URL** — community mirrors drift. Validate at integration time and drop with a comment if dead.
- **Slack workspace size** — Bruno + Lisa only. `users_conversations(limit=200)` paginates, but at this size one page suffices.
- **ClickUp custom fields** — the plan does not surface custom fields in `Task`. If Phase 6's heartbeat needs them, expand the dataclass and the GET request.

**Confidence score: 8/10.**

The 2 lost points:
- HuggingFace mirror URL drift (1 point) — may need swapping post-implementation.
- Slack `mentions_since_last_run` derived from text-substring matching `<@BOT_USER_ID>` is fragile if Slack changes the mention serialization format (1 point) — the proper solution is `app_mentions` event subscription via Socket Mode (Phase 7), but that's out of scope here.

Everything else is well-established API surface with stable docs, and the dependencies are already pinned in `pyproject.toml`.
