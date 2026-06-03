#!/usr/bin/env python3
"""gap_analysis.py — deterministic "knowledge gap" scanner (BaaS C1).

Flags ongoing entities (projects / clients / goals) whose vault page hasn't been
touched in N+ days — gbrain's "nothing filed about X in 6 weeks, you're probably
missing something." Zero-LLM: a filesystem-mtime recency scan over a folder
allowlist, skipping closed (archived/done) entities and the finance boundary.

Surfaced two ways:
  - CLI (demo / on-demand): `gap_analysis.py [--json] [--days N] [--folders ...]`.
  - Heartbeat: `gaps_to_surface()` + `mark_surfaced()` fold a once-daily
    "Knowledge gaps" block into the daily log and a one-line summary into the
    tick notification — no per-30-min-tick spam.

Why mtime, not the frontmatter `updated:` field: mtime captures Obsidian
hand-edits too, and is the conservative "has ANYTHING happened to this entity"
measure. `updated:` only reflects the last *agent* write (shared.atomic_write),
so a file Bruno edited by hand would falsely read as stale.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import (  # noqa: E402
    BRT,
    STATE_DIR,
    load_env,
    load_state,
    now_brt,
    parse_capture,
    save_state,
    vault_path,
)

GAP_STATE_PATH = STATE_DIR / "gap-analysis-state.json"

# Ongoing entities that "go stale". Point-in-time folders (meetings, news-digest,
# daily) are deliberately excluded — they're events, not entities you neglect.
DEFAULT_FOLDERS = ("projects", "clients", "goals")

# Per-folder staleness thresholds (days) — different natural cadences (a weekly
# goal vs a slower-moving client). Folders without an entry use DEFAULT_STALE_DAYS.
DEFAULT_STALE_DAYS = 14
STALE_THRESHOLDS = {
    "projects": 14,
    "clients": 21,
    "goals": 10,
}

# Closed entities never nag.
_CLOSED_STATUSES = {"archived", "done", "completed", "cancelled"}

# Never surface the finance boundary file (SOUL.md no-financial-data).
_EXCLUDED_RELPATHS = {"personal/finance.md"}

# Dated point-in-time artifacts that live under the entity folders but aren't
# ongoing entities: weekly/monthly review snapshots (goals/YYYY-Www-review.md)
# and any date-stamped file. A past week's review is *supposed* to be stale —
# flagging it is noise. Matched on the filename stem.
_DATED_ARTIFACT_RE = re.compile(
    r"(^\d{4}-W\d{2}\b)|(^\d{4}-\d{2}-\d{2}\b)|(-review$)", re.IGNORECASE
)


def _env_folders() -> tuple[str, ...]:
    env = os.environ.get("BRUNOS_GAP_FOLDERS")
    if env:
        return tuple(f.strip() for f in env.split(",") if f.strip())
    return DEFAULT_FOLDERS


def scan_gaps(
    now: datetime,
    *,
    vault: Path | None = None,
    folders: tuple[str, ...] | None = None,
    thresholds: dict | None = None,
    default_days: int | None = None,
) -> list[dict]:
    """Return stale entity files, most-stale first. Pure: no writes, no state.

    A gap = an .md under one of `folders` whose filesystem mtime is older than
    that folder's threshold AND whose frontmatter `status` isn't closed. Each
    gap dict: {path, name, folder, type, status, days, threshold, mtime_iso}.
    """
    vault = vault or vault_path()
    folders = folders if folders is not None else _env_folders()
    thresholds = STALE_THRESHOLDS if thresholds is None else thresholds
    default_days = DEFAULT_STALE_DAYS if default_days is None else default_days
    mem = vault / "Memory"
    gaps: list[dict] = []
    for folder in folders:
        base = mem / folder
        if not base.is_dir():
            continue
        cutoff_days = int(thresholds.get(folder, default_days))
        for p in sorted(base.rglob("*.md")):
            if p.name.startswith("_"):  # _README.md and machine/meta files
                continue
            if _DATED_ARTIFACT_RE.search(p.stem):  # dated review/snapshot artifacts
                continue
            rel = p.relative_to(mem).as_posix()
            if rel in _EXCLUDED_RELPATHS:
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            mtime_dt = datetime.fromtimestamp(mtime, tz=BRT)
            days = (now - mtime_dt).days
            if days < cutoff_days:
                continue
            fm = parse_capture(p)
            fm = fm[0] if fm else {}
            status = (fm.get("status") or "").lower()
            if status in _CLOSED_STATUSES:
                continue
            gaps.append(
                {
                    "path": rel,
                    "name": p.stem,
                    "folder": folder,
                    "type": fm.get("type") or folder.rstrip("s"),
                    "status": status or "unknown",
                    "days": days,
                    "threshold": cutoff_days,
                    "mtime_iso": mtime_dt.strftime("%Y-%m-%dT%H:%M:%S-03:00"),
                }
            )
    gaps.sort(key=lambda g: -g["days"])
    return gaps


# ── once-daily surfacing guard (heartbeat) ──


def due_today(now: datetime, state_path: Path | None = None) -> bool:
    """True iff gaps haven't already been surfaced today (once-daily guard)."""
    state = load_state(state_path or GAP_STATE_PATH, default={}) or {}
    return state.get("last_surfaced_date") != now.strftime("%Y-%m-%d")


