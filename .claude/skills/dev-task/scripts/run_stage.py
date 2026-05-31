#!/usr/bin/env python3
"""dev-task detached worker — runs ONE stage (plan | execute) of a run.

Spawned by dev_task.py `dispatch_worker`, detached + nice'd, with stdout/stderr
streamed to the run's trace log. This is the ONLY part of the skill that calls
the Claude SDK; it runs as its own process in the worktree so the chat daemon
stays responsive and memory-light (the design's "execute is a detached job").

  plan     prime the worktree + write an implementation plan to a known path,
           then post it (Slack Canvas, fallback to summary) for approval.
  execute  implement the approved plan, run validations, then deterministically
           verify → commit → push → open a DRAFT PR. Records the PR url.

Every outcome — success or failure — lands in the manifest and (best-effort) in
the Slack thread. Nothing fails silently; that's the whole point.

Recursion guard: CLAUDE_INVOKED_BY=dev-task set BEFORE importing the SDK.
"""

from __future__ import annotations

import os

os.environ.setdefault("CLAUDE_INVOKED_BY", "dev-task")

import asyncio  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import traceback  # noqa: E402
from pathlib import Path  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[3]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))
sys.path.insert(0, str(SCRIPT_DIR))

from shared import _ts_brt, load_env  # noqa: E402

import dev_task as dt  # noqa: E402

SONNET = "claude-sonnet-4-6"
PLAN_MAX_TURNS = 40
EXECUTE_MAX_TURNS = 80
SLACK_PLAN_PREVIEW = 3500

# Secret / conflict markers we refuse to commit (deterministic pre-PR gate;
# the full grep checklist arrives as references/execution-pitfalls.md).
_CONFLICT_MARKERS = ("<<<<<<< ", "=======\n", ">>>>>>> ")
_SECRET_HINTS = (
    "BEGIN RSA PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY", "BEGIN PRIVATE KEY",
    "aws_secret_access_key", "xoxb-", "xoxp-", "xapp-", "sk-ant-", "sk-proj-",
    "-----BEGIN",
)


def _log(msg: str) -> None:
    print(f"[dev-task {_ts_brt()}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# SDK runner — streams a compact trace to stdout (→ the run log)
# --------------------------------------------------------------------------- #
async def _run_agent(prompt: str, *, cwd: Path, max_turns: int, model: str) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        setting_sources=["project"],   # load the TARGET repo's CLAUDE.md + commands
        permission_mode="bypassPermissions",  # non-interactive autonomy
        cwd=str(cwd),
        max_turns=max_turns,
        model=model,
    )
    parts: list[str] = []
    async for msg in query(prompt=prompt, options=options):
        # Stream a compact trace: assistant text + tool calls.
        content = getattr(msg, "content", None)
        if content is not None:
            try:
                for block in content:
                    t = getattr(block, "text", None)
                    if isinstance(t, str) and t.strip():
                        parts.append(t)
                        _log(f"· {t.strip()[:200]}")
                    name = getattr(block, "name", None)  # ToolUseBlock
                    if name:
                        _log(f"  ⚙ tool: {name}")
            except TypeError:
                pass
        direct = getattr(msg, "text", None)
        if isinstance(direct, str) and direct.strip():
            parts.append(direct)
    return "\n".join(parts).strip()


def _agent(prompt: str, *, cwd: Path, max_turns: int, model: str = SONNET) -> str:
    return asyncio.run(_run_agent(prompt, cwd=cwd, max_turns=max_turns, model=model))


# --------------------------------------------------------------------------- #
# git / gh
# --------------------------------------------------------------------------- #
def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _has_changes(wt: Path) -> bool:
    return bool(_git(wt, "status", "--porcelain").stdout.strip())


def _diff_text(wt: Path) -> str:
    return _git(wt, "diff", "HEAD").stdout + _git(wt, "diff", "--cached").stdout


