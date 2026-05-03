# Phase 7 — Slack chat bot (`.claude/chat/bot.py`)

> The following plan should be complete, but it's important that you validate documentation and codebase patterns and task sanity before you start implementing. Pay special attention to naming of existing utils/types/models — import from the right files (`shared.py`, `integrations.slack`, `claude_agent_sdk`).

## Feature Description

A long-running daemon that turns Bruno's BrunOS Slack workspace into a remote chat surface for his second brain. The bot listens on Slack Socket Mode for DMs in the personal workspace, routes each Slack thread to a stateful `ClaudeSDKClient` session, and lets Bruno talk to BrunOS from any Slack client (laptop, mobile) without opening a terminal. The agent inherits all Phase 1–5 capabilities — vault navigation (`brunos-vault` skill), hybrid memory search (`memory-search` skill), and the Phase 4 integration dispatcher (`query.py`) for live Slack/GitHub/ClickUp/Gmail/Calendar/RSS reads.

This is the **only** autonomous-send surface in BrunOS, gated by the SOUL.md "Slack send carve-out": replies are only sent in DMs (or threads where the bot is @mentioned) inside Bruno's personal Slack workspace. Every other surface (email, GitHub comments, X, etc.) remains draft-only.

## User Story

> As Bruno, on the move or away from my laptop,
> I want to DM my BrunOS Slack bot natural-language questions ("what's overdue today?", "draft a reply to Lisa's last message", "what did I write about voice corpus design last week?")
> so that I can interact with my second brain remotely without opening a terminal, and so the conversation lives in a thread I can re-open later instead of vanishing.

## Problem Statement

Phases 0–6 give BrunOS proactive capabilities (heartbeat, reflection, drafts, habits) that fire on schedule and write into the vault. But the only way to **converse** with the agent today is via `claude` CLI on the laptop. Bruno is mobile (sales calls, travel, commute) and Slack is already his daily messenger — making Slack DMs a chat surface delivers remote conversational access at near-zero new infra cost (Socket Mode = no public URL, no port forwarding, reuses the existing `xoxb-` + `xapp-` tokens).

## Solution Statement

Build a single Python daemon at `.claude/chat/bot.py` that:

1. Connects to Slack via `slack_bolt.AsyncApp` + `AsyncSocketModeHandler` (outbound WebSocket).
2. Listens **only** for `message.im` events (DMs), filtering bot-self echoes via `bot_id` / `subtype` / cached `bot_user_id`.
3. Maintains an in-process `dict[thread_key, ClaudeSDKClient]` keyed by Slack thread root `ts`.
4. On each DM, sends Bruno's text to that thread's `ClaudeSDKClient.query(...)`, streams the response, and replies in-thread via Bolt's `say()`.
5. Sets `os.environ["CLAUDE_INVOKED_BY"] = "chat"` BEFORE importing `claude_agent_sdk` (recursion guard — without this, every SDK child session triggers SessionEnd flush hooks that loop).
6. Uses `setting_sources=["project"]` so each session loads `CLAUDE.md` and the existing skills (`brunos-vault`, `memory-search`, `news-digest`, `weekly-review`) — no duplication.
7. Builds a per-session `system_prompt` that injects vault canonical context (SOUL.md + USER.md + MEMORY.md + last 3 daily logs + HEARTBEAT.md + HABITS.md) by reusing `hooks/session-start-context.py::build_context()`.
8. Allows `Read`, `Write`, `Edit`, `Bash` tools so the agent can read/write the vault, draft replies, and shell out to `query.py` / `memory_search.py`.
9. Persists thread→session mapping in SQLite at `.claude/data/chat.db` so the bot can resume a thread's prior context after a daemon restart by replaying Slack thread history.

## Feature Metadata

- **Feature Type**: New Capability
- **Estimated Complexity**: High
- **Primary Systems Affected**:
  - New: `.claude/chat/` package (bot, session manager, adapter)
  - Reused: `.claude/scripts/integrations/slack.py`, `.claude/scripts/shared.py`, `.claude/hooks/session-start-context.py`, `.claude/scripts/memory_search.py`, `.claude/scripts/query.py`
  - Touched: `.claude/.env.example` (Slack send-scope note), `CLAUDE.md` (Phase 7 marker + run command)
- **Dependencies**:
  - Already declared in `pyproject.toml`: `slack_bolt>=1.20`, `slack_sdk>=3.27`, `claude-agent-sdk>=0.1`
  - Slack workspace config (NOT in code): `chat:write` bot scope must be added; Event Subscriptions enabled with **only** `message.im`; Socket Mode enabled with `xapp-...` token having `connections:write`.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: READ THESE BEFORE IMPLEMENTING

