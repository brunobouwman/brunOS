"""Per-thread ClaudeSDKClient cache + SQLite thread index.

One stateful SDK session per Slack thread root `ts`. The in-memory dict is the
hot path; SQLite indexes known threads AND persists each thread's SDK
`session_id` so a reaped or restarted thread can RESUME instead of starting
fresh.

Memory discipline (the box is shared with Lisa, no swap):
  - Idle reap: a thread with no activity for `idle_reap_seconds` (default 60
    min) has its SDK subprocess flushed-then-closed. Re-engaging the thread
    transparently resumes from the on-disk transcript.
  - LRU cap: at most `max_live_sessions` live clients at once; the
    least-recently-active is evicted when a new one pushes over the cap.

Knowledge capture: when enabled, before a client is closed (idle reap, LRU
evict, or shutdown) the thread transcript is handed to `memory_flush.py` via
`dispatch_flush(source="chat-session")` — same pipeline the SessionEnd/PreCompact
hooks use. memory_flush's 2KB-floor + FLUSH_OK gate keep trivial chatter out of
the daily log; durable bullets get promoted to MEMORY.md by reflection.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import dispatch_flush, _ts_brt  # noqa: E402

IDLE_REAP_SECONDS = 60 * 60  # reap a thread idle for 60 min
MAX_LIVE_SESSIONS = 4  # hard cap on concurrent live SDK subprocesses
REAP_INTERVAL_SECONDS = 5 * 60  # how often the background loop scans for idle threads

# Claude Code / Agent SDK write each session's transcript here as <session_id>.jsonl.
_TRANSCRIPTS_ROOT = Path.home() / ".claude" / "projects"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class SessionManager:
    """Owns the thread→ClaudeSDKClient map, its SQLite index, and reaping."""

    def __init__(
        self,
        options_factory: Callable[[str | None], "ClaudeAgentOptions"],  # noqa: F821
        db_path: Path,
        *,
        idle_reap_seconds: int = IDLE_REAP_SECONDS,
        max_live_sessions: int = MAX_LIVE_SESSIONS,
        reap_interval_seconds: int = REAP_INTERVAL_SECONDS,
        flush_enabled: bool = True,
    ) -> None:
        self._options_factory = options_factory
        self._db_path = db_path
        self._idle_reap_seconds = idle_reap_seconds
        self._max_live_sessions = max_live_sessions
        self._reap_interval_seconds = reap_interval_seconds
        self._flush_enabled = flush_enabled
        self._clients: dict[str, "ClaudeSDKClient"] = {}  # noqa: F821
        self._last_active: dict[str, float] = {}  # thread_key → monotonic ts
        self._session_ids: dict[str, str] = {}  # thread_key → SDK session_id
        self._locks: dict[str, asyncio.Lock] = {}
        self._init_db()

    # ---- locks (owned here so the reaper and the request path coordinate) ----

    def lock_for(self, thread_key: str) -> asyncio.Lock:
        lock = self._locks.get(thread_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[thread_key] = lock
        return lock

    # ---- SQLite index -------------------------------------------------------

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
            # Added in the resume/reap work — guarded so existing DBs migrate.
            try:
                conn.execute("ALTER TABLE chat_threads ADD COLUMN sdk_session_id TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists
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

    def _lookup_session_id(self, thread_key: str) -> str | None:
        """Resume id for a thread — memory first, then the SQLite index."""
        cached = self._session_ids.get(thread_key)
        if cached:
            return cached
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT sdk_session_id FROM chat_threads WHERE thread_key = ?",
                (thread_key,),
            ).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            self._session_ids[thread_key] = row[0]
            return row[0]
        return None

    def note_session_id(self, thread_key: str, session_id: str | None) -> None:
        """Persist the SDK session_id the adapter captured from a response."""
        if not session_id or self._session_ids.get(thread_key) == session_id:
            return
        self._session_ids[thread_key] = session_id
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "UPDATE chat_threads SET sdk_session_id = ? WHERE thread_key = ?",
                (session_id, thread_key),
            )
            conn.commit()
        finally:
            conn.close()

    # ---- activity tracking --------------------------------------------------

    def touch(self, thread_key: str) -> None:
        self._last_active[thread_key] = time.monotonic()

    # ---- session lifecycle --------------------------------------------------

    async def get_or_create(self, thread_key: str) -> "ClaudeSDKClient":  # noqa: F821
        from claude_agent_sdk import ClaudeSDKClient

        client = self._clients.get(thread_key)
        if client is not None:
            self.touch(thread_key)
            self._record_thread(thread_key, created=False)
            return client

        resume_id = self._lookup_session_id(thread_key)
        is_new_thread = resume_id is None

        try:
            client = await self._connect(resume_id)
            if resume_id:
                _log(f"[chat] resumed SDK session {resume_id} for thread {thread_key}")
            else:
                _log(f"[chat] new SDK session for thread {thread_key}")
        except Exception as e:
            # Transcript pruned (30-day default) or otherwise unresumable —
            # fall back to a fresh session rather than failing the message.
            if resume_id:
                _log(
                    f"[chat] resume failed for {thread_key} "
                    f"({type(e).__name__}: {e}); starting fresh"
                )
                client = await self._connect(None)
            else:
                raise

        self._clients[thread_key] = client
        self.touch(thread_key)
        self._record_thread(thread_key, created=is_new_thread)
        await self._enforce_cap(protect=thread_key)
        return client

    async def _connect(self, resume_id: str | None) -> "ClaudeSDKClient":  # noqa: F821
        from claude_agent_sdk import ClaudeSDKClient

        options = self._options_factory(resume_id)
        client = ClaudeSDKClient(options=options)
        await client.connect()
        return client

    async def _enforce_cap(self, *, protect: str) -> None:
        """Evict least-recently-active live clients beyond the cap."""
        while len(self._clients) > self._max_live_sessions:
            candidates = [k for k in self._clients if k != protect]
            if not candidates:
                break
            victim = min(candidates, key=lambda k: self._last_active.get(k, 0.0))
            await self._reap(victim, reason="lru")

    async def reap_idle(self) -> None:
        now = time.monotonic()
        stale = [
            key
            for key in list(self._clients)
            if now - self._last_active.get(key, now) >= self._idle_reap_seconds
        ]
        for key in stale:
            await self._reap(key, reason="idle")

    async def _reap(self, thread_key: str, *, reason: str) -> bool:
        """Flush the thread's knowledge, then close its SDK subprocess.

        Skips if the thread is mid-request (lock held) — it'll be caught on the
        next scan. The session_id is retained so the next message resumes.
        """
        lock = self.lock_for(thread_key)
        if lock.locked():
            return False
        async with lock:
            client = self._clients.pop(thread_key, None)
            if client is None:
                return False
            self._last_active.pop(thread_key, None)
            self._flush_thread(thread_key)
            try:
                await client.disconnect()
            except Exception as e:
                _log(f"[chat] disconnect failed for {thread_key}: {type(e).__name__}: {e}")
            _log(f"[chat] reaped session for thread {thread_key} ({reason})")
            return True

    def _flush_thread(self, thread_key: str) -> None:
        """Hand the thread transcript to memory_flush.py (fire-and-forget)."""
        if not self._flush_enabled:
            _log(f"[chat] transcript flush disabled for thread {thread_key}")
            return
        session_id = self._session_ids.get(thread_key)
        if not session_id:
            return
        transcript = next(_TRANSCRIPTS_ROOT.glob(f"*/{session_id}.jsonl"), None)
        if transcript is None:
            return
        try:
            dispatch_flush(
                {
                    "session_id": session_id,
                    "transcript_path": str(transcript),
                    # Resumed threads grow one transcript across many reaps; flush
                    # only the not-yet-distilled tail so the daily log doesn't
                    # accumulate duplicate bullets. (memory_flush watermark)
                    "_incremental": True,
                },
                source="chat-session",
            )
        except Exception as e:
            _log(f"[chat] flush dispatch failed for {thread_key}: {type(e).__name__}: {e}")

    async def run_reaper(self, stop_event: asyncio.Event) -> None:
        """Background loop: scan for idle threads until shutdown."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self._reap_interval_seconds
                )
            except asyncio.TimeoutError:
                pass
            if stop_event.is_set():
                return
            try:
                await self.reap_idle()
            except Exception as e:
                _log(f"[chat] reaper scan failed: {type(e).__name__}: {e}")

    async def close_all(self) -> None:
        keys = list(self._clients.keys())
        _log(f"[chat] closing {len(keys)} session(s)")
        for key in keys:
            client = self._clients.pop(key, None)
            if client is None:
                continue
            self._flush_thread(key)
            try:
                await client.disconnect()
            except Exception as e:
                _log(f"[chat] disconnect failed for {key}: {type(e).__name__}: {e}")
