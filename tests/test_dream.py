#!/usr/bin/env python3
"""Standalone tests for memory_dream (no pytest, Haiku stubbed).
Run: uv run python tests/test_dream.py

Covers: tolerant JSON parse, the adaptive gate (skip < trigger, no model call),
enabled-kinds filtering, entry rendering (process vs decision, low-confidence →
provisional + confidence:low), confidentiality scrub, idempotent question enqueue,
and the dry-run vs write paths (watermark advance + queued questions).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "memory_dream", REPO / ".claude" / "scripts" / "memory_dream.py"
)
md = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(md)

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


class _patch:
    def __init__(self, **kw):
        self.kw = kw
        self.orig = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.orig[k] = getattr(md, k)
            setattr(md, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.orig.items():
            setattr(md, k, v)


def _stub_reason(payload):
    async def _fake(prompt_text, *, model, system_prompt):
        return payload
    return _fake


def _caps(n, start_day=1):
    """n fake captures: (created_iso, Path, fm, body) ascending."""
    out = []
    for i in range(n):
        created = f"2026-06-{start_day + i:02d}T10:00:00-03:00"
        out.append((created, Path(f"/tmp/c{i}.md"), {"project": "vertik"}, f"body {i}"))
    return out


PROCESS_JSON = json.dumps([{
    "kind": "process", "category": "process", "name": "Verify eval fails pre-fix",
    "when_to_use": "before declaring a regression fixed",
    "technique": "run the eval on pre-fix code; confirm it FAILS, then fix",
    "identifiers_present": False,
}])

LOWCONF_DECISION_JSON = json.dumps([{
    "kind": "decision", "name": "Worktree isolation for parallel agents",
    "decision": "use git worktrees for concurrent dev-task agents",
    "context": "two sessions on a shared checkout collided",
    "inferred_rationale": "avoid branch hijack",
    "confidence": 0.3,
    "alternatives": ["shared checkout with locks"],
    "reversal_conditions": ["single-agent only"],
    "source_refs": ["c0.md"],
}])


def test_parse_items_tolerant():
    print("[test_parse_items_tolerant]")
    check(md._parse_items("[]") == [], "empty array")
    check(md._parse_items("garbage no array") is None, "no array → None")
    fenced = "```json\n" + PROCESS_JSON + "\n```"
    out = md._parse_items(fenced)
    check(out and out[0]["name"] == "Verify eval fails pre-fix", "fenced array parsed")
    bad = json.dumps([{"kind": "nope", "name": "x"}, {"kind": "process", "name": ""}])
    check(md._parse_items(bad) == [], "invalid kind + empty name filtered")


def test_enabled_kinds():
    print("[test_enabled_kinds]")
    with _patch(brain_config=_FakeCfg({"dreaming.extract": ["processes", "decisions"]})):
        check(md._enabled_kinds() == {"process", "pattern", "prompt", "decision"}, "both")
    with _patch(brain_config=_FakeCfg({"dreaming.extract": ["decisions"]})):
        check(md._enabled_kinds() == {"decision"}, "decisions only")


class _FakeCfg:
    """Minimal brain_config stand-in returning configured values, else real defaults."""
    def __init__(self, overrides):
        self.overrides = overrides

    def get(self, path=None):
        if path in self.overrides:
            return self.overrides[path]
        return md._real_brain_config.get(path)


md._real_brain_config = md.brain_config  # snapshot real module for fallback


def test_render_process_entry():
    print("[test_render_process_entry]")
    item = json.loads(PROCESS_JSON)[0]
    slug, content, provisional = md._render_entry(item, excluded=frozenset(), threshold=0.6)
    check(slug == "verify-eval-fails-pre-fix", f"slug derived ({slug})")
    check(provisional is False, "process never provisional")
    check("category: process" in content, "category in frontmatter")
    check("## Technique" in content, "technique section present")
    check("confidence:" not in content, "no confidence field on a process")


def test_render_lowconf_decision_provisional():
    print("[test_render_lowconf_decision_provisional]")
    item = json.loads(LOWCONF_DECISION_JSON)[0]
    slug, content, provisional = md._render_entry(item, excluded=frozenset(), threshold=0.6)
    check(provisional is True, "confidence 0.3 < 0.6 → provisional")
    check("confidence: low" in content, "confidence:low in frontmatter")
    check("rationale question is open" in content, "open-question note in body")
    check("## Reversal conditions" in content, "reversal section present")
    # high-confidence variant
    item2 = dict(item, confidence=0.9)
    _, content2, prov2 = md._render_entry(item2, excluded=frozenset(), threshold=0.6)
    check(prov2 is False and "confidence: high" in content2, "confidence 0.9 → high, not provisional")


def test_scrub_applied():
    print("[test_scrub_applied]")
    item = dict(json.loads(PROCESS_JSON)[0],
                technique="connect to db at postgres://u:p@host/db and ping 10.1.2.3")
    _, content, _ = md._render_entry(item, excluded=frozenset(), threshold=0.6)
    check("postgres://" not in content, "connection string scrubbed")
    check("10.1.2.3" not in content, "internal IP scrubbed")


def test_enqueue_idempotent():
    print("[test_enqueue_idempotent]")
    with tempfile.TemporaryDirectory() as td:
        q = Path(td) / "decision_questions.json"
        with _patch(DECISION_QUESTIONS_PATH=q):
            item = json.loads(LOWCONF_DECISION_JSON)[0]
            a = md._enqueue_question(item, "ref1", ["c0.md"], 0.3)
            b = md._enqueue_question(item, "ref1", ["c0.md"], 0.3)
        check(a is True and b is False, "second enqueue of same ref_id is a no-op")
        queue = json.loads(q.read_text())
        check(len(queue) == 1, "queue has one entry")
        check(queue[0]["asked"] is False and queue[0]["answered"] is False, "fresh flags")


def test_adaptive_gate_skips_without_model_call():
    print("[test_adaptive_gate_skips_without_model_call]")
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("model must not be called when gated")

    with _patch(_gather_captures=lambda floor: _caps(3),
                _reason=_boom,
                brain_config=_FakeCfg({"dreaming.trigger_min_captures": 5}),
                _log=lambda *a, **k: None):
        rc = md._run(dry_run=True, since_days=None)
    check(rc == 0, "gated run returns 0")
    check(called["n"] == 0, "no model call below trigger")


def test_run_writes_entries_and_advances_watermark():
    print("[test_run_writes_entries_and_advances_watermark]")
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        dream_state = Path(td) / "dream.json"
        q = Path(td) / "decision_questions.json"
        caps = _caps(6)
        with _patch(
            vault_path=lambda: vault,
            _gather_captures=lambda floor: caps,
            _reason=_stub_reason(LOWCONF_DECISION_JSON),
            _dedup_is_duplicate=lambda q_: False,
            DREAM_STATE_PATH=dream_state,
            DECISION_QUESTIONS_PATH=q,
            brain_config=_FakeCfg({"dreaming.trigger_min_captures": 5}),
            _log=lambda *a, **k: None,
        ):
            rc = md._run(dry_run=False, since_days=None)
        check(rc == 0, "run returns 0")
        files = list((vault / "Memory" / "playbook").glob("*.md"))
        check(len(files) == 1, f"one playbook entry written ({len(files)})")
        check("confidence: low" in files[0].read_text(), "low-confidence decision written provisional")
        state = json.loads(dream_state.read_text())
        check(state["watermark"] == caps[-1][0], "watermark advanced to newest capture")
        queue = json.loads(q.read_text())
        check(len(queue) == 1, "rationale question enqueued for low-confidence decision")


def test_dry_run_writes_nothing():
    print("[test_dry_run_writes_nothing]")
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        dream_state = Path(td) / "dream.json"
        with _patch(
            vault_path=lambda: vault,
            _gather_captures=lambda floor: _caps(6),
            _reason=_stub_reason(PROCESS_JSON),
            _dedup_is_duplicate=lambda q_: False,
            DREAM_STATE_PATH=dream_state,
            brain_config=_FakeCfg({"dreaming.trigger_min_captures": 5}),
            _log=lambda *a, **k: None,
        ):
            rc = md._run(dry_run=True, since_days=None)
        check(rc == 0, "dry-run returns 0")
        check(not (vault / "Memory" / "playbook").exists(), "no playbook dir created")
        check(not dream_state.exists(), "watermark NOT advanced in dry-run")


def main():
    test_parse_items_tolerant()
    test_enabled_kinds()
    test_render_process_entry()
    test_render_lowconf_decision_provisional()
    test_scrub_applied()
    test_enqueue_idempotent()
    test_adaptive_gate_skips_without_model_call()
    test_run_writes_entries_and_advances_watermark()
    test_dry_run_writes_nothing()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
