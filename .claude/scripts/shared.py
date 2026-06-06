"""Cross-cutting utilities for BrunOS.

Std-lib only. Imported by hooks running with system python3 (no .venv).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = REPO_ROOT / ".claude" / "data" / "state"
LOCK_DIR = STATE_DIR / "locks"
BRT = ZoneInfo("America/Sao_Paulo")

DANGEROUS_BASH_PATTERNS: list[str] = [
    # Destructive filesystem
    r"\brm\s+(-[rRf]+\s+)*(/|\$HOME|~|\.|\*)\s*$",
    r"\brm\s+-[rRf]+\s+(/|\$HOME|~)",
    r"\bdd\s+if=",
    r"\bmkfs(\.|\s)",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    r">\s*/dev/sd[a-z]",
    r"\bchmod\s+-R\s+777\s+/",
    r"\bfind\s+/\s+.*-delete",
    r"\bshred\b",
    # Privilege escalation
    r"\bsudo\b",
    r"\bsu\s+-",
    r"\bchmod\s+777\b",
    r"\bchown\s+root\b",
    r"\bsetuid\b",
    r"\bdoas\b",
    # Outbound exfil
    r"\bcurl\s+(-[a-zA-Z]+\s+)*https?://",
    r"\bwget\s+.+\|\s*(sh|bash|zsh|python)",
    r"\bcurl\s+.+\|\s*(sh|bash|zsh|python)",
    r"\bnc\s+(-[a-zA-Z]+\s+)*-e\b",
    r"bash\s+-i\s+>&\s+/dev/tcp/",
    r"\b/dev/tcp/",
    r"\bsocat\b",
    # Package install
    r"\bpip3?\s+install\b",
    r"\buv\s+(pip\s+)?install\b",
    r"\bnpm\s+(install|i)\b",
    r"\byarn\s+add\b",
    r"\bpnpm\s+(add|install|i)\b",
    r"\bbrew\s+install\b",
    r"\bapt(-get)?\s+install\b",
    r"\bdnf\s+install\b",
    # Git destructive
    r"\bgit\s+push\s+(-[a-zA-Z]+\s+)*--force(-with-lease)?\s+.*\b(main|master)\b",
    r"\bgit\s+push\s+(-[a-zA-Z]+\s+)*-f\s+.*\b(main|master)\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[fdx]+",
    r"\bgit\s+branch\s+-D\b",
    r"\bgit\s+checkout\s+\.",
    r"\bgit\s+restore\s+\.",
    r"--no-verify\b",
    # Process kill / system
    r"\bpkill\s+-f\b",
    r"\bkillall\s+-9\b",
    r"\bkill\s+-9\s+1\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bhalt\b",
]


def _ts_brt(dt: datetime | None = None) -> str:
    """RFC3339 timestamp in America/Sao_Paulo with explicit -03:00 offset."""
    dt = dt or now_brt()
    return dt.strftime("%Y-%m-%dT%H:%M:%S-03:00")


def now_brt() -> datetime:
    return datetime.now(tz=BRT)


def _parse_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        out[key] = val
    return out


DOTENV_PATH = REPO_ROOT / ".claude" / ".env"


def load_env() -> None:
    """Load `.claude/.env` into os.environ via python-dotenv (override=False).

    Lazy import — hooks running on system python (no .venv) skip this and read
    from stdin instead, so they don't pay the import cost.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(DOTENV_PATH, override=False)


@lru_cache(maxsize=1)
def vault_path() -> Path:
    """Resolve BRUNOS_VAULT_PATH from env or .claude/.env. Raise if unset."""
    val = os.environ.get("BRUNOS_VAULT_PATH")
    if not val:
        env = _parse_dotenv(DOTENV_PATH)
        val = env.get("BRUNOS_VAULT_PATH")
    if not val:
        raise RuntimeError(
            "BRUNOS_VAULT_PATH not set in environment or .claude/.env "
            f"(checked {DOTENV_PATH})"
        )
    return Path(val).expanduser().resolve()


