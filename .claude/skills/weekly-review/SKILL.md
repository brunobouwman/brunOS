---
name: weekly-review
description: Sunday-evening weekly review draft generator for BrunOS. Use when Bruno asks for the weekly review, this week's recap, the Sunday review, or runs aggregate_week.py. Pulls past-7-day GitHub commits/PRs/issues, current ClickUp state (overdue/due-today), calendar density, daily-log themes (via memory_search), and active goals; passes the bundle to Opus 4.7; writes Memory/goals/YYYY-Www-review.md as a DRAFT for Bruno to refine — never auto-finalizes. Triggers on "weekly review", "this week's recap", "Sunday review", "/weekly-review", planning-cadence prompts.
---

# Weekly Review Skill

Sunday-evening review draft. Pulls real-world signal from ClickUp, GitHub, Calendar, and the daily logs; hands the bundle to Opus 4.7; writes a draft Bruno refines before Monday.

## When to invoke

- Sunday evening (~30-min planning block).
- Mid-week ad-hoc when Bruno wants a recap before a meeting.
- Phase 6's heartbeat will fire this at 19:00 BRT Sundays once it ships.

## How to invoke

```bash
uv run python .claude/skills/weekly-review/scripts/aggregate_week.py
```

Optional flags:
- `--week YYYY-Www` — review a specific ISO week (default: current).
- `--dry-run` — print to stdout, skip vault write.
- `--force` — overwrite a refined review (defeats the in-band protection — see below).

## Output

`BrunOS/Memory/goals/YYYY-Www-review.md` (e.g. `2026-W18-review.md`). Frontmatter `type: goal`, `tags: [weekly-review]`. The first line of the body is always:

```
_Draft for review — refine before Monday._
```

This is the in-band marker the script uses to detect "Bruno hasn't refined this yet" on a re-run. After Bruno edits the file (removing or rewording the marker), `--force` is required to overwrite — prevents accidentally clobbering a refined version.

## What the script gathers

| Source | Function | Window |
|---|---|---|
| GitHub | `recent_commits(days=7)`, `open_prs()`, `assigned_to_me()` | past 7 days (commits) / current state (PRs+issues) |
| ClickUp | `overdue()`, `due_today()` | current state across Vertik + Protostack |
| Calendar | `week()` | current ISO week's events |
| Daily logs | `memory_search "themes" --path-prefix daily --k 30`, then post-filter by week | past 7 days |
| Goals | direct vault read of `goals/this_week.md`, `this_month.md`, `personal_vision.md` | static |

> **Note**: ClickUp's Phase 4 API doesn't yet expose completed-in-range. "What got done" leans on GitHub commits + daily-log themes. Add a ClickUp completion-history function to `integrations/clickup.py` later if Bruno wants tighter task signal.

## Pipeline

1. Resolve target ISO week (Mon 00:00 BRT → Sun 23:59 BRT).
2. Gather data sequentially (each integration uses `with_retry` internally — no extra orchestration needed).
3. Build a structured bundle (~30K char cap; theme section truncated first if over).
4. Single **Opus 4.7** call (`max_turns=1`, `allowed_tools=[]`, `setting_sources=None`). System prompt = `references/review-template.md`.
5. `atomic_write` to `Memory/goals/YYYY-Www-review.md`.

## References

- `${CLAUDE_SKILL_DIR}/references/review-template.md` — section structure + tone the Opus call follows.
