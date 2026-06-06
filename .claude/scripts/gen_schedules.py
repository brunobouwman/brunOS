"""Generate timer units from brain-config cadence strings.

Turns the per-brain cadence in brain-config.json into the platform's scheduler
units, splitting reflection into three independently-scheduled passes:

    reflect-inbox   reflection.inbox_pass.cadence    (default hourly, 08-20)
    reflect-curate  reflection.memory_curation.cadence  (default daily@08:00)
    dream           dreaming.cadence                  (default nightly@03:00)

  --platform mac  → launchd plists  (deploy/launchd/com.bruno.brunos.<key>.plist)
  --platform vps  → systemd .service+.timer (deploy/systemd/brunoosbrain-<key>.*)
  --platform both → emit for both
Default platform is the host OS. `--dry-run` prints the units instead of writing.
Idempotent: re-running with the same config rewrites byte-identical files.

This supersedes the single `brunoosbrain-reflect` / `com.bruno.brunos.reflection`
unit. The scripts themselves never schedule — they only read behavior toggles;
cadence lives here. Onboarding calls this; here we ship it + the default units.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

import brain_config  # noqa: E402

SYSTEMD_DIR = REPO_ROOT / "deploy" / "systemd"
LAUNCHD_DIR = REPO_ROOT / "deploy" / "launchd"

# Host-specific deploy constants, matched to the existing sibling units.
VPS_WORKDIR = "/home/bruno/claude-second-brain"
VPS_UV = "/usr/local/bin/uv"
VPS_USER = "bruno"
# systemd interprets a bare OnCalendar in the system timezone (the VPS runs UTC),
# so the BRT cadence MUST carry an explicit zone or it fires 3h off. (launchd
# StartCalendarInterval uses the Mac's local time — already BRT — so no suffix
# there; the plist TZ env only sets the process clock, not the trigger.)
TIMEZONE = "America/Sao_Paulo"
MAC_WORKDIR = "/Users/brunobouwman/Documents/brunOS-brain"
MAC_UV = "/Users/brunobouwman/.local/bin/uv"
MAC_PATH = "/Users/brunobouwman/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
MAC_LOGDIR = "/Users/brunobouwman/Library/Logs"

# The three split units. cadence_path/hours_path point into brain-config; the
# script + args are what each unit runs.
UNITS = [
    {
        "key": "reflect-inbox",
        "label": "inbox pass",
        "script": ".claude/scripts/memory_reflect.py",
        "args": ["--inbox-only"],
        "enabled_path": "reflection.inbox_pass.enabled",
        "cadence_path": "reflection.inbox_pass.cadence",
        "hours_path": "reflection.inbox_pass.hours",
        "timeout": 1800,
    },
    {
        "key": "reflect-curate",
        "label": "memory curation (daily-log distill + buffer drain + evict)",
        "script": ".claude/scripts/memory_reflect.py",
        "args": ["--skip-inbox"],
        "enabled_path": "reflection.memory_curation.enabled",
        "cadence_path": "reflection.memory_curation.cadence",
        "hours_path": None,
        "timeout": 600,
    },
    {
        "key": "dream",
        "label": "dreaming (procedure + decision extraction)",
        "script": ".claude/scripts/memory_dream.py",
        "args": [],
        "enabled_path": "dreaming.enabled",
        "cadence_path": "dreaming.cadence",
        "hours_path": None,
        "timeout": 1800,
    },
    {
        "key": "comms-capture",
        "label": "comms capture (high-signal knowledge from comms channels)",
        "script": ".claude/scripts/comms_capture.py",
        "args": [],
        "enabled_path": "comms_capture.enabled",
        "cadence_path": "comms_capture.cadence",
        "hours_path": "comms_capture.hours",
        "timeout": 1800,
    },
]


def parse_cadence(cadence: str | None, hours: str | None = None) -> dict:
    """Normalize a cadence string to a schedule descriptor.

    "hourly" (+ optional hours "LO-HI") → {kind: hourly, hours: [..], minute}
    "daily@HH:MM" / "nightly@HH:MM"     → {kind: daily, hour, minute}
    """
    c = (cadence or "").strip().lower()
    if c == "hourly":
        lo, hi = 8, 20
        if hours:
            m = re.match(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$", hours)
            if m:
                lo, hi = int(m.group(1)), int(m.group(2))
        return {"kind": "hourly", "hours": list(range(lo, hi + 1)), "minute": 0}
    m = re.match(r"(?:daily|nightly)@(\d{1,2}):(\d{2})$", c)
    if m:
        return {"kind": "daily", "hour": int(m.group(1)), "minute": int(m.group(2))}
    raise ValueError(f"unrecognized cadence: {cadence!r}")


# --- systemd ------------------------------------------------------------------


def systemd_oncalendar(d: dict) -> str:
    if d["kind"] == "hourly":
        lo, hi = d["hours"][0], d["hours"][-1]
        return f"*-*-* {lo:02d}..{hi:02d}:{d['minute']:02d}:00 {TIMEZONE}"
    return f"*-*-* {d['hour']:02d}:{d['minute']:02d}:00 {TIMEZONE}"


def systemd_service(unit: dict) -> str:
    exec_args = " ".join(unit["args"])
    exec_line = f"{VPS_UV} run python {unit['script']}" + (f" {exec_args}" if exec_args else "")
    return (
        "[Unit]\n"
        f"Description=brunoosbrain {unit['label']}\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "OnFailure=brunoosbrain-alert@%n.service\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"User={VPS_USER}\n"
        f"Group={VPS_USER}\n"
        f"WorkingDirectory={VPS_WORKDIR}\n"
        f"EnvironmentFile={VPS_WORKDIR}/.claude/.env\n"
        "Environment=TZ=America/Sao_Paulo\n"
        "Environment=PATH=/usr/local/bin:/usr/bin:/bin\n"
        f"ExecStart={exec_line}\n"
        f"TimeoutStartSec={unit['timeout']}\n"
        "LogsDirectory=brunoosbrain\n"
        f"StandardOutput=append:/var/log/brunoosbrain/{unit['key']}.log\n"
        f"StandardError=append:/var/log/brunoosbrain/{unit['key']}.log\n"
    )


def systemd_timer(unit: dict, d: dict) -> str:
    return (
        "[Unit]\n"
        f"Description=brunoosbrain {unit['label']} — {unit['cadence']}\n\n"
        "[Timer]\n"
        f"OnCalendar={systemd_oncalendar(d)}\n"
        "Persistent=true\n"
        f"Unit=brunoosbrain-{unit['key']}.service\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


# --- launchd ------------------------------------------------------------------


def _plist_cal_block(d: dict) -> str:
    def one(hour: int, minute: int) -> str:
        return (
            "        <dict>\n"
            f"            <key>Hour</key><integer>{hour}</integer>\n"
            f"            <key>Minute</key><integer>{minute}</integer>\n"
            "        </dict>\n"
        )
    if d["kind"] == "hourly":
        entries = "".join(one(h, d["minute"]) for h in d["hours"])
        return (
            "    <key>StartCalendarInterval</key>\n"
            "    <array>\n"
            f"{entries}"
            "    </array>\n"
        )
    return (
        "    <key>StartCalendarInterval</key>\n"
        "    <dict>\n"
        f"        <key>Hour</key><integer>{d['hour']}</integer>\n"
        f"        <key>Minute</key><integer>{d['minute']}</integer>\n"
        "    </dict>\n"
    )


def launchd_plist(unit: dict, d: dict) -> str:
    label = f"com.bruno.brunos.{unit['key']}"
    args = "".join(
        f"        <string>{a}</string>\n"
        for a in [MAC_UV, "run", "python", unit["script"], *unit["args"]]
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        f"    <string>{label}</string>\n\n"
        "    <!-- Failover unit: ships disabled on the Mac (reflection/dream run on the\n"
        "         VPS; MEMORY.md/playbook writes aren't dual-run safe). Operator enables\n"
        "         on failover — see deploy/bin/install-mac-launchd.sh. -->\n"
        "    <key>Disabled</key>\n"
        "    <true/>\n\n"
        "    <key>WorkingDirectory</key>\n"
        f"    <string>{MAC_WORKDIR}</string>\n\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"{args}"
        "    </array>\n\n"
        "    <key>EnvironmentVariables</key>\n"
        "    <dict>\n"
        "        <key>TZ</key>\n"
        "        <string>America/Sao_Paulo</string>\n"
        "        <key>PATH</key>\n"
        f"        <string>{MAC_PATH}</string>\n"
        "    </dict>\n\n"
        "    <key>StandardOutPath</key>\n"
        f"    <string>{MAC_LOGDIR}/{label}.log</string>\n"
        "    <key>StandardErrorPath</key>\n"
        f"    <string>{MAC_LOGDIR}/{label}.log</string>\n\n"
        f"{_plist_cal_block(d)}"
        "</dict>\n"
        "</plist>\n"
    )


# --- driver -------------------------------------------------------------------


def _resolve_units() -> list[dict]:
    out = []
    for u in UNITS:
        u = dict(u)
        u["enabled"] = brain_config.get(u["enabled_path"]) is not False
        cadence = brain_config.get(u["cadence_path"])
        hours = brain_config.get(u["hours_path"]) if u["hours_path"] else None
        u["cadence"] = cadence
        u["descriptor"] = parse_cadence(cadence, hours)
        out.append(u)
    return out


def _emit(text: str, path: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"\n# ===== {path} =====")
        print(text, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}")


def run(platform: str, dry_run: bool) -> int:
    units = _resolve_units()
    for u in units:
        if not u["enabled"]:
            print(f"# {u['key']}: disabled by brain-config — skipping")
            continue
        d = u["descriptor"]
        if platform in ("vps", "both"):
            _emit(systemd_service(u), SYSTEMD_DIR / f"brunoosbrain-{u['key']}.service", dry_run)
            _emit(systemd_timer(u, d), SYSTEMD_DIR / f"brunoosbrain-{u['key']}.timer", dry_run)
        if platform in ("mac", "both"):
            _emit(launchd_plist(u, d), LAUNCHD_DIR / f"com.bruno.brunos.{u['key']}.plist", dry_run)
    return 0


def main(argv: list[str]) -> int:
    default_platform = "mac" if sys.platform == "darwin" else "vps"
    ap = argparse.ArgumentParser(description="Generate split reflection/dream timer units from brain-config")
    ap.add_argument("--platform", choices=["mac", "vps", "both"], default=default_platform,
                    help=f"target scheduler (default: {default_platform})")
    ap.add_argument("--dry-run", action="store_true", help="print units instead of writing them")
    args = ap.parse_args(argv[1:])
    return run(args.platform, args.dry_run)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
