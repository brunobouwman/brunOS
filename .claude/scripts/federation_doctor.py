#!/usr/bin/env python3
"""federation_doctor.py — BaaS federation observability dashboard.

Shows per-inbox capture/cleared/staleness stats + canary-test status.

Usage:
  uv run python .claude/scripts/federation_doctor.py [--inbox SLUG] [--canary] [--json] [--alert]

Options:
  --inbox SLUG   Limit output to a single inbox project slug
  --canary       Also run tests/test_privacy_gate.py and show result
  --json         Emit machine-readable JSON instead of the text table
  --alert        Evaluate health thresholds and report to the reflect/federation
                 dead-man's-switch (status file + #bruno_ops Slack + healthcheck).
                 This is the independent state-health switch: even if reflection
                 silently wedges (never throws), stale-uncleared / over-cap-doc /
                 quarantine here still fires an alert. Daily systemd timer.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

# Canonical capture/frontmatter/time/vault helpers live in shared.py (stdlib-only,
# no asyncio/SDK side effects). Aliased to the local names this module already uses.
from shared import (  # noqa: E402
    STATE_DIR,
    _ts_brt,
    load_env,
    now_brt as _now_brt,
    parse_capture as _parse_capture,
    parse_iso as _parse_iso,
    read_text as _read_text,  # noqa: F401  (kept for parity / external callers)
    vault_path as _resolve_vault_path,
)

# Health thresholds for --alert. Captures normally clear next-day, so an oldest
# uncleared capture older than this means reflection is stuck (the watermark
# isn't advancing / clearing). project_doc_cap mirrors memory_reflect's
# PROJECT_DOC_CAP_BYTES (kept local — federation_doctor stays SDK-free).
HEALTH_THRESHOLDS = {
    "oldest_uncleared_max_days": 3,
    "project_doc_cap_bytes": 8192,
}


def _inbox_sessions_dir(vault: Path) -> Path:
    return vault / "Memory" / "_inbox" / "sessions"


def _iter_inbox_projects(vault: Path) -> list[str]:
    base = _inbox_sessions_dir(vault)
    if not base.is_dir():
        return []
    return sorted(
        d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith("_")
    )


def _fmt_staleness(td) -> str:
    total = int(td.total_seconds())
    if total < 0:
        return "future?"
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours > 0:
        return f"{hours}h {minutes}m ago"
    return f"{minutes}m ago"


# ── state helpers ──

def _load_watermark_state() -> dict:
    state_path = REPO_ROOT / ".claude" / "data" / "state" / "inbox_reflection.json"
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _check_rsync_state() -> str:
    """Check if inbox rsync state file exists. Currently deferred → N/A."""
    rsync_state = REPO_ROOT / ".claude" / "data" / "state" / "inbox-rsync-state.json"
    if rsync_state.exists():
        try:
            data = json.loads(rsync_state.read_text(encoding="utf-8"))
            ts = data.get("last_success") or data.get("last_run") or "?"
            return f"ok ({ts})"
        except Exception:
            return "exists (unreadable)"
    return "N/A"


# ── per-inbox stats ──

def _collect_inbox_stats(vault: Path, slug: str, watermark_state: dict) -> dict:
    inbox_dir = _inbox_sessions_dir(vault) / slug
    captures = list(inbox_dir.glob("*.md"))
    captures = [c for c in captures if not c.stem.startswith("_")]

    total = 0
    cleared = 0
    quarantined = 0
    oldest_uncleared_dt: datetime | None = None
    newest_dt: datetime | None = None
    scrubbed_count = 0

    for p in captures:
        parsed = _parse_capture(p)
        if parsed is None:
            continue
        fm, body = parsed
        total += 1
        created_dt = _parse_iso(fm.get("created"))

        if created_dt is not None:
            if newest_dt is None or created_dt > newest_dt:
                newest_dt = created_dt

        status = fm.get("share_status")
        if status == "cleared":
            cleared += 1
        else:
            if status == "quarantined":
                quarantined += 1
            # An uncleared capture (open OR quarantined) ages the staleness clock.
            if created_dt is not None:
                if oldest_uncleared_dt is None or created_dt < oldest_uncleared_dt:
                    oldest_uncleared_dt = created_dt

        if "[REDACTED-" in body:
            scrubbed_count += 1

    now = _now_brt()
    staleness = _fmt_staleness(now - newest_dt) if newest_dt else "N/A"
    watermark = watermark_state.get(slug, "none")

    # Continuity doc size — the compaction-deadlock signal (the whole file is
    # capped by memory_reflect._append_continuity).
    project_doc = vault / "Memory" / "projects" / f"{slug}.md"
    project_doc_bytes = (
        len(project_doc.read_bytes()) if project_doc.exists() else 0
    )

    return {
        "slug": slug,
        "total": total,
        "cleared": cleared,
        "uncleared": total - cleared,
        "quarantined": quarantined,
        "oldest_uncleared": oldest_uncleared_dt.isoformat() if oldest_uncleared_dt else None,
        "newest": newest_dt.isoformat() if newest_dt else None,
        "staleness": staleness,
        "watermark": watermark,
        "project_doc_bytes": project_doc_bytes,
        "rsynced": "N/A",
        "acked": "N/A",
        "scrubbed_count": scrubbed_count,
    }


# ── canary runner ──

def _run_canary() -> dict:
    test_path = REPO_ROOT / "tests" / "test_privacy_gate.py"
    if not test_path.exists():
        return {"status": "ERROR", "message": f"test file not found: {test_path}"}
    try:
        result = subprocess.run(
            ["uv", "run", "python", str(test_path)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=60,
        )
        output = result.stdout.strip()
        # Parse summary line: "N passed, M failed, K skipped"
        summary_match = re.search(r"(\d+) passed,\s*(\d+) failed,\s*(\d+) skipped", output)
        if summary_match:
            passed = int(summary_match.group(1))
            failed = int(summary_match.group(2))
            skipped = int(summary_match.group(3))
            status = "PASS" if result.returncode == 0 else "FAIL"
            return {
                "status": status,
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "output": output,
            }
        return {
            "status": "FAIL" if result.returncode != 0 else "PASS",
            "output": output or result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "message": "canary test timed out (60s)"}
    except FileNotFoundError:
        return {"status": "ERROR", "message": "uv not found; cannot run canary tests"}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


# ── health verdict (cross-run state thresholds) ──

def health_verdict(stats_list: list[dict], thresholds: dict | None = None) -> dict:
    """Evaluate per-inbox stats against health thresholds.

    Returns {"ok": bool, "violations": [str], "slugs": [str]}. Violations:
    - oldest uncleared capture older than oldest_uncleared_max_days (reflection
      stuck / watermark not advancing — captures normally clear next-day);
    - quarantined captures present (withheld content needing manual review);
    - continuity doc over project_doc_cap_bytes (compaction not keeping up).
    """
    th = thresholds or HEALTH_THRESHOLDS
    now = _now_brt()
    violations: list[str] = []
    slugs: set[str] = set()
    for s in stats_list:
        slug = s["slug"]
        ou = s.get("oldest_uncleared")
        if ou:
            dt = _parse_iso(ou)
            if dt is not None:
                age_days = (now - dt).total_seconds() / 86400
                if age_days > th["oldest_uncleared_max_days"]:
                    violations.append(
                        f"{slug}: oldest uncleared capture {age_days:.1f}d old "
                        f"(> {th['oldest_uncleared_max_days']}d) — reflection stuck?"
                    )
                    slugs.add(slug)
        q = s.get("quarantined", 0)
        if q:
            violations.append(f"{slug}: {q} quarantined capture(s) need manual review")
            slugs.add(slug)
        pdb = s.get("project_doc_bytes", 0)
        cap = th["project_doc_cap_bytes"]
        if pdb > cap:
            violations.append(
                f"{slug}: continuity doc {pdb}B > {cap}B cap — compaction not keeping up"
            )
            slugs.add(slug)
    return {"ok": not violations, "violations": violations, "slugs": sorted(slugs)}


def _emit_health_alert(stats_list: list[dict]) -> int:
    """Report the health verdict to the federation-doctor dead-man's-switch.

    Independent of the reflect switch: reflect proves the writer ran; this proves
    the on-disk federation state is healthy. Returns 0 if healthy, 1 if violations.
    """
    from sync_common import SyncReporter

    reporter = SyncReporter(
        service="federation-doctor",
        status_file=STATE_DIR / "federation-doctor-state.json",
        lock_file=STATE_DIR / "locks" / "federation-doctor.run.lock",
        healthcheck_env="BRUNOS_FEDERATION_DOCTOR_HEALTHCHECK_URL",
    )
    verdict = health_verdict(stats_list)
    state = reporter.load()
    attempt_ts = _ts_brt()
    if verdict["ok"]:
        reporter.log("federation health OK")
        reporter.record_success(state, attempt_ts)
        return 0
    msg = " | ".join(verdict["violations"])
    reporter.log(f"federation health DEGRADED: {msg}")
    reporter.record_failure(
        state, attempt_ts, kind="federation_health", msg=msg, paths=verdict["slugs"]
    )
    return 1


# ── output formatters ──

def _print_text_table(stats_list: list[dict], canary: dict | None, now: datetime) -> None:
    ts = now.strftime("%Y-%m-%dT%H:%M:%S%z")
    # Insert colon in tz offset for RFC3339 compat
    if len(ts) > 5 and ts[-5] in "+-":
        ts = ts[:-2] + ":" + ts[-2:]
    print(f"\nBrunOS Federation Doctor — {ts}")

    if not stats_list:
        print("\n  No inbox projects found.")
    else:
        print("\nINBOX STATUS")
        # Column widths
        w_slug = max(len("Inbox"), max(len(s["slug"]) for s in stats_list))
        w_total = max(len("Total"), max(len(str(s["total"])) for s in stats_list))
        w_cleared = max(len("Cleared"), max(len(str(s["cleared"])) for s in stats_list))
        w_uncleared = max(len("Uncleared"), max(len(str(s["uncleared"])) for s in stats_list))
        w_stale = max(len("Staleness"), max(len(s["staleness"]) for s in stats_list))
        w_newest = max(len("Newest-capture"), max(len(s["newest"] or "N/A") for s in stats_list))
        w_wm = max(len("Watermark"), max(len(str(s["watermark"])) for s in stats_list))

        header = (
            f"{'Inbox':{w_slug}}  {'Total':>{w_total}}  {'Cleared':>{w_cleared}}"
            f"  {'Uncleared':>{w_uncleared}}  {'Staleness':{w_stale}}"
            f"  {'Newest-capture':{w_newest}}  {'Watermark':{w_wm}}"
        )
        sep = "  ".join(["-" * w_slug, "-" * w_total, "-" * w_cleared,
                         "-" * w_uncleared, "-" * w_stale, "-" * w_newest, "-" * w_wm])
        print(header)
        print(sep)
        for s in stats_list:
            row = (
                f"{s['slug']:{w_slug}}  {s['total']:>{w_total}}  {s['cleared']:>{w_cleared}}"
                f"  {s['uncleared']:>{w_uncleared}}  {s['staleness']:{w_stale}}"
                f"  {(s['newest'] or 'N/A'):{w_newest}}  {str(s['watermark']):{w_wm}}"
            )
            print(row)

        total_all = sum(s["total"] for s in stats_list)
        cleared_all = sum(s["cleared"] for s in stats_list)
        uncleared_all = total_all - cleared_all
        pct = int(100 * cleared_all / total_all) if total_all else 0

        print("\nSUMMARY")
        print(f"  Total captures: {total_all}  "
              f"Cleared: {cleared_all} ({pct}%)  "
              f"Uncleared: {uncleared_all} ({100-pct}%)")

    if canary is None:
        print("\nCANARY TEST (--canary to run)")
        print("  Not run. Use --canary to execute the privacy gate test suite.")
    else:
        print("\nCANARY TEST")
        status = canary.get("status", "?")
        if "passed" in canary:
            summary = f"  {canary['passed']} passed, {canary['failed']} failed, {canary['skipped']} skipped"
            print(f"  Running tests/test_privacy_gate.py ...")
            print(summary)
            mark = "✓" if status == "PASS" else "✗"
            print(f"  Status: {status} {mark}")
        else:
            msg = canary.get("message") or canary.get("output") or ""
            print(f"  Status: {status}")
            if msg:
                print(f"  {msg}")
    print()


def _print_json(stats_list: list[dict], canary: dict | None, now: datetime) -> None:
    out = {
        "generated_at": now.isoformat(),
        "inboxes": stats_list,
        "summary": {
            "total": sum(s["total"] for s in stats_list),
            "cleared": sum(s["cleared"] for s in stats_list),
            "uncleared": sum(s["uncleared"] for s in stats_list),
        },
        "health": health_verdict(stats_list),
        "canary": canary,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))


# ── main ──

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="BaaS federation observability dashboard")
    parser.add_argument("--inbox", default=None, metavar="SLUG",
                        help="Limit to a single inbox project slug")
    parser.add_argument("--canary", action="store_true",
                        help="Run the privacy gate test suite and show result")
    parser.add_argument("--json", action="store_true", dest="emit_json",
                        help="Emit machine-readable JSON")
    parser.add_argument("--alert", action="store_true",
                        help="Evaluate health thresholds → dead-man's-switch + Slack")
    args = parser.parse_args(argv[1:])

    if args.alert:
        load_env()  # pick up BRUNOS_ALERT_CHANNEL + healthcheck URL on manual runs

    try:
        vault = _resolve_vault_path()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    watermark_state = _load_watermark_state()

    slugs = _iter_inbox_projects(vault)
    if args.inbox:
        slugs = [s for s in slugs if s == args.inbox]
        if not slugs:
            print(f"ERROR: no inbox found for slug '{args.inbox}'", file=sys.stderr)
            return 1

    stats_list = [_collect_inbox_stats(vault, slug, watermark_state) for slug in slugs]

    canary: dict | None = None
    if args.canary:
        canary = _run_canary()

    now = _now_brt()
    if args.emit_json:
        _print_json(stats_list, canary, now)
    else:
        _print_text_table(stats_list, canary, now)

    rc = 0
    # Exit non-zero if canary failed
    if canary and canary.get("status") not in ("PASS", None):
        rc = 1
    if args.alert:
        if _emit_health_alert(stats_list):
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
