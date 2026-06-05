#!/usr/bin/env python3
"""Slackbot watchdog — Track D Phase 1 health probe for the chat-bot daemon.

The slackbot is the only long-running daemon in the stack (Type=simple,
Restart=on-failure, RestartSec=10). Before this watchdog, every failure mode
was invisible without manually tailing journalctl:

  - crash-looping every 10s (systemd dutifully restarts it forever),
  - expired SLACK_BOT_TOKEN / SLACK_APP_TOKEN (connects fail, restarts thrash),
  - duplicate instances (Socket Mode is a fan-out broadcast → duplicate replies),
  - plain down (unit dead, or stopped and never brought back after failover).

Oneshot, run by `brunoosbrain-slackbot-watchdog.timer` every 15 min. Reports via
the shared SyncReporter: status file + rate-limited Slack alert (#bruno_ops) +
healthchecks.io dead-man's-switch (BRUNOS_SLACKBOT_HEALTHCHECK_URL — configure
the check's grace at ~30 min).

Checks, in order (systemd ones skipped gracefully where systemctl is absent, so
the same script is usable on the Mac during failover):
  1. unit ActiveState == active            → else "unit-down"
  2. NRestarts delta since last watchdog   → >= 3 means "restart-storm"
  3. bot.py process count                  → > 1 means "duplicate-instance"
  4. Slack auth.test with the bot token    → failure means "auth-failed"

FAILOVER NOTE: when the slackbot is intentionally stopped (single-instance
policy — Mac takes over), this watchdog will alert "unit-down" hourly. Stop
`brunoosbrain-slackbot-watchdog.timer` together with the slackbot, or set
BRUNOS_SLACKBOT_WATCHDOG_DISABLED=1 in .env to silence it without systemd
fiddling (the watchdog then exits 0 without reporting — the healthchecks.io
check should be paused too, or it fires on the missing ping; that's the
dead-man's-switch doing its job).

CLI:
  --dry-run      evaluate + print, no reporting
  --skip-smoke   skip the Slack auth.test call
  --unit NAME    systemd unit to inspect (default brunoosbrain-slackbot.service)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import _ts_brt, load_env  # noqa: E402
from sync_common import make_reporter  # noqa: E402

load_env()

DEFAULT_UNIT = "brunoosbrain-slackbot.service"
RESTART_STORM_THRESHOLD = 3  # restarts since the previous watchdog run


def _log(msg: str) -> None:
    print(msg, flush=True)


# --- pure helpers (unit-tested in tests/test_slackbot_watchdog.py) ---


def parse_show_output(text: str) -> dict[str, str]:
    """Parse `systemctl show -p A -p B` KEY=VALUE lines into a dict."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def count_bot_processes(ps_lines: list[str]) -> int:
    """Count REAL bot interpreters in `ps -eo pid,comm,args` output.

    A single `uv run python .claude/chat/bot.py` shows up twice (the uv parent
    and the python child both carry bot.py in args), so matching on args alone
    overcounts. Require the executable (comm) to be a python, then match args.
    """
    n = 0
    for line in ps_lines:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        _pid, comm, args = parts
        if "python" not in comm.lower():
            continue
        if ".claude/chat/bot.py" in args or args.rstrip().endswith("chat/bot.py"):
            n += 1
    return n


