"""Slack Bolt event registration: filter DMs + channel @mentions, route to SDK.

Two surfaces wired into the same SessionManager:
  - `message` events with `channel_type=im`  → DMs (auto-reply, no mention needed).
  - `app_mention` events                       → channel @mentions of the bot.

Session keying is `f"{channel_id}:{thread_ts}"` so DMs and channel threads can
run in parallel without colliding, and so two different channels can each have
their own ongoing conversation.

Channel UX: every continuation requires another @mention. Slack does NOT
deliver `app_mention` for follow-up replies in the same thread, and we don't
subscribe to `message.channels` (that's a fire hose). Bruno @mentions to
continue.

Self-echo filter mirrors integrations.slack._filter_msg — keep them in lockstep.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from chat.channel_registry import render_context, resolve_slack_event  # noqa: E402
from chat.session_manager import SessionManager  # noqa: E402
from sanitize import wrap_external  # noqa: E402


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


def _common_filter(event: dict, bot_user_id: str) -> bool:
    """Shared self-echo / subtype / empty-text filter."""
    if event.get("bot_id"):
        return False
    if event.get("subtype"):
        return False
    if event.get("user") == bot_user_id:
        return False
    if not (event.get("text") or "").strip():
        return False
    return True


def _should_handle_dm(event: dict, bot_user_id: str) -> bool:
    """True if this is a fresh, user-authored DM."""
    if event.get("channel_type") != "im":
        return False
    return _common_filter(event, bot_user_id)


def _should_handle_mention(event: dict, bot_user_id: str) -> bool:
    """True if this is a fresh, user-authored channel @mention.

    Channel type isn't filtered — `app_mention` already implies a channel
    surface. We rely on the common filter for self-echo / subtype / empty text.
    """
    return _common_filter(event, bot_user_id)


# Backwards-compat alias for any external caller / test still using the old name.
_should_handle = _should_handle_dm


def _derive_session_key(event: dict) -> str:
    """Stable session key: `<channel_id>:<thread_root_ts>`.

    Same DM + same thread → same session. Same channel + same thread root ts
    → same session. Different channels can never collide.
    """
    channel = event.get("channel", "")
    thread_or_ts = event.get("thread_ts") or event["ts"]
    return f"{channel}:{thread_or_ts}"


def _derive_slack_thread_ts(event: dict) -> str:
    """Slack thread_ts to pass to say() — keeps replies in the same thread."""
    return event.get("thread_ts") or event["ts"]


# Backwards-compat alias for any external caller / test still using the old name.
_derive_thread_key = _derive_session_key


def _strip_bot_mention(text: str, bot_user_id: str) -> str:
    """Drop the `<@U…>` self-mention from a channel @mention before sending to SDK."""
    return re.sub(rf"<@{re.escape(bot_user_id)}>\s*", "", text).strip()


def register(
    app,
    bot_user_id: str,
    session_manager: SessionManager,
    *,
    enforce_channel_registry: bool = False,
) -> None:
    """Wire DM and @mention events to the SDK round-trip."""

    async def _route(event: dict, say, user_text: str, *, surface: str) -> None:
        session_key = _derive_session_key(event)
        slack_thread_ts = _derive_slack_thread_ts(event)
        if enforce_channel_registry:
            decision = resolve_slack_event(event)
            if not decision.allowed:
                _log(
                    f"[chat] registry refused {surface} for {session_key}: "
                    f"{decision.reason}"
                )
                await say(text=decision.refusal_text, thread_ts=slack_thread_ts)
                return
            user_text = render_context(decision) + user_text
        # Lock is owned by the SessionManager so the idle reaper never closes a
        # client mid-request.
        async with session_manager.lock_for(session_key):
            try:
                client = await session_manager.get_or_create(session_key)
                await client.query(user_text)
                parts: list[str] = []
                session_id: str | None = None
                async for sdk_msg in client.receive_response():
                    session_id = getattr(sdk_msg, "session_id", None) or session_id
                    text = _extract_text(sdk_msg)
                    if text:
                        parts.append(text)
                session_manager.note_session_id(session_key, session_id)
                reply = "".join(parts).strip() or "(no response)"
                await say(text=reply, thread_ts=slack_thread_ts)
                session_manager.touch(session_key)
            except Exception as e:
                _log(
                    f"[chat] {surface} handler failed for {session_key}: "
                    f"{type(e).__name__}: {e}"
                )
                try:
                    await say(
                        text=f"_I hit an error: `{type(e).__name__}`. Check daemon stderr._",
                        thread_ts=slack_thread_ts,
                    )
                except Exception as e2:
                    _log(f"[chat] error-reply also failed: {type(e2).__name__}: {e2}")

    @app.event("message")
    async def _on_message(event, say, logger) -> None:  # noqa: ARG001
        if not _should_handle_dm(event, bot_user_id):
            return
        wrapped = wrap_external(
            event["text"],
            "slack",
            channel=event.get("channel", ""),
            user=event.get("user", ""),
            surface="dm",
        )
        await _route(event, say, wrapped, surface="dm")

    @app.event("app_mention")
    async def _on_app_mention(event, say, logger) -> None:  # noqa: ARG001
        if not _should_handle_mention(event, bot_user_id):
            return
        user_text = _strip_bot_mention(event["text"], bot_user_id)
        if not user_text:
            slack_thread_ts = _derive_slack_thread_ts(event)
            await say(
                text="_Yes? Mention me with a question or instruction._",
                thread_ts=slack_thread_ts,
            )
            return
        wrapped = wrap_external(
            user_text,
            "slack",
            channel=event.get("channel", ""),
            user=event.get("user", ""),
            surface="mention",
        )
        await _route(event, say, wrapped, surface="mention")