- `.claude/scripts/integrations/slack.py` (lines 1–373) — **read in full**.
  - `_client()` (73–86): cached `WebClient` with `RateLimitErrorRetryHandler`. Phase 7 uses Bolt's `AsyncWebClient` instead but follows the same lazy-singleton pattern.
  - `_filter_msg(m, bot_user_id)` (154–162): the canonical filter for self-echoes — mirror this logic in the bot's event handler.
  - `_bot_user_id(client, state)` (103–110): caches `auth.test().user_id` into `slack-state.json`. Phase 7 reuses this state file (don't fork) — the bot calls `client.auth_test()` at startup and reuses the same `bot_user_id` field.
  - `send_message`, `reply_in_thread` (273–288): existing send helpers gated by SOUL.md carve-out. Phase 7 uses Bolt's `say()` instead (it goes through the same `chat.postMessage` API + the bot token).
  - State file path: `.claude/data/state/slack-state.json`. Phase 7 must NOT clobber the `channels` map written by `since_last_run()` — only read/write `bot_user_id`.

- `.claude/scripts/shared.py` (lines 1–318) — **read 1–195 for utility surface**.
  - `vault_path()` (75–87): always resolve via this; never hardcode `BrunOS/`.
  - `now_brt()`, `_ts_brt()` (28–35): canonical timestamp helpers.
  - `atomic_write()` (133–148): use for any vault write.
  - `save_state()`, `load_state()` (183–194): for chat-db sidecar JSON if SQLite is overkill for the MVP.
  - `load_env()` (62–72): call once at bot startup so `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` are present.

- `.claude/hooks/session-start-context.py` (lines 1–91) — **reuse `build_context()` directly**.
  - Returns the same canonical concatenation (SOUL+USER+MEMORY+last-3-dailies+HEARTBEAT+HABITS) the SessionStart hook injects on every Claude Code launch. Importing it gives the bot identical context per chat session without re-implementing the read order.

- `.claude/skills/news-digest/scripts/digest.py` (lines 14–96) — **mirror the SDK invocation pattern**.
  - Top-of-file `os.environ.setdefault("CLAUDE_INVOKED_BY", "news-digest")` BEFORE any SDK import. Phase 7 uses `"chat"`.
  - `_extract_text(msg)` (55–71): assistant-message text extraction. Phase 7 needs the same logic for streaming responses out of `ClaudeSDKClient.receive_response()`.
  - The script uses `query()` (one-shot). Phase 7 uses **`ClaudeSDKClient`** (stateful) — different surface; verify the API at implementation time (see Gotchas).

- `.claude/skills/weekly-review/scripts/aggregate_week.py` (lines 14–97) — same recursion-guard pattern. Confirms the project standard: env-var set, then NumPy-style noqa imports.

- `.claude/scripts/query.py` (lines 1–75) — **what the bot's Bash calls will hit**.
  - The bot agent can shell out: `uv run python .claude/scripts/query.py slack since`, `... gmail unread`, `... clickup overdue`. Same dispatcher Phase 4 ships.
  - `query.py` calls `load_env()` itself, so the bot doesn't need to pre-export tokens to the subprocess environment — they're already in the parent process env.

- `.claude/scripts/integrations/registry.py` (lines 29–36) — confirms which env vars gate which integrations. Useful for the bot's startup banner ("connected: slack ✓ github ✓ clickup ✓ ...").

- `.claude/skills/brunos-vault/SKILL.md` and `.claude/skills/memory-search/SKILL.md` — **the bot inherits these via `setting_sources=["project"]`**. Don't re-implement; trust the progressive-disclosure model.

- `CLAUDE.md` (project root) — **read top-to-bottom**.
  - "Proactivity: Assistant level" section: codifies what the bot is allowed to do without asking. The Slack-on-DM carve-out (line under "Allowed without asking") is what authorizes the bot's autonomous reply.
  - "Recursion guard" section: every Agent SDK script MUST set `CLAUDE_INVOKED_BY` before SDK import.
  - "`setting_sources` policy": every `ClaudeAgentOptions(...)` call MUST pass `setting_sources` explicitly.

- `.claude/settings.json` (lines 1–36) — already wires SessionStart, PreCompact, SessionEnd hooks. The bot's child SDK sessions inherit these. The `CLAUDE_INVOKED_BY=chat` env var causes both flush hooks to exit early (verify by reading `.claude/hooks/session-end-flush.py` — same recursion guard pattern as PreCompact).

- `.claude/.env.example` (lines 23–25) — `SLACK_BOT_TOKEN` (`xoxb-...`) and `SLACK_APP_TOKEN` (`xapp-...`) already declared. Confirm both are populated in `.claude/.env` before running the bot.

### New Files to Create

- `.claude/chat/__init__.py` — empty package marker.
- `.claude/chat/bot.py` — entrypoint daemon. `uv run python .claude/chat/bot.py` starts it.
- `.claude/chat/session_manager.py` — owns the `dict[thread_key, ClaudeSDKClient]` and the SQLite-backed thread index for restart resilience.
- `.claude/chat/system_prompt.py` — builds the per-session system prompt by composing `build_context()` + the chat-mode preamble + the Slack carve-out reminder.
- `.claude/chat/adapters/__init__.py` — empty package marker.
- `.claude/chat/adapters/slack_adapter.py` — Bolt event registration, self-echo filter, thread-key derivation. Encapsulates Slack-specific glue so a future Discord/Teams adapter can drop in behind the same protocol (don't write the protocol yet — YAGNI; just keep all Slack imports in this one file).
- `.claude/data/state/chat.db` — SQLite (auto-created on first run; gitignored already by `.claude/data/`).
- `tests/chat/__init__.py` — package marker. `tests/` directory does not yet exist in this repo; this phase introduces it. **Confirm with Bruno before adding** (no test framework is configured in `pyproject.toml`); if he wants tests deferred, the bot ships test-less and Phase 8 / a follow-up sets up `pytest`. Default plan below assumes minimal pytest setup; the implementer should ask if uncertain.

### Relevant Documentation — READ THESE BEFORE IMPLEMENTING

- [slack_bolt Python — async basic concepts](https://slack.dev/bolt-python/concepts) — full doc index, but specifically:
  - **Async basics**: how to use `AsyncApp`, decorators, `say()`. Required because Phase 7 must not block the event loop while the SDK reasons.
  - **Socket Mode**: `from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler` — the `start_async()` entrypoint. No public URL needed; only requires `xapp-` token with `connections:write`.

- [Slack API — message.im event](https://api.slack.com/events/message.im) — confirms the event shape (`channel_type: "im"`, `user`, `text`, `ts`, `thread_ts`, `bot_id`, `subtype`).

- [Slack API — Socket Mode overview](https://api.slack.com/apis/connections/socket) — explains `xapp-` token, scope requirements, why it's the right choice for a personal-workspace bot vs HTTP Events.

- [`claude-agent-sdk` Python — README + reference](https://github.com/anthropics/claude-agent-sdk-python) — the SDK is at `>=0.1,<0.2` per `pyproject.toml`. **Critical to verify at implementation time:** the exact API surface for **`ClaudeSDKClient`** (stateful, multi-turn).
  - Open the installed package source: `uv run python -c "import claude_agent_sdk, inspect; print(inspect.getsourcefile(claude_agent_sdk))"` then read `client.py` / `__init__.py` to confirm:
    1. Whether the class is `ClaudeSDKClient` or named differently in 0.1.x.
    2. The connect / query / receive_response coroutine names and shapes.
    3. Whether `options=` accepts the same `ClaudeAgentOptions` object as `query()` does (used in `digest.py` / `aggregate_week.py`).
    4. Whether the SDK supports session resume by ID (relevant for chat.db persistence — if not, fall back to replaying Slack thread history into `client.query()` on resume).

- [Slack send-scope `chat:write`](https://api.slack.com/scopes/chat:write) — confirms this scope must be added to the bot token. **The bot CANNOT send without it; chat.postMessage will return `missing_scope`.** Bruno may need to (1) add the scope in the Slack app config and (2) reinstall the app in the workspace to pick up the new scope.

- [Slack Event Subscriptions setup](https://api.slack.com/apis/connections/events-api#subscriptions) — only `message.im` should be subscribed (PRD Phase 7 explicit requirement). Subscribing `message.channels` would deliver every channel message and pollute the bot's event stream.

### Patterns to Follow

**Recursion guard (mandatory)** — pattern from `digest.py:19` and `aggregate_week.py:18`:

```python
from __future__ import annotations
import os
os.environ.setdefault("CLAUDE_INVOKED_BY", "chat")
# ...all subsequent imports, including claude_agent_sdk
```

**SDK options block** — pattern from `digest.py:74–83`, but with `setting_sources=["project"]` and `Read/Write/Edit/Bash` tools (chat needs to do work, not just reason):

```python
options = ClaudeAgentOptions(
    allowed_tools=["Read", "Write", "Edit", "Bash"],
    setting_sources=["project"],          # loads CLAUDE.md + skills
    system_prompt=system_prompt,           # see system_prompt.py
    model="claude-sonnet-4-6",
    max_turns=15,
)
```

**Lazy-singleton client** — pattern from `slack.py:73–86`:

```python
_APP: AsyncApp | None = None

async def _app() -> AsyncApp:
    global _APP
    if _APP is None:
        _APP = AsyncApp(token=os.environ["SLACK_BOT_TOKEN"])
    return _APP
```

**Vault read** — `shared.vault_path()` ALWAYS, never `Path("BrunOS/Memory/...")`:

```python
from shared import vault_path
soul = (vault_path() / "Memory" / "SOUL.md").read_text(encoding="utf-8")
```

**stderr logging** — pattern from `digest.py:51–53` and `aggregate_week.py:58–60`:

```python
def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
```

The bot must NOT write to stdout — Bolt's logger and the SDK both use stdout for their own protocol payloads in some configurations. stderr is the project standard for status logs.

**TODO sanitize markers** — pattern from `digest.py:139–140`. Anywhere external content (Slack message text from Bruno's DM) flows into a Claude prompt, leave a comment:

```python
# TODO(Phase 8): wrap user-facing text in <external_data> via sanitize.py.
```

DM content is from Bruno himself, so injection risk is lower than RSS/Gmail — but he may paste quoted external text. Mark the boundary now so Phase 8's retrofit catches it.

**Module path resolution** — pattern from `digest.py:29–30`:

```python
REPO_ROOT = Path(__file__).resolve().parents[<n>]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))
```

For `.claude/chat/bot.py`, `parents[2]` reaches the repo root (matches the `scripts/` pattern). For `.claude/chat/adapters/slack_adapter.py`, `parents[3]`.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation

Wire the package layout, env loading, recursion guard, and a smoke-test entrypoint that connects to Slack but doesn't yet route to Claude. Verify the WebSocket comes up and `auth.test` returns a `bot_user_id`.

**Tasks:**

- Create `.claude/chat/` and `.claude/chat/adapters/` packages.
- Set `CLAUDE_INVOKED_BY=chat` at the top of `bot.py` before any other import.
- Implement `bootstrap()` that calls `shared.load_env()` and validates `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` are set, exiting with a clear error if not.
- Stand up `AsyncApp` + `AsyncSocketModeHandler.start_async()` with a no-op `@app.event("message")` that just logs `event["channel_type"]` to stderr.
- Confirm: running `uv run python .claude/chat/bot.py`, then DMing the bot from Slack, prints `channel_type=im` to the terminal.

### Phase 2: Core Implementation

Wire the per-thread `ClaudeSDKClient`, the system prompt builder, and the round-trip from DM → SDK → reply.

**Tasks:**

- Build `system_prompt.py::build_chat_system_prompt()` that composes:
  1. A short preamble ("You are BrunOS running as a Slack DM bot in Bruno's personal workspace. Replies stay in-thread. The Slack carve-out from SOUL.md authorizes you to send messages here without asking — but only here. All other surfaces are draft-only.").
  2. The output of `hooks.session_start_context.build_context()` (import as a module, see Gotcha).
  3. The current BRT timestamp via `_ts_brt()`.
- Build `session_manager.py::SessionManager` with:
  - `get_or_create(thread_key) -> ClaudeSDKClient` — creates a new SDK client with the chat options on first use, returns cached client on subsequent messages.
  - `close_all()` — `await client.disconnect()` for each cached client during shutdown.
  - SQLite persistence stub: store `(thread_key, created_at, last_message_at)` so a future restart can list "warm" threads and decide whether to replay history.
- In `slack_adapter.py`, register the `message` handler with the Bolt-standard self-echo filter:
  ```python
  if event.get("channel_type") != "im": return
  if event.get("bot_id") or event.get("subtype"): return
  if event.get("user") == bot_user_id: return
  ```
- Derive `thread_key = event.get("thread_ts") or event["ts"]` so the first message in a DM starts a new thread, and subsequent threaded messages route to the same SDK client.
- Send Bruno's text to `client.query(...)`, accumulate the response via `client.receive_response()` + `_extract_text()`, then `await say(text=reply, thread_ts=thread_key)`.

### Phase 3: Integration

Make the bot first-class in the project: docs, run command, env-var smoke test, and a graceful-shutdown hook so Ctrl+C closes WebSocket + all SDK clients cleanly.

**Tasks:**

- Add a `--smoke-test` flag to `bot.py` that calls `auth.test`, prints the bot user ID + workspace, exits 0 — useful for Phase 9 launchd verification.
- Wire `signal.SIGINT` / `signal.SIGTERM` handlers that call `session_manager.close_all()` then `await handler.close_async()`.
- Update `CLAUDE.md`:
  - Add `uv run python .claude/chat/bot.py` to the Build commands list.
  - Add a "Phase 7 — Slack chat bot" section documenting the `chat:write` scope requirement and the `message.im`-only Event Subscription rule.
  - Mark Phase 7 done in the Phase status checklist.
- Update `.claude/.env.example`:
  - Above the existing `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` lines, add a comment that Phase 7 requires `chat:write` added to the bot token's scopes AND reinstalling the app in the workspace.

### Phase 4: Testing & Validation

Manual end-to-end is the spine; pure-function unit tests cover the filter + thread-key logic that's bug-prone.

**Tasks:**

- Manual: `uv run python .claude/chat/bot.py --smoke-test` exits 0 and prints the bot user ID.
- Manual: full bot → DM "list overdue ClickUp tasks" → bot replies with content sourced via shelling to `query.py clickup overdue` (verifies the SDK's Bash tool reaches the Phase 4 dispatcher).
- Manual: DM "what did I write about voice corpus?" → bot replies with content sourced via `memory_search.py --path-prefix drafts/sent` (verifies skill discovery via `setting_sources=["project"]`).
- Manual: DM "search for 'phase 5'" then a follow-up "expand on point 2" — verifies multi-turn threading.
- Manual: send the bot's own posted message back through Slack (edit a previous bot reply); verify the bot does NOT re-process it (self-echo filter).
- Pure-function unit tests (if pytest is approved by Bruno — see "New Files to Create"):
  - `test_filter_self_echo` — every combination of `bot_id` / `subtype` / `user==bot_user_id`.
  - `test_thread_key_derivation` — `thread_ts` present vs absent.
  - `test_build_chat_system_prompt` — happy path includes SOUL/USER/MEMORY markers; falls through gracefully if vault is missing files.

---

## STEP-BY-STEP TASKS

Execute every task in order. Each task is atomic and independently testable.

### CREATE `.claude/chat/__init__.py`

- **IMPLEMENT**: empty file (package marker).
- **VALIDATE**: `test -f .claude/chat/__init__.py`

### CREATE `.claude/chat/adapters/__init__.py`

- **IMPLEMENT**: empty file (package marker).
- **VALIDATE**: `test -f .claude/chat/adapters/__init__.py`

### CREATE `.claude/chat/system_prompt.py`

- **IMPLEMENT**:
  - Module-level `from __future__ import annotations`.
  - Add the standard `REPO_ROOT = Path(__file__).resolve().parents[2]` + `sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))` + a second insert for `.claude/hooks/` so `import session_start_context` resolves.
  - Function `build_chat_system_prompt() -> str` that:
    1. Imports `session_start_context` (the hook module) and calls its `build_context()` to get the canonical vault block.
    2. Prepends a chat-mode preamble (~200 words): identity ("BrunOS running as Slack DM bot"), explicit Slack carve-out reminder, `chat:write` scope authorization, format guidance (Slack mrkdwn — single-asterisk bold, single-underscore italic, NO real markdown headers since Slack ignores `##`).
    3. Appends current BRT timestamp via `_ts_brt()`.
- **PATTERN**: composition pattern from `aggregate_week.py:_build_bundle()` — string sections joined with `\n\n`.
- **IMPORTS**: `from shared import _ts_brt`. Hook import is by `sys.path` injection because hooks aren't a package; OR use `importlib.util` to load `session_start_context.py` by path (cleaner — no path hack). Choose `importlib.util` for cleanliness.
- **GOTCHA**: `session_start_context.py` reads `sys.stdin` in its `main()` but `build_context()` is pure — only that function should be imported. Don't trigger the hook's main path.
- **GOTCHA**: Slack mrkdwn ≠ markdown. The agent should be told to use `*bold*` not `**bold**`, `_italic_` not `*italic*`, `>quote` for quotes, and ``` ` ``` / ` ``` ` for code. Tables and `##` headers will render as plain text.
- **VALIDATE**: `uv run python -c "from chat.system_prompt import build_chat_system_prompt; p = build_chat_system_prompt(); assert 'SOUL.md' in p and 'USER.md' in p and 'MEMORY.md' in p and 'Slack' in p; print(f'system prompt: {len(p)} chars')"` (run from `.claude/`).

### CREATE `.claude/chat/session_manager.py`

- **IMPLEMENT**:
  - Class `SessionManager` with:
    - `__init__(self, options_factory: Callable[[], ClaudeAgentOptions], db_path: Path)`.
    - Async `get_or_create(thread_key: str) -> ClaudeSDKClient` — checks in-memory dict, creates+connects new client on miss, persists `(thread_key, created_at_brt)` to SQLite `chat_threads` table.
    - Async `close_all()` — iterates clients, awaits `disconnect()` (or whatever the SDK exposes — verify), clears the dict.
    - `_init_db(db_path)` — `CREATE TABLE IF NOT EXISTS chat_threads (thread_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, last_message_at TEXT NOT NULL)`. No `vec0` / FTS5 — this is just a thread index, not RAG.
- **PATTERN**: Lazy-singleton + dict pattern from `slack.py:_CLIENT` / `_USER_NAMES`.
- **IMPORTS**: `import sqlite3`, `from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions`, `from shared import _ts_brt, atomic_write`.
- **GOTCHA**: `ClaudeSDKClient` API surface in `claude-agent-sdk>=0.1,<0.2` — names like `connect()` / `query()` / `receive_response()` / `disconnect()` are best-guess from common SDK patterns. **Run** `uv run python -c "import claude_agent_sdk, inspect; [print(n) for n in dir(claude_agent_sdk) if not n.startswith('_')]"` and read the relevant class source before coding. Adjust method names to match.
- **GOTCHA**: SQLite from async context — use `sqlite3` synchronously inside a `loop.run_in_executor()` if writes become hot, but the chat-thread table sees ≤1 write per DM, so plain `sqlite3` calls are fine. Don't pull in `aiosqlite`.
- **VALIDATE**: `uv run python -c "from chat.session_manager import SessionManager; print(SessionManager.__init__.__doc__ or 'init exists')"`.

### CREATE `.claude/chat/adapters/slack_adapter.py`

- **IMPLEMENT**:
  - Function `register(app: AsyncApp, bot_user_id: str, session_manager: SessionManager) -> None` that decorates `@app.event("message")` with the DM filter and the SDK round-trip.
  - Filter logic mirrors `slack.py:_filter_msg`:
    ```python
    if event.get("channel_type") != "im": return
    if event.get("bot_id") or event.get("subtype"): return
    if event.get("user") == bot_user_id: return
    if not event.get("text"): return
    ```
  - Derive `thread_key = event.get("thread_ts") or event["ts"]`.
  - Per-thread asyncio lock (`asyncio.Lock`) keyed on `thread_key` to prevent two concurrent messages in the same thread from interleaving.
  - Round-trip:
    1. `client = await session_manager.get_or_create(thread_key)`.
    2. `await client.query(event["text"])` (or whatever the SDK names it).
    3. Accumulate response chunks via `_extract_text()` (copy from `digest.py:55–71`).
    4. `await say(text=reply, thread_ts=thread_key)`.
  - Wrap the round-trip in `try/except` — on exception, post a friendly "I hit an error: <type>; check stderr" reply rather than crashing the event loop.
- **PATTERN**: error-handling style from `digest.py:_run` (try/except around each pipeline stage with stderr logs).
- **IMPORTS**: `from slack_bolt.async_app import AsyncApp`, `from chat.session_manager import SessionManager`.
- **GOTCHA**: Bolt's `say()` injects the channel automatically only when called inside an event handler — outside the handler closure, you'd have to `await app.client.chat_postMessage(...)` directly.
- **GOTCHA**: A long SDK turn (Sonnet thinking 30s) could time out Slack's keep-alive. Bolt handles this internally for HTTP Events but Socket Mode is fine — the WebSocket stays open. No explicit ack needed for `message` events. Optional UX polish (skip for MVP): drop a 👀 reaction immediately via `app.client.reactions_add(...)` then post the reply when ready.
- **VALIDATE**: `uv run python -c "from chat.adapters.slack_adapter import register; print('register importable')"`.

### CREATE `.claude/chat/bot.py`

- **IMPLEMENT**:
  - **First two lines** (before any other import):
    ```python
    from __future__ import annotations
    import os
    os.environ.setdefault("CLAUDE_INVOKED_BY", "chat")
    ```
  - `REPO_ROOT = Path(__file__).resolve().parents[2]` + sys.path inserts.
  - `from shared import load_env, _ts_brt`. Call `load_env()` early.
  - `_log(msg)` helper — stderr, flush=True, mirroring `digest.py:_log`.
  - `_options_factory()` returns a fresh `ClaudeAgentOptions(allowed_tools=["Read", "Write", "Edit", "Bash"], setting_sources=["project"], system_prompt=build_chat_system_prompt(), model="claude-sonnet-4-6", max_turns=15)`. **Build the system prompt ONCE** at startup and reuse — calling `build_context()` reads the vault and is too expensive per-message; the canonical context only refreshes when the daemon restarts (acceptable trade-off for MVP).
  - `async def main_async(smoke_test: bool)`:
    1. `load_env()`.
    2. Validate tokens; exit 1 with clear stderr if either missing.
    3. Build `AsyncApp(token=os.environ["SLACK_BOT_TOKEN"])`.
    4. `auth = await app.client.auth_test()`; `bot_user_id = auth["user_id"]`.
    5. **Persist `bot_user_id` into `slack-state.json`** — load existing state (don't clobber the `channels` map), set `state["bot_user_id"] = bot_user_id`, save. This keeps Phase 4's `slack.py` and Phase 7's bot in sync on the bot identity.
    6. If `smoke_test`: `_log(f"smoke ok: bot_user_id={bot_user_id} team={auth['team']}")` and return 0.
    7. `session_manager = SessionManager(_options_factory, REPO_ROOT / ".claude" / "data" / "state" / "chat.db")`.
    8. `register(app, bot_user_id, session_manager)`.
    9. Wire SIGINT/SIGTERM signal handlers via `loop.add_signal_handler(...)` that set a `shutdown_event`.
    10. `handler = AsyncSocketModeHandler(app, app_token=os.environ["SLACK_APP_TOKEN"])`; `await handler.start_async()`; `await shutdown_event.wait()`.
    11. On shutdown: `await session_manager.close_all()`; `await handler.close_async()`; `_log("bot stopped")`.
  - `main(argv)` — argparse for `--smoke-test`, then `asyncio.run(main_async(...))`.
- **PATTERN**: argparse + `asyncio.run` pattern from `digest.py:main` and `aggregate_week.py:main`.
- **IMPORTS**: `import asyncio, signal, argparse, sys`; `from slack_bolt.async_app import AsyncApp`; `from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler`; `from chat.system_prompt import build_chat_system_prompt`; `from chat.session_manager import SessionManager`; `from chat.adapters.slack_adapter import register`; `from shared import load_state, save_state, STATE_DIR`.
- **GOTCHA**: `asyncio.get_event_loop()` is deprecated in 3.13. Use `asyncio.get_running_loop()` inside `main_async()` — it must be called AFTER `asyncio.run` has started the loop.
- **GOTCHA**: `loop.add_signal_handler` is Unix-only. Phase 9 deploys to macOS (launchd) and Linux VPS (systemd) — both Unix, so this is fine. Don't add Windows fallback.
- **GOTCHA**: When the bot calls `app.client.auth_test()` with the bot token, it returns the *bot's* user ID (under `user_id`), not Bruno's. That's exactly what the filter needs. `bot_id` in the `auth.test` response is the *app* ID and is NOT what `event.get("user")` returns — don't confuse the two.
- **VALIDATE**:
  - `uv run python .claude/chat/bot.py --smoke-test` → prints `smoke ok: bot_user_id=U... team=...` and exits 0.
  - `uv run python -m py_compile .claude/chat/bot.py .claude/chat/system_prompt.py .claude/chat/session_manager.py .claude/chat/adapters/slack_adapter.py` → exit 0 (syntax check).

### UPDATE `.claude/.env.example`

- **IMPLEMENT**: Add a comment block above the `SLACK_BOT_TOKEN` line explaining Phase 7's additional requirements.
- **PATTERN**: comment style of the existing `# --- Slack (Phase 4.1) ---` header.
- **CONTENT**:
  ```
  # --- Slack (Phase 4.1 + Phase 7) ---
  # Phase 7 (chat bot) requires:
  #   1. `chat:write` added to Bot Token scopes (Slack app → OAuth & Permissions).
  #   2. App reinstalled in workspace after scope change (banner prompts you).
  #   3. Event Subscriptions enabled with ONLY `message.im` under Bot Events.
  #      Do NOT subscribe `message.channels` — pollutes the event stream.
  #   4. Socket Mode enabled; the App-Level Token below needs `connections:write`.
  ```
- **VALIDATE**: `grep -q "Phase 7" .claude/.env.example` exits 0.

### UPDATE `CLAUDE.md`

- **IMPLEMENT**:
  - In the **Build commands** code block, add `uv run python .claude/chat/bot.py` and `uv run python .claude/chat/bot.py --smoke-test` under a new `# Phase 7 — Slack chat bot:` comment.
  - After the existing "## Skills (Phase 5)" section, add a new "## Slack chat bot (Phase 7)" section documenting:
    - Entry point and what triggers it.
    - The `chat:write` scope + `message.im`-only Event Subscriptions requirement.
    - The carve-out: this is the **only** autonomous-send surface in BrunOS; SOUL.md prohibits sending elsewhere.
    - Recursion-guard value: `CLAUDE_INVOKED_BY=chat`.
    - Tool surface: `Read | Write | Edit | Bash` with `setting_sources=["project"]` (loads CLAUDE.md + skills).
    - The Phase 6 / Phase 7 boundary: heartbeat handles non-DM channels via polling; chat bot owns DMs via Socket Mode push.
  - In the **Phase status** checklist, replace the line `- [ ] Phase 7 — Slack chat bot (optional)` with `- [x] Phase 7 — Slack chat bot (YYYY-MM-DD)` once the build is complete.
- **PATTERN**: existing "## Skills (Phase 5)" section structure for prose style and code-fence formatting.
- **VALIDATE**: `grep -q "Phase 7" CLAUDE.md && grep -q ".claude/chat/bot.py" CLAUDE.md` exits 0.

### CREATE `tests/chat/test_bot.py` (CONDITIONAL)

- **PRECONDITION**: Confirm with Bruno that pytest setup is wanted. If yes:
- **IMPLEMENT**:
  - `test_filter_self_echo` — table-driven test covering all `bot_id` / `subtype` / matching-`user` combinations against the adapter's filter helper. Extract the filter into a pure function `_should_handle(event, bot_user_id) -> bool` in `slack_adapter.py` so it's testable without async fixtures.
  - `test_thread_key_derivation` — pure-function `_derive_thread_key(event)` test: `thread_ts` present → that, else `ts`.
  - `test_build_chat_system_prompt` — sets `BRUNOS_VAULT_PATH` to a tmp dir with stub SOUL.md/USER.md/MEMORY.md, asserts the output contains the expected markers and is non-empty.
- **IMPORTS**: `import pytest`; from the adapter and system_prompt modules.
- **GOTCHA**: `vault_path()` is `lru_cache(maxsize=1)`-decorated. Tests that override `BRUNOS_VAULT_PATH` must call `vault_path.cache_clear()` between cases.
- **VALIDATE**: `uv run pytest tests/chat/ -v` (after adding `pytest` to optional dev deps in `pyproject.toml` if not already present).

---

## TESTING STRATEGY

### Unit Tests

The repo has **no existing test framework wired**. This phase is the natural place to introduce one (`pytest`) but the call belongs to Bruno — propose, don't unilaterally add. If approved:

- Test only the pure functions: `_should_handle`, `_derive_thread_key`, `build_chat_system_prompt`. Mock SDK and Slack at the module boundary; do NOT spin up a real `AsyncApp` in unit tests.
- Add `pytest>=8` and `pytest-asyncio>=0.23` to `pyproject.toml` under `[project.optional-dependencies]` `dev = [...]` — keep them out of the runtime set.

### Integration Tests

- Manual end-to-end (the only realistic option for a Socket Mode bot): start the daemon, DM it from Slack, verify replies as outlined in "Phase 4: Testing & Validation" above.

### Edge Cases

- **Self-echo loop**: bot's own message arriving back through `message.im` → MUST drop. Test with all three filter conditions.
- **Edited message** (`subtype=message_changed`) → MUST drop.
- **Empty text** (file-only DM, e.g. an image upload with no caption) → MUST drop.
- **Two DMs in flight in same thread**: per-thread `asyncio.Lock` serializes; replies arrive in order.
- **DM with @ mention in the text** (e.g. Bruno tags a coworker): no special handling — it's just text content; the bot replies in-thread.
- **Thread length > SDK token budget**: `max_turns=15` caps it; SDK will compact internally. If a single message itself is >100KB, post a friendly "that's too long, paste it as a file or shorten it" reply.
- **Slack `chat:write` scope missing** at runtime: `chat.postMessage` returns `missing_scope`; bot should log a clear stderr error and post nothing (Slack will surface the silence to Bruno who can check the daemon logs).
- **Slack rate limits during a busy minute**: Bolt's built-in retry handles it; if it bubbles up, the per-thread try/except catches it.
- **Daemon crash mid-conversation**: in-memory dict is lost. Restart → SessionManager reads `chat_threads` from SQLite; on next DM in a known thread, it can either start fresh (MVP) or replay thread history via `conversations.replies` to rebuild context. MVP starts fresh; document the limitation in CLAUDE.md.
- **Bruno DMs from a thread inside a public channel where he @mentioned the bot**: per the `message.im` scoping, the bot does NOT receive that event (only DMs come through). If support for `app_mention` in channels is desired, that's a Phase 7.5 addition — out of scope here.

---

## VALIDATION COMMANDS

Execute every command to ensure zero regressions and feature correctness.

### Level 1: Syntax & Style

```bash
# Syntax check every new module:
uv run python -m py_compile .claude/chat/bot.py .claude/chat/system_prompt.py .claude/chat/session_manager.py .claude/chat/adapters/slack_adapter.py

# Confirm imports resolve:
uv run python -c "from chat.system_prompt import build_chat_system_prompt; from chat.session_manager import SessionManager; from chat.adapters.slack_adapter import register; print('imports ok')"
```

### Level 2: Unit Tests (only if pytest is approved)

```bash
uv run pytest tests/chat/ -v
```

### Level 3: Integration / Smoke

```bash
# Smoke test — connects, fetches bot identity, exits:
uv run python .claude/chat/bot.py --smoke-test

# Verify Phase 4 dispatcher still works (the bot shells out to it):
uv run python .claude/scripts/query.py slack channels

# Verify memory_search still works (the bot shells out to it):
uv run python .claude/scripts/memory_search.py "test query" --k 3
```

### Level 4: Manual Validation

Order matters — each step builds on the previous.

1. **Daemon starts cleanly**: `uv run python .claude/chat/bot.py` → stderr shows `bot started: bot_user_id=U...`. Leave running.
2. **DM a simple greeting** ("hi") → bot replies in-thread within ~10s.
3. **DM a vault question**: "what's the SOUL.md tone summary?" → bot replies citing the SOUL.md content (proves system-prompt context injection).
4. **DM an integration query**: "what ClickUp tasks are overdue?" → bot replies with content fetched via shelling to `query.py clickup overdue` (proves Bash tool access + Phase 4 dispatcher reuse).
5. **DM a memory-search query**: "find anything I wrote about voice corpus" → bot returns hits sourced via `memory_search.py` (proves skill discovery via `setting_sources=["project"]`).
6. **Multi-turn**: reply in the same thread "expand on the second result" → bot retains context (proves per-thread `ClaudeSDKClient` cache).
7. **Self-echo robustness**: edit one of the bot's prior replies → bot does NOT re-process (verify in stderr — no new SDK turn fires).
8. **Graceful shutdown**: `Ctrl+C` in the daemon terminal → stderr shows `closing N sessions; bot stopped`; no orphan WebSocket. Restart and DM again — new thread works (old thread starts fresh in MVP).

### Level 5: Optional

- Run the existing skills via the bot: ask "run news-digest in dry-run" → bot can shell out via Bash to `uv run python .claude/skills/news-digest/scripts/digest.py --dry-run`.
- Confirm Phase 6 (running in parallel terminal) does NOT also reply to the same DM. Per the design: heartbeat polls non-DM channels; chat bot owns DMs. If Phase 6's heartbeat ever tries to send a DM reply autonomously, that's a coordination bug — flag it back to Phase 6.

---

## ACCEPTANCE CRITERIA

- [ ] `uv run python .claude/chat/bot.py --smoke-test` exits 0 with the bot user ID printed.
- [ ] `uv run python .claude/chat/bot.py` starts cleanly, connects to Socket Mode, logs `bot_user_id` to stderr.
- [ ] DMs in `message.im` are routed to a per-thread `ClaudeSDKClient` and replied to in-thread.
- [ ] Self-echo filter blocks all three failure modes (`bot_id`, `subtype`, `user==bot_user_id`).
- [ ] System prompt includes the canonical vault context (SOUL/USER/MEMORY/last-3-dailies/HEARTBEAT/HABITS) via `build_context()` reuse.
- [ ] `setting_sources=["project"]` is set on every `ClaudeAgentOptions(...)` (no defaults relied on).
- [ ] `CLAUDE_INVOKED_BY=chat` is set BEFORE any `claude_agent_sdk` import in `bot.py`.
- [ ] Slack mrkdwn (not real markdown) is mentioned in the system prompt.
- [ ] Graceful shutdown closes all sessions and the WebSocket.
- [ ] `CLAUDE.md` documents the run command, scope requirements, and Phase 7 boundary with Phase 6.
- [ ] `.claude/.env.example` documents the new `chat:write` + Event Subscription requirements.
- [ ] No regression: `uv run python .claude/scripts/query.py slack channels` still returns the channel list.
- [ ] No regression: `uv run python .claude/scripts/memory_search.py "test" --k 3` still returns hits.

---

## COMPLETION CHECKLIST

- [ ] All step-by-step tasks completed in order.
- [ ] Each task's validation command passed at the time of completion.
- [ ] All Level 1–4 validation commands pass.
- [ ] Manual end-to-end (8-step sequence) confirmed.
- [ ] No regressions to Phase 4 (`query.py`) or Phase 5 (skills).
- [ ] `CLAUDE.md` and `.claude/.env.example` updated.
- [ ] Bruno confirms the bot replies correctly to a DM from his phone (the actual UX target).

---

## NOTES

### Design decisions & trade-offs

- **`ClaudeSDKClient` (stateful) over `query()` (one-shot)** — necessary for multi-turn DM threads. Phase 5 skills use `query()` because they're one-shot pipelines; chat is fundamentally multi-turn.
- **In-memory dict + SQLite-stub for thread persistence** — full session resume across daemon restarts is plausibly out of scope for MVP. Restart → fresh SDK session per thread is acceptable; Bruno can paste context back if needed. The SQLite table is wired so a future enhancement (replay-on-resume) is a small change, not a refactor.
- **Build system prompt once at startup, not per message** — `build_context()` does ~6 file reads per call. For a chatty bot that's ~6 reads per message; doing it once at startup is the right cache window. Trade-off: vault edits during the daemon's run aren't reflected until restart. Acceptable: the daemon will run for hours, not days, in normal use.
- **Slack mrkdwn only** — Slack's `text` parameter renders mrkdwn (`*bold*`, `_italic_`, `>quote`, `` `code` ``, ` ```fence``` `). It does NOT render real markdown — `## headers` show as literal text, tables render as ASCII. Telling the model this in the system prompt prevents ugly replies.
- **No `AsyncWebClient` direct usage** — Bolt's `say()` and `app.client.<method>` are async-aware. Importing `AsyncWebClient` separately just adds surface area.
- **No `chat.db` SQLite-vec / FTS5** — chat persistence is a tiny key-value table, not a search index. `sqlite3` from std-lib is sufficient; do not pull in `sqlite-vec` for this table.
- **No new Anthropic SDK calls in production code without explicit `setting_sources`** — already a CLAUDE.md rule. The bot honors it on every options block.

### Phase 6 / Phase 7 coordination

Phase 6 (running in parallel) ships the heartbeat with autonomous Slack send-on-@mention as the SOUL.md carve-out. Phase 7 ships the chat bot which owns DMs. To avoid double-replies:

- **Phase 7 owns**: DMs (`channel_type=im`), via Socket Mode push events.
- **Phase 6 heartbeat owns**: non-DM channel @mentions, via polling `slack.mentions_since_last_run()`.
- **Shared state**: both processes read/write `slack-state.json`. The bot updates `bot_user_id` once at startup; the heartbeat updates `channels[<id>] = last_ts` on each tick. They don't collide on keys.
- **If Phase 6 ships before Phase 7 lands**: heartbeat may briefly see and reply to DM @mentions. Once Phase 7 is live, Phase 6's mention scan must filter out `is_im=True` channels — that's a Phase 6 task, not Phase 7. Flag it back to that branch if needed.

### `chat:write` scope: what Bruno needs to do in Slack UI

Phase 4 didn't request `chat:write` (drafts only). Phase 7 requires it — and adding a scope to an existing Slack app forces a **reinstall in the workspace** before the new scope takes effect. Steps:

1. api.slack.com → Apps → BrunOS app → OAuth & Permissions → Bot Token Scopes → Add `chat:write`.
2. Top of same page: yellow banner "Reinstall your app" → click → confirm.
3. Verify `auth.test` returns `ok=true` and the bot can `chat.postMessage` to a DM.

If Bruno hasn't done this yet, the bot will start, accept events, and silently fail on send with `missing_scope`. The bot's adapter should detect this and log a clear "scope chat:write missing — see CLAUDE.md Phase 7 setup" stderr message rather than just failing.

### `app_mention` in public channels — out of scope

Bruno may eventually want `@brunos summarize this channel` to work in public channels too. That requires (a) subscribing `app_mention` events, (b) handling them separately from `message.im`, (c) reconfirming the SOUL.md carve-out covers @mentions in non-DM contexts. Defer to a Phase 7.5 / Phase 11.

### Confidence

**8/10** for one-pass implementation success.

The +2 risk:
- `claude-agent-sdk>=0.1,<0.2` has a young, possibly fluid `ClaudeSDKClient` API surface. The implementer must inspect the installed package source as the first step (the validation command in the `session_manager.py` task does this) and adjust method names if they don't match `connect()` / `query()` / `receive_response()` / `disconnect()`. This is the single most likely source of a re-roll.
- Bolt async + signal handlers + `asyncio.run` interaction has subtle gotchas on Python 3.13 (deprecated `get_event_loop`, `add_signal_handler` quirks under uvloop). If smoke test passes but the daemon doesn't shut down cleanly under SIGINT, that's where to look.

Everything else is well-supported by existing patterns in `digest.py`, `aggregate_week.py`, and `slack.py`.
