#!/usr/bin/env python3
"""One-time migration: consolidate alias inbox slugs onto their canonical slug.

A pre-fix bug let an explicit --project flag (e.g. a Codex precompact hook
passing --project=vertik-lab-agent) bypass canonicalize_slug, so a single repo
split across multiple inbox folders AND project continuity docs
(vertik / vertik-lab-agent / vertik-lab-agent-chat-ui). The code fix
(write_inbox_capture now canonicalizes) prevents NEW splits; this migrates the
EXISTING data.

RUN ON THE HOST THAT OWNS THE CLEARED TRUTH — i.e. the VPS, where reflection has
already stamped `share_status: cleared`. Running on the Mac is unsafe: `_inbox/`
is gitignored and rsync is one-way Mac→VPS (--update --no-delete), so a Mac-side
move re-creates uncleared copies on the VPS. Idempotent + --dry-run.

For each inbox session folder whose name canonicalizes to a DIFFERENT slug:
  1. Move its captures into the canonical folder. On filename collision, keep the
     CLEARED copy (target-cleared → drop source; source-cleared → replace target;
     identical → drop source; otherwise leave source + warn, no data loss).
  2. Merge the stray project doc's continuity bullets into the canonical doc
     (append bullets missing under '## Auto-consolidated continuity', exact-match
     dedup), preserving the canonical doc's frontmatter + header.
  3. Remove the now-empty stray folder + the stray project doc.
  4. Fold the stray slug's watermark into the canonical key (keep the max), then
     drop the stray key from inbox_reflection.json.

Usage:
  uv run python deploy/bin/consolidate_inbox_slugs.py [--dry-run] [--vault PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import (  # noqa: E402
    STATE_DIR,
    atomic_write,
    canonicalize_slug,
    load_env,
    load_state,
    parse_capture,
    read_text,
    save_state,
    vault_path,
)

load_env()

CONTINUITY_HEADER = "## Auto-consolidated continuity"
INBOX_WATERMARK_PATH = STATE_DIR / "inbox_reflection.json"


def _log(msg: str) -> None:
    sys.stdout.write(msg + "\n")


def _is_cleared(path: Path) -> bool:
    parsed = parse_capture(path)
    return bool(parsed and parsed[0].get("share_status") == "cleared")


def _continuity_bullets(doc_text: str) -> list[str]:
    """Extract '- ' bullet lines under the continuity header (to next '## ' / EOF)."""
    lines = doc_text.splitlines()
    out: list[str] = []
    in_section = False
    for ln in lines:
        if ln.strip() == CONTINUITY_HEADER:
            in_section = True
            continue
        if in_section and ln.startswith("## "):
            break
        if in_section and ln.lstrip().startswith("- "):
            out.append(ln.rstrip())
    return out


def _merge_doc(canon_doc: Path, stray_doc: Path, dry_run: bool) -> int:
    """Append stray continuity bullets missing from canon doc. Returns # appended."""
    stray_text = read_text(stray_doc)
    if not stray_text:
        return 0
    stray_bullets = _continuity_bullets(stray_text)
    if not stray_bullets:
        return 0
    canon_text = read_text(canon_doc)
    if not canon_text:
        _log(f"    ! canonical doc {canon_doc.name} missing; leaving stray doc in place")
        return -1
    existing = set(_continuity_bullets(canon_text))
    new_bullets = [b for b in stray_bullets if b not in existing]
    if not new_bullets:
        return 0
    if dry_run:
        return len(new_bullets)
    idx = canon_text.find(CONTINUITY_HEADER)
    if idx < 0:
        merged = canon_text.rstrip() + f"\n\n{CONTINUITY_HEADER}\n" + "\n".join(new_bullets) + "\n"
    else:
        insert_at = idx + len(CONTINUITY_HEADER)
        merged = (
            canon_text[:insert_at]
            + "\n" + "\n".join(new_bullets)
            + canon_text[insert_at:]
        )
    atomic_write(canon_doc, merged)
    return len(new_bullets)


