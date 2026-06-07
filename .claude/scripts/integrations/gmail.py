"""Gmail integration: read-only metadata listing and body-fetch.

Two paths:
- ``unread`` / ``recent`` — Phase-4 heartbeat paths, metadata-format only
  (snippet + headers). Used for reactive notification / draft prompting.
- ``fetch_since`` — comms-capture feeder path (body-fetch, format=full).
  Fetches full text/plain bodies for high-signal distillation by
  comms_capture.py. NEVER fetches with gmail.send scope.

NEVER use gmail.send. Scope is hardcoded readonly+modify (label/mark-read) in
bootstrap_google_oauth.py.
"""

from __future__ import annotations

import argparse
import base64
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


@dataclass(frozen=True)
class EmailMessage:
    id: str
    thread_id: str
    from_addr: str
    subject: str
    date_iso: str
    internal_date_ms: int   # Gmail internalDate, Unix ms
    body_text: str          # text/plain body (may be truncated)
    snippet: str            # Gmail API snippet (fallback when body_text is empty)
    to_addr: str = ""       # raw To header (for participant allow/deny scoping)
    cc_addr: str = ""       # raw Cc header (for participant allow/deny scoping)


def _svc():
    return _service("gmail", "v1")


def _header(headers: list[dict], name: str) -> str:
    target = name.lower()
    for h in headers:
        if (h.get("name") or "").lower() == target:
            return h.get("value", "") or ""
    return ""


def _extract_body_text(payload: dict, max_chars: int = 4000) -> str:
    """Recursively extract the first text/plain body from a Gmail format=full payload.

    LIMITATION: text/plain only. An HTML-only email (no text/plain alternative part)
    yields "" here, and the comms-capture reader falls back to the ~200-char Gmail
    snippet for distillation. Multipart/alternative mail (most clients, incl. Gmail's
    own compose) carries a text/plain part, so sent/replied threads are unaffected;
    pure-HTML senders (newsletters, some marketing mail) lose body content. If HTML
    senders ever become a meaningful capture source, add an HTML→text fallback here.
    """
    mime_type = (payload.get("mimeType") or "")
    if mime_type == "text/plain":
        data = (payload.get("body") or {}).get("data", "") or ""
        if data:
            try:
                raw = base64.urlsafe_b64decode(data + "==")
                return raw.decode("utf-8", errors="replace")[:max_chars].strip()
            except Exception:
                pass
    if mime_type.startswith("multipart/"):
        for part in (payload.get("parts") or []):
            text = _extract_body_text(part, max_chars)
            if text:
                return text
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


def fetch_since(
    query: str,
    since_ms: int | None = None,
    max_results: int = 100,
    max_body_chars: int = 4000,
    max_messages: int = 500,
) -> tuple[list[EmailMessage], int | None]:
    """Stateless body-fetch: emails matching `query` newer than since_ms.

    `query` is any valid Gmail search string (e.g. "in:sent", "label:SENT").
    `since_ms` is the exclusive lower bound as a Unix timestamp in milliseconds
    (Gmail internalDate format). None = no lower bound (rely on max_messages).
    `max_results` is the per-page list size; `max_messages` bounds the total
    kept across pages (mirrors slack.fetch_channel_history's max_messages cap).

    PAGINATES via nextPageToken so a busy window (or a long cold-start lookback)
    is fully covered instead of silently truncating at one page — without this,
    a cursor that advances to the newest-seen message would skip every message
    older than the page cap. Bounded by max_messages as a runaway guard; daily
    sent/replied volume is far below it.

    Returns (emails_ascending_by_internalDate, newest_internal_date_ms_or_None).
    NEVER writes state — the caller owns the cursor.

    NEVER fetches with gmail.send scope. Read-only.
    """
    svc = _svc()
    full_query = query
    if since_ms:
        since_epoch_s = since_ms // 1000
        full_query = f"{query} after:{since_epoch_s}"

    out: list[EmailMessage] = []
    page_token: str | None = None
    while True:
        kwargs = {"userId": "me", "q": full_query, "maxResults": max_results}
        if page_token:
            kwargs["pageToken"] = page_token
        listing = svc.users().messages().list(**kwargs).execute()
        stubs = listing.get("messages") or []
        for stub in stubs:
            try:
                full = svc.users().messages().get(
                    userId="me",
                    id=stub["id"],
                    format="full",
                ).execute()
            except Exception as e:
                print(f"[gmail] get {stub.get('id')} failed: {e}", file=sys.stderr)
                continue
            internal_date_ms = int(full.get("internalDate") or 0)
            # Strict inequality: skip messages at or before the watermark. The
            # `after:` operator is second-granular and inclusive, so this ms-level
            # backstop is what makes the watermark exact.
            if since_ms and internal_date_ms <= since_ms:
                continue
            payload = full.get("payload") or {}
            headers = payload.get("headers") or []
            body_text = _extract_body_text(payload, max_body_chars)
            out.append(EmailMessage(
                id=full.get("id", stub["id"]),
                thread_id=full.get("threadId", ""),
                from_addr=_header(headers, "From"),
                subject=_header(headers, "Subject"),
                date_iso=_header(headers, "Date"),
                internal_date_ms=internal_date_ms,
                body_text=body_text,
                snippet=full.get("snippet") or "",
                to_addr=_header(headers, "To"),
                cc_addr=_header(headers, "Cc"),
            ))
        page_token = listing.get("nextPageToken") or None
        if not page_token or len(out) >= max_messages:
            break

    out.sort(key=lambda e: e.internal_date_ms)
    newest_ms = max((e.internal_date_ms for e in out), default=None)
    return out, newest_ms


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
