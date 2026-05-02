"""RSS integration: polite polling of curated AI feeds.

Uses feedparser ETag/Last-Modified caching so repeated polls cost ~0 bandwidth.
Per-feed try/except — one dead feed never breaks the others.

State:  .claude/data/state/rss-state.json
        {"_schema_version": 1, "feeds": {<url>: {"etag", "modified", "seen_ids", "last_poll_iso"}}}
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import STATE_DIR, load_state, now_brt, save_state  # noqa: E402

NAME = "rss"
STATE_PATH = STATE_DIR / f"{NAME}-state.json"
SEEN_CAP = 200

# Curated AI engineering feeds — PRD §4.5. Validate at integration time.
DEFAULT_FEEDS: list[str] = [
    "https://rss.arxiv.org/rss/cs.AI",
    "https://rss.arxiv.org/rss/cs.LG",
    "https://rss.arxiv.org/rss/cs.CL",
    "https://www.anthropic.com/news/rss.xml",
    "https://openai.com/blog/rss.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://research.google/blog/rss/",
    "https://news.smol.ai/rss.xml",
    "https://simonwillison.net/atom/everything/",
    "https://hnrss.org/newest?q=AI+OR+LLM&points=50",
]


@dataclass(frozen=True)
class FeedItem:
    feed_url: str
    item_id: str
    title: str
    link: str
    summary: str
    published_iso: str


def _load() -> dict:
    state = load_state(STATE_PATH, default=None)
    if not isinstance(state, dict):
        return {"_schema_version": 1, "feeds": {}}
    state.setdefault("_schema_version", 1)
    state.setdefault("feeds", {})
    return state


def _per_feed(state: dict, url: str) -> dict:
    return state["feeds"].setdefault(
        url,
        {"etag": "", "modified": "", "seen_ids": [], "last_poll_iso": ""},
    )


def new_items(feeds: list[str] | None = None) -> list[FeedItem]:
    import feedparser

    feeds = feeds or DEFAULT_FEEDS
    state = _load()
    out: list[FeedItem] = []

    for url in feeds:
        per = _per_feed(state, url)
        try:
            kwargs = {}
            if per.get("etag"):
                kwargs["etag"] = per["etag"]
            if per.get("modified"):
                kwargs["modified"] = per["modified"]
            parsed = feedparser.parse(url, **kwargs)
        except Exception as e:
            print(f"[rss] parse {url}: {type(e).__name__}: {e}", file=sys.stderr)
            continue

        status = getattr(parsed, "status", 0)
        if status == 304:
            per["last_poll_iso"] = now_brt().isoformat()
            continue
        if parsed.bozo and not parsed.entries:
            err = getattr(parsed, "bozo_exception", "")
            print(f"[rss] {url} bozo (no entries): {err}", file=sys.stderr)
            per["last_poll_iso"] = now_brt().isoformat()
            continue

        seen = deque(per.get("seen_ids", []), maxlen=SEEN_CAP)
        seen_set = set(seen)
        new_ids: list[str] = []
        for entry in parsed.entries:
            iid = entry.get("id") or entry.get("link") or ""
            if not iid or iid in seen_set:
                continue
            new_ids.append(iid)
            out.append(
                FeedItem(
                    feed_url=url,
                    item_id=iid,
                    title=entry.get("title", "") or "(no title)",
                    link=entry.get("link", "") or "",
                    summary=(entry.get("summary", "") or "")[:400],
                    published_iso=entry.get("published", "") or entry.get("updated", "") or "",
                )
            )
        for iid in new_ids:
            seen.append(iid)
            seen_set.add(iid)
        per["seen_ids"] = list(seen)
        per["etag"] = parsed.get("etag", "") or per.get("etag", "")
        per["modified"] = parsed.get("modified", "") or per.get("modified", "")
        per["last_poll_iso"] = now_brt().isoformat()

    save_state(STATE_PATH, state)
    return out


def list_feeds() -> str:
    state = _load()
    lines = ["### RSS feeds", ""]
    for url in DEFAULT_FEEDS:
        per = state["feeds"].get(url, {})
        seen = len(per.get("seen_ids", []))
        last = per.get("last_poll_iso", "never")
        lines.append(f"- {url}\n    seen={seen} last_poll={last}")
    return "\n".join(lines) + "\n"


def format_for_context(items: list[FeedItem]) -> str:
    if not items:
        return "_No new RSS items._\n"
    by_feed: dict[str, list[FeedItem]] = {}
    for it in items:
        by_feed.setdefault(it.feed_url, []).append(it)
    lines = ["### RSS", ""]
    for url, group in by_feed.items():
        lines.append(f"**{url}** ({len(group)})")
        for it in group:
            lines.append(f"- [{it.title}]({it.link})")
        lines.append("")
    return "\n".join(lines)


# --- CLI ---


def add_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(NAME, help="RSS integration (curated AI feeds)")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("new", help="New items since last poll")
    sp.add_parser("feeds", help="List configured feeds with state")
    p.set_defaults(_handler=cli)


def cli(args: argparse.Namespace) -> int:
    cmd = args.cmd
    if cmd == "new":
        items = new_items()
        print(format_for_context(items))
        return 0
    if cmd == "feeds":
        print(list_feeds())
        return 0
    return 2
