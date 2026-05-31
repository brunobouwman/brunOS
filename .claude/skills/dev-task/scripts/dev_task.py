#!/usr/bin/env python3
"""dev-task orchestrator CLI + run manifest — the deterministic spine + monitoring.

Owns everything that MUST be deterministic and observable in the autonomous-dev
skill, so the reasoning layer (run_stage.py) only has to think:

- target resolution + HARD GUARD (never the BrunOS prod code repo or the vault)
- git worktree isolation (branch off main; main checkout is never touched)
- a per-run MANIFEST  (.claude/data/state/dev-task/runs/<run_id>.json)  — the
  durable, queryable record of every run: stages, timestamps, plan, PR, errors
- a per-run TRACE LOG (.claude/data/state/dev-task/runs/<run_id>.log)   — the
  full stdout/stderr of the detached worker, so reliability can be traced after
- dispatch of the detached plan/execute worker (run_stage.py), resource-capped
- status / list  — phone-friendly monitoring you can ask the bot for
- best-effort Slack thread notifications

This module never calls the Claude SDK. The heavy reasoning (prime, plan-feature,
execute) lives in run_stage.py, spawned detached so the chat daemon stays light.

Schema version: 1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import (  # noqa: E402
    STATE_DIR,
    _resolve_uv,
    _ts_brt,
    file_lock,
    load_env,
    load_state,
    now_brt,
    save_state,
    vault_path,
)

SCHEMA_VERSION = 1
DEVTASK_DIR = STATE_DIR / "dev-task"
RUNS_DIR = DEVTASK_DIR / "runs"
REPOS_REGISTRY = DEVTASK_DIR / "repos.json"  # optional {alias: abs_path}

WORKER = Path(__file__).resolve().parent / "run_stage.py"

# Stage lifecycle (also the set of valid `stage` values in a manifest).
STAGES = (
    "resolved",          # target guarded, worktree created
    "planning",          # plan worker dispatched
    "awaiting_approval", # plan written, Canvas/summary posted, waiting for "go"
    "executing",         # execute worker dispatched
    "verifying",         # tests + pre-PR checklist running
    "pr_open",           # draft PR opened (terminal-success)
    "failed",            # any stage errored (terminal-fail)
    "aborted",           # user aborted (terminal)
)
_STAGE_EMOJI = {
    "resolved": "🌱", "planning": "🧭", "awaiting_approval": "⏸️",
    "executing": "🚀", "verifying": "🧪", "pr_open": "✅",
    "failed": "❌", "aborted": "🛑",
}


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #
class GuardError(RuntimeError):
    """Raised when a target resolves to a forbidden repo (BrunOS / vault)."""


class ResolveError(RuntimeError):
    """Raised when a target can't be resolved to an existing git repo."""


# --------------------------------------------------------------------------- #
# git helpers
# --------------------------------------------------------------------------- #
def _git(repo: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=check,
    )


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists() and _git(path, "rev-parse", "--git-dir").returncode == 0


def _base_ref(repo: Path) -> str:
    """Best branch to fork from: origin/main → main → master → HEAD."""
    for ref in ("origin/main", "main", "origin/master", "master"):
        if _git(repo, "rev-parse", "--verify", "--quiet", ref).returncode == 0:
            return ref
    return "HEAD"


# --------------------------------------------------------------------------- #
# target resolution + the HARD GUARD (the whole reason this skill is safe)
# --------------------------------------------------------------------------- #
def _forbidden_roots() -> list[Path]:
    roots = [REPO_ROOT.resolve()]
    try:
        roots.append(vault_path())
    except Exception:
        pass
    return roots


def _load_registry() -> dict[str, str]:
    reg = load_state(REPOS_REGISTRY, default={})
    return reg if isinstance(reg, dict) else {}


