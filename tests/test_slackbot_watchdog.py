#!/usr/bin/env python3
"""Standalone tests for the slackbot watchdog's pure logic (no pytest).
Run: uv run python tests/test_slackbot_watchdog.py

Covers: systemctl-show parsing, bot-process counting (uv parent must NOT count
as a second instance), and the evaluate() verdict matrix (unit-down,
restart-storm, duplicate-instance, auth-failed, Mac no-systemd mode).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["BRUNOS_DISABLE_REPORTING"] = "1"

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from slackbot_watchdog import (  # noqa: E402
    RESTART_STORM_THRESHOLD,
    count_bot_processes,
    evaluate,
    parse_show_output,
)

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def main() -> int:
    print("== parse_show_output ==")
    info = parse_show_output(
        "ActiveState=active\nSubState=running\nNRestarts=4\n"
        "ExecMainStartTimestamp=Wed 2026-06-03 08:00:01 -03\n"
    )
    check(info["ActiveState"] == "active", "parses ActiveState")
    check(info["NRestarts"] == "4", "parses NRestarts")
    check(parse_show_output("") == {}, "empty output → empty dict")

    print("== count_bot_processes ==")
    ps = [
        "  PID COMM             ARGS",
        " 1001 uv               /usr/local/bin/uv run python .claude/chat/bot.py",
        " 1002 python3.12       python .claude/chat/bot.py",
        " 1003 claude           claude --resume abc",
        " 1004 python3.12       python .claude/scripts/heartbeat.py",
    ]
    check(count_bot_processes(ps) == 1,
          "one real instance: uv parent does NOT double-count")
    ps_dup = ps + [" 1005 python3.12       python /home/bruno/claude-second-brain/.claude/chat/bot.py"]
    check(count_bot_processes(ps_dup) == 2, "duplicate interpreter counted")
    check(count_bot_processes([]) == 0, "no processes → 0")

    print("== evaluate: healthy ==")
    v, extra = evaluate(
        unit_info={"ActiveState": "active", "NRestarts": "4"},
        prev_nrestarts=4, proc_count=1, smoke_ok=True,
    )
    check(v == [], "active + no new restarts + 1 proc + auth ok → no violations")
    check(extra.get("nrestarts") == 4 and extra.get("nrestarts_delta") == 0,
          "extra carries nrestarts + delta")

    print("== evaluate: failure modes ==")
    v, _ = evaluate(unit_info={"ActiveState": "inactive", "NRestarts": "4"},
                    prev_nrestarts=4, proc_count=0, smoke_ok=None)
    check(any(x.startswith("unit-down") for x in v), "inactive unit → unit-down")

    v, extra = evaluate(
        unit_info={"ActiveState": "active", "NRestarts": "4" },
        prev_nrestarts=4 - RESTART_STORM_THRESHOLD, proc_count=1, smoke_ok=True,
    )
    check(any(x.startswith("restart-storm") for x in v),
          f"NRestarts delta {RESTART_STORM_THRESHOLD} → restart-storm")

    v, _ = evaluate(unit_info={"ActiveState": "active", "NRestarts": "0"},
                    prev_nrestarts=0, proc_count=2, smoke_ok=True)
    check(any(x.startswith("duplicate-instance") for x in v),
          "2 interpreters → duplicate-instance")

    v, _ = evaluate(unit_info={"ActiveState": "active", "NRestarts": "0"},
                    prev_nrestarts=0, proc_count=1, smoke_ok=False)
    check(any(x.startswith("auth-failed") for x in v), "smoke fail → auth-failed")

    print("== evaluate: degraded probes ==")
    v, _ = evaluate(unit_info=None, prev_nrestarts=None, proc_count=1, smoke_ok=None)
    check(v == [], "Mac mode (no systemd) with a running process → healthy")
    v, _ = evaluate(unit_info=None, prev_nrestarts=None, proc_count=0, smoke_ok=None)
    check(any(x.startswith("down") for x in v),
          "no systemd AND no process → down")
    v, _ = evaluate(unit_info={"ActiveState": "active", "NRestarts": "7"},
                    prev_nrestarts=None, proc_count=None, smoke_ok=None)
    check(v == [], "first run (no prev nrestarts) never storms")

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
