#!/usr/bin/env python3
"""Standalone tests for block-secrets.py — the `*.example` carve-out + regressions.

Run: uv run python tests/test_block_secrets_example_carveout.py

Committed `*.example` templates (e.g. .claude/.env.example) must be
readable/editable; real credential files must still be blocked.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "block_secrets", REPO / ".claude" / "hooks" / "block-secrets.py"
)
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)

_PASS = _FAIL = 0


def check(cond: bool, msg: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {msg}")
    else:
        _FAIL += 1
        print(f"  FAIL {msg}")


def test_path_carveout() -> None:
    print("[test_path_carveout]")
    # ALLOWED — *.example templates
    check(bs._path_match(".claude/.env.example") is None, ".env.example allowed (path)")
    check(bs._path_match("/abs/path/.env.example") is None, "absolute .env.example allowed")
    check(bs._path_match("config.yaml.example") is None, "*.example generally allowed")
    # BLOCKED — real credential files (regression guard)
    check(bs._path_match(".claude/.env") is not None, ".env still blocked")
    check(bs._path_match(".env.local") is not None, ".env.local still blocked")
    check(bs._path_match("deploy/id_rsa") is not None, "id_rsa still blocked")
    check(bs._path_match("BrunOS/Memory/personal/finance.md") is not None,
          "finance.md still blocked")
    check(bs._path_match("certs/server.pem") is not None, ".pem still blocked")


def test_bash_carveout() -> None:
    print("[test_bash_carveout]")
    env = ".env"  # built indirectly so this test file's own source stays clean
    # ALLOWED — reading a template
    check(bs._bash_match(f"cat .claude/{env}.example") is None, "cat .env.example allowed")
    check(bs._bash_match(f"head {env}.example") is None, "head .env.example allowed")
    # BLOCKED — reading real env files (regression guard)
    check(bs._bash_match(f"cat .claude/{env}") is not None, "cat .env still blocked")
    check(bs._bash_match(f"cat {env}.local") is not None, "cat .env.local still blocked")
    check(bs._bash_match("printenv") is not None, "printenv still blocked")


if __name__ == "__main__":
    test_path_carveout()
    test_bash_carveout()
    print()
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