@contextlib.contextmanager
def file_lock(path: os.PathLike[str] | str):
    """Exclusive flock keyed by md5 of the absolute target path.

    Locks a sibling file in .claude/data/state/locks/, not the target itself
    (target may not exist yet).
    """
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    abs_path = str(Path(path).resolve())
    digest = hashlib.md5(abs_path.encode("utf-8")).hexdigest()
    lock_path = LOCK_DIR / f"{digest}.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


_FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_UPDATED_RE = re.compile(r"^(updated:\s*).*$", re.MULTILINE)


def _stamp_updated(content: str, ts: str) -> str:
    """Patch the `updated:` field inside YAML frontmatter, if present.

    No-op when no frontmatter block exists. Inserts `updated:` at end of
    frontmatter when block exists but field is missing.
    """
    m = _FM_RE.match(content)
    if not m:
        return content
    fm = m.group(1)
    if _UPDATED_RE.search(fm):
        new_fm = _UPDATED_RE.sub(f"updated: {ts}", fm, count=1)
    else:
        new_fm = fm.rstrip() + f"\nupdated: {ts}"
    return content[: m.start()] + f"---\n{new_fm}\n---\n" + content[m.end() :]


# --- Capture / frontmatter parsing (shared by memory_reflect + federation_doctor) ---

_SCALAR_FM_RE = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$")


def read_text(path: os.PathLike[str] | str) -> str:
    """Read a file as UTF-8; return "" on any OSError (missing/unreadable)."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def parse_iso(s: str | None) -> datetime | None:
    """Parse an RFC3339 timestamp (e.g. 2026-05-23T20:47:17-03:00). None on failure."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.strip())
    except (ValueError, TypeError):
        return None


def parse_capture(path: os.PathLike[str] | str) -> tuple[dict, str] | None:
    """Split a capture into (scalar-frontmatter dict, body). None if no frontmatter.

    Only scalar `key: value` fields are captured (block lists like `tags:` are
    skipped — callers need `created`, `project`, `default_export`, `share_status`,
    `status`, none of which are block lists). Tolerant: malformed files return
    None so the caller can skip + log.
    """
    text = read_text(path)
    if not text:
        return None
    m = _FM_RE.match(text)
    if not m:
        return None
    body = text[m.end():]
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        sm = _SCALAR_FM_RE.match(line)
        if sm and sm.group(2).strip():  # skip block-list headers (empty value)
            fm[sm.group(1)] = sm.group(2).strip()
    return fm, body


