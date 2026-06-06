#!/usr/bin/env python3
"""Provision healthchecks.io checks for a brain — Track D Phase 2.

Onboarding a brain (LisaOS now, client brains later) should auto-onboard its
monitoring: run this once per brain×host and every service gets its dead-man
check, named/tagged/graced by convention, with the env block to paste into the
instance's .env printed at the end. Idempotent — re-running updates in place
(healthchecks.io upsert via `unique: ["name"]`), so it's safe to run on every
onboarding pass or after catalog changes.

Model (decided 2026-06-03): ONE Protostack healthchecks.io account, ONE project
per brain (API keys are project-scoped → the key you pass selects the brain's
project), alerts via the project's integrations (default `channels: "*"` = all,
i.e. the shared Protostack ops channel once wired in the project settings).

Conventions (mirror deploy/README.md "Monitoring" table):
  check name/slug:  <brain>-<svc>-<host>     e.g. lisaos-heartbeat-vps
  tags:             brain:<brain> svc:<svc> host:<host>
  env var:          per-service (BRUNOS_* legacy names; --var-prefix swaps the
                    BRUNOS token, e.g. --var-prefix LISAOS, IFF the brain's
                    code reads the renamed vars — default keeps BRUNOS_*).

Usage:
  HEALTHCHECKS_API_KEY=<project-rw-key> \\
  uv run python .claude/scripts/provision_healthchecks.py \\
      --brain lisaos --host vps \\
      [--services heartbeat,reflect,vault-sync,memory-doctor,slackbot-watchdog] \\
      [--channels "*"] [--var-prefix BRUNOS] [--dry-run] [--json]

  --dry-run prints the would-be payloads and env block without any HTTP.
  Exit codes: 0 ok; 2 config error; 1 API failure (a 403 usually means the
  project hit its check limit — free tier is 20; upgrade or self-host).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import load_env  # noqa: E402

load_env()

API_BASE = "https://healthchecks.io"
DEFAULT_TZ = "America/Sao_Paulo"

# --- Service catalog -------------------------------------------------------
# One entry per instrumented service (Track D Phase 1). `timeout` = expected
# period in seconds (period-based checks); `schedule` = cron in --tz
# (cron-based checks; tighter than a 26h grace for jobs with a fixed time).
# Dailies without a precisely-pinned time use period 86400 + 6h grace —
# tighten to cron per-brain later if wanted. `env` is the variable the
# Phase 1 reporter code reads for that service.
SERVICE_CATALOG: dict[str, dict] = {
    "vault-sync": {
        "env": "BRUNOS_HEALTHCHECK_URL",
        "timeout": 120, "grace": 600,
        "desc": "vault git-sync (vault_sync.py, every 2 min)",
    },
    "code-sync": {
        "env": "BRUNOS_CODESYNC_HEALTHCHECK_URL",
        "timeout": 1800, "grace": 2700,
        "desc": "pull-only code sync (code_sync.py, every 30 min, consumer hosts only)",
    },
    "heartbeat": {
        "env": "BRUNOS_HEARTBEAT_HEALTHCHECK_URL",
        "schedule": "0,30 8-22 * * *", "grace": 2700,
        "desc": "heartbeat tick (every 30 min, 08:00-22:00 local)",
    },
    "reflect": {
        "env": "BRUNOS_REFLECT_HEALTHCHECK_URL",
        "timeout": 86400, "grace": 21600,
        "desc": "daily reflection (memory_reflect.py)",
    },
    "federation-doctor": {
        "env": "BRUNOS_FEDERATION_DOCTOR_HEALTHCHECK_URL",
        "timeout": 86400, "grace": 21600,
        "desc": "daily federation state-health verdict (federation_doctor.py --alert)",
    },
    "memory-doctor": {
        "env": "BRUNOS_MEMORY_DOCTOR_HEALTHCHECK_URL",
        "schedule": "15 9 * * *", "grace": 3600,
        "desc": "daily RAG index + search-canary health (memory_doctor.py)",
    },
    "comms-capture": {
        "env": "BRUNOS_COMMS_CAPTURE_HEALTHCHECK_URL",
        "schedule": "0 22 * * *", "grace": 3600,
        "desc": "comms-capture feeder (daily@22:00, Slack channel knowledge extraction)",
    },
    "slackbot-watchdog": {
        "env": "BRUNOS_SLACKBOT_HEALTHCHECK_URL",
        "timeout": 900, "grace": 1800,
        "desc": "slackbot watchdog (crash-loop/token/duplicate probe, every 15 min)",
    },
    "inbox-rsync": {
        "env": "BRUNOS_INBOX_RSYNC_HEALTHCHECK_URL",
        "timeout": 120, "grace": 600,
        "desc": "producer inbox rsync to VPS (sync_inbox.py, Mac-only)",
    },
    "linos-inbox-sync": {
        "env": "BRUNOS_LINOS_INBOX_SYNC_HEALTHCHECK_URL",
        "timeout": 86400, "grace": 21600,
        "desc": "cleared+in-scope inbox push to consumer brain (sync_cleared_inbox.py)",
    },
    "inbox-retire": {
        "env": "BRUNOS_INBOX_RETIRE_HEALTHCHECK_URL",
        "timeout": 86400, "grace": 21600,
        "desc": "producer-side local capture retirement (retire_local_inbox.py, Mac-only)",
    },
    "linos-consumer": {
        "env": "LINOS_CONSUMER_HEALTHCHECK_URL",
        "timeout": 86400, "grace": 21600,
        "desc": "company-brain consumer loop (linos_consumer.py)",
    },
    "linos-ack-sync": {
        "env": "LINOS_ACK_SYNC_HEALTHCHECK_URL",
        "schedule": "30 9 * * *", "grace": 3600,
        "desc": "federation ack return-leg: push LinOS acks → bruno (sync_acks.py)",
    },
}

# Sensible default subset for a fresh personal brain on one host (the LisaOS
# starter set): the two zero-code-change probes + the three core services.
DEFAULT_SERVICES = "heartbeat,reflect,vault-sync,memory-doctor,slackbot-watchdog"


def _log(msg: str) -> None:
    print(msg, flush=True)


# --- pure helpers (unit-tested in tests/test_provision_healthchecks.py) ---


def check_name(brain: str, svc: str, host: str) -> str:
    return f"{brain}-{svc}-{host}"


def env_var_for(svc: str, var_prefix: str) -> str:
    """The env var the reporter code reads, with the BRUNOS token swapped to
    --var-prefix. Only the leading BRUNOS is swapped (LINOS_* stays LINOS_*)."""
    env = SERVICE_CATALOG[svc]["env"]
    if var_prefix and var_prefix != "BRUNOS" and env.startswith("BRUNOS_"):
        return var_prefix + env[len("BRUNOS"):]
    return env


def build_check_payload(
    brain: str, svc: str, host: str, *, tz: str, channels: str
) -> dict:
    """The healthchecks.io v3 create/upsert body for one service check."""
    spec = SERVICE_CATALOG[svc]
    name = check_name(brain, svc, host)
    payload: dict = {
        "name": name,
        "slug": name,
        "tags": f"brain:{brain} svc:{svc} host:{host}",
        "desc": spec.get("desc", ""),
        "grace": spec["grace"],
        "channels": channels,
        "unique": ["name"],  # idempotent upsert: re-run = update in place
    }
    if "schedule" in spec:
        payload["schedule"] = spec["schedule"]
        payload["tz"] = tz
    else:
        payload["timeout"] = spec["timeout"]
    return payload


def env_block(results: list[dict], var_prefix: str) -> str:
    """The .env lines to paste into the instance, from provision results
    ([{svc, ping_url}, ...])."""
    lines = [f"{env_var_for(r['svc'], var_prefix)}={r['ping_url']}" for r in results]
    return "\n".join(lines)


def parse_services(arg: str) -> list[str]:
    svcs = [s.strip() for s in arg.split(",") if s.strip()]
    unknown = [s for s in svcs if s not in SERVICE_CATALOG]
    if unknown:
        raise ValueError(
            f"unknown service(s): {', '.join(unknown)} "
            f"(known: {', '.join(sorted(SERVICE_CATALOG))})"
        )
    return svcs


# --- API ---


def upsert_check(api_base: str, api_key: str, payload: dict) -> dict:
    """POST the upsert. Returns the check object. Raises RuntimeError with an
    actionable message on API errors."""
    req = urllib.request.Request(
        api_base.rstrip("/") + "/api/v3/checks/",
        data=json.dumps(payload).encode("utf-8"),
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            created = resp.status == 201
            body = json.loads(resp.read().decode("utf-8"))
            body["_created"] = created
            return body
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:200]
        except Exception:  # noqa: BLE001
            pass
        if e.code == 403:
            raise RuntimeError(
                f"403 for {payload['name']!r} — project check limit reached? "
                "(free tier = 20 checks; upgrade the Protostack account or "
                f"self-host healthchecks). {detail}"
            ) from e
        if e.code == 401:
            raise RuntimeError(
                "401 — invalid/missing API key. Use the project's READ-WRITE "
                "key (keys are project-scoped: the key selects the brain's project)."
            ) from e
        raise RuntimeError(f"{e.code} for {payload['name']!r}: {detail}") from e


# --- main ---


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Provision healthchecks.io checks for a brain (Track D Phase 2)")
    ap.add_argument("--brain", required=True, help="brain id, e.g. brunos / lisaos / colinas")
    ap.add_argument("--host", required=True, help="host label, e.g. vps / mac / prod")
    ap.add_argument("--services", default=DEFAULT_SERVICES,
                    help=f"comma-separated subset of the catalog (default: {DEFAULT_SERVICES})")
    ap.add_argument("--channels", default="*",
                    help='integrations to notify: "*" = all in the project (default), or comma-separated names/UUIDs')
    ap.add_argument("--tz", default=DEFAULT_TZ, help="timezone for cron-based checks")
    ap.add_argument("--var-prefix", default="BRUNOS",
                    help="env-var prefix the brain's code reads (swap only if the code was renamed)")
    ap.add_argument("--api-base", default=os.environ.get("HEALTHCHECKS_API_BASE", API_BASE),
                    help="healthchecks instance (override when self-hosting)")
    ap.add_argument("--dry-run", action="store_true", help="print payloads + env block, no HTTP")
    ap.add_argument("--json", action="store_true", dest="emit_json", help="machine-readable output")
    args = ap.parse_args(argv[1:])

    try:
        services = parse_services(args.services)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    api_key = os.environ.get("HEALTHCHECKS_API_KEY", "").strip()
    if not api_key and not args.dry_run:
        print("ERROR: set HEALTHCHECKS_API_KEY (the brain's project READ-WRITE key) "
              "or use --dry-run", file=sys.stderr)
        return 2

    payloads = [
        build_check_payload(args.brain, svc, args.host, tz=args.tz, channels=args.channels)
        for svc in services
    ]

    if args.dry_run:
        if args.emit_json:
            print(json.dumps({"dry_run": True, "payloads": payloads}, indent=2))
        else:
            _log(f"DRY-RUN: would upsert {len(payloads)} check(s) for "
                 f"{args.brain}@{args.host} → {args.api_base}")
            for p in payloads:
                cadence = p.get("schedule") or f"every {p['timeout']}s"
                _log(f"  {p['name']:40s} {cadence} (grace {p['grace']}s)")
            _log("\n# env block (ping URLs filled in on a real run):")
            for svc in services:
                _log(f"{env_var_for(svc, args.var_prefix)}=<ping_url>")
        return 0

    results: list[dict] = []
    for svc, payload in zip(services, payloads):
        try:
            check = upsert_check(args.api_base, api_key, payload)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        results.append({
            "svc": svc,
            "name": payload["name"],
            "ping_url": check.get("ping_url", ""),
            "created": check.get("_created", False),
            "status": check.get("status", "?"),
        })
        _log(f"  {'created' if check.get('_created') else 'updated'}  {payload['name']}")

    if args.emit_json:
        print(json.dumps({"brain": args.brain, "host": args.host, "checks": results}, indent=2))
    else:
        _log(f"\n{len(results)} check(s) upserted for {args.brain}@{args.host}.")
        _log("\n# --- paste into the instance's .claude/.env ---")
        _log(env_block(results, args.var_prefix))
        _log("\n# Reminder: wire the project's Slack integration (shared Protostack "
             "ops channel) in the healthchecks project settings — channels='*' "
             "binds checks to ALL project integrations.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
