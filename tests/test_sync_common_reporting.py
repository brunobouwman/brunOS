#!/usr/bin/env python3
"""Standalone tests for Track D Phase 1 additions to sync_common (no pytest).
Run: uv run python tests/test_sync_common_reporting.py

Covers: healthcheck ping-with-body (POST body = status.json, /fail suffix,
body truncation, body errors never block the ping), make_reporter naming +
BRUNOS_DISABLE_REPORTING kill-switch, and report_outcome success/failure wiring.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Must be set BEFORE importing sync_common consumers — but make_reporter reads
# it at call time, so toggling per-check below works too.
os.environ.pop("BRUNOS_DISABLE_REPORTING", None)
os.environ.pop("BRUNOS_ALERT_CHANNEL", None)  # never attempt a Slack send in tests

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

import sync_common  # noqa: E402
from sync_common import SyncReporter, make_reporter, report_outcome  # noqa: E402

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


class _CapturedPing:
    """Monkeypatch target for urllib.request.urlopen capturing the Request."""

    def __init__(self):
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)

        class _Resp:
            def read(self):
                return b"OK"

        return _Resp()


def _tmp_reporter(tmp: Path, env_name: str) -> SyncReporter:
    return SyncReporter(
        service="test-svc",
        status_file=tmp / "test-svc-state.json",
        lock_file=tmp / "locks" / "test-svc.run.lock",
        healthcheck_env=env_name,
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="sync-common-test-"))
    env_name = "TEST_SVC_HEALTHCHECK_URL"
    os.environ[env_name] = "https://hc-ping.example/abc"

    captured = _CapturedPing()
    orig_urlopen = sync_common.urllib.request.urlopen
    sync_common.urllib.request.urlopen = captured
    try:
        print("== healthcheck ping-with-body ==")
        r = _tmp_reporter(tmp, env_name)

        r.healthcheck(success=True)
        check(captured.requests[-1].full_url == "https://hc-ping.example/abc",
              "success pings the bare URL")
        check(captured.requests[-1].data is None, "no body → GET-style ping")

        r.healthcheck(success=False)
        check(captured.requests[-1].full_url == "https://hc-ping.example/abc/fail",
              "failure pings <url>/fail")

        r.healthcheck(success=True, body={"a": 1, "nested": {"b": [1, 2]}})
        body = captured.requests[-1].data
        check(body is not None and json.loads(body.decode()) == {"a": 1, "nested": {"b": [1, 2]}},
              "body is the JSON-serialized state")
        check(captured.requests[-1].get_header("Content-type") == "application/json",
              "body ping sets Content-Type json")

        big = {"blob": "x" * (sync_common.PING_BODY_MAX_BYTES + 10_000)}
        r.healthcheck(success=True, body=big)
        check(len(captured.requests[-1].data) <= sync_common.PING_BODY_MAX_BYTES,
              "oversized body truncated under the ping cap")

        r.healthcheck(success=True, body={"bad": object()})  # non-JSON value
        sent_raw = captured.requests[-1].data
        check(sent_raw is not None and "object" in sent_raw.decode(),
              "non-JSON value stringified via default=str — ping still carries a body")

        print("== record_success / record_failure carry the body ==")
        n_before = len(captured.requests)
        st = r.load()
        r.record_success(st, "2026-06-03T10:00:00-03:00", extra={"k": "v"})
        check(len(captured.requests) == n_before + 1, "record_success pings once")
        sent = json.loads(captured.requests[-1].data.decode())
        check(sent.get("k") == "v" and sent.get("service") == "test-svc",
              "success ping body is the saved status state")

        st = r.load()
        r.record_failure(st, "2026-06-03T10:01:00-03:00", kind="boom", msg="it broke")
        check(captured.requests[-1].full_url.endswith("/fail"),
              "record_failure pings /fail")
        sent = json.loads(captured.requests[-1].data.decode())
        check((sent.get("last_error") or {}).get("type") == "boom",
              "failure ping body carries last_error")
        on_disk = json.loads((tmp / "test-svc-state.json").read_text())
        check(on_disk.get("consecutive_failures") == 1,
              "status file recorded the failure")

        print("== make_reporter ==")
        rep = make_reporter("some-svc", env_name)
        check(rep is not None and rep.status_file.name == "some-svc-state.json",
              "make_reporter applies the <service>-state.json convention")
        check(rep.lock_file.name == "some-svc.run.lock",
              "make_reporter applies the lock convention")
        os.environ["BRUNOS_DISABLE_REPORTING"] = "1"
        check(make_reporter("some-svc", env_name) is None,
              "BRUNOS_DISABLE_REPORTING=1 → make_reporter returns None")
        os.environ.pop("BRUNOS_DISABLE_REPORTING")

        print("== report_outcome ==")
        report_outcome(None, ok=True)  # must be a silent no-op
        check(True, "report_outcome(None) is a no-op")
        r2 = _tmp_reporter(tmp, env_name)
        report_outcome(r2, ok=True, extra={"x": 1})
        st2 = json.loads(r2.status_file.read_text())
        check(st2.get("x") == 1 and st2.get("last_error") is None,
              "report_outcome ok=True records success + extra")
        report_outcome(r2, ok=False, kind="kindly", msg="nope")
        st2 = json.loads(r2.status_file.read_text())
        check((st2.get("last_error") or {}).get("type") == "kindly",
              "report_outcome ok=False records failure kind")
    finally:
        sync_common.urllib.request.urlopen = orig_urlopen
        os.environ.pop(env_name, None)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
