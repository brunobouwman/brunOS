"""Weekly review draft generator: ClickUp + GitHub + Calendar + daily-log themes → Opus 4.7.

Pulls a structured bundle of past-7-day signal, hands it to Opus 4.7 with the review
template as system prompt, writes a draft to BrunOS/Memory/goals/YYYY-Www-review.md.

Idempotency: a refined review (first non-frontmatter line ≠ the draft marker) is
NOT overwritten unless --force is passed. Re-runs on an unrefined draft overwrite
freely.

Model locked: claude-opus-4-7. Recursion guard mandatory — set CLAUDE_INVOKED_BY
before SDK import.
"""

from __future__ import annotations

import os

os.environ.setdefault("CLAUDE_INVOKED_BY", "weekly-review")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from datetime import datetime, time, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import (  # noqa: E402
    BRT,
    atomic_write,
    load_env,
    now_brt,
    vault_path,
    _ts_brt,
)

load_env()

from integrations import calendar as cal_mod  # noqa: E402
from integrations import clickup as clickup_mod  # noqa: E402
from integrations import github as github_mod  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = SCRIPT_DIR.parent / "references" / "review-template.md"

OPUS_MODEL = "claude-opus-4-7"
DRAFT_MARKER = "_Draft for review — refine before Monday._"
MAX_BUNDLE_CHARS = 30_000

DAILY_DATE_RE = re.compile(r"daily/(\d{4})-(\d{2})-(\d{2})\.md")
WEEK_ARG_RE = re.compile(r"^(\d{4})-W(\d{1,2})$")


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _extract_text(msg) -> str:
    direct = getattr(msg, "text", None)
    if isinstance(direct, str) and direct:
        return direct
    chunks: list[str] = []
    content = getattr(msg, "content", None)
    if content is None:
        return ""
    try:
        iterator = iter(content)
    except TypeError:
        return ""
    for block in iterator:
        t = getattr(block, "text", None)
        if isinstance(t, str) and t:
            chunks.append(t)
    return "\n".join(chunks)


async def _reason(prompt_text: str, *, model: str, system_prompt: str) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        allowed_tools=[],
        setting_sources=None,
        system_prompt=system_prompt,
        max_turns=1,
        model=model,
    )
    parts: list[str] = []
    async for msg in query(prompt=prompt_text, options=options):
        text = _extract_text(msg)
        if text:
            parts.append(text)
    return "".join(parts).strip()


def _resolve_week(week_arg: str | None) -> tuple[int, int, datetime, datetime]:
    """Return (iso_year, iso_week, monday_00:00 BRT, sunday_23:59:59 BRT)."""
    if week_arg:
        m = WEEK_ARG_RE.match(week_arg)
        if not m:
            raise SystemExit(f"--week must be YYYY-Www (got {week_arg!r})")
        year, week = int(m.group(1)), int(m.group(2))
    else:
        cal = now_brt().isocalendar()
        year, week = cal.year, cal.week
    monday_date = datetime.fromisocalendar(year, week, 1)
    sunday_date = datetime.fromisocalendar(year, week, 7)
    monday_brt = datetime.combine(monday_date.date(), time(0, 0, 0), tzinfo=BRT)
    sunday_brt = datetime.combine(sunday_date.date(), time(23, 59, 59), tzinfo=BRT)
    return year, week, monday_brt, sunday_brt


def _gather_clickup() -> str:
    parts = ["### ClickUp"]
    try:
        overdue = clickup_mod.overdue()
        due_today = clickup_mod.due_today()
    except Exception as e:
        return f"### ClickUp\n_Unavailable: {type(e).__name__}: {e}_\n"
    parts.append("")
    parts.append(f"**Overdue ({len(overdue)})**")
    if overdue:
        for t in overdue[:30]:
            parts.append(f"- [{t.workspace}] {t.name} ({t.status}) — {t.url}")
    else:
        parts.append("- (none)")
    parts.append("")
    parts.append(f"**Due today ({len(due_today)})**")
    if due_today:
        for t in due_today[:30]:
            parts.append(f"- [{t.workspace}] {t.name} ({t.status}) — {t.url}")
    else:
        parts.append("- (none)")
    parts.append("")
    parts.append("_Note: ClickUp completion-history is not yet exposed by integrations.clickup; "
                 "rely on GitHub commits + daily-log themes for 'what got done' signal._")
    return "\n".join(parts) + "\n"