def atomic_write(
    path: os.PathLike[str] | str,
    content: str,
    *,
    stamp_updated: bool | None = None,
) -> None:
    """Atomic write via tmp+os.replace. Stamps `updated:` for .md files by default."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if stamp_updated is None:
        stamp_updated = p.suffix == ".md"
    if stamp_updated:
        content = _stamp_updated(content, _ts_brt())
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, p)


def _new_daily(dt: datetime) -> str:
    date_s = dt.strftime("%Y-%m-%d")
    ts = _ts_brt(dt)
    return (
        "---\n"
        "type: daily\n"
        f"created: {ts}\n"
        f"updated: {ts}\n"
        "tags:\n"
        "  - daily\n"
        "status: active\n"
        "---\n"
        f"\n# {date_s}\n\n"
    )


def append_to_daily_log(line: str, dt: datetime | None = None) -> Path:
    """Append a line (or block) to today's daily log under file lock.

    Creates the file with proper frontmatter if missing. Returns the path written.
    """
    dt = dt or now_brt()
    daily_path = vault_path() / "Memory" / "daily" / f"{dt.strftime('%Y-%m-%d')}.md"
    with file_lock(daily_path):
        existing = daily_path.read_text(encoding="utf-8") if daily_path.exists() else _new_daily(dt)
        if not existing.endswith("\n"):
            existing += "\n"
        new_content = existing + line + "\n"
        atomic_write(daily_path, new_content)
    return daily_path


_PROJECT_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_VALID_EXPORT_TARGETS = {"personal", "linos-protostack", "discard"}

# Explicit declared read-scopes for company-brain consumers.
# Maps consumer slug → frozenset of allowed default_export values.
# Unknown consumers are denied (fail-closed). LinOS reads only "linos-protostack".
CONSUMER_READ_SCOPES: dict[str, frozenset[str]] = {
    "linos": frozenset({"linos-protostack"}),
    # Future: "vertikos": frozenset({"vertik"}),
}


def validate_consumer_read(capture_fm: dict, consumer: str) -> bool:
    """Return True iff capture's default_export is in the declared scope for consumer.

    Unknown consumers are denied (fail-closed). Does NOT check share_status —
    the consuming brain is responsible for that gate (see linos_consumer.py,
    which skips any capture whose share_status != "cleared").
    """
    allowed = CONSUMER_READ_SCOPES.get(consumer)
    if allowed is None:
        return False  # unknown consumer → deny
    export = str(capture_fm.get("default_export") or "").strip()
    return export in allowed


def _slug(s: str) -> str:
    return _PROJECT_SLUG_RE.sub("-", s.strip().lower()).strip("-") or "unknown"


_GENERIC_PARENT_DIRS = {
    "documents", "projects", "code", "repos", "src", "workspace",
    "dev", "work", "github", "brunobouwman",
}


# Manual slug aliases — collapse auto-derived slugs onto one canonical slug so a
# single repo never splits across two inbox folders. Applied at the end of every
# auto-derivation path (Claude Code path-based + Codex canonical-path + Codex
# worktree URL-basename). NOTE: Claude Code hooks that pass an explicit --project
# bypass derivation entirely, so keep those flags pointed at the SAME canonical
# value (both Vertik repos' hooks now use --project=vertik).
_SLUG_ALIASES = {
    # Vertik — lab-agent + lab-agent-chat-ui are one context: "vertik".
    "vertik-lab-agent": "vertik",                 # canonical-path derivation (lab-agent)
    "vertik-lab-agent-chat-ui": "vertik",         # canonical-path derivation (chat-ui)
    "lab-agent": "vertik",                         # Codex worktree URL-basename fallback (lab-agent)
    "lab-agent-chat-ui": "vertik",                 # Codex worktree URL-basename fallback (chat-ui)
    "vertik-studio": "vertik",                     # retired prior canonical (pre-2026-05-24)
    # Protostack / Memorial Colinas.
    "protostack-colinas": "colinas",              # canonical-path derivation
    "memorial-colinas": "colinas",                # Codex worktree URL-basename fallback (repo = memorial-colinas.git)
}


def canonicalize_slug(slug: str | None) -> str | None:
    """Map an auto-derived slug onto its canonical alias, if one is defined."""
    if slug is None:
        return None
    return _SLUG_ALIASES.get(slug, slug)


def derive_project_slug_from_path(path: os.PathLike[str] | str | None) -> str | None:
    """Auto-derive a project slug from an arbitrary directory path.

    Returns None when the path is missing/unreadable or resolves to the
    BrunOS repo itself (route to daily log). Otherwise returns a slug of
    the form `<parent>-<base>` when the parent isn't a generic wrapper dir,
    else just `<base>`. Matches the existing `vertik-lab-agent` convention.
    """
    if not path:
        return None
    try:
        project_dir = Path(path).resolve()
    except OSError:
        return None
    try:
        if project_dir == REPO_ROOT.resolve():
            return None
    except OSError:
        pass
    base = project_dir.name
    if not base:
        return None
    parent_name = project_dir.parent.name
    if parent_name and parent_name.lower() not in _GENERIC_PARENT_DIRS:
        return canonicalize_slug(_slug(f"{parent_name}-{base}"))
    return canonicalize_slug(_slug(base))


def derive_project_slug() -> str | None:
    """Auto-derive a flush-routing project slug from $CLAUDE_PROJECT_DIR.

    Thin wrapper over `derive_project_slug_from_path` for Claude Code hooks
    that rely on the env var. Codex paths (watcher, backfill) call the
    `_from_path` variant directly with `session_meta.cwd`.
    """
    return derive_project_slug_from_path(os.environ.get("CLAUDE_PROJECT_DIR"))


def write_inbox_capture(
    *,
    project: str,
    default_export: str,
    session_id: str,
    source: str,
    body: str,
    dt: datetime | None = None,
) -> Path:
    """Write a session-capture file into the per-project inbox.

    Path: vault/Memory/_inbox/sessions/<project-slug>/<YYYY-MM-DD>-<HHMMSS>-<sid>.md
    Frontmatter carries `project`, `default_export`, `session_id`, `source` so
    Phase B reflection can route without re-classifying every item from scratch.

    Phase A only writes — no classification, no promotion. Reflection picks
    these up on its own cadence.
    """
    dt = dt or now_brt()
    # Canonicalize at the write boundary so NO caller can split a repo across
    # inbox folders: an explicit --project flag (e.g. a Codex precompact hook
    # passing --project=vertik-lab-agent) bypasses path-derivation's
    # canonicalize_slug, so apply it here too — this is the single chokepoint
    # every capture passes through.
    project_slug = canonicalize_slug(_slug(project)) or _slug(project)
    if default_export not in _VALID_EXPORT_TARGETS:
        default_export = "personal"
    sid_short = (session_id or "unknown").replace("-", "")[:8] or "unknown"
    fname = f"{dt.strftime('%Y-%m-%d')}-{dt.strftime('%H%M%S')}-{sid_short}.md"
    inbox_dir = vault_path() / "Memory" / "_inbox" / "sessions" / project_slug
    target = inbox_dir / fname
    ts = _ts_brt(dt)
    frontmatter = (
        "---\n"
        "type: inbox\n"
        f"created: {ts}\n"
        f"updated: {ts}\n"
        f"project: {project_slug}\n"
        f"default_export: {default_export}\n"
        f"session_id: {session_id or 'unknown'}\n"
        f"source: {source}\n"
        "tags:\n"
        "  - inbox\n"
        f"  - {project_slug}\n"
        "  - session-capture\n"
        "status: active\n"
        "---\n\n"
    )
    body_text = body if body.endswith("\n") else body + "\n"
    with file_lock(target):
        atomic_write(target, frontmatter + body_text)
    return target


def save_state(path: os.PathLike[str] | str, obj) -> None:
    atomic_write(path, json.dumps(obj, indent=2, ensure_ascii=False), stamp_updated=False)


def load_state(path: os.PathLike[str] | str, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


# --- Personal pending buffer (Phase B) ---------------------------------------
# The hourly inbox pass + daily-log distill buffer promotable personal items here
# instead of writing MEMORY.md per batch; the daily curation stage drains it. It's
# canonical knowledge that just hasn't been promoted yet — so the agent's context
# (session-start-context) and memory_search surface it intraday, otherwise a fact
# learned at 09:00 wouldn't be recallable until tomorrow's curation + reindex.
PERSONAL_PENDING_PATH = STATE_DIR / "personal_pending.json"  # [{type,text,source,ts}]


def load_personal_pending() -> list[dict]:
    """Return the buffered personal items (list of {type,text,source,ts}); [] if none."""
    data = load_state(PERSONAL_PENDING_PATH, default=[])
    return data if isinstance(data, list) else []


def format_personal_pending(items: list[dict] | None = None) -> str:
    """Render the pending buffer as a markdown block for the agent's context.

    Empty string when the buffer is empty (caller omits the section). Each item is
    one bullet tagged with its type + originating project so the agent can weigh it
    like a MEMORY.md entry that hasn't been curated yet.
    """
    items = load_personal_pending() if items is None else items
    lines: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "").strip()
        if not text:
            continue
        typ = str(it.get("type") or "note")
        src = str(it.get("source") or "").strip()
        suffix = f"  _[{src}]_" if src else ""
        lines.append(f"- ({typ}) {text}{suffix}")
    if not lines:
        return ""
    return (
        "## Pending personal (extracted today, not yet curated into MEMORY.md)\n"
        "_Drained into MEMORY.md by the daily curation pass. Treat as fresh, "
        "low-friction memory; verify before relying on it for an irreversible action._\n"
        + "\n".join(lines)
    )


def _extract_status(exc: BaseException) -> int | None:
    sc = getattr(exc, "status_code", None)
    if isinstance(sc, int):
        return sc
    resp = getattr(exc, "response", None)
    if resp is not None:
        sc = getattr(resp, "status_code", None)
        if isinstance(sc, int):
            return sc
    resp_g = getattr(exc, "resp", None)
    if resp_g is not None:
        sc = getattr(resp_g, "status", None)
        if isinstance(sc, int):
            return sc
        if isinstance(sc, str) and sc.isdigit():
            return int(sc)
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    return None


def with_retry(
    fn,
    *,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    retry_on: tuple[int, ...] = (429, 500, 502, 503),
):
    """Call fn() with exponential backoff on retryable HTTP-style errors."""
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            status = _extract_status(e)
            if status is None or status not in retry_on or attempt == max_retries:
                raise
            last_exc = e
            time.sleep(backoff_base * (2**attempt))
    if last_exc is not None:
        raise last_exc


_UV_FALLBACK_PATHS = (
    Path.home() / ".local" / "bin" / "uv",
    Path("/opt/homebrew/bin/uv"),
    Path("/usr/local/bin/uv"),
)


def _resolve_uv() -> str | None:
    import shutil

    found = shutil.which("uv")
    if found:
        return found
    for p in _UV_FALLBACK_PATHS:
        if p.exists():
            return str(p)
    return None


def dispatch_flush(
    stdin_data: dict,
    source: str,
    *,
    project: str | None = None,
    default_export: str | None = None,
    sync: bool = False,
) -> Path | None:
    """Persist transcript JSON and run memory_flush.py via `uv run`.

    Falls back to .venv/bin/python or sys.executable if uv is not on PATH.
    Returns the kickoff transcript path on success; None if write failed.

    `project` and `default_export` are Phase A capture-routing metadata.
    When `project` is set (and not "brunos"), the flush is routed to
    Memory/_inbox/sessions/<project>/ instead of the daily log. The
    default_export tag rides along in the inbox file's frontmatter for
    Phase B reflection to consume.

    `sync=False` (default): Popen detached so the hook returns immediately.
    `sync=True`: subprocess.run waits to completion (used by the Codex
    backfill so we serialize Anthropic calls instead of fanning out 30
    concurrent processes).
    """
    import subprocess
    import sys
    import uuid

    session_id = stdin_data.get("session_id") or f"unknown-{uuid.uuid4().hex[:8]}"
    transcript_path = STATE_DIR / f"flush-{session_id}.json"
    payload = dict(stdin_data)
    payload.setdefault("_source", source)
    payload.setdefault("_dispatched_at", _ts_brt())
    if project:
        payload["_project"] = project
    if default_export:
        payload["_default_export"] = default_export
    try:
        save_state(transcript_path, payload)
    except OSError:
        return None

    flush_script = REPO_ROOT / ".claude" / "scripts" / "memory_flush.py"
    uv_bin = _resolve_uv()
    if uv_bin:
        cmd = [
            uv_bin, "run", "--project", str(REPO_ROOT),
            "python", str(flush_script), str(transcript_path),
        ]
    else:
        venv_python = REPO_ROOT / ".venv" / "bin" / "python"
        python_bin = str(venv_python) if venv_python.exists() else sys.executable
        cmd = [python_bin, str(flush_script), str(transcript_path)]

    try:
        if sync:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                cwd=str(REPO_ROOT),
                check=False,
            )
        else:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                cwd=str(REPO_ROOT),
            )
    except OSError:
        return transcript_path
    return transcript_path


def trim_dedup_entries(entries: dict, max_age_days: int = 1) -> dict:
    """Drop entries older than max_age_days from a session_id->timestamp map."""
    cutoff = now_brt() - timedelta(days=max_age_days)
    kept: dict = {}
    for sid, ts in entries.items():
        try:
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            continue
        if dt >= cutoff:
            kept[sid] = ts
    return kept