def _move_captures(stray_dir: Path, canon_dir: Path, dry_run: bool) -> tuple[int, int]:
    """Move stray captures into canon_dir. Returns (moved, left_behind)."""
    moved = left = 0
    if not dry_run:
        canon_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(stray_dir.glob("*.md")):
        dst = canon_dir / src.name
        if not dst.exists():
            _log(f"    move {src.name}")
            if not dry_run:
                src.replace(dst)
            moved += 1
            continue
        # collision: prefer the cleared copy; never lose data silently.
        if _is_cleared(dst):
            _log(f"    collision {src.name}: target cleared → drop source")
            if not dry_run:
                src.unlink()
            moved += 1
        elif _is_cleared(src):
            _log(f"    collision {src.name}: source cleared → replace target")
            if not dry_run:
                src.replace(dst)
            moved += 1
        elif read_text(src) == read_text(dst):
            _log(f"    collision {src.name}: identical → drop source")
            if not dry_run:
                src.unlink()
            moved += 1
        else:
            _log(f"    ! collision {src.name}: neither cleared, differing content → LEFT in place (resolve manually)")
            left += 1
    return moved, left


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print plan, write nothing")
    ap.add_argument("--vault", default=None, help="override vault path")
    args = ap.parse_args(argv)

    vault = Path(args.vault).expanduser() if args.vault else vault_path()
    sessions = vault / "Memory" / "_inbox" / "sessions"
    projects = vault / "Memory" / "projects"
    if not sessions.is_dir():
        _log(f"no inbox sessions dir at {sessions}; nothing to do")
        return 0

    state = load_state(INBOX_WATERMARK_PATH, default={}) or {}
    if not isinstance(state, dict):
        state = {}
    state_dirty = False

    strays = []
    for d in sorted(sessions.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        canon = canonicalize_slug(d.name)
        if canon and canon != d.name:
            strays.append((d, canon))

    if not strays:
        _log("no alias slug folders to consolidate — already canonical.")
        return 0

    _log(f"{'DRY-RUN: ' if args.dry_run else ''}consolidating {len(strays)} stray slug folder(s):")
    blocked = 0
    for stray_dir, canon in strays:
        _log(f"  {stray_dir.name} → {canon}")
        canon_dir = sessions / canon
        moved, left = _move_captures(stray_dir, canon_dir, args.dry_run)
        _log(f"    captures: {moved} moved, {left} left behind")

        appended = _merge_doc(projects / f"{canon}.md", projects / f"{stray_dir.name}.md", args.dry_run)
        if appended >= 0:
            _log(f"    project doc: {appended} continuity bullet(s) merged → {canon}.md")

        # fold watermark: keep the max so moved+cleared captures aren't reprocessed.
        stray_wm = state.get(stray_dir.name)
        canon_wm = state.get(canon)
        if stray_wm and (canon_wm is None or stray_wm > canon_wm):
            _log(f"    watermark: {canon} → {stray_wm} (folded from {stray_dir.name})")
            if not args.dry_run:
                state[canon] = stray_wm
                state_dirty = True

        # only remove stray folder/doc/key when fully drained.
        remaining = list(stray_dir.glob("*.md")) if not args.dry_run else []
        if left > 0 or (not args.dry_run and remaining) or appended < 0:
            _log(f"    ! {stray_dir.name} NOT removed (unresolved captures or missing canon doc)")
            blocked += 1
            continue
        if not args.dry_run:
            stray_doc = projects / f"{stray_dir.name}.md"
            if stray_doc.exists():
                stray_doc.unlink()
            try:
                stray_dir.rmdir()
            except OSError:
                pass
            if stray_dir.name in state:
                del state[stray_dir.name]
                state_dirty = True
        _log(f"    removed stray folder + doc + watermark key")

    if state_dirty and not args.dry_run:
        save_state(INBOX_WATERMARK_PATH, state)

    _log(f"\ndone. {len(strays) - blocked}/{len(strays)} folders consolidated"
         + (f", {blocked} blocked (manual resolution needed)" if blocked else ""))
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