_REMOTE_RE = __import__("re").compile(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$")


def _remote_owner_repo(wt: Path) -> str | None:
    """owner/name from the worktree's origin remote (handles ssh + https+token)."""
    url = _git(wt, "remote", "get-url", "origin").stdout.strip()
    m = _REMOTE_RE.search(url)
    return m.group(1) if m else None


def _open_draft_pr(owner_repo: str, branch: str, title: str, body: str) -> str:
    """Open a DRAFT PR for an already-pushed branch via the FGPAT (no `gh` dep).

    Falls back to a `[WIP]` regular PR + `draft` label on the private-free 422,
    mirroring integrations.github.open_draft_pr's handling.
    """
    load_env()
    from github import GithubException  # PyGithub (already a dep)
    from integrations import github as gh_mod

    g = gh_mod._client()
    repo = g.get_repo(owner_repo)
    base = repo.default_branch
    try:
        pr = repo.create_pull(title=f"[dev-task] {title}", body=body,
                              head=branch, base=base, draft=True)
    except GithubException as e:
        if getattr(e, "status", None) != 422:
            raise
        pr = repo.create_pull(title=f"[WIP] {title}", body=body,
                             head=branch, base=base, draft=False)
        try:
            pr.add_to_labels("draft")
        except GithubException:
            pass
    return pr.html_url


# --------------------------------------------------------------------------- #
# plan delivery — Slack Canvas, graceful fallback to summary + thread message
# --------------------------------------------------------------------------- #
def _plan_summary(plan_text: str, m: dict) -> str:
    head = []
    for ln in plan_text.splitlines():
        if ln.strip():
            head.append(ln.strip())
        if len(head) >= 12:
            break
    return (
        f"🧭 *Plan ready* for `{m['run_id']}` · {m.get('repo')} · `{m.get('branch')}`\n"
        f"_{m.get('task_summary','')}_\n\n" + "\n".join(head)
    )


def _post_plan(m: dict, plan_text: str) -> str | None:
    """Post the plan for approval. Returns a canvas url if one was created."""
    summary = _plan_summary(plan_text, m)
    approve_line = (
        "\n\n———\nReply *go* to approve → I'll execute and open a draft PR. "
        "Or *changes: …* to revise. (`dev_task status` anytime.)"
    )
    canvas_url = None
    try:
        load_env()
        from integrations import slack
        client = slack._client()
        # Best-effort Slack Canvas (needs canvases:write scope). Fallback below.
        try:
            resp = client.canvases_create(
                title=f"dev-task plan · {m.get('slug','')}",
                document_content={"type": "markdown", "markdown": plan_text[:90000]},
            )
            cid = resp.get("canvas_id") if hasattr(resp, "get") else None
            if cid:
                canvas_url = f"https://slack.com/canvas/{cid}"
        except Exception as e:  # noqa: BLE001 — scope likely missing; fall back
            _log(f"canvas unavailable ({type(e).__name__}: {e}); falling back to summary")

        if canvas_url:
            dt.notify(m, summary + f"\n\n📄 Full plan: {canvas_url}" + approve_line)
        else:
            dt.notify(m, summary + approve_line)
            tail = plan_text if len(plan_text) <= SLACK_PLAN_PREVIEW else (
                plan_text[:SLACK_PLAN_PREVIEW] + "\n…(truncated — full plan: "
                f"{m.get('plan_path')})"
            )
            dt.notify(m, "```\n" + tail + "\n```")
    except Exception as e:  # noqa: BLE001
        _log(f"plan post failed: {type(e).__name__}: {e}")
    return canvas_url


# --------------------------------------------------------------------------- #
# stages
# --------------------------------------------------------------------------- #
def stage_plan(m: dict) -> None:
    wt = Path(m["worktree"])
    plan_path = wt / ".agents" / "plans" / f"{m['run_id']}-{m['slug']}.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)

    prompt = f"""You are BrunOS's autonomous dev agent, working inside a git worktree of \
the `{m['repo']}` project (branch `{m['branch']}`). Your ONLY job in this step is to PLAN \
— do not implement anything.

1. Build full context: read CLAUDE.md / AGENTS.md / README, the directory structure, \
recent git history, entry points, and the files relevant to the task. If this project \
has a planning command (e.g. /core_piv_loop:prime then /core_piv_loop:plan-feature), \
use it.
2. Write a detailed, single-pass-executable implementation plan to EXACTLY this path:
   {plan_path}
   The plan MUST include: problem/goal, the specific files to create/modify (with paths), \
ordered atomic tasks, a testing strategy, and concrete validation commands to run \
(build, lint, tests). Keep it concrete and grounded in the real code you read.
3. Do NOT write any code outside that plan file. Do NOT commit. Stop when the plan is written.

The feature/task to plan:
---
{m['context']}
---
"""
    _log(f"PLAN start — worktree={wt} plan_path={plan_path}")
    out = _agent(prompt, cwd=wt, max_turns=PLAN_MAX_TURNS)
    _log(f"PLAN agent finished ({len(out)} chars of trace text)")

    if not plan_path.exists():
        # fallback: newest plan the agent may have written elsewhere
        candidates = sorted(
            list((wt / ".agents" / "plans").glob("*.md"))
            + list((wt / ".agent" / "plans").glob("*.md")),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if candidates:
            plan_path = candidates[0]
        else:
            raise RuntimeError("plan agent did not produce a plan file")

    plan_text = plan_path.read_text(encoding="utf-8")
    dt.record_stage(m["run_id"], "awaiting_approval", note=f"plan at {plan_path}",
                    plan_path=str(plan_path), plan_text=plan_text[:8000])
    m = dt.load_manifest(m["run_id"])
    canvas = _post_plan(m, plan_text)
    if canvas:
        dt.record_stage(m["run_id"], "awaiting_approval", note="canvas posted",
                        canvas_url=canvas)

    if m.get("auto_approve"):
        _log("auto_approve set → dispatching execute")
        dt.record_stage(m["run_id"], "executing", note="auto-approved")
        dt.dispatch_worker(m["run_id"], "execute")


def stage_execute(m: dict) -> None:
    wt = Path(m["worktree"])
    plan_path = m.get("plan_path")
    if not plan_path or not Path(plan_path).exists():
        raise RuntimeError(f"plan file missing: {plan_path}")

    prompt = f"""You are BrunOS's autonomous dev agent, in a git worktree of `{m['repo']}` \
(branch `{m['branch']}`). Implement the approved plan FULLY.

1. Read the plan at: {plan_path}
2. Implement every task in it, following the project's existing conventions.
3. Run the plan's validation commands (build, lint, tests). Fix failures and re-run \
until they pass. If a command can't run, say so explicitly.
4. Do NOT commit, push, or open a PR — the orchestrator does that deterministically. \
Stop when the code is implemented and validations pass (or you've exhausted reasonable \
attempts). End with a short summary of what you changed and the test results.
"""
    _log(f"EXECUTE start — worktree={wt} plan={plan_path}")
    summary = _agent(prompt, cwd=wt, max_turns=EXECUTE_MAX_TURNS)
    _log("EXECUTE agent finished")

    dt.record_stage(m["run_id"], "verifying", note="agent done; running pre-PR gate")

    if not _has_changes(wt):
        raise RuntimeError("execute produced no changes — nothing to commit")

    # Deterministic pre-PR gate (seed; full grep checklist in references/).
    diff = _diff_text(wt)
    for marker in _CONFLICT_MARKERS:
        if marker in diff:
            raise RuntimeError("unresolved merge-conflict markers in the diff — refusing to PR")
    for hint in _SECRET_HINTS:
        if hint in diff:
            raise RuntimeError(f"possible secret in the diff ({hint!r}) — refusing to PR")

    # commit
    title = f"{m['slug']}: {m.get('task_summary','')}".strip()[:72]
    body_commit = f"{title}\n\nBrunOS dev-task {m['run_id']} (auto-generated; review before merge)."
    if (r := _git(wt, "add", "-A")).returncode != 0:
        raise RuntimeError(f"git add failed: {r.stderr}")
    if (r := _git(wt, "commit", "-m", body_commit)).returncode != 0:
        raise RuntimeError(f"git commit failed: {r.stderr or r.stdout}")

    # push
    if (r := _git(wt, "push", "-u", "origin", m["branch"])).returncode != 0:
        raise RuntimeError(f"git push failed: {r.stderr or r.stdout}")

    # draft PR via the FGPAT (PyGithub) — no `gh` dependency on the host
    owner_repo = _remote_owner_repo(wt)
    if not owner_repo:
        raise RuntimeError("could not parse a github owner/repo from the origin remote")
    pr_body = (
        f"Auto-generated by **BrunOS dev-task** `{m['run_id']}`.\n\n"
        f"**Task:** {m.get('task_summary','')}\n\n"
        f"**Plan:** `{plan_path}` (first commit on this branch).\n\n"
        f"**Agent summary:**\n\n{summary[:4000]}\n\n"
        f"---\n⚠️ Draft — review before merge. Trace log: `{m.get('log_path')}`."
    )
    try:
        pr_url = _open_draft_pr(owner_repo, m["branch"], title, pr_body)
    except Exception as e:  # noqa: BLE001
        # Push succeeded, so the branch is safe even if PR creation failed
        # (FGPAT allowlist gap, etc.) — report honestly, don't pretend success.
        raise RuntimeError(
            f"branch pushed OK to {owner_repo}:{m['branch']} but draft-PR creation "
            f"failed ({type(e).__name__}: {e}). Likely the FGPAT doesn't cover this "
            f"repo (allowlist is fixed at token creation)."
        ) from e

    test_summary = summary[:300]
    dt.record_stage(m["run_id"], "pr_open", note="draft PR opened",
                    pr_url=pr_url, test_summary=test_summary)
    m = dt.load_manifest(m["run_id"])
    dt.notify(m, f"✅ *Draft PR opened* for `{m['run_id']}` ({m['repo']}):\n{pr_url}\n"
                 f"_Review when you're back. `dev_task status --run {m['run_id']}` for the trace._")


# --------------------------------------------------------------------------- #
# entry
# --------------------------------------------------------------------------- #
def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: run_stage.py <run_id> <plan|execute>", file=sys.stderr)
        return 2
    run_id, stage = argv[1], argv[2]
    m = dt.load_manifest(run_id)
    if m is None:
        print(f"unknown run_id: {run_id}", file=sys.stderr)
        return 2
    try:
        if stage == "plan":
            stage_plan(m)
        elif stage == "execute":
            stage_execute(m)
        else:
            print(f"unknown stage: {stage}", file=sys.stderr)
            return 2
        return 0
    except Exception as e:  # noqa: BLE001 — record + alert, never silent
        tb = traceback.format_exc()
        _log(f"STAGE {stage} FAILED: {type(e).__name__}: {e}\n{tb}")
        try:
            dt.record_stage(run_id, "failed", note=f"{stage}: {type(e).__name__}: {e}",
                            error=f"{type(e).__name__}: {e}")
            m = dt.load_manifest(run_id)
            dt.notify(m, f"❌ *dev-task {stage} failed* (`{run_id}`): {type(e).__name__}: {e}\n"
                         f"_Worktree kept for inspection. Trace: `{m.get('log_path')}`._")
        except Exception as e2:  # noqa: BLE001
            _log(f"failure-recording also failed: {e2}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
