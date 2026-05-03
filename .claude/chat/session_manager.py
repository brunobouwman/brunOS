"""Per-thread ClaudeSDKClient cache + SQLite thread index.

One stateful SDK session per Slack thread root `ts`. The in-memory dict is the
hot path; SQLite is just an index so a future restart can list known threads.
For MVP, a daemon restart starts each thread fresh — no replay-on-resume.
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import _ts_brt  # noqa: E402


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class SessionManager:
    """Owns the thread→ClaudeSDKClient map and its SQLite index."""

    def __init__(
        self,
        options_factory: Callable[[], "ClaudeAgentOptions"],  # noqa: F821
        db_path: Path,
    ) -> None:
        self._options_factory = options_factory
        self._db_path = db_path
        self._clients: dict[str, "ClaudeSDKClient"] = {}  # noqa: F821
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS chat_threads ("
                "thread_key TEXT PRIMARY KEY, "
                "created_at TEXT NOT NULL, "
                "last_message_at TEXT NOT NULL"
                ")"
            )
            conn.commit()
        finally:
            conn.close()

    def _record_thread(self, thread_key: str, *, created: bool) -> None:
        ts = _ts_brt()
        conn = sqlite3.connect(self._db_path)
        try:
            if created:
                conn.execute(
                    "INSERT OR IGNORE INTO chat_threads "
                    "(thread_key, created_at, last_message_at) VALUES (?, ?, ?)",
                    (thread_key, ts, ts),
                )
            conn.execute(
                "UPDATE chat_threads SET last_message_at = ? WHERE thread_key = ?",
                (ts, thread_key),
            )
            conn.commit()
        finally:
            conn.close()

    async def get_or_create(self, thread_key: str) -> "ClaudeSDKClient":  # noqa: F821
        from claude_agent_sdk import ClaudeSDKClient

        client = self._clients.get(thread_key)
        if client is not None:
            self._record_thread(thread_key, created=False)
            return client

        options = self._options_factory()
        client = ClaudeSDKClient(options=options)
        await client.connect()
        self._clients[thread_key] = client
        self._record_thread(thread_key, created=True)
        _log(f"[chat] new SDK session for thread {thread_key}")
        return client

    async def close_all(self) -> None:
        keys = list(self._clients.keys())
        _log(f"[chat] closing {len(keys)} session(s)")
        for key in keys:
            client = self._clients.pop(key, None)
            if client is None:
                continue
            try:
                await client.disconnect()
            except Exception as e:
                _log(f"[chat] disconnect failed for {key}: {type(e).__name__}: {e}")
