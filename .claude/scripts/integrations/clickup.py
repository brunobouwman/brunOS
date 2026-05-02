"""ClickUp integration: cross-list overdue/today queries across multiple workspaces.

Token: pk_… at CLICKUP_API_TOKEN (user-scoped — one token covers every workspace
the user is a member of).

Workspaces: CLICKUP_WORKSPACES env, format `name:id,name:id`. Reads default to
all configured workspaces (results tagged); writes require explicit --workspace.

GOTCHA: ClickUp uses Unix MILLISECONDS, not seconds. Forgetting `* 1000`
returns [] because everything looks 50 years in the future.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import now_brt, with_retry  # noqa: E402

NAME = "clickup"
BASE_URL = "https://api.clickup.com/api/v2"


@dataclass(frozen=True)
class Task:
    workspace: str
    list_id: str
    list_name: str
    id: str
    name: str
    status: str
    due_date_ms: int | None
    url: str
    assignees: tuple[str, ...]


def _token() -> str:
    t = os.environ.get("CLICKUP_API_TOKEN", "").strip()
    if not t:
        raise RuntimeError("CLICKUP_API_TOKEN not set in environment (.claude/.env)")
    return t


def _workspaces() -> dict[str, str]:
    raw = os.environ.get("CLICKUP_WORKSPACES", "").strip()
    if not raw:
        raise RuntimeError(
            "CLICKUP_WORKSPACES not set in environment (.claude/.env). "
            "Format: name:team_id,name:team_id"
        )
    out: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise RuntimeError(f"Malformed CLICKUP_WORKSPACES entry: {part!r}")
        name, _, team_id = part.partition(":")
        name = name.strip()
        team_id = team_id.strip()
        if not name or not team_id:
            raise RuntimeError(f"Malformed CLICKUP_WORKSPACES entry: {part!r}")
        out[name] = team_id
    return out


def _scope_workspaces(workspace: str | None) -> dict[str, str]:
    all_ws = _workspaces()
    if workspace is None:
        return all_ws
    if workspace not in all_ws:
        raise RuntimeError(
            f"Workspace {workspace!r} not configured. Known: {sorted(all_ws)}"
        )
    return {workspace: all_ws[workspace]}


def _headers() -> dict[str, str]:
    return {"Authorization": _token(), "Content-Type": "application/json"}


def _request(method: str, path: str, *, params=None, json_body=None) -> dict:
    url = f"{BASE_URL}/{path.lstrip('/')}"

    def call() -> dict:
        r = requests.request(
            method,
            url,
            headers=_headers(),
            params=params,
            json=json_body,
            timeout=30,
        )
        remaining = r.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                if int(remaining) < 5:
                    time.sleep(5)
            except ValueError:
                pass
        if r.status_code >= 400:
            err = requests.HTTPError(
                f"ClickUp {method} {path} → {r.status_code}: {r.text[:200]}"
            )
            err.response = r
            err.status_code = r.status_code
            raise err
        return r.json() if r.content else {}

    return with_retry(call)


def _get(path: str, **params) -> dict:
    return _request("GET", path, params=params)


def _post(path: str, json_body: dict) -> dict:
    return _request("POST", path, json_body=json_body)


def _put(path: str, json_body: dict) -> dict:
    return _request("PUT", path, json_body=json_body)


def _brt_day_window_ms() -> tuple[int, int]:
    now = now_brt()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _to_task(workspace: str, raw: dict) -> Task:
    lst = raw.get("list") or {}
    due = raw.get("due_date")
    try:
        due_ms = int(due) if due is not None else None
    except (TypeError, ValueError):
        due_ms = None
    return Task(
        workspace=workspace,
        list_id=str(lst.get("id", "")),
        list_name=str(lst.get("name", "")),
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        status=str((raw.get("status") or {}).get("status", "")),
        due_date_ms=due_ms,
        url=str(raw.get("url", "")),
        assignees=tuple(a.get("username", "") for a in raw.get("assignees", [])),
    )


def _team_tasks(team_id: str, **filters) -> list[dict]:
    """Cross-list query under /team/{team_id}/task with paging."""
    out: list[dict] = []
    page = 0
    while True:
        params = {"page": page, "include_closed": "false", "subtasks": "true"}
        params.update({k: v for k, v in filters.items() if v is not None})
        resp = _get(f"team/{team_id}/task", **params)
        tasks = resp.get("tasks", [])
        out.extend(tasks)
        if len(tasks) < 100:
            break
        page += 1
        if page > 20:
            break
    return out


def overdue(workspace: str | None = None) -> list[Task]:
    now_ms = int(now_brt().timestamp() * 1000)
    out: list[Task] = []
    for ws_name, team_id in _scope_workspaces(workspace).items():
        try:
            raw_tasks = _team_tasks(team_id, due_date_lt=now_ms)
        except Exception as e:
            print(f"[clickup] {ws_name} overdue failed: {e}", file=sys.stderr)
            continue
        out.extend(_to_task(ws_name, t) for t in raw_tasks)
    return out


def due_today(workspace: str | None = None) -> list[Task]:
    start_ms, end_ms = _brt_day_window_ms()
    out: list[Task] = []
    for ws_name, team_id in _scope_workspaces(workspace).items():
        try:
            raw_tasks = _team_tasks(team_id, due_date_gt=start_ms, due_date_lt=end_ms)
        except Exception as e:
            print(f"[clickup] {ws_name} today failed: {e}", file=sys.stderr)
            continue
        out.extend(_to_task(ws_name, t) for t in raw_tasks)
    return out


def update_status(task_id: str, new_status: str) -> Task:
    raw = _get(f"task/{task_id}")
    list_id = (raw.get("list") or {}).get("id")
    if not list_id:
        raise RuntimeError(f"Task {task_id} has no list — cannot validate status")
    list_info = _get(f"list/{list_id}")
    valid = [s.get("status", "") for s in list_info.get("statuses", [])]
    canonical = next(
        (s for s in valid if s.lower() == new_status.lower()), None
    )
    if not canonical:
        raise RuntimeError(
            f"Status {new_status!r} not in list config. Valid: {valid}"
        )
    _put(f"task/{task_id}", {"status": canonical})
    workspace_for_task = "?"
    for ws_name, team_id in _workspaces().items():
        if (raw.get("team_id") or "") == team_id:
            workspace_for_task = ws_name
            break
    return _to_task(workspace_for_task, {**raw, "status": {"status": canonical}})


def create_task(
    workspace: str,
    list_id: str,
    name: str,
    description: str | None = None,
    due_date_ms: int | None = None,
    due_date_time: bool = False,
) -> Task:
    body: dict = {"name": name}
    if description is not None:
        body["description"] = description
    if due_date_ms is not None:
        body["due_date"] = due_date_ms
        body["due_date_time"] = due_date_time
    raw = _post(f"list/{list_id}/task", body)
    return _to_task(workspace, raw)


def format_for_context(tasks: list[Task]) -> str:
    if not tasks:
        return "_No ClickUp tasks._\n"
    by_ws: dict[str, list[Task]] = {}
    for t in tasks:
        by_ws.setdefault(t.workspace, []).append(t)
    lines = ["### ClickUp", ""]
    for ws, items in by_ws.items():
        lines.append(f"**{ws}** ({len(items)})")
        for t in items:
            due = ""
            if t.due_date_ms:
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(t.due_date_ms / 1000, tz=timezone.utc)
                due = f" (due: {dt.date().isoformat()})"
            lines.append(f"- [{t.status}] {t.name}{due} — {t.list_name} — {t.url}")
        lines.append("")
    return "\n".join(lines)


# --- CLI ---


def add_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(NAME, help="ClickUp integration")
    sp = p.add_subparsers(dest="cmd", required=True)

    po = sp.add_parser("overdue", help="Tasks past their due date")
    po.add_argument("--workspace", default=None)

    pt = sp.add_parser("today", help="Tasks due today (BRT)")
    pt.add_argument("--workspace", default=None)

    pc = sp.add_parser("create", help="Create a task")
    pc.add_argument("--workspace", required=True)
    pc.add_argument("--list", dest="list_id", required=True)
    pc.add_argument("--name", required=True)
    pc.add_argument("--description", default=None)

    ps = sp.add_parser("status", help="Update task status")
    ps.add_argument("task_id")
    ps.add_argument("new_status")

    p.set_defaults(_handler=cli)


def cli(args: argparse.Namespace) -> int:
    cmd = args.cmd
    if cmd == "overdue":
        tasks = overdue(args.workspace)
        print(format_for_context(tasks))
        return 0
    if cmd == "today":
        tasks = due_today(args.workspace)
        print(format_for_context(tasks))
        return 0
    if cmd == "create":
        t = create_task(args.workspace, args.list_id, args.name, args.description)
        print(f"Created: {t.id} ({t.url})")
        return 0
    if cmd == "status":
        t = update_status(args.task_id, args.new_status)
        print(f"Updated: {t.id} → {t.status}")
        return 0
    return 2
