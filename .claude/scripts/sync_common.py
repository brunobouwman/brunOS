#!/usr/bin/env python3
"""Shared reliability runtime for BrunOS git-sync services (vault + code).

One status-file + rate-limited Slack alert + healthchecks.io dead-man's-switch +
run-lock implementation, parametrized per service, so the two syncs can't drift.
"""

from __future__ import annotations

import fcntl
import os
import socket
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import _ts_brt, load_state, now_brt, save_state  # noqa: E402

NET_TIMEOUT = 90
ALERT_REPEAT_SECONDS = 3600
SCHEMA_VERSION = 1


class GitError(Exception):
    def __init__(self, cmd: str, cp: subprocess.CompletedProcess):
        self.cmd = cmd
        self.cp = cp
        tail = (cp.stderr or cp.stdout or "").strip().splitlines()
        super().__init__(
            f"git {cmd} failed (rc={cp.returncode}): {tail[-1] if tail else '(no output)'}"
        )


def git(cwd: Path, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )


def git_ok(cwd: Path, *args: str, timeout: float | None = None) -> str:
    cp = git(cwd, *args, timeout=timeout)
    if cp.returncode != 0:
        raise GitError(" ".join(args), cp)
    return cp.stdout.strip()


def count(cwd: Path, rng: str) -> int:
    return int(git_ok(cwd, "rev-list", "--count", rng) or "0")


def is_push_reject(cp: subprocess.CompletedProcess) -> bool:
    blob = f"{cp.stdout}\n{cp.stderr}".lower()
    return any(
        s in blob
        for s in ("rejected", "non-fast-forward", "fetch first", "updates were rejected")
    )


def host_label() -> str:
    lbl = os.environ.get("BRUNOS_SYNC_HOST_LABEL", "").strip()
    return lbl or (socket.gethostname() or "unknown").split(".")[0]


def older_than(ts_str: str | None, seconds: float) -> bool:
    if not ts_str:
        return True
    try:
        then = datetime.fromisoformat(ts_str)
    except ValueError:
        return True
    return (now_brt() - then).total_seconds() > seconds


class SyncReporter:
    """Status file + rate-limited Slack alert + healthcheck ping + run-lock (per service)."""

    def __init__(self, *, service: str, status_file, lock_file, healthcheck_env: str):
        self.service = service
        self.status_file = Path(status_file)
        self.lock_file = Path(lock_file)
        self.healthcheck_env = healthcheck_env
        self._lock_fd: int | None = None

    def log(self, msg: str) -> None:
        print(f"[{self.service}] {msg}", flush=True)

    def send_alert(self, text: str) -> bool:
        self.log(f"ALERT: {text.splitlines()[0]}")
        channel = os.environ.get("BRUNOS_ALERT_CHANNEL", "").strip()
        if not channel:
            self.log("ALERT: BRUNOS_ALERT_CHANNEL unset — skipping Slack send")
            return False
        try:
            from integrations import slack

            slack.send_message(slack._client(), channel=channel, text=text)
            return True
        except Exception as e:  # noqa: BLE001
            self.log(f"ALERT: Slack send failed: {type(e).__name__}: {e}")
            return False

    def healthcheck(self, success: bool) -> None:
        url = os.environ.get(self.healthcheck_env, "").strip()
        if not url:
            return
        target = url if success else url.rstrip("/") + "/fail"
        try:
            urllib.request.urlopen(target, timeout=10)  # noqa: S310
        except Exception as e:  # noqa: BLE001
            self.log(f"healthcheck ping ({'ok' if success else 'fail'}) failed: {type(e).__name__}")

    def load(self) -> dict:
        return load_state(self.status_file, default={}) or {}

    def record_success(self, state: dict, attempt_ts: str, extra: dict | None = None) -> None:
        state.update(
            {
                "_schema_version": SCHEMA_VERSION,
                "service": self.service,
                "host": host_label(),
                "last_success": _ts_brt(),
                "last_attempt": attempt_ts,
                "last_error": None,
                "consecutive_failures": 0,
            }
        )
        if extra:
            state.update(extra)
        save_state(self.status_file, state)
        self.healthcheck(success=True)

    def record_failure(
        self, state: dict, attempt_ts: str, kind: str, msg: str, paths: list[str] | None = None
    ) -> None:
        fails = int(state.get("consecutive_failures", 0)) + 1
        sig = f"{kind}:{','.join(paths) if paths else ''}"
        last_sig = (state.get("last_error") or {}).get("signature")
        last_alert = state.get("last_alert_ts")
        state.update(
            {
                "_schema_version": SCHEMA_VERSION,
                "service": self.service,
                "host": host_label(),
                "last_attempt": attempt_ts,
                "last_error": {
                    "type": kind,
                    "msg": msg,
                    "paths": paths or [],
                    "signature": sig,
                    "ts": attempt_ts,
                },
                "consecutive_failures": fails,
            }
        )
        if fails == 1 or sig != last_sig or older_than(last_alert, ALERT_REPEAT_SECONDS):
            if self.send_alert(
                f"⚠ {self.service} FAILED on {host_label()} [{kind}] (failure #{fails})\n{msg}"
            ):
                state["last_alert_ts"] = _ts_brt()
        save_state(self.status_file, state)
        self.healthcheck(success=False)

    def try_lock(self) -> bool:
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd = fd
            return True
        except OSError:
            os.close(fd)
            return False

    def unlock(self) -> None:
        if self._lock_fd is None:
            return
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(self._lock_fd)
            self._lock_fd = None
