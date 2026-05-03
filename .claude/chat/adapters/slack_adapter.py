"""Slack Bolt event registration: filter DMs, route to per-thread SDK session.

Encapsulates Slack-specific glue (event filter, thread-key derivation, Bolt
say()). A future Discord/Teams adapter can drop in alongside without touching
the bot.py daemon.

Self-echo filter mirrors integrations.slack._filter_msg — keep them in lockstep.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from chat.session_manager import SessionManager  # noqa: E402


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _extract_text(msg) -> str:
    """Pull assistant text out of an SDK message (TextBlock content or .text).

    Mirrors digest.py:_extract_text — same shape used in every Phase 5 script.
    """
    direct = getattr(msg, "text", None)
    if isinstance(direct, str) and direct:
        return direct
    chunks: list[str] = []
    content = getattr(msg, "content", None)
    if content is None:
        return ""
    try:
        iterator = iter(content)
    except TypeError:
        return ""
    for block in iterator:
        t = getattr(block, "text", None)
        if isinstance(t, str) and t:
            chunks.append(t)
    return "\n".join(chunks)


def _should_handle(event: dict, bot_user_id: str) -> bool:
    """True if this event is a fresh, user-authored DM we should route."""
    if event.get("channel_type") != "im":
        return False
    if event.get("bot_id"):
        return False
    if event.get("subtype"):
        return False
    if event.get("user") == bot_user_id:
        return False
    if not (event.get("text") or "").strip():
        return False
    return True


def _derive_thread_key(event: dict) -> str:
    """The Slack thread root ts — `thread_ts` if threaded, else the message `ts`."""
    return event.get("thread_ts") or event["ts"]


def register(app, bot_user_id: str, session_manager: SessionManager) -> None:
    """Wire @app.event('message') to the SDK round-trip."""
    thread_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(thread_key: str) -> asyncio.Lock:
        lock = thread_locks.get(thread_key)
        if lock is None:
            lock = asyncio.Lock()
            thread_locks[thread_key] = lock
        return lock

    @app.event("message")
    async def _on_message(event, say, logger) -> None:  # noqa: ARG001
        if not _should_handle(event, bot_user_id):
            return

        thread_key = _derive_thread_key(event)
        # TODO(Phase 8): wrap user-facing text in <external_data> via sanitize.py.
        # DM content is from Bruno but he may paste quoted external text.
        user_text = event["text"]

        async with _lock_for(thread_key):
            try:
                client = await session_manager.get_or_create(thread_key)
                await client.query(user_text)
                parts: list[str] = []
                async for sdk_msg in client.receive_response():
                    text = _extract_text(sdk_msg)
                    if text:
                        parts.append(text)
                reply = "".join(parts).strip()
                if not reply:
                    reply = "(no response)"
                await say(text=reply, thread_ts=thread_key)
            except Exception as e:
                _log(f"[chat] handler failed for {thread_key}: {type(e).__name__}: {e}")
                try:
                    await say(
                        text=f"_I hit an error: `{type(e).__name__}`. Check daemon stderr._",
                        thread_ts=thread_key,
                    )
                except Exception as e2:
                    _log(f"[chat] error-reply also failed: {type(e2).__name__}: {e2}")
