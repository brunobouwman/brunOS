#!/usr/bin/env python3
"""Standalone tests for company_brain_reflect.py.

Run: uv run python tests/test_company_brain_reflect.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

import company_brain_reflect as cbr  # noqa: E402

_PASS = _FAIL = 0
BRT = timezone(timedelta(hours=-3))
FIXED_NOW = datetime(2026, 6, 7, 9, 15, 0, tzinfo=BRT)


def check(condition: bool, label: str) -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _make_vault(root: Path) -> Path:
    vault = root / "CompanyVault"
    _write(vault / "Memory/LINMEMORY.md", "# Company memory\n\nDurable company facts.\n")
    _write(vault / "Memory/DECISIONS.md", "# Decisions\n\n- Use GitHub as source of truth.\n")
    _write(vault / "Memory/STANDARDS.md", "# Standards\n\n- Document every process.\n")
    _write(vault / "Memory/projects/brain.md", "# Brain\n\nBootstrap company brain for clients.\n")
    _write(vault / "Memory/clients/acme.md", "# Acme\n\nClient onboarding placeholder.\n")
    _write(vault / "Memory/joint/colinas/2026-06-07-note.md", "# Joint note\n\nImported client context.\n")
    _write(vault / "Memory/_imports/legacy.md", "# Legacy import\n\nShould not be read.\n")
    _write(vault / "Memory/_inbox/sessions/project/capture.md", "# Inbox\n\nShould not be read.\n")
    return vault


async def _fake_llm_reflect(prompt_text: str, *, system_prompt: str, model: str) -> str:
    check("Memory/_imports/legacy.md" not in prompt_text, "reflection excludes _imports")
    check("Memory/_inbox/sessions/project/capture.md" not in prompt_text, "reflection excludes _inbox")
    return json.dumps(
        {
            "leadership": ["Company brain bootstrap is ready for dogfood."],
            "risks": ["Monitoring is not wired yet."],
            "decisions_needed": ["Decide promotion path for proposed playbooks."],
            "gaps": [
                {
                    "gap": "Client bootstrap checklist needs an owner",
                    "why_it_matters": "It keeps implementations repeatable.",
                    "suggested_owner": "ops",
                    "source_refs": ["Memory/projects/brain.md"],
                }
            ],
            "memory_candidates": ["Company brains should write reviewable artifacts first."],
            "standards_candidates": ["Promote playbook proposals only after human review."],
            "source_refs": ["Memory/LINMEMORY.md", "Memory/projects/brain.md"],
        }
    )


async def _fake_llm_dream(prompt_text: str, *, system_prompt: str, model: str) -> str:
    check("client bootstrap material" in system_prompt, "dream prompt keeps client bootstrap scope")
    return json.dumps(
        {
            "playbook_candidates": [
                {
                    "title": "Company Brain Bootstrap",
                    "category": "process",
                    "problem": "New client brains need repeatable setup.",
                    "proposed_process": ["Seed vault", "Configure timers", "Run dry-run"],
                    "evidence": ["Memory/projects/brain.md"],
                    "adoption_check": "Use after one successful LinOS dogfood run.",
                }
            ],
            "decision_questions": ["Which artifacts are promoted automatically?"],
            "source_refs": ["Memory/projects/brain.md"],
        }
    )


def _patch_clock_and_state(tmp: Path) -> None:
    cbr.STATE_DIR = tmp / "state"
    cbr.STATE_DIR.mkdir(parents=True, exist_ok=True)
    cbr.now_brt = lambda: FIXED_NOW
    cbr._ts_brt = lambda dt=None: (dt or FIXED_NOW).strftime("%Y-%m-%dT%H:%M:%S-03:00")


def test_collect_sources_is_company_safe() -> None:
    print("[test_collect_sources_is_company_safe]")
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(Path(tmpdir))
        docs = cbr._collect_sources(vault, since_days=30, max_docs=20)
        rels = {doc.rel for doc in docs}
        check("Memory/LINMEMORY.md" in rels, "collects company memory")
        check("Memory/projects/brain.md" in rels, "collects project docs")
        check("Memory/clients/acme.md" in rels, "collects client docs")
        check("Memory/joint/colinas/2026-06-07-note.md" in rels, "collects recent joint docs")
        check("Memory/_imports/legacy.md" not in rels, "does not collect legacy imports")
        check("Memory/_inbox/sessions/project/capture.md" not in rels, "does not collect inbox captures")


def test_reflect_dry_run_writes_nothing() -> None:
    print("[test_reflect_dry_run_writes_nothing]")
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        vault = _make_vault(root)
        _patch_clock_and_state(root)
        original = cbr._call_llm
        cbr._call_llm = _fake_llm_reflect
        try:
            out = cbr.run_reflect(profile="acme", vault=vault, dry_run=True, since_days=30)
        finally:
            cbr._call_llm = original
        check(out["profile"] == "acme", "dry-run keeps arbitrary profile")
        check(not (vault / "Memory/digests/leadership/2026-W23.md").exists(), "dry-run skips leadership write")
        check(not (vault / "Memory/MEMORY.md").exists(), "dry-run never creates personal MEMORY.md")


def test_reflect_writes_reviewable_artifacts() -> None:
    print("[test_reflect_writes_reviewable_artifacts]")
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        vault = _make_vault(root)
        _patch_clock_and_state(root)
        original = cbr._call_llm
        cbr._call_llm = _fake_llm_reflect
        try:
            out = cbr.run_reflect(profile="linos", vault=vault, dry_run=False, since_days=30)
        finally:
            cbr._call_llm = original
        leadership = vault / out["leadership_path"]
        gaps = vault / out["gaps_path"]
        check(leadership.exists(), "writes weekly leadership digest")
        check(gaps.exists(), "writes daily gaps digest")
        check("Company brain bootstrap is ready" in leadership.read_text(encoding="utf-8"), "leadership includes model result")
        check("Client bootstrap checklist" in gaps.read_text(encoding="utf-8"), "gaps include model result")
        check(not (vault / "Memory/MEMORY.md").exists(), "real run never creates personal MEMORY.md")


def test_dream_writes_proposed_playbook() -> None:
    print("[test_dream_writes_proposed_playbook]")
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        vault = _make_vault(root)
        _patch_clock_and_state(root)
        original = cbr._call_llm
        cbr._call_llm = _fake_llm_dream
        try:
            out = cbr.run_dream(profile="clientco", vault=vault, dry_run=False, since_days=30)
        finally:
            cbr._call_llm = original
        playbook = vault / out["playbook_path"]
        body = playbook.read_text(encoding="utf-8")
        check(playbook.exists(), "writes company playbook proposal")
        check("status: proposed" in body, "playbook remains proposed")
        check("Company Brain Bootstrap" in body, "playbook includes candidate")
        check("profile: clientco" in body, "playbook preserves generic client profile")


def main() -> int:
    test_collect_sources_is_company_safe()
    test_reflect_dry_run_writes_nothing()
    test_reflect_writes_reviewable_artifacts()
    test_dream_writes_proposed_playbook()
    print(f"\nPASS={_PASS} FAIL={_FAIL}")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
