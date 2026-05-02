"""Gmail integration: read-only metadata listing.

Uses metadata-format only — full message bodies are NOT fetched in Phase 4.
Phase 6's draft generator pulls full bodies on-demand for messages it intends
to reply to.

NEVER use gmail.send. Scope is hardcoded readonly+modify (label/mark-read) in
bootstrap_google_oauth.py.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from integrations._google import _service  # noqa: E402

NAME = "gmail"


@dataclass(frozen=True)
class EmailHeader:
    id: str
    thread_id: str
    from_addr: str
    subject: str
    date_iso: str
    snippet: str


def _svc():
    return _service("gmail", "v1")


def _header(headers: list[dict], name: str) -> str:
    target = name.lower()
    for h in headers:
        if (h.get("name") or "").lower() == target:
            return h.get("value", "") or ""
    return ""


def _list_then_fetch(query: str, max_results: int) -> list[EmailHeader]:
    svc = _svc()
    listing = svc.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    msgs = listing.get("messages", []) or []
    out: list[EmailHeader] = []
    for stub in msgs:
        try:
            full = svc.users().messages().get(
                userId="me",
                id=stub["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
        except Exception as e:
            print(f"[gmail] get {stub.get('id')} failed: {e}", file=sys.stderr)
            continue
        payload_headers = (full.get("payload") or {}).get("headers", []) or []
        out.append(
            EmailHeader(
                id=full.get("id", stub["id"]),
                thread_id=full.get("threadId", ""),
                from_addr=_header(payload_headers, "From"),
                subject=_header(payload_headers, "Subject"),
                date_iso=_header(payload_headers, "Date"),
                snippet=full.get("snippet", "") or "",
            )
        )
    return out


def unread(max_results: int = 50) -> list[EmailHeader]:
    return _list_then_fetch("is:unread", max_results)


def recent(hours: int, max_results: int = 50) -> list[EmailHeader]:
    return _list_then_fetch(f"newer_than:{hours}h", max_results)


def format_for_context(headers: list[EmailHeader]) -> str:
    if not headers:
        return "_No Gmail items._\n"
    lines = ["### Gmail", ""]
    for h in headers:
        snippet = h.snippet[:120].replace("\n", " ")
        lines.append(f"- From: {h.from_addr}")
        lines.append(f"  Subject: {h.subject}")
        if snippet:
            lines.append(f"  Snippet: {snippet}")
    return "\n".join(lines) + "\n"


# --- CLI ---


def add_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(NAME, help="Gmail integration (read-only)")
    sp = p.add_subparsers(dest="cmd", required=True)

    pu = sp.add_parser("unread", help="Unread messages")
    pu.add_argument("--max", type=int, default=50)

    pr = sp.add_parser("recent", help="Messages from last N hours")
    pr.add_argument("hours", type=int)
    pr.add_argument("--max", type=int, default=50)

    p.set_defaults(_handler=cli)


def cli(args: argparse.Namespace) -> int:
    cmd = args.cmd
    if cmd == "unread":
        items = unread(max_results=args.max)
        print(format_for_context(items))
        return 0
    if cmd == "recent":
        items = recent(hours=args.hours, max_results=args.max)
        print(format_for_context(items))
        return 0
    return 2
