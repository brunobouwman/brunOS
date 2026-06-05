#!/usr/bin/env python3
"""Standalone tests for provision_healthchecks.py (no pytest).
Run: uv run python tests/test_provision_healthchecks.py

Covers: naming/tag conventions, cron vs period payloads, the unique-upsert
field, env-var prefix swapping (BRUNOS→LISAOS swaps, LINOS_* untouched),
env-block emission, service parsing, dry-run never touching the network, and
actionable 403/401 error mapping.
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
from pathlib import Path

os.environ["BRUNOS_DISABLE_REPORTING"] = "1"
os.environ.pop("HEALTHCHECKS_API_KEY", None)

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

import provision_healthchecks as ph  # noqa: E402

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
    print("== conventions ==")
    check(ph.check_name("lisaos", "heartbeat", "vps") == "lisaos-heartbeat-vps",
          "check name <brain>-<svc>-<host>")

    p = ph.build_check_payload("lisaos", "heartbeat", "vps",
                               tz="America/Sao_Paulo", channels="*")
    check(p["name"] == "lisaos-heartbeat-vps" and p["slug"] == p["name"],
          "payload name == slug")
    check(p["tags"] == "brain:lisaos svc:heartbeat host:vps", "tag convention")
    check(p["unique"] == ["name"], "upsert via unique:[name]")
    check(p["channels"] == "*", "channels passthrough")

    print("== cron vs period ==")
    check("schedule" in p and p["schedule"] == "0,30 8-22 * * *"
          and p["tz"] == "America/Sao_Paulo" and "timeout" not in p,
          "heartbeat → cron check with tz, no timeout")
    pv = ph.build_check_payload("lisaos", "vault-sync", "vps",
                                tz="America/Sao_Paulo", channels="*")
    check(pv.get("timeout") == 120 and "schedule" not in pv and "tz" not in pv,
          "vault-sync → period check, no schedule/tz")
    pm = ph.build_check_payload("brunos", "memory-doctor", "vps",
                                tz="America/Sao_Paulo", channels="*")
    check(pm.get("schedule") == "15 9 * * *" and pm["grace"] == 3600,
          "memory-doctor → daily cron 09:15 + 1h grace")

    print("== env vars ==")
    check(ph.env_var_for("heartbeat", "BRUNOS") == "BRUNOS_HEARTBEAT_HEALTHCHECK_URL",
          "default prefix keeps BRUNOS_*")
    check(ph.env_var_for("heartbeat", "LISAOS") == "LISAOS_HEARTBEAT_HEALTHCHECK_URL",
          "prefix swap BRUNOS→LISAOS")
    check(ph.env_var_for("vault-sync", "LISAOS") == "LISAOS_HEALTHCHECK_URL",
          "legacy vault-sync var swaps cleanly")
    check(ph.env_var_for("linos-consumer", "LISAOS") == "LINOS_CONSUMER_HEALTHCHECK_URL",
          "LINOS_* vars are never prefix-swapped")

    blk = ph.env_block(
        [{"svc": "heartbeat", "ping_url": "https://hc-ping.com/aaa"},
         {"svc": "vault-sync", "ping_url": "https://hc-ping.com/bbb"}],
        "BRUNOS",
    )
    check(blk == ("BRUNOS_HEARTBEAT_HEALTHCHECK_URL=https://hc-ping.com/aaa\n"
                  "BRUNOS_HEALTHCHECK_URL=https://hc-ping.com/bbb"),
          "env block emission")

    print("== service parsing ==")
    check(ph.parse_services("heartbeat, reflect") == ["heartbeat", "reflect"],
          "parses + strips")
    try:
        ph.parse_services("heartbeat,nope")
        check(False, "unknown service raises")
    except ValueError as e:
        check("nope" in str(e), "unknown service raises with the name")
    check(all(s in ph.SERVICE_CATALOG for s in ph.parse_services(ph.DEFAULT_SERVICES)),
          "default service set is all-known")

    print("== dry-run makes no HTTP calls ==")
    def _boom(*a, **k):  # noqa: ANN001, ANN003
        raise AssertionError("network touched during --dry-run")
    orig = ph.urllib.request.urlopen
    ph.urllib.request.urlopen = _boom
    try:
        out = io.StringIO()
        old_stdout, sys.stdout = sys.stdout, out
        try:
            rc = ph.main(["prog", "--brain", "lisaos", "--host", "vps", "--dry-run"])
        finally:
            sys.stdout = old_stdout
        check(rc == 0, "dry-run exits 0 without an API key")
        check("lisaos-heartbeat-vps" in out.getvalue(), "dry-run prints the checks")
        check("BRUNOS_HEARTBEAT_HEALTHCHECK_URL" in out.getvalue(),
              "dry-run prints the env block skeleton")
    finally:
        ph.urllib.request.urlopen = orig

    rc = ph.main(["prog", "--brain", "lisaos", "--host", "vps"])
    check(rc == 2, "real run without API key → config error (2)")
    rc = ph.main(["prog", "--brain", "x", "--host", "y", "--services", "bogus", "--dry-run"])
    check(rc == 2, "unknown service → config error (2)")

    print("== API error mapping ==")
    class _Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body
        def read(self):
            return json.dumps(self._body).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _mk_http_error(code):
        return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b"{}"))

    ph.urllib.request.urlopen = lambda req, timeout=30: (_ for _ in ()).throw(_mk_http_error(403))
    try:
        ph.upsert_check("https://hc.example", "k", {"name": "n"})
        check(False, "403 raises")
    except RuntimeError as e:
        check("limit" in str(e), "403 → actionable check-limit message")
    ph.urllib.request.urlopen = lambda req, timeout=30: (_ for _ in ()).throw(_mk_http_error(401))
    try:
        ph.upsert_check("https://hc.example", "k", {"name": "n"})
        check(False, "401 raises")
    except RuntimeError as e:
        check("READ-WRITE" in str(e), "401 → wrong-key message")

    print("== upsert happy path ==")
    captured = {}
    def _ok(req, timeout=30):
        captured["url"] = req.full_url
        captured["key"] = req.get_header("X-api-key")
        captured["body"] = json.loads(req.data.decode())
        return _Resp(201, {"ping_url": "https://hc-ping.com/zzz", "status": "new"})
    ph.urllib.request.urlopen = _ok
    try:
        res = ph.upsert_check("https://hc.example", "secret",
                              ph.build_check_payload("lisaos", "reflect", "vps",
                                                     tz="UTC", channels="*"))
        check(captured["url"] == "https://hc.example/api/v3/checks/", "v3 endpoint")
        check(captured["key"] == "secret", "X-Api-Key header set")
        check(captured["body"]["unique"] == ["name"], "upsert body carries unique")
        check(res["_created"] is True and res["ping_url"].endswith("/zzz"),
              "201 → _created + ping_url surfaced")
    finally:
        ph.urllib.request.urlopen = orig

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
