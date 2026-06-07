"""Draft lifecycle: filename helpers + expire + (stub) sent-capture.

Phase 6 ships:
  - `draft_filename` — idempotent filename for a (source, source_id) pair.
  - `expire_old_drafts(now)` — moves drafts/active/*.md older than 24h to drafts/expired/.
  - `capture_sent_replies(slack_msgs, gmail_msgs)` — STUB; real implementation in Phase 6.5.
  - `format_active_drafts_summary()` — short summary for the heartbeat agent prompt.

Drafts use this frontmatter (per PRD §6.3 + USER.md):

    ---
    type: draft
    source: gmail | slack | github
    source_id: <provider-specific id>
    recipient: ...
    subject: ...
    context: ...
    created: 2026-05-03T14:30-03:00
    updated: 2026-05-03T14:30-03:00
    status: active
    language: portuguese | english
    tags:
      - draft
    ---
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared import (  # noqa: E402
    atomic_write,
    file_lock,
    now_brt,
    vault_path,
)

ACTIVE_REL = Path("Memory/drafts/active")
EXPIRED_REL = Path("Memory/drafts/expired")
SENT_REL = Path("Memory/drafts/sent")

EXPIRY_HOURS = 24

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_CREATED_RE = re.compile(r"^created:\s*(.+?)\s*$", re.MULTILINE)
_SOURCE_ID_RE = re.compile(r"^source_id:\s*(.+?)\s*$", re.MULTILINE)
_STATUS_RE = re.compile(r"^(status:\s*).*$", re.MULTILINE)


def _slug(value: str) -> str:
    """Slugify a recipient string for use in filenames."""
    if not value:
        return "unknown"
    s = value.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:32] or "unknown"


def _hash(source: str, source_id: str) -> str:
    """Deterministic 4-char filename hash. Same source+source_id → same hash."""
    return hashlib.md5(f"{source}:{source_id}".encode("utf-8")).hexdigest()[:4]


def draft_filename(
    source: str,
    source_id: str,
    recipient: str,
    created_dt: datetime,
) -> str:
    """Canonical filename `YYYY-MM-DD_<source>_<recipient-slug>_<hash>.md`."""
    date_part = created_dt.strftime("%Y-%m-%d")
    return f"{date_part}_{source}_{_slug(recipient)}_{_hash(source, source_id)}.md"


def _drafts_dir(rel: Path) -> Path:
    return vault_path() / rel


def _parse_created(text: str) -> datetime | None:
    fm = _FRONTMATTER_RE.match(text)
    if not fm:
        return None
    block = fm.group(1)
    m = _CREATED_RE.search(block)
    if not m:
        return None
    raw = m.group(1).strip()
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _parse_source_id(text: str) -> str | None:
    fm = _FRONTMATTER_RE.match(text)
    if not fm:
        return None
    m = _SOURCE_ID_RE.search(fm.group(1))
    return m.group(1).strip() if m else None


def expire_old_drafts(now_dt: datetime) -> list[Path]:
    """Move drafts/active/*.md older than EXPIRY_HOURS to drafts/expired/.

    Returns list of (final) destination paths. Idempotent within a tick — files
    already moved by a prior tick won't reappear in active/.
    """
    active_dir = _drafts_dir(ACTIVE_REL)
    expired_dir = _drafts_dir(EXPIRED_REL)
    moved: list[Path] = []
    if not active_dir.is_dir():
        return moved
    expired_dir.mkdir(parents=True, exist_ok=True)
    cutoff = now_dt - timedelta(hours=EXPIRY_HOURS)

    # rglob (not glob) so drafts nested in subdirectories of active/ (e.g.
    # `active/protostack-baas/*.md`) also expire — top-level-only glob left
    # them stuck in active/ forever. Surfaced by a LisaOS diagnose-brain run,
    # 2026-06-07. The relative subpath is preserved under expired/ to keep
    # provenance and avoid same-name collisions across subdirs.
    for path in sorted(active_dir.rglob("*.md")):
        rel = path.relative_to(active_dir)
        if any(part.startswith("_") for part in rel.parts):
            # skip _-prefixed files and meta subdirs (e.g. _archive/)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        created = _parse_created(text)
        if created is None or created.tzinfo is None:
            # Unparseable / naive timestamp — skip; conservative default is to keep.
            continue
        if created > cutoff:
            continue
        # Flip status to expired; atomic_write stamps `updated:`.
        new_text = _STATUS_RE.sub("status: expired", text, count=1)
        dest = expired_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(dest):
            atomic_write(dest, new_text)
        try:
            path.unlink()
        except OSError:
            pass
        moved.append(dest)
    return moved


def capture_sent_replies(slack_msgs: list, gmail_msgs: list) -> list[Path]:  # noqa: ARG001
    """Detect drafts whose recipient already replied; move active/ → sent/.

    # TODO(Phase 6.5): real implementation. Phase 4's slack/gmail readers don't
    expose enough signal to reliably detect "Bruno actually sent his reply on
    the platform". Until then, the voice corpus grows manually (Bruno moves
    files); the heartbeat still uses `memory_search --path-prefix drafts/sent`
    over whatever's there.
    """
    sys.stderr.write(
        "[drafts] capture_sent_replies: stub — real implementation lands in Phase 6.5\n"
    )
    return []


def format_active_drafts_summary() -> str:
    """Short list of (source_id, filename) for currently-active drafts.

    The heartbeat agent uses this to skip re-drafting items it already drafted
    in a prior tick.
    """
    active_dir = _drafts_dir(ACTIVE_REL)
    if not active_dir.is_dir():
        return "_No active drafts._"
    rows: list[str] = []
    for path in sorted(active_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        sid = _parse_source_id(text) or "?"
        rows.append(f"- {path.name} (source_id={sid})")
    if not rows:
        return "_No active drafts._"
    return "Active drafts (do NOT re-draft these source_ids):\n" + "\n".join(rows)