def resolve_target(repo_arg: str) -> tuple[str, Path]:
    """Resolve an alias OR absolute path to a guarded, existing git repo.

    Returns (slug, resolved_path). Raises ResolveError / GuardError.
    The GUARD is the point of this skill: we REFUSE to operate on the BrunOS
    prod code repo (its read-only code-sync consumer dies on divergence) or the
    vault. Project repos only.
    """
    if not repo_arg or not repo_arg.strip():
        raise ResolveError("no repo given")
    raw = repo_arg.strip()

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute() or not candidate.exists():
        # try the alias registry
        reg = _load_registry()
        if raw in reg:
            candidate = Path(reg[raw]).expanduser()
        else:
            raise ResolveError(
                f"'{raw}' is not an existing absolute path and not a known alias. "
                f"Pass an absolute repo path, or add it to {REPOS_REGISTRY} "
                f'as {{"{raw}": "/abs/path/to/repo"}}.'
            )

    path = candidate.resolve()
    if not path.exists():
        raise ResolveError(f"resolved path does not exist: {path}")
    if not _is_git_repo(path):
        raise ResolveError(f"not a git repo (no usable .git): {path}")

    # THE GUARD — refuse BrunOS code repo / vault and anything inside them.
    for forbidden in _forbidden_roots():
        try:
            if path == forbidden or forbidden in path.parents or path in forbidden.parents:
                raise GuardError(
                    f"REFUSED: {path} is (inside) a protected root ({forbidden}). "
                    "dev-task never branches/commits in the BrunOS code repo or the "
                    "vault — that's the incident this skill exists to prevent. "
                    "Target a project repo instead."
                )
        except GuardError:
            raise
        except Exception:
            continue

    slug = _slugify(path.name)
    return slug, path


# --------------------------------------------------------------------------- #
# slugs / ids
# --------------------------------------------------------------------------- #
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, *, max_words: int = 6) -> str:
    words = _SLUG_RE.sub("-", text.strip().lower()).strip("-").split("-")
    words = [w for w in words if w][:max_words]
    return "-".join(words) or "task"


def new_run_id() -> str:
    return f"dt-{now_brt().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


# --------------------------------------------------------------------------- #
# manifest CRUD
# --------------------------------------------------------------------------- #
def _manifest_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


def log_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.log"


def load_manifest(run_id: str) -> dict | None:
    m = load_state(_manifest_path(run_id), default=None)
    return m if isinstance(m, dict) else None


def save_manifest(m: dict) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    m["updated"] = _ts_brt()
    save_state(_manifest_path(m["run_id"]), m)


def record_stage(run_id: str, stage: str, *, note: str | None = None, **fields) -> dict:
    """Advance a run's stage, append to its timeline, set extra fields. Locked."""
    with file_lock(_manifest_path(run_id)):
        m = load_manifest(run_id)
        if m is None:
            raise ResolveError(f"unknown run_id: {run_id}")
        m["stage"] = stage
        m.setdefault("stages", []).append(
            {"stage": stage, "ts": _ts_brt(), "note": note}
        )
        for k, v in fields.items():
            m[k] = v
        save_manifest(m)
        return m


