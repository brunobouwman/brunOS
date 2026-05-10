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


def _slug(s: str) -> str:
    return _PROJECT_SLUG_RE.sub("-", s.strip().lower()).strip("-") or "unknown"


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
    project_slug = _slug(project)
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
) -> Path | None:
    """Persist transcript JSON and Popen memory_flush.py detached via `uv run`.

    Falls back to .venv/bin/python or sys.executable if uv is not on PATH.
    Returns the transcript path on success; None if write failed.

    `project` and `default_export` are Phase A capture-routing metadata.
    When `project` is set (and not "brunos"), the flush is routed to
    Memory/_inbox/sessions/<project>/ instead of the daily log. The
    default_export tag rides along in the inbox file's frontmatter for
    Phase B reflection to consume.
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
