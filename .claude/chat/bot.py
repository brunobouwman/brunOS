"""Phase 7 — Slack chat bot daemon.

Long-running process that turns Bruno's BrunOS Slack workspace into a chat
surface for his second brain. Connects to Slack via Socket Mode, listens for
DMs (`message.im`), routes each thread to a stateful ClaudeSDKClient session.

The Slack carve-out from SOUL.md authorizes autonomous send in DMs (and only
in DMs). All other surfaces remain draft-only.

Run:
  uv run python .claude/chat/bot.py            # foreground daemon
  uv run python .claude/chat/bot.py --smoke-test  # connect + auth.test, exit 0
"""

from __future__ import annotations

import os

os.environ.setdefault("CLAUDE_INVOKED_BY", "chat")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / ".claude"))

from shared import STATE_DIR, load_env, load_state, save_state  # noqa: E402

from chat.adapters.slack_adapter import register  # noqa: E402
from chat.session_manager import SessionManager  # noqa: E402
from chat.system_prompt import build_chat_system_prompt  # noqa: E402

SLACK_STATE_PATH = STATE_DIR / "slack-state.json"
CHAT_DB_PATH = STATE_DIR / "chat.db"

DEFAULT_CHAT_MODEL = "claude-sonnet-4-6"
MAX_TURNS = 15


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _persist_bot_user_id(bot_user_id: str) -> None:
    """Merge bot_user_id into slack-state.json without clobbering channels map."""
    state = load_state(SLACK_STATE_PATH, default=None)
    if not isinstance(state, dict):
        state = {"_schema_version": 1, "channels": {}, "bot_user_id": None}
    state.setdefault("_schema_version", 1)
    state.setdefault("channels", {})
    state["bot_user_id"] = bot_user_id
    save_state(SLACK_STATE_PATH, state)


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _build_options_factory(system_prompt_builder):
    """Return a factory that yields a fresh ClaudeAgentOptions per session.

    The system prompt (which carries the vault context) is REBUILT per session
    via `system_prompt_builder()` — the SessionStart hook is skipped for chat
    (CLAUDE_INVOKED_BY=chat), so this is the sole, always-fresh source of vault
    context. Rebuilding is ~6 cheap vault reads (hook module is cached), so a
    week-long daemon never serves stale MEMORY/daily-log context.

    Accepts an optional `resume` SDK session_id so a reaped/restarted thread
    continues its prior conversation instead of starting fresh. `fork_session`
    stays False so resume keeps the same session_id (and transcript file).
    """
    from claude_agent_sdk import ClaudeAgentOptions

    def _factory(resume: str | None = None) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            allowed_tools=["Read", "Write", "Edit", "Bash"],
            setting_sources=["project"],
            system_prompt=system_prompt_builder(),
            model=os.environ.get("CHAT_MODEL", DEFAULT_CHAT_MODEL),
            max_turns=MAX_TURNS,
            resume=resume,
            fork_session=False,
        )

    return _factory


async def _smoke_test(bot_token: str) -> int:
    from slack_bolt.async_app import AsyncApp

    app = AsyncApp(token=bot_token)
    try:
        auth = await app.client.auth_test()
    except Exception as e:
        _log(f"smoke fail: auth.test → {type(e).__name__}: {e}")
        return 1
    if not auth.get("ok"):
        _log(f"smoke fail: auth.test ok=false ({auth})")
        return 1
    _log(
        f"smoke ok: bot_user_id={auth.get('user_id')} "
        f"team={auth.get('team')} url={auth.get('url')}"
    )
    _persist_bot_user_id(auth["user_id"])
    return 0


async def main_async(smoke_test: bool) -> int:
    load_env()
    chat_profile = os.environ.get("CHAT_BRAIN_PROFILE", "brunos").strip().lower()
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    app_token = os.environ.get("SLACK_APP_TOKEN", "").strip()
    if not bot_token:
        _log("SLACK_BOT_TOKEN not set in .claude/.env — aborting")
        return 1
    if not smoke_test and not app_token:
        _log("SLACK_APP_TOKEN not set in .claude/.env — aborting")
        return 1

    if smoke_test:
        return await _smoke_test(bot_token)

    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_bolt.async_app import AsyncApp

    app = AsyncApp(token=bot_token)
    auth = await app.client.auth_test()
    bot_user_id = auth["user_id"]
    _persist_bot_user_id(bot_user_id)
    _log(f"[chat] bot started: bot_user_id={bot_user_id} team={auth.get('team')}")

    # Build once at startup to validate + log size; the factory rebuilds it
    # fresh per session so a long-running daemon never serves stale context.
    _log(
        f"[chat] system prompt builds at {len(build_chat_system_prompt())} chars "
        f"(rebuilt fresh per session)"
    )
    flush_enabled = _env_flag("CHAT_FLUSH_ENABLED", default=True)
    _log(f"[chat] transcript flush enabled={flush_enabled}")
    registry_enabled = _env_flag(
        "CHAT_CHANNEL_REGISTRY_ENABLED",
        default=(chat_profile == "linos"),
    )
    _log(f"[chat] channel registry enabled={registry_enabled}")

    session_manager = SessionManager(
        options_factory=_build_options_factory(build_chat_system_prompt),
        db_path=CHAT_DB_PATH,
        flush_enabled=flush_enabled,
    )
    register(
        app,
        bot_user_id,
        session_manager,
        enforce_channel_registry=registry_enabled,
    )

    handler = AsyncSocketModeHandler(app, app_token=app_token)

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _request_shutdown() -> None:
        if not shutdown_event.is_set():
            _log("[chat] shutdown signal received")
            shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown)

    await handler.connect_async()
    _log("[chat] socket mode connected; waiting for DMs")
    reaper_task = asyncio.create_task(session_manager.run_reaper(shutdown_event))
    try:
        await shutdown_event.wait()
    finally:
        _log("[chat] shutting down")
        reaper_task.cancel()
        try:
            await reaper_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await session_manager.close_all()
        except Exception as e:
            _log(f"[chat] close_all failed: {type(e).__name__}: {e}")
        try:
            await handler.close_async()
        except Exception as e:
            _log(f"[chat] handler close failed: {type(e).__name__}: {e}")
        _log("[chat] bot stopped")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="BrunOS Slack chat bot (Phase 7)")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="connect + auth.test, print bot identity, exit 0",
    )
    args = parser.parse_args(argv[1:])
    return asyncio.run(main_async(smoke_test=args.smoke_test))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
