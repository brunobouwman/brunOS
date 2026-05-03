"""Heartbeat snapshot + diff (PRD §6.1 stage 2).

Function names are LOCKED — `build_snapshot` and `diff_snapshot` are explicitly
named in the PRD so they're greppable across the codebase.

build_snapshot extracts JSON-serializable scalar dicts from each integration's
dataclasses and sorts every list by a stable key. Stable ordering matters: the
diff is set-membership over `tuple(sorted(d.items()))` and only works if the
inputs are deterministic.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared import (  # noqa: E402
    STATE_DIR,
    _ts_brt,
    load_state,
    save_state,
)

SNAPSHOT_PATH = STATE_DIR / "heartbeat-state.json"


def build_snapshot(gathered: dict) -> dict:
    """Build a deterministic JSON-serializable snapshot from gathered integration data.

    Each list is sorted by a stable key (composite of natural ID fields).
    Values are scalars only — required by the diff's `tuple(sorted(d.items()))`
    membership trick.
    """
    return {
        "ts": _ts_brt(),
        "slack": sorted(
            (
                {"channel_id": m.channel_id, "ts": m.ts}
                for m in gathered.get("slack_msgs", [])
            ),
            key=lambda d: (d["channel_id"], d["ts"]),
        ),
        "github_assigned": sorted(
            (
                {"repo": i.repo, "number": i.number}
                for i in gathered.get("github_assigned", [])
            ),
            key=lambda d: (d["repo"], d["number"]),
        ),
        "github_prs": sorted(
            (
                {"repo": p.repo, "number": p.number, "updated_at": p.updated_at}
                for p in gathered.get("github_prs", [])
            ),
            key=lambda d: (d["repo"], d["number"]),
        ),
        "github_commits": sorted(
            (
                {"repo": c.repo, "sha": c.sha}
                for c in gathered.get("github_commits", [])
            ),
            key=lambda d: (d["repo"], d["sha"]),
        ),
        "clickup_overdue": sorted(
            (
                {"workspace": t.workspace, "id": t.id, "status": t.status}
                for t in gathered.get("clickup_overdue", [])
            ),
            key=lambda d: (d["workspace"], d["id"]),
        ),
        "clickup_today": sorted(
            (
                {"workspace": t.workspace, "id": t.id, "status": t.status}
                for t in gathered.get("clickup_today", [])
            ),
            key=lambda d: (d["workspace"], d["id"]),
        ),
        "gmail_unread": sorted(
            (
                {"id": e.id, "thread_id": e.thread_id}
                for e in gathered.get("gmail_unread", [])
            ),
            key=lambda d: d["id"],
        ),
        "calendar_today": sorted(
            (
                {"id": e.id, "start_iso": e.start_iso}
                for e in gathered.get("calendar_today", [])
            ),
            key=lambda d: (d["start_iso"], d["id"]),
        ),
        "rss_new": sorted(
            (
                {"feed_url": it.feed_url, "item_id": it.item_id}
                for it in gathered.get("rss_new", [])
            ),
            key=lambda d: (d["feed_url"], d["item_id"]),
        ),
    }


def diff_snapshot(current: dict, previous: dict) -> dict:
    """Per-category list of items in `current` not in `previous`.

    First-run / cold-start: previous is None or {} → all current items are 'new'.
    The membership check uses tuple(sorted(d.items())) which only works when
    every value is a scalar. If a future field becomes a list, this breaks —
    update both build_snapshot and this trick at the same time.
    """
    if not previous:
        return {k: list(v) for k, v in current.items() if k != "ts"}
    out: dict = {}
    for k, current_list in current.items():
        if k == "ts":
            continue
        prev_list = previous.get(k, []) or []
        prev_keys = {tuple(sorted(d.items())) for d in prev_list}
        out[k] = [d for d in current_list if tuple(sorted(d.items())) not in prev_keys]
    return out


def load_previous_snapshot() -> dict:
    return load_state(SNAPSHOT_PATH, default={}) or {}


def save_current_snapshot(snap: dict) -> None:
    save_state(SNAPSHOT_PATH, snap)


def is_empty_delta(delta: dict) -> bool:
    """True if delta has no items the heartbeat AGENT should reason about.

    RSS deltas are excluded — RSS is for the news-digest skill (07:30 BRT),
    not for the per-tick heartbeat agent. A tick with only RSS deltas hits the
    fast-path so we don't pay for SDK calls on background feed traffic.
    """
    return all(not v for k, v in delta.items() if k != "rss_new")
