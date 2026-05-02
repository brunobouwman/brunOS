"""Slack integration: read + send + state.

Phase 4 priority #1. Polling reader for "what changed since last run" plus the
Phase-4 send surface (autonomous on @mention per the SOUL.md carve-out).

State:  .claude/data/state/slack-state.json
        {"_schema_version": 1, "channels": {<id>: <last_ts>}, "bot_user_id": "..."}

NOT included in this module:
  - Socket Mode listener (Phase 7).
  - Heartbeat-driven send (Phase 6 wires send_message into reasoning).
  - chat:write.public / chat.scheduleMessage / files.upload — out of scope.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import (  # noqa: E402
    STATE_DIR,
    load_state,
    now_brt,
    save_state,
    with_retry,
)

NAME = "slack"
STATE_PATH = STATE_DIR / f"{NAME}-state.json"
DEFAULT_STATE: dict = {"_schema_version": 1, "channels": {}, "bot_user_id": None}
COLD_START_LOOKBACK_H = 1


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    is_im: bool
    is_member: bool


@dataclass(frozen=True)
class Message:
    channel_id: str
    ts: str
    user_id: str | None
    text: str
    thread_ts: str | None
    permalink: str | None


@dataclass(frozen=True)
class Mention:
    channel_id: str
    ts: str
    user_id: str | None
    text: str
    thread_ts: str | None


_CLIENT = None
_USER_NAMES: dict[str, str] = {}


def _client():
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN not set in environment (.claude/.env)")
    from slack_sdk import WebClient
    from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

    client = WebClient(token=token)
    client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=3))
    _CLIENT = client
    return _CLIENT


def _load() -> dict:
    state = load_state(STATE_PATH, default=None)
    if not isinstance(state, dict):
        return dict(DEFAULT_STATE)
    state.setdefault("_schema_version", 1)
    state.setdefault("channels", {})
    state.setdefault("bot_user_id", None)
    return state


def _save(state: dict) -> None:
    save_state(STATE_PATH, state)


def _bot_user_id(client, state: dict) -> str:
    if state.get("bot_user_id"):
        return state["bot_user_id"]
    resp = with_retry(lambda: client.auth_test())
    uid = resp["user_id"]
    state["bot_user_id"] = uid
    _save(state)
    return uid


def _user_name(client, user_id: str | None) -> str:
    if not user_id:
        return "unknown"
    if user_id in _USER_NAMES:
        return _USER_NAMES[user_id]
    try:
        resp = with_retry(lambda: client.users_info(user=user_id))
        u = resp["user"]
        display = u.get("profile", {}).get("display_name") or u.get("real_name") or user_id
    except Exception:
        display = user_id
    _USER_NAMES[user_id] = display
    return display


def list_channels(client) -> list[Channel]:
    out: list[Channel] = []
    cursor: str | None = None
    while True:
        kwargs = {
            "types": "public_channel,private_channel,im,mpim",
            "limit": 200,
        }
        if cursor:
            kwargs["cursor"] = cursor
        resp = with_retry(lambda: client.users_conversations(**kwargs))
        for ch in resp.get("channels", []):
            out.append(
                Channel(
                    id=ch["id"],
                    name=ch.get("name") or f"im:{ch.get('user', ch['id'])}",
                    is_im=bool(ch.get("is_im")),
                    is_member=bool(ch.get("is_member", True)),
                )
            )
        cursor = resp.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            break
    return out


def _filter_msg(m: dict, bot_user_id: str) -> bool:
    """True if this message should be kept (not from bot, not subtype noise)."""
    if m.get("subtype") and m.get("subtype") not in (None, "thread_broadcast"):
        return False
    if m.get("bot_id"):
        return False
    if m.get("user") == bot_user_id:
        return False
    return True


def since_last_run(client) -> list[Message]:
    state = _load()
    bot_uid = _bot_user_id(client, state)
    channels = list_channels(client)
    cold_start_ts = (now_brt() - timedelta(hours=COLD_START_LOOKBACK_H)).timestamp()
    aggregated: list[Message] = []

    for ch in channels:
        if not ch.is_member:
            continue
        last_ts = state["channels"].get(ch.id)
        oldest = str(last_ts) if last_ts else f"{cold_start_ts:.6f}"
        cursor: str | None = None
        max_seen = float(oldest)
        while True:
            kwargs = {"channel": ch.id, "oldest": oldest, "limit": 200, "inclusive": False}
            if cursor:
                kwargs["cursor"] = cursor
            try:
                resp = with_retry(lambda: client.conversations_history(**kwargs))
            except Exception as e:
                print(f"[slack] history failed for {ch.id} ({ch.name}): {e}", file=sys.stderr)
                break
            for m in resp.get("messages", []):
                ts = m.get("ts", "0")
                try:
                    ts_f = float(ts)
                    if ts_f > max_seen:
                        max_seen = ts_f
                except ValueError:
                    pass
                if not _filter_msg(m, bot_uid):
                    continue
                aggregated.append(
                    Message(
                        channel_id=ch.id,
                        ts=ts,
                        user_id=m.get("user"),
                        text=m.get("text", ""),
                        thread_ts=m.get("thread_ts"),
                        permalink=None,
                    )
                )
            cursor = resp.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                break
        # Always advance state so quiet channels don't re-scan from cold-start
        # every poll. Use max of (highest-seen ts, current oldest).
        state["channels"][ch.id] = f"{max(max_seen, float(oldest)):.6f}"

    _save(state)
    return aggregated


def get_thread(client, channel: str, ts: str) -> list[Message]:
    out: list[Message] = []
    cursor: str | None = None
    while True:
        kwargs = {"channel": channel, "ts": ts, "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor
        resp = with_retry(lambda: client.conversations_replies(**kwargs))
        for m in resp.get("messages", []):
            out.append(
                Message(
                    channel_id=channel,
                    ts=m.get("ts", ""),
                    user_id=m.get("user"),
                    text=m.get("text", ""),
                    thread_ts=m.get("thread_ts"),
                    permalink=None,
                )
            )
        cursor = resp.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            break
    return out


def mentions_since_last_run(client) -> list[Mention]:
    """Mentions are derived from since_last_run by substring-matching <@bot_user_id>.

    Proper Slack `app_mention` events arrive via Socket Mode (Phase 7).
    """
    state = _load()
    bot_uid = _bot_user_id(client, state)
    needle = f"<@{bot_uid}>"
    msgs = since_last_run(client)
    return [
        Mention(
            channel_id=m.channel_id,
            ts=m.ts,
            user_id=m.user_id,
            text=m.text,
            thread_ts=m.thread_ts,
        )
        for m in msgs
        if needle in m.text
    ]


def dms_since_last_run(client) -> list[Message]:
    msgs = since_last_run(client)
    state = _load()
    im_ids = {ch.id for ch in list_channels(client) if ch.is_im}
    return [m for m in msgs if m.channel_id in im_ids]


def send_message(client, channel: str, text: str, thread_ts: str | None = None) -> dict:
    """Post a message. Returns the raw API response dict.

    Note: this is the autonomous send surface gated by SOUL.md carve-out
    ("Slack on @mention only"). Phase 6's heartbeat decides WHEN to call this;
    Phase 4 just exposes it.
    """
    kwargs = {"channel": channel, "text": text}
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    resp = with_retry(lambda: client.chat_postMessage(**kwargs))
    return resp.data if hasattr(resp, "data") else dict(resp)


def reply_in_thread(client, channel: str, parent_ts: str, text: str) -> dict:
    return send_message(client, channel=channel, text=text, thread_ts=parent_ts)


def format_for_context(messages: list[Message] | list[Mention], client=None) -> str:
    if not messages:
        return "_No new Slack messages._\n"
    grouped: dict[str, list] = {}
    for m in messages:
        grouped.setdefault(m.channel_id, []).append(m)
    lines: list[str] = ["### Slack", ""]
    for ch_id, items in grouped.items():
        lines.append(f"**#{ch_id}** ({len(items)} messages)")
        for m in items:
            who = _user_name(client, m.user_id) if client else (m.user_id or "?")
            text = m.text.replace("\n", " ")
            if len(text) > 200:
                text = text[:200] + "…"
            lines.append(f"- @{who}: {text}")
        lines.append("")
    return "\n".join(lines)


# --- CLI ---


def add_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(NAME, help="Slack integration")
    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("channels", help="List channels the bot is in")
    sp.add_parser("since", help="Messages since last run (updates state)")
    sp.add_parser("mentions", help="Mentions of the bot since last run")
    sp.add_parser("dms", help="DMs since last run")

    pt = sp.add_parser("thread", help="Fetch a thread")
    pt.add_argument("channel")
    pt.add_argument("ts")

    ps = sp.add_parser("send", help="Send a message to a channel")
    ps.add_argument("channel")
    ps.add_argument("text")

    pr = sp.add_parser("reply", help="Reply in a thread")
    pr.add_argument("channel")
    pr.add_argument("parent_ts")
    pr.add_argument("text")

    p.set_defaults(_handler=cli)


def cli(args: argparse.Namespace) -> int:
    client = _client()
    cmd = args.cmd
    if cmd == "channels":
        chans = list_channels(client)
        for c in chans:
            kind = "im" if c.is_im else "ch"
            member = "✓" if c.is_member else " "
            print(f"  [{member}] {kind} {c.id}  {c.name}")
        return 0
    if cmd == "since":
        msgs = since_last_run(client)
        print(format_for_context(msgs, client=client))
        return 0
    if cmd == "mentions":
        ms = mentions_since_last_run(client)
        print(format_for_context(ms, client=client))
        return 0
    if cmd == "dms":
        msgs = dms_since_last_run(client)
        print(format_for_context(msgs, client=client))
        return 0
    if cmd == "thread":
        msgs = get_thread(client, args.channel, args.ts)
        print(format_for_context(msgs, client=client))
        return 0
    if cmd == "send":
        resp = send_message(client, args.channel, args.text)
        print(json.dumps({"ok": resp.get("ok"), "ts": resp.get("ts"), "channel": resp.get("channel")}))
        return 0 if resp.get("ok") else 1
    if cmd == "reply":
        resp = reply_in_thread(client, args.channel, args.parent_ts, args.text)
        print(json.dumps({"ok": resp.get("ok"), "ts": resp.get("ts"), "channel": resp.get("channel")}))
        return 0 if resp.get("ok") else 1
    return 2
