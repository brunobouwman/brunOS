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

CHAT_MODEL = "claude-sonnet-4-6"
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


def _build_options_factory(system_prompt: str):
    """Return a factory that yields a fresh ClaudeAgentOptions per session.

    The system prompt is built ONCE at startup (vault file reads are expensive)
    and reused for every per-thread session — daemon restart refreshes it.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    def _factory() -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            allowed_tools=["Read", "Write", "Edit", "Bash"],
            setting_sources=["project"],
            system_prompt=system_prompt,
            model=CHAT_MODEL,
            max_turns=MAX_TURNS,
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

    system_prompt = build_chat_system_prompt()
    _log(f"[chat] system prompt built: {len(system_prompt)} chars")

    session_manager = SessionManager(
        options_factory=_build_options_factory(system_prompt),
        db_path=CHAT_DB_PATH,
    )
    register(app, bot_user_id, session_manager)

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
    try:
        await shutdown_event.wait()
    finally:
        _log("[chat] shutting down")
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