def mark_surfaced(
    now: datetime, gaps: list[dict], state_path: Path | None = None
) -> None:
    save_state(
        state_path or GAP_STATE_PATH,
        {
            "last_surfaced_date": now.strftime("%Y-%m-%d"),
            "last_surfaced_count": len(gaps),
            "last_surfaced_ts": now.strftime("%Y-%m-%dT%H:%M:%S-03:00"),
        },
    )


def gaps_to_surface(
    now: datetime, *, state_path: Path | None = None, **scan_kw
) -> list[dict]:
    """scan_gaps + once-daily guard. Returns [] if already surfaced today.

    Does NOT mutate state — the caller appends the block, then calls
    `mark_surfaced` so a crash before the write re-surfaces next tick.
    """
    if not due_today(now, state_path):
        return []
    return scan_gaps(now, **scan_kw)


# ── formatting ──


def format_summary(gaps: list[dict], limit: int = 3) -> str:
    """One-line notification, e.g. '3 stale: vertik 18d, colinas 25d, +1 more'."""
    if not gaps:
        return ""
    head = ", ".join(f"{g['name']} {g['days']}d" for g in gaps[:limit])
    extra = f", +{len(gaps) - limit} more" if len(gaps) > limit else ""
    return f"{len(gaps)} stale: {head}{extra}"


def format_block(gaps: list[dict], now: datetime) -> str:
    """Markdown block appended to the daily log when gaps surface."""
    lines = [
        f"\n## Knowledge gaps ({now.strftime('%H:%M')})",
        "",
        "Entities past their staleness threshold — likely missing recent context "
        "worth filing:",
        "",
    ]
    for g in gaps:
        lines.append(
            f"- `{g['path']}` — {g['days']}d stale "
            f"(threshold {g['threshold']}d, status: {g['status']})"
        )
    return "\n".join(lines) + "\n"


def format_table(gaps: list[dict]) -> str:
    if not gaps:
        return "No knowledge gaps — every tracked entity is fresh."
    width = max(len(g["path"]) for g in gaps)
    rows = [f"{'ENTITY'.ljust(width)}  DAYS  THRESH  STATUS"]
    for g in gaps:
        rows.append(
            f"{g['path'].ljust(width)}  {str(g['days']).rjust(4)}  "
            f"{str(g['threshold']).rjust(6)}  {g['status']}"
        )
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scan the vault for stale ('gap') entities — projects/clients/goals "
        "with no recent updates."
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument(
        "--days",
        type=int,
        default=None,
        help="uniform staleness threshold in days (overrides per-folder defaults)",
    )
    ap.add_argument(
        "--folders",
        default=None,
        help="comma-separated folder allowlist (default: projects,clients,goals)",
    )
    args = ap.parse_args()
    load_env()
    folders = (
        tuple(f.strip() for f in args.folders.split(",") if f.strip())
        if args.folders
        else None
    )
    thresholds = {} if args.days is not None else None
    gaps = scan_gaps(
        now_brt(), folders=folders, thresholds=thresholds, default_days=args.days
    )
    if args.json:
        print(json.dumps(gaps, ensure_ascii=False, indent=2))
    else:
        print(format_table(gaps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
