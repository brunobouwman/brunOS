---
name: dev-task
description: Autonomous coding task for BrunOS — take a feature/fix/refactor in a PROJECT repo end-to-end (isolate a git worktree → plan → get Bruno's approval → execute in the background → open a DRAFT PR), with full monitoring. Use WHENEVER Bruno asks to build/implement/fix/refactor something in a project repo, to "open a PR for", to "work on a task", OR references a ClickUp task to implement — e.g. "implement this task from ClickUp", "implement CU-1234", "do the ClickUp task about X", "ship the <name> task". In those cases auto-resolve everything yourself (look up ClickUp, default the repo, use the current Slack thread) — do NOT make Bruno specify repo/context/channel. Also use to check on dev work: "dev-task status", "how's that PR", "what's running". NEVER targets the BrunOS code repo or the vault.
---

# dev-task — autonomous dev (plan → approve → execute → draft PR)

Turns a Slack request (often from Bruno's phone) into a draft PR in a **project repo**, with every step recorded so the PR is findable later and the execution is traceable for reliability. You orchestrate in short turns; the heavy work runs **detached in the background** — never block the chat session running it.

## When to use
- Bruno: "build/implement/fix/refactor X in <repo>", "open a PR for …", "work on <ClickUp task>".
- Bruno checks in: "dev-task status", "how's the PR", "what's running on dev".

## The boundary (non-negotiable)
- **Project repos ONLY.** The script HARD-REFUSES the BrunOS code repo and the vault (that read-only checkout is what `code-sync` breaks on — the incident this skill exists to prevent). Don't fight the refusal; it's correct.
- **PRs are DRAFT.** Never merge, never mark ready — that stays Bruno's call (SOUL.md: GitHub PRs are ask-first).

## Auto-resolve — Bruno should NOT have to specify params

When Bruno says something like *"implement this ClickUp task"* / *"implement CU-1234"* / *"do the ClickUp task about the budget bug"*, resolve everything yourself — he gives the task, you fill the rest:

1. **ClickUp lookup (deterministic, no MCP needed):**
   - If he gave an id/URL: `uv run python .claude/scripts/query.py clickup task <id-or-url> --json` → use `name` + `description` as the context, and `--source clickup:<id>`.
   - If he described it instead of giving an id: `query.py clickup today` / `clickup overdue` to find the match; if more than one plausibly fits, ask which (one short question), else proceed.
2. **Target repo — default it, don't ask:** use the default in `.claude/data/state/dev-task/repos.json` / your USER.md default (currently **`brunos-dev`**). Only ask when the default is ambiguous (multiple repos cloned and the task doesn't indicate which).
3. **Channel + thread — fill from the current Slack message automatically** (you already have them; never ask Bruno for these).

Then call `start` (below). The only thing that should ever come from Bruno is the task itself (and occasionally a repo, once more are cloned).

## Flow

All commands: `uv run python .claude/skills/dev-task/scripts/dev_task.py …`

**1. Start a task** (with the auto-resolved values above):

```bash
dev_task.py start --repo <alias-or-abs-path> \
  --context "<ClickUp name + description, or Bruno's words>" \
  --channel <current Slack channel id> --thread <current thread ts> \
  [--source clickup:<task_id>] [--slug short-name]
```

This guards + worktrees the repo and dispatches the **plan** worker in the background. Tell Bruno planning is underway and the plan will be posted here for approval. **Do not** wait/run it inline.

**2. Approval gate.** The plan worker posts a Slack **Canvas** (or a summary + plan if Canvas scope isn't set up) and pauses at `awaiting_approval`. When Bruno replies in the thread:
- On **any new message in a thread**, first check `dev_task.py status --thread <ts>` to see if there's a pending run (the plan worker, not you, advanced its state — always re-read the manifest).
- Bruno approves ("go", "approve", "ship it", "yes") → `dev_task.py approve --thread <ts>`. This dispatches **execute** in the background. Tell him you'll post the PR here when done.
- Bruno says "changes: …" → re-planning isn't automated yet (MVP). `dev_task.py abort --run <id> --cleanup`, then `start` again with the refined context.
- Fully autonomous mode: add `--auto-approve` to `start` to skip the gate (use only for task types Bruno has explicitly trusted).

**3. Execute → draft PR (automatic, background).** The execute worker implements the plan, runs validations, then deterministically verifies → commits → pushes → opens a **draft PR**, and posts the PR link in the thread. On failure it posts the error + keeps the worktree for inspection. You do nothing here except relay if asked.

## Monitoring (what Bruno asks for from his phone)
- `dev_task.py status` — all runs, newest first, with stage + PR link.
- `dev_task.py status --active` — only in-flight runs.
- `dev_task.py status --run <id>` — full timeline + plan path + PR + **trace log path**.
- Durable records (VPS-local): manifest `.claude/data/state/dev-task/runs/<id>.json`, trace log `…/<id>.log` (full agent + build output — this is how we trace reliability).

When Bruno asks "status" / "how's it going", run the relevant `status` command and relay it (it's already Slack-formatted).

## Prereqs / first-run caveats (verify before relying on it)
- **PR creation uses the GitHub FGPAT** (`integrations/github.py` / PyGithub — no `gh` needed). The target repo MUST be in the FGPAT allowlist (fixed at token creation — a new repo means re-issuing the token) and the token needs push rights. The target **repo must already be cloned** on the host running the bot (the VPS); if not, `start` fails with clear guidance — clone it first.
- **Canvas** needs the Slack app's `canvases:write` scope + reinstall. Until then plan delivery falls back to a summary + plan message (still works).
- The background workers run on **bypass-permissions** in the *target* repo, so they're governed by the target repo's hooks/settings, not BrunOS's security hooks. Only point this at trusted repos.
- Resource note: execute runs `nice`/`ionice`-capped (shared box, no resource bump yet). If we hit OOM, that's the signal to bump (per the design doc).

## References
- `references/execution-pitfalls.md` — the pre-PR checklist (grep/check one-liners). The worker enforces a deterministic seed of it (conflict markers, secret hints); the fuller checklist is the human-review companion.
