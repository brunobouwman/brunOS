"""HABITS.md helpers — deterministic reset + per-pillar signal detection.

`habits.py` does NOT modify checkboxes. The heartbeat AGENT applies check-marks
via the Edit tool based on the signals computed here. This module:

  - resets HABITS.md once per day (08:00-style: archive Today → History,
    create fresh checklist).
  - computes per-pillar boolean signals from the heartbeat's snapshot delta.
  - reports unchecked pillars and the evening-nudge window.

The five pillars (matching HABITS.md headings):
  - sales-ai        ClickUp Vertik|Protostack task → Done OR clients/ edited today
  - lisa-freelance  ClickUp Protostack task with Lisa-related work → Done
  - ai-learning     research/ edited today OR commit on a learning repo today
  - health          self-reported only — never auto
  - content         weekly: content/ edited this week OR published RSS post
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared import (  # noqa: E402
    BRT,
    atomic_write,
    file_lock,
    now_brt,
    vault_path,
)

HABITS_PATH_REL = "Memory/HABITS.md"

PILLAR_KEYS = ("sales-ai", "lisa-freelance", "ai-learning", "health", "content")

# Map pillar key → substring that must appear in the bold-text label of the pillar
# heading in HABITS.md. The agent uses these to find + edit the right checkbox.
PILLAR_LABELS = {
    "sales-ai": "Sales-AI work",
    "lisa-freelance": "Lisa freelance",
    "ai-learning": "AI engineering learning",
    "health": "Health",
    "content": "Content",
}

DATE_LINE_RE = re.compile(r"^\*\*Date:\*\*\s*(.+?)\s*$", re.MULTILINE)
TODAY_HEADER_RE = re.compile(r"^## Today\s*$", re.MULTILINE)
NOTES_HEADER_RE = re.compile(r"^## Notes for today\s*$", re.MULTILINE)
HISTORY_HEADER_RE = re.compile(r"^## History\s*$", re.MULTILINE)
HISTORY_PLACEHOLDER_RE = re.compile(
    r"\*\(Empty — first morning reset will populate\.\)\*\s*",
)
PILLAR_LINE_RE = re.compile(r"^- \[(?P<box>[ x])\] \*\*(?P<label>[^*]+)\*\*", re.MULTILINE)


def _today_str() -> str:
    return now_brt().strftime("%Y-%m-%d")


def _habits_path() -> Path:
    return vault_path() / HABITS_PATH_REL


# Default Today template — kept verbatim from BrunOS/Memory/HABITS.md so the
# auto-detected hints stay in sync after every reset.
TODAY_TEMPLATE = """## Today

**Date:** {date}

- [ ] **Sales-AI work (Protostack future / Vertik now)** — one customer-facing or production-grade-code action today
  - *Auto-detected:* ClickUp task in Vertik or Protostack list moved to Done, OR file in `clients/` edited today
- [ ] **Lisa freelance** — one delivery-side action on something with Lisa
  - *Auto-detected:* ClickUp task in Protostack list assigned to me with Lisa-related work moved to Done
- [ ] **AI engineering learning** — 30 min reading or one experiment
  - *Auto-detected:* new file in `research/`, OR GitHub commit on a learning-tagged repo today
- [ ] **Health** — one intentional action (movement, food, sleep prep)
  - *Self-reported only* — no auto-detection
- [ ] **Content** — ship one piece this week (cumulative, not daily)
  - *Auto-detected weekly:* new file in `content/`, OR evidence of a published post via RSS

## Notes for today

*Heartbeat or Bruno can drop short notes here — what got skipped, what blocker came up, etc. Cleared at next 08:00 reset.*

-

