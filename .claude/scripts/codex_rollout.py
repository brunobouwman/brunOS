"""Codex (OpenAI CLI / Desktop) session rollout parser + slug derivation.

Codex writes each session as a JSONL rollout at
~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl.

The file starts with one `session_meta` line (id, cwd, model_provider,
originator, git.repository_url, ...), followed by a stream of `event_msg`
and `response_item` lines per turn. We only care about the conversational
stream:

- event_msg.user_message      — user input
- event_msg.agent_message     — assistant text reply

Everything else (token_count, reasoning blobs, function_call/patch noise,
base_instructions, turn_context) is skipped — the distillation step asks
Sonnet for decisions/lessons/blockers, and tool-call mechanics rarely
carry that signal.

Slug derivation: Codex auto-managed worktrees live at
~/.codex/worktrees/<hash>/<repo>. Naïve cwd-based slugging would bucket
captures from those sessions under `<hash>-<repo>` — different folder for
every worktree, even though they're the same project. `derive_project_slug`
normalizes that: for canonical-path sessions it learns
{repo_url → canonical_slug}; for worktree sessions it looks up the cache
to roll up to the canonical slug. Cache lives at
.claude/data/state/codex_repo_slugs.json and is hand-editable.

Std-lib only. Used by memory_flush.py, codex_watcher.py, codex_backfill.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

# shared.py lives next to us; safe to import without modifying sys.path here
# because every caller already inserts .claude/scripts at sys.path[0].
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import (  # noqa: E402
    STATE_DIR,
    _slug,
    canonicalize_slug,
    derive_project_slug_from_path,
    load_state,
    save_state,
)


REPO_SLUG_CACHE_PATH = STATE_DIR / "codex_repo_slugs.json"
_CODEX_WORKTREES_MARKER = "/.codex/worktrees/"


def read_session_meta(path: Path) -> Optional[dict]:
    """Read just the first JSONL line and return its `session_meta.payload`.

    Returns None if the file is unreadable, empty, malformed, or its first
    line is not a session_meta event. Cheap: stops at one line. Used by the
    watcher to derive project slug from cwd before deciding to flush.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            first = f.readline()
    except OSError:
        return None
    if not first.strip():
        return None
    try:
        obj = json.loads(first)
    except json.JSONDecodeError:
        return None
    if obj.get("type") != "session_meta":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload


def parse_rollout(path: Path) -> Optional[tuple[dict, str]]:
    """Parse a Codex rollout JSONL into (meta, transcript_text).

    meta: dict with keys {session_id, cwd, originator, model_provider,
        cli_version, started_at} — best-effort, missing keys omitted.
    transcript_text: chronological "USER:\\n...\\n\\nASSISTANT:\\n..."
        plaintext suitable for the Sonnet distiller. Empty string if no
        conversational events were found (still returns a tuple so the
        caller can decide).

    Returns None only on hard read failures (missing file, no session_meta).
    """
    meta_payload = read_session_meta(path)
    if meta_payload is None:
        return None

    meta: dict = {}
    if (sid := meta_payload.get("id")):
        meta["session_id"] = sid
    if (cwd := meta_payload.get("cwd")):
        meta["cwd"] = cwd
    if (orig := meta_payload.get("originator")):
        meta["originator"] = orig
    if (mp := meta_payload.get("model_provider")):
        meta["model_provider"] = mp
    if (cv := meta_payload.get("cli_version")):
        meta["cli_version"] = cv
    if (ts := meta_payload.get("timestamp")):
        meta["started_at"] = ts

    chunks: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            # First line is session_meta; we already consumed it logically,
            # but f starts at byte 0 — just skip it again here.
            next(f, None)
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "event_msg":
                    continue
                payload = obj.get("payload") or {}
                p_type = payload.get("type")
                if p_type == "user_message":
                    msg = (payload.get("message") or "").strip()
                    if msg:
                        chunks.append(f"USER:\n{msg}\n")
                elif p_type == "agent_message":
                    msg = (payload.get("message") or "").strip()
                    if msg:
                        chunks.append(f"ASSISTANT:\n{msg}\n")
    except OSError:
        return meta, ""

    return meta, "\n".join(chunks)


def _url_to_basename_slug(repo_url: str) -> Optional[str]:
    """Pick a slug from a git remote URL when no canonical mapping exists.

    https://github.com/owner/repo.git → repo
    git@github.com:owner/repo.git     → repo
    """
    if not repo_url:
        return None
    last = repo_url.rstrip("/").rsplit("/", 1)[-1]
    last = last.rsplit(":", 1)[-1]  # handles ssh-style git@host:owner/repo.git
    if last.endswith(".git"):
        last = last[:-4]
    return _slug(last) if last else None


def _load_cache() -> dict:
    return load_state(REPO_SLUG_CACHE_PATH, default={}) or {}


def _save_cache(cache: dict) -> None:
    save_state(REPO_SLUG_CACHE_PATH, cache)


def derive_project_slug(meta: dict) -> Optional[str]:
    """Codex-aware slug derivation with worktree rollup.

    `meta` is a session_meta payload (as returned by `read_session_meta` or
    `parse_rollout`). Strategy:

    1. Codex worktree path (cwd contains "/.codex/worktrees/"):
         - Look up meta.git.repository_url in the slug cache.
         - On hit: return the canonical slug (rolls up to the same inbox
           folder as canonical-path sessions).
         - On miss: fall back to URL basename slug (`lab-agent`) — better
           than the hash-prefixed `<hash>-lab-agent`. The first canonical
           session afterwards will populate the cache.

    2. Canonical path (anywhere else):
         - Derive slug from cwd via `derive_project_slug_from_path` (same
           rules Claude Code uses).
         - If we have a repo URL and the derived slug differs from any
           cached value, UPDATE the cache so future worktree sessions
           inherit this slug.

    Returns None when the slug resolves to BrunOS itself (route to daily
    log) or when no signal is available. Side-effect: writes to the slug
    cache on canonical-path sessions with a git remote.
    """
    cwd = meta.get("cwd") or ""
    git = meta.get("git") or {}
    repo_url = git.get("repository_url")

    is_worktree = _CODEX_WORKTREES_MARKER in cwd

    if is_worktree:
        if repo_url:
            cache = _load_cache()
            cached = cache.get(repo_url)
            if cached:
                return canonicalize_slug(cached)
            return canonicalize_slug(_url_to_basename_slug(repo_url))
        # Worktree but no repo URL — last-resort: use cwd directly (yields
        # `<hash>-<repo>`). Still better than crashing.
        return derive_project_slug_from_path(cwd) if cwd else None

    # Canonical path. Derive from cwd as before.
    slug = derive_project_slug_from_path(cwd) if cwd else None
    if slug and repo_url:
        cache = _load_cache()
        if cache.get(repo_url) != slug:
            cache[repo_url] = slug
            _save_cache(cache)
    return slug