def _gather_github() -> str:
    repo = os.environ.get("GITHUB_DEFAULT_REPO", "").strip()
    if not repo:
        return "### GitHub\n_GITHUB_DEFAULT_REPO not set; skipping._\n"
    try:
        g = github_mod._client()
        commits = github_mod.recent_commits(g, repo, days=7)
        prs = github_mod.open_prs(g, repo)
        issues = github_mod.assigned_to_me(g, repo)
    except Exception as e:
        return f"### GitHub\n_Unavailable for {repo}: {type(e).__name__}: {e}_\n"
    parts = [f"### GitHub ({repo})", ""]
    parts.append(f"**Commits in past 7 days ({len(commits)})**")
    if commits:
        for c in commits[:40]:
            parts.append(f"- {c.sha} {c.message} (@{c.author})")
    else:
        parts.append("- (none)")
    parts.append("")
    parts.append(f"**Open PRs ({len(prs)})**")
    if prs:
        for p in prs:
            tag = "draft" if p.draft else "ready"
            parts.append(f"- #{p.number} ({tag}) {p.title} — {p.url}")
    else:
        parts.append("- (none)")
    parts.append("")
    parts.append(f"**Issues assigned to me ({len(issues)})**")
    if issues:
        for i in issues:
            parts.append(f"- #{i.number} {i.title} — {i.url}")
    else:
        parts.append("- (none)")
    return "\n".join(parts) + "\n"


def _gather_calendar(start_dt: datetime, end_dt: datetime) -> str:
    try:
        events = cal_mod.week()
    except Exception as e:
        return f"### Calendar\n_Unavailable: {type(e).__name__}: {e}_\n"
    in_window: list = []
    for ev in events:
        try:
            ev_start = datetime.fromisoformat(ev.start_iso)
        except ValueError:
            continue
        if ev_start.tzinfo is None:
            ev_start = ev_start.replace(tzinfo=BRT)
        if start_dt <= ev_start <= end_dt:
            in_window.append((ev_start, ev))
    in_window.sort(key=lambda t: t[0])
    parts = ["### Calendar", ""]
    parts.append(f"**Events this week ({len(in_window)})**")
    by_day: dict[str, int] = {}
    for ev_start, ev in in_window:
        day = ev_start.strftime("%a %m-%d")
        by_day[day] = by_day.get(day, 0) + 1
    if by_day:
        parts.append("")
        parts.append("Density per day:")
        for day, count in by_day.items():
            parts.append(f"- {day}: {count} event(s)")
        parts.append("")
        parts.append("Sample events:")
        for ev_start, ev in in_window[:25]:
            attendees = f" ({len(ev.attendees)} attendees)" if ev.attendees else ""
            parts.append(f"- {ev_start.strftime('%a %H:%M')} {ev.summary}{attendees}")
    else:
        parts.append("- (no events in window)")
    return "\n".join(parts) + "\n"


def _gather_daily_themes(start_dt: datetime, end_dt: datetime) -> str:
    """memory_search 'themes' across daily logs, post-filter chunks to the target week."""
    search_script = REPO_ROOT / ".claude" / "scripts" / "memory_search.py"
    parts = ["### Daily-log themes", ""]
    try:
        result = subprocess.run(
            [
                sys.executable, str(search_script),
                "themes decisions blockers learnings",
                "--k", "30", "--path-prefix", "daily",
            ],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        parts.append(f"_memory_search failed: {type(e).__name__}: {e}_")
        return "\n".join(parts) + "\n"
    if result.returncode != 0 or not result.stdout.strip():
        parts.append("_memory_search returned no results._")
        return "\n".join(parts) + "\n"
    try:
        hits = json.loads(result.stdout)
    except json.JSONDecodeError:
        parts.append("_memory_search returned non-JSON output._")
        return "\n".join(parts) + "\n"
    if not isinstance(hits, list):
        hits = []

    in_window: list[dict] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        path = hit.get("file_path") or ""
        m = DAILY_DATE_RE.search(path)
        if not m:
            continue
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=BRT)
        except ValueError:
            continue
        if start_dt.date() <= d.date() <= end_dt.date():
            in_window.append(hit)
    parts.append(f"**Chunks from daily logs in window ({len(in_window)})**")
    if not in_window:
        parts.append("- (no matches)")
        return "\n".join(parts) + "\n"
    parts.append("")
    for hit in in_window[:15]:
        path = hit.get("file_path", "")
        snippet = re.sub(r"\s+", " ", hit.get("content", "")).strip()[:300]
        parts.append(f"- `{path}`: {snippet}")
    return "\n".join(parts) + "\n"


