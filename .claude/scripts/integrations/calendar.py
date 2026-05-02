"""Google Calendar integration: today / week reads against the primary calendar.

Read-only (calendar.events.readonly scope). Times in BRT.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from integrations._google import _service  # noqa: E402
from shared import now_brt  # noqa: E402

NAME = "calendar"


@dataclass(frozen=True)
class Event:
    id: str
    summary: str
    start_iso: str
    end_iso: str
    attendees: tuple[str, ...]
    location: str
    html_link: str


def _svc():
    return _service("calendar", "v3")


def _start_end(ev: dict) -> tuple[str, str]:
    s = ev.get("start") or {}
    e = ev.get("end") or {}
    return (
        s.get("dateTime") or s.get("date") or "",
        e.get("dateTime") or e.get("date") or "",
    )


def _list_window(time_min_iso: str, time_max_iso: str) -> list[Event]:
    svc = _svc()
    resp = svc.events().list(
        calendarId="primary",
        timeMin=time_min_iso,
        timeMax=time_max_iso,
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute()
    out: list[Event] = []
    for ev in resp.get("items", []) or []:
        start_iso, end_iso = _start_end(ev)
        out.append(
            Event(
                id=ev.get("id", ""),
                summary=ev.get("summary", "") or "(no title)",
                start_iso=start_iso,
                end_iso=end_iso,
                attendees=tuple(
                    a.get("email", "") for a in (ev.get("attendees") or [])
                ),
                location=ev.get("location", "") or "",
                html_link=ev.get("htmlLink", "") or "",
            )
        )
    return out


def today() -> list[Event]:
    now = now_brt()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return _list_window(start.isoformat(), end.isoformat())


def week() -> list[Event]:
    now = now_brt()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return _list_window(start.isoformat(), end.isoformat())


def format_for_context(events: list[Event]) -> str:
    if not events:
        return "_No calendar events._\n"
    lines = ["### Calendar", ""]
    for e in events:
        time_part = f"{e.start_iso[:16]}–{e.end_iso[11:16]}" if "T" in e.start_iso else e.start_iso
        loc = f" @ {e.location}" if e.location else ""
        n = len(e.attendees)
        att = f" ({n} attendees)" if n else ""
        lines.append(f"- {time_part}: {e.summary}{loc}{att}")
    return "\n".join(lines) + "\n"


# --- CLI ---


def add_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(NAME, help="Google Calendar integration (read-only)")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("today", help="Events today (BRT)")
    sp.add_parser("week", help="Events for next 7 days (BRT)")
    p.set_defaults(_handler=cli)


def cli(args: argparse.Namespace) -> int:
    cmd = args.cmd
    if cmd == "today":
        print(format_for_context(today()))
        return 0
    if cmd == "week":
        print(format_for_context(week()))
        return 0
    return 2