def evaluate(
    *,
    unit_info: dict[str, str] | None,
    prev_nrestarts: int | None,
    proc_count: int | None,
    smoke_ok: bool | None,
) -> tuple[list[str], dict]:
    """Pure verdict over the collected probes. Returns (violations, extra).

    None inputs mean "probe unavailable / skipped" and never count as a
    violation by themselves — except no-systemd AND no-process, which is down.
    """
    violations: list[str] = []
    extra: dict = {}

    nrestarts: int | None = None
    if unit_info is not None:
        active = unit_info.get("ActiveState", "unknown")
        extra["active_state"] = active
        try:
            nrestarts = int(unit_info.get("NRestarts", ""))
        except ValueError:
            nrestarts = None
        if nrestarts is not None:
            extra["nrestarts"] = nrestarts
        if active != "active":
            violations.append(
                f"unit-down: ActiveState={active} "
                "(intentional failover stop? stop the watchdog timer too)"
            )
        elif nrestarts is not None and prev_nrestarts is not None:
            delta = nrestarts - prev_nrestarts
            extra["nrestarts_delta"] = delta
            if delta >= RESTART_STORM_THRESHOLD:
                violations.append(
                    f"restart-storm: {delta} restarts since last watchdog run "
                    f"(NRestarts {prev_nrestarts} → {nrestarts})"
                )
    if proc_count is not None:
        extra["bot_processes"] = proc_count
        if proc_count > 1:
            violations.append(
                f"duplicate-instance: {proc_count} bot.py interpreters running "
                "(Socket Mode broadcast → duplicate replies)"
            )
        if unit_info is None and proc_count == 0:
            violations.append("down: no systemd unit and no bot.py process")
    if smoke_ok is False:
        violations.append("auth-failed: Slack auth.test rejected the bot token")
    if smoke_ok is not None:
        extra["smoke_ok"] = smoke_ok
    return violations, extra


# --- probes ---


def _probe_unit(unit: str) -> dict[str, str] | None:
    """systemctl show, or None where systemd is unavailable (Mac)."""
    if shutil.which("systemctl") is None:
        return None
    try:
        cp = subprocess.run(
            ["systemctl", "show", unit, "-p", "ActiveState", "-p", "SubState",
             "-p", "NRestarts", "-p", "ExecMainStartTimestamp"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    return parse_show_output(cp.stdout)


def _probe_processes() -> int | None:
    try:
        cp = subprocess.run(
            ["ps", "-eo", "pid,comm,args"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    return count_bot_processes(cp.stdout.splitlines())


def _probe_smoke() -> bool | None:
    """auth.test with the bot token. None when no token configured (skip)."""
    if not os.environ.get("SLACK_BOT_TOKEN", "").strip():
        return None
    try:
        from integrations import slack

        slack._client().auth_test()
        return True
    except Exception as e:  # noqa: BLE001
        _log(f"  smoke: auth.test failed: {type(e).__name__}: {e}")
        return False


# --- main ---


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Slackbot watchdog (Track D Phase 1)")
    ap.add_argument("--dry-run", action="store_true", help="evaluate + print, no reporting")
    ap.add_argument("--skip-smoke", action="store_true", help="skip Slack auth.test")
    ap.add_argument("--unit", default=DEFAULT_UNIT)
    args = ap.parse_args(argv[1:])

    if os.environ.get("BRUNOS_SLACKBOT_WATCHDOG_DISABLED", "").strip():
        _log("watchdog disabled via BRUNOS_SLACKBOT_WATCHDOG_DISABLED — exiting 0")
        return 0

    reporter = None if args.dry_run else make_reporter(
        "slackbot-watchdog", "BRUNOS_SLACKBOT_HEALTHCHECK_URL"
    )
    prev_state = reporter.load() if reporter is not None else {}
    prev_nrestarts = prev_state.get("nrestarts")
    prev_nrestarts = int(prev_nrestarts) if isinstance(prev_nrestarts, (int, str)) and str(prev_nrestarts).isdigit() else None

    unit_info = _probe_unit(args.unit)
    proc_count = _probe_processes()
    smoke_ok = None if args.skip_smoke else _probe_smoke()

    violations, extra = evaluate(
        unit_info=unit_info,
        prev_nrestarts=prev_nrestarts,
        proc_count=proc_count,
        smoke_ok=smoke_ok,
    )

    _log(f"slackbot watchdog ({_ts_brt()}): unit={args.unit} "
         f"probes={extra} violations={violations or 'none'}")

    if reporter is None:
        return 1 if violations else 0

    state = reporter.load()
    state.update(extra)
    if violations:
        reporter.record_failure(
            state, _ts_brt(), kind="slackbot-health", msg=" | ".join(violations)
        )
        return 1
    reporter.record_success(state, _ts_brt(), extra=extra)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
