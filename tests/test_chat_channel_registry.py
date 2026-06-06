#!/usr/bin/env python3
"""Standalone tests for company chat channel registry (no pytest).

Run: uv run python tests/test_chat_channel_registry.py
"""

from __future__ import annotations

import importlib.util
import json
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
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_config(vault: Path, channels: dict) -> None:
    cfg = vault / "Memory" / "Brain" / "brain-config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"channels": channels}, indent=2), encoding="utf-8")


def _registry(vault: Path, state_cfg: Path):
    os.environ["BRUNOS_VAULT_PATH"] = str(vault)
    os.environ["CHAT_CHANNEL_REGISTRY_STATE_CONFIG"] = str(state_cfg)
    sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))
    sys.path.insert(0, str(REPO_ROOT / ".claude"))
    import shared  # noqa: PLC0415

    shared.vault_path.cache_clear()
    return _load_module(
        "chat_channel_registry_test",
        REPO_ROOT / ".claude" / "chat" / "channel_registry.py",
    )


def test_fail_closed_unknown_channel() -> None:
    print("[test_fail_closed_unknown_channel]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        vault = root / "LinOS"
        _write_config(vault, {})
        reg = _registry(vault, root / "missing-state.json")
        decision = reg.resolve_slack_event({"channel": "C_UNKNOWN", "user": "U_BRUNO"})
        check(decision.allowed is False, "unknown channel is refused")
        check(decision.reason == "unknown_channel", "reason is unknown_channel")


def test_fail_closed_unknown_user() -> None:
    print("[test_fail_closed_unknown_user]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        vault = root / "LinOS"
        _write_config(
            vault,
            {
                "slack:C_TEST": {
                    "status": "enabled",
                    "default_persona": "company-query",
                    "allowed_personas": ["company-query"],
                    "allowed_users": ["U_BRUNO"],
                    "allowed_sources": ["LINMEMORY"],
                    "ingestion_mode": "ask-only",
                }
            },
        )
        reg = _registry(vault, root / "missing-state.json")
        decision = reg.resolve_slack_event({"channel": "C_TEST", "user": "U_OTHER"})
        check(decision.allowed is False, "unlisted user is refused")
        check(decision.reason == "unknown_user", "reason is unknown_user")


def test_allowed_channel_context() -> None:
    print("[test_allowed_channel_context]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        vault = root / "LinOS"
        _write_config(
            vault,
            {
                "slack:C_TEST": {
                    "name": "founder-test",
                    "status": "enabled",
                    "default_persona": "company-query",
                    "allowed_personas": ["company-query", "company-judge"],
                    "allowed_users": ["U_BRUNO"],
                    "allowed_sources": ["LINMEMORY", "projects", "standards"],
                    "write_targets": [],
                    "ingestion_mode": "ask-only",
                    "external_action": "answer-only",
                }
            },
        )
        reg = _registry(vault, root / "missing-state.json")
        decision = reg.resolve_slack_event({"channel": "C_TEST", "user": "U_BRUNO"})
        context = reg.render_context(decision)
        check(decision.allowed is True, "listed user in enabled channel is allowed")
        check(decision.persona == "company-query", "default persona resolved")
        check(decision.ingestion_mode == "ask-only", "ingestion mode resolved")
        check('channel="slack:C_TEST"' in context, "context includes channel key")
        check('allowed_sources="LINMEMORY,projects,standards"' in context,
              "context includes allowed sources")


if __name__ == "__main__":
    test_fail_closed_unknown_channel()
    test_fail_closed_unknown_user()
    test_allowed_channel_context()
    print(f"\npassed={_PASS} failed={_FAIL}")
    raise SystemExit(1 if _FAIL else 0)