"""


def _extract_pillar_states(today_block: str) -> dict[str, bool]:
    """Parse the five pillar checkbox states out of a Today block."""
    states = {k: False for k in PILLAR_KEYS}
    for m in PILLAR_LINE_RE.finditer(today_block):
        label = m.group("label").strip()
        checked = m.group("box") == "x"
        for key, needle in PILLAR_LABELS.items():
            if needle in label:
                states[key] = checked
                break
    return states


def _archive_line(prev_date: str, states: dict[str, bool]) -> str:
    parts = [f"{k} {'✓' if states[k] else '✗'}" for k in PILLAR_KEYS]
    return f"- {prev_date}: " + ", ".join(parts)


def _split_sections(text: str) -> tuple[str, str, str, str]:
    """Return (prelude, today_block, notes_block, history_block) — stripped of headers."""
    today_m = TODAY_HEADER_RE.search(text)
    notes_m = NOTES_HEADER_RE.search(text)
    history_m = HISTORY_HEADER_RE.search(text)
    if not (today_m and notes_m and history_m):
        # Unexpected shape — caller falls back to leaving the file untouched.
        return text, "", "", ""
    prelude = text[: today_m.start()]
    today_block = text[today_m.end() : notes_m.start()]
    notes_block = text[notes_m.end() : history_m.start()]
    history_block = text[history_m.end() :]
    return prelude, today_block, notes_block, history_block


def reset_for_today_if_needed() -> bool:
    """Archive yesterday's Today section and create a fresh one if Date != today.

    Returns True iff a reset happened.
    """
    path = _habits_path()
    text = path.read_text(encoding="utf-8")
    today_str = _today_str()

    date_match = DATE_LINE_RE.search(text)
    if date_match and date_match.group(1).strip() == today_str:
        return False

    prelude, today_block, _notes_block, history_block = _split_sections(text)
    if not today_block:
        sys.stderr.write("[habits] HABITS.md unrecognized shape — skipping reset\n")
        return False

    prev_date = (
        date_match.group(1).strip() if date_match else "unknown"
    )
    if prev_date == "_Auto-set by heartbeat at 08:00 BRT_":
        prev_date = "unknown"

    states = _extract_pillar_states(today_block)
    archive = _archive_line(prev_date, states)

    if HISTORY_PLACEHOLDER_RE.search(history_block):
        new_history = HISTORY_PLACEHOLDER_RE.sub("", history_block)
        new_history = new_history.lstrip("\n")
        new_history_block = "\n" + archive + "\n" + new_history
    else:
        # Insert the archive line at the top of History (newest first).
        leading_blanks = ""
        rest = history_block
        m = re.match(r"\A(\s*)", history_block)
        if m:
            leading_blanks = m.group(1)
            rest = history_block[m.end() :]
        new_history_block = leading_blanks + archive + "\n" + rest

    new_today_section = TODAY_TEMPLATE.format(date=today_str)
    new_text = (
        prelude
        + new_today_section
        + "## History\n"
        + new_history_block
    )

    with file_lock(path):
        atomic_write(path, new_text)
    return True


def unchecked_pillars() -> list[str]:
    """Return list of pillar keys whose checkbox is `- [ ]` in HABITS.md."""
    text = _habits_path().read_text(encoding="utf-8")
    _, today_block, _, _ = _split_sections(text)
    if not today_block:
        return []
    states = _extract_pillar_states(today_block)
    return [k for k, v in states.items() if not v]


def evening_nudge_due(now_dt: datetime) -> bool:
    """True iff now is in the 18:00–19:00 BRT window on a weekday (Mon–Fri)."""
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=BRT)
    local = now_dt.astimezone(BRT)
    if local.weekday() >= 5:  # Sat=5, Sun=6
        return False
    return local.hour == 18


def _vault_paths_modified_today(now_dt: datetime, rel: str) -> bool:
    """True iff any *.md under vault/Memory/<rel> has mtime within today (BRT)."""
    base = vault_path() / "Memory" / rel
    if not base.is_dir():
        return False
    start_today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    for p in base.rglob("*.md"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=BRT)
        except OSError:
            continue
        if mtime >= start_today:
            return True
    return False


def _commits_since(prev_snapshot: dict, current_snapshot: dict) -> list[dict]:
    """Commits in current_snapshot not present in prev_snapshot."""
    prev = {(c.get("repo"), c.get("sha")) for c in (prev_snapshot.get("github_commits") or [])}
    return [
        c for c in (current_snapshot.get("github_commits") or [])
        if (c.get("repo"), c.get("sha")) not in prev
    ]


def _clickup_done_transitions(prev_snapshot: dict, current_snapshot: dict) -> list[dict]:
    """ClickUp tasks whose status flipped to a 'done'-ish value since prev tick.

    Approximates "moved to Done" — Phase 4 doesn't expose a completion-event stream.
    """
    prev_by_id: dict[tuple[str, str], str] = {}
    for key in ("clickup_overdue", "clickup_today"):
        for t in prev_snapshot.get(key) or []:
            prev_by_id[(t["workspace"], t["id"])] = (t.get("status") or "").lower()

    done_keywords = ("done", "complete", "closed", "shipped")
    transitions: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for key in ("clickup_overdue", "clickup_today"):
        for t in current_snapshot.get(key) or []:
            ident = (t["workspace"], t["id"])
            if ident in seen:
                continue
            cur = (t.get("status") or "").lower()
            prev = prev_by_id.get(ident)
            if prev is None:
                continue
            if prev != cur and any(k in cur for k in done_keywords):
                transitions.append({**t, "_prev_status": prev})
                seen.add(ident)
    return transitions


def detect_signals(snapshot: dict, prev_snapshot: dict) -> dict[str, bool]:
    """Per-pillar signal: True if today's snapshot has evidence pillar is satisfied.

    Cold-start (prev_snapshot empty) → all signals False (no transitions to detect).
    `health` is never auto-detected.
    """
    now = now_brt()
    signals = {k: False for k in PILLAR_KEYS}

    if not prev_snapshot:
        # File-mtime signals (sales-ai/ai-learning/content) still work without a
        # prior snapshot, so detect those even on cold start.
        prev_snapshot = {}

    transitions = _clickup_done_transitions(prev_snapshot, snapshot)

    for t in transitions:
        ws = (t.get("workspace") or "").lower()
        if ws in ("vertik", "protostack"):
            signals["sales-ai"] = True
        if ws == "protostack":
            # Heuristic: any Protostack done task counts as Lisa-side activity.
            # Real "with Lisa" detection would need task assignees.
            name = (t.get("name") or "").lower()
            if "lisa" in name or ws == "protostack":
                signals["lisa-freelance"] = True

    if _vault_paths_modified_today(now, "clients"):
        signals["sales-ai"] = True
    if _vault_paths_modified_today(now, "research"):
        signals["ai-learning"] = True
    if _vault_paths_modified_today(now, "content"):
        signals["content"] = True

    # Commits today on any repo are a weak ai-learning signal in cold-start mode.
    new_commits = _commits_since(prev_snapshot, snapshot)
    if new_commits:
        signals["ai-learning"] = True

    return signals
