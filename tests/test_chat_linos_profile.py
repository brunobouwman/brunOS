#!/usr/bin/env python3
"""Standalone tests for the LinOS chat profile (no pytest).

Run: uv run python tests/test_chat_linos_profile.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_PASS = _FAIL = 0


def check(condition: bool, label: str) -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_linos_vault(vault: Path) -> None:
    memory = vault / "Memory"
    memory.mkdir(parents=True)
    for name, marker in {
        "SOUL.md": "LinOS soul marker",
        "USER.md": "LinOS user marker",
        "LINMEMORY.md": "LinOS durable company memory marker",
        "STANDARDS.md": "LinOS standards marker",
        "DECISIONS.md": "LinOS decisions marker",
        "ROUTINES.md": "LinOS routines marker",
        "ACCESS_POLICY.md": "LinOS access policy marker",
        "_excluded-people.md": "LinOS excluded people marker",
        "_brain-filing-rules.md": "LinOS filing rules marker",
    }.items():
        (memory / name).write_text(f"---\ntype: system\ntags:\n  - test\n---\n\n{marker}\n")


def test_linos_prompt_uses_company_context() -> None:
    print("[test_linos_prompt_uses_company_context]")
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "LinOS"
        _seed_linos_vault(vault)
        os.environ["BRUNOS_VAULT_PATH"] = str(vault)
        os.environ["CHAT_BRAIN_PROFILE"] = "linos"

        sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))
        sys.path.insert(0, str(REPO_ROOT / ".claude"))
        sp = _load_module(
            "chat_system_prompt_test", REPO_ROOT / ".claude" / "chat" / "system_prompt.py"
        )
        prompt = sp.build_chat_system_prompt()

        check("You are LinOS" in prompt, "prompt uses LinOS identity")
        check("channel/group context" in prompt, "prompt carries channel-scope boundary")
        check("fail closed" in prompt, "prompt fails closed on unknown scope")
        check("LinOS durable company memory marker" in prompt, "LINMEMORY.md loaded")
        check("LinOS access policy marker" in prompt, "ACCESS_POLICY.md loaded")
        check("HEARTBEAT.md / HABITS.md" not in prompt, "BrunOS personal tail omitted")


def test_session_manager_flush_can_be_disabled() -> None:
    print("[test_session_manager_flush_can_be_disabled]")
    sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))
    sys.path.insert(0, str(REPO_ROOT / ".claude"))
    sm_mod = _load_module(
        "chat_session_manager_test",
        REPO_ROOT / ".claude" / "chat" / "session_manager.py",
    )

    called = {"dispatch": False}

    def fake_dispatch(*args, **kwargs):  # noqa: ARG001
        called["dispatch"] = True

    sm_mod.dispatch_flush = fake_dispatch
    with tempfile.TemporaryDirectory() as td:
        mgr = sm_mod.SessionManager(
            options_factory=lambda resume=None: None,
            db_path=Path(td) / "chat.db",
            flush_enabled=False,
        )
        mgr._session_ids["C123:1"] = "sdk-session"
        mgr._flush_thread("C123:1")
        check(called["dispatch"] is False, "dispatch_flush not called when disabled")


if __name__ == "__main__":
    test_linos_prompt_uses_company_context()
    test_session_manager_flush_can_be_disabled()
    print(f"\npassed={_PASS} failed={_FAIL}")
    raise SystemExit(1 if _FAIL else 0)