def all_runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    out = []
    for p in RUNS_DIR.glob("dt-*.json"):
        m = load_state(p, default=None)
        if isinstance(m, dict) and m.get("run_id"):
            out.append(m)
    out.sort(key=lambda m: m.get("created", ""), reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Slack notify (best-effort — never raises)
# --------------------------------------------------------------------------- #
def notify(m: dict, text: str) -> None:
    channel = m.get("channel")
    thread = m.get("thread_ts")
    if not channel:
        return
    try:
        load_env()
        from integrations import slack
        slack.send_message(slack._client(), channel, text, thread_ts=thread)
    except Exception as e:  # noqa: BLE001 — observability must not crash a run
        print(f"[dev-task] slack notify failed: {type(e).__name__}: {e}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# worktree
# --------------------------------------------------------------------------- #
def create_worktree(repo: Path, slug: str, run_id: str) -> tuple[Path, str]:
    """Create an isolated worktree off the base branch. Returns (wt_path, branch)."""
    branch = f"dev-task/{slug}-{run_id.split('-')[-1]}"
    wt = repo.parent / f"{repo.name}-dt-{slug}-{run_id.split('-')[-1]}"
    _git(repo, "fetch", "origin", "--quiet")  # best-effort
    base = _base_ref(repo)
    res = _git(repo, "worktree", "add", "-b", branch, str(wt), base)
    if res.returncode != 0:
        raise ResolveError(f"worktree add failed: {res.stderr.strip() or res.stdout.strip()}")
    return wt, branch


def remove_worktree(repo: Path, wt: str) -> None:
    _git(repo, "worktree", "remove", "--force", wt)


# --------------------------------------------------------------------------- #
# dispatch the detached worker (resource-capped, survives the chat session)
# --------------------------------------------------------------------------- #
def dispatch_worker(run_id: str, stage: str) -> None:
    """Spawn run_stage.py detached + nice'd; its output streams to the trace log.

    nice/ionice cap CPU+IO so a runaway build can't starve Lisa or the WebSocket
    on the shared box (we are NOT bumping resources yet — see the design doc).
    """
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    lp = log_path(run_id)
    uv = _resolve_uv()
    if uv:
        cmd = [uv, "run", "--project", str(REPO_ROOT), "python", str(WORKER), run_id, stage]
    else:
        venv = REPO_ROOT / ".venv" / "bin" / "python"
        cmd = [str(venv) if venv.exists() else sys.executable, str(WORKER), run_id, stage]
    # Best-effort niceness wrapper (Linux). Skipped cleanly if `nice` is absent.
    prefix: list[str] = []
    if subprocess.run(["which", "nice"], capture_output=True).returncode == 0:
        prefix = ["nice", "-n", "15"]
        if subprocess.run(["which", "ionice"], capture_output=True).returncode == 0:
            prefix += ["ionice", "-c3"]
    logf = open(lp, "a", encoding="utf-8")  # noqa: SIM115 — handed to child
    logf.write(f"\n===== dispatch {stage} @ {_ts_brt()} =====\n")
    logf.flush()
    subprocess.Popen(
        prefix + cmd,
        stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True, cwd=str(REPO_ROOT),
    )


# --------------------------------------------------------------------------- #
# CLI handlers
# --------------------------------------------------------------------------- #
def cmd_start(args: argparse.Namespace) -> int:
    try:
        slug_repo, repo_path = resolve_target(args.repo)
    except (GuardError, ResolveError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2

    slug = _slugify(args.slug) if args.slug else _slugify(args.context)
    run_id = new_run_id()
    try:
        wt, branch = create_worktree(repo_path, slug, run_id)
    except ResolveError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1

    m = {
        "_schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created": _ts_brt(),
        "updated": _ts_brt(),
        "repo": slug_repo,
        "repo_path": str(repo_path),
        "worktree": str(wt),
        "branch": branch,
        "slug": slug,
        "source": args.source or "freeform",
        "task_summary": (args.context or "").strip()[:200],
        "context": (args.context or "").strip(),
        "stage": "resolved",
        "stages": [{"stage": "resolved", "ts": _ts_brt(), "note": f"worktree {wt}"}],
        "plan_path": None,
        "plan_text": None,
        "canvas_url": None,
        "pr_url": None,
        "test_summary": None,
        "error": None,
        "channel": args.channel,
        "thread_ts": args.thread,
        "log_path": str(log_path(run_id)),
        "auto_approve": bool(args.auto_approve),
    }
    save_manifest(m)
    record_stage(run_id, "planning", note="plan worker dispatched")
    dispatch_worker(run_id, "plan")
    print(json.dumps({"ok": True, "run_id": run_id, "repo": slug_repo,
                      "branch": branch, "worktree": str(wt), "stage": "planning"}))
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    m = _find_run(args)
    if m is None:
        print(json.dumps({"ok": False, "error": "no matching run"}))
        return 2
    if m["stage"] != "awaiting_approval":
        print(json.dumps({"ok": False, "run_id": m["run_id"], "stage": m["stage"],
                          "error": f"run is '{m['stage']}', not awaiting_approval"}))
        return 2
    record_stage(m["run_id"], "executing", note="approved → execute dispatched")
    dispatch_worker(m["run_id"], "execute")
    print(json.dumps({"ok": True, "run_id": m["run_id"], "stage": "executing"}))
    return 0


def cmd_abort(args: argparse.Namespace) -> int:
    m = _find_run(args)
    if m is None:
        print(json.dumps({"ok": False, "error": "no matching run"}))
        return 2
    if args.cleanup and m.get("worktree") and m.get("repo_path"):
        try:
            remove_worktree(Path(m["repo_path"]), m["worktree"])
        except Exception as e:  # noqa: BLE001
            print(f"[dev-task] worktree cleanup failed: {e}", file=sys.stderr)
    record_stage(m["run_id"], "aborted", note="aborted by user")
    print(json.dumps({"ok": True, "run_id": m["run_id"], "stage": "aborted"}))
    return 0


def _find_run(args: argparse.Namespace) -> dict | None:
    if getattr(args, "run", None):
        return load_manifest(args.run)
    if getattr(args, "thread", None):
        runs = [m for m in all_runs() if m.get("thread_ts") == args.thread]
        # Prefer an actionable (non-terminal) run in this thread, newest first.
        live = [m for m in runs if m.get("stage") not in ("pr_open", "failed", "aborted")]
        return (live or runs or [None])[0]
    return None


def _fmt_age(created: str) -> str:
    try:
        from datetime import datetime
        delta = now_brt() - datetime.fromisoformat(created)
        mins = int(delta.total_seconds() // 60)
        if mins < 60:
            return f"{mins}m"
        if mins < 1440:
            return f"{mins // 60}h{mins % 60:02d}m"
        return f"{mins // 1440}d"
    except Exception:
        return "?"


def cmd_status(args: argparse.Namespace) -> int:
    if getattr(args, "run", None):
        m = load_manifest(args.run)
        if m is None:
            print(f"no such run: {args.run}")
            return 2
        if args.json:
            print(json.dumps(m, indent=2, ensure_ascii=False))
            return 0
        print(_status_detail(m))
        return 0

    runs = all_runs()
    if args.active:
        runs = [m for m in runs if m.get("stage") not in ("pr_open", "failed", "aborted")]
    runs = runs[: args.limit]
    if args.json:
        print(json.dumps(runs, indent=2, ensure_ascii=False))
        return 0
    if not runs:
        print("No dev-task runs yet.")
        return 0
    lines = ["*dev-task runs*"]
    for m in runs:
        emoji = _STAGE_EMOJI.get(m.get("stage", ""), "•")
        pr = f" — <{m['pr_url']}|PR>" if m.get("pr_url") else ""
        lines.append(
            f"{emoji} `{m['run_id']}` · {m.get('repo','?')} · {m.get('stage','?')} "
            f"· {_fmt_age(m.get('created',''))} ago · _{m.get('task_summary','')[:60]}_{pr}"
        )
    print("\n".join(lines))
    return 0


def _status_detail(m: dict) -> str:
    lines = [
        f"{_STAGE_EMOJI.get(m.get('stage',''),'•')} *{m['run_id']}* — {m.get('stage','?')}",
        f"repo: {m.get('repo')} ({m.get('repo_path')})",
        f"branch: {m.get('branch')}   worktree: {m.get('worktree')}",
        f"task: {m.get('task_summary')}",
    ]
    if m.get("plan_path"):
        lines.append(f"plan: {m['plan_path']}")
    if m.get("pr_url"):
        lines.append(f"PR: {m['pr_url']}")
    if m.get("test_summary"):
        lines.append(f"tests: {m['test_summary']}")
    if m.get("error"):
        lines.append(f"error: {m['error']}")
    lines.append(f"trace log: {m.get('log_path')}")
    lines.append("timeline:")
    for s in m.get("stages", []):
        note = f" — {s['note']}" if s.get("note") else ""
        lines.append(f"  · {s['ts']}  {s['stage']}{note}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="dev_task", description="autonomous-dev orchestrator + monitor")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("start", help="resolve+guard target, make worktree, dispatch planning")
    ps.add_argument("--repo", required=True, help="absolute repo path OR alias in repos.json")
    ps.add_argument("--context", required=True, help="feature request / task description")
    ps.add_argument("--slug", default=None, help="optional explicit feature slug")
    ps.add_argument("--source", default=None, help="e.g. clickup:<task_id> (provenance)")
    ps.add_argument("--channel", default=None, help="Slack channel id for notifications")
    ps.add_argument("--thread", default=None, help="Slack thread ts for notifications")
    ps.add_argument("--auto-approve", action="store_true",
                    help="skip the approval gate → execute right after planning")
    ps.set_defaults(_h=cmd_start)

    pa = sub.add_parser("approve", help="approve a plan → dispatch execute")
    g = pa.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", help="run_id")
    g.add_argument("--thread", help="Slack thread ts (finds the awaiting run)")
    pa.set_defaults(_h=cmd_approve)

    pab = sub.add_parser("abort", help="abort a run (optionally remove its worktree)")
    gb = pab.add_mutually_exclusive_group(required=True)
    gb.add_argument("--run")
    gb.add_argument("--thread")
    pab.add_argument("--cleanup", action="store_true", help="also remove the worktree")
    pab.set_defaults(_h=cmd_abort)

    pst = sub.add_parser("status", help="list runs / show one run (phone-friendly)")
    pst.add_argument("--run", default=None, help="show one run's full detail + timeline")
    pst.add_argument("--active", action="store_true", help="only non-terminal runs")
    pst.add_argument("--limit", type=int, default=10)
    pst.add_argument("--json", action="store_true")
    pst.set_defaults(_h=cmd_status)

    args = p.parse_args(argv[1:])
    return args._h(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