def _gather_goals() -> str:
    parts = ["### Active goals", ""]
    base = vault_path() / "Memory" / "goals"
    for fname in ("this_week.md", "this_month.md", "personal_vision.md"):
        path = base / fname
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        m = re.match(r"\A---\n.*?\n---\n", text, re.DOTALL)
        body = text[m.end():] if m else text
        body = body.strip()
        if len(body) > 1500:
            body = body[:1500] + "\n…(truncated)"
        parts.append(f"#### {fname}")
        parts.append(body)
        parts.append("")
    return "\n".join(parts) + "\n"


def _truncate_bundle(sections: list[str], cap: int) -> str:
    """Concatenate sections; if over cap, truncate the (large) themes section first."""
    bundle = "\n\n".join(sections)
    if len(bundle) <= cap:
        return bundle
    # First try: chop the themes section (last entry by convention) to half size.
    if len(sections) >= 1:
        truncated = sections[:-1] + [sections[-1][: max(1000, cap // 4)] + "\n…(truncated)\n"]
        bundle = "\n\n".join(truncated)
    if len(bundle) > cap:
        bundle = bundle[:cap] + "\n…(hard-truncated)\n"
    return bundle


def _build_frontmatter(year: int, week: int) -> str:
    ts = _ts_brt()
    return (
        "---\n"
        "type: goal\n"
        f"created: {ts}\n"
        f"updated: {ts}\n"
        "tags:\n"
        "  - weekly-review\n"
        "status: active\n"
        f"iso_week: {year}-W{week:02d}\n"
        "---\n\n"
    )


def _is_refined(existing_text: str) -> bool:
    """True if the file has been edited past the draft marker."""
    m = re.match(r"\A---\n.*?\n---\n", existing_text, re.DOTALL)
    body = existing_text[m.end():] if m else existing_text
    body_lines = [ln for ln in body.splitlines() if ln.strip()]
    if not body_lines:
        return False
    return body_lines[0].strip() != DRAFT_MARKER


def _build_bundle(start_dt: datetime, end_dt: datetime, year: int, week: int) -> str:
    # TODO(Phase 8): wrap each external section in <external_data> via sanitize.py.
    # ClickUp/GitHub/Calendar payloads are third-party content and a prompt-injection vector.
    header = (
        f"# Weekly review bundle — {year}-W{week:02d}\n"
        f"Window: {start_dt.isoformat()} → {end_dt.isoformat()}\n"
    )
    sections = [
        header,
        _gather_clickup(),
        _gather_github(),
        _gather_calendar(start_dt, end_dt),
        _gather_goals(),
        _gather_daily_themes(start_dt, end_dt),
    ]
    return _truncate_bundle(sections, MAX_BUNDLE_CHARS)


def _run(week_arg: str | None, dry_run: bool, force: bool) -> int:
    _log(f"weekly-review start ({_ts_brt()})")
    year, week, start_dt, end_dt = _resolve_week(week_arg)
    _log(f"  ISO week: {year}-W{week:02d}  ({start_dt.date()} → {end_dt.date()})")

    out_path = vault_path() / "Memory" / "goals" / f"{year}-W{week:02d}-review.md"

    if not dry_run and out_path.exists() and not force:
        existing = out_path.read_text(encoding="utf-8")
        if _is_refined(existing):
            _log(f"refined review exists at {out_path}; pass --force to overwrite. Aborting.")
            return 2

    _log("stage 1: gathering bundle")
    bundle = _build_bundle(start_dt, end_dt, year, week)
    _log(f"  bundle: {len(bundle)} chars")

    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    _log("stage 2: synthesizing via Opus 4.7")
    try:
        synthesis = asyncio.run(_reason(bundle, model=OPUS_MODEL, system_prompt=template))
    except Exception as e:
        _log(f"  Opus call failed: {type(e).__name__}: {e}")
        return 1
    if not synthesis.strip():
        _log("  Opus returned empty output; aborting write")
        return 1

    if DRAFT_MARKER not in synthesis.split("\n", 4)[0]:
        synthesis = f"{DRAFT_MARKER}\n\n{synthesis.lstrip()}"

    content = (
        _build_frontmatter(year, week)
        + f"# Weekly review — {year}-W{week:02d}\n\n"
        + synthesis.strip() + "\n"
    )

    if dry_run:
        sys.stdout.write(content)
        _log(f"dry-run complete (would have written {out_path})")
        return 0

    atomic_write(out_path, content)
    _log(f"wrote review → {out_path} ({len(content)} chars)")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Sunday-evening weekly review draft")
    parser.add_argument("--week", default=None, help="ISO week as YYYY-Www (default: current)")
    parser.add_argument("--dry-run", action="store_true", help="print to stdout, skip vault write")
    parser.add_argument("--force", action="store_true", help="overwrite refined review")
    args = parser.parse_args(argv[1:])
    return _run(week_arg=args.week, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
