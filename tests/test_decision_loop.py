#!/usr/bin/env python3
"""Standalone tests for the decision-rationale loop (no pytest).
Run: uv run python tests/test_decision_loop.py

Covers notify_adapter selection (slack/none/unknown), NoneAdapter sends nothing,
SlackAdapter with no target does not send, rate-limited delivery marks `asked`
only on confirmed delivery, and answer reconciliation patches the playbook entry
(confidence low→high + confirmed rationale) and marks the question answered.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / ".claude" / "scripts" / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

md = _load("memory_dream", "memory_dream.py")
na = _load("notify_adapter", "notify_adapter.py")

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
    def __init__(self, target, **kw):
        self.target = target
        self.kw = kw
        self.orig = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.orig[k] = getattr(self.target, k)
            setattr(self.target, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.orig.items():
            setattr(self.target, k, v)


class _FakeCfg:
    def __init__(self, overrides, real):
        self.overrides = overrides
        self.real = real

    def get(self, path=None):
        if path in self.overrides:
            return self.overrides[path]
        return self.real.get(path)


class _FakeAdapter(na.NotifyAdapter):
    name = "fake"

    def __init__(self, ok=True):
        self.ok = ok
        self.asked = []

    def ask(self, question, ref_id):
        self.asked.append(ref_id)
        return self.ok


def test_adapter_selection():
    print("[test_adapter_selection]")
    check(isinstance(na.get_adapter("none"), na.NoneAdapter), "name='none' → NoneAdapter")
    check(isinstance(na.get_adapter("slack"), na.SlackAdapter), "name='slack' → SlackAdapter")
    check(isinstance(na.get_adapter("bogus"), na.NoneAdapter), "unknown → NoneAdapter (fail-safe)")


def test_none_adapter_sends_nothing():
    print("[test_none_adapter_sends_nothing]")
    check(na.NoneAdapter().ask("q?", "ref") is False, "NoneAdapter.ask returns False")


def test_slack_adapter_no_target_no_send():
    print("[test_slack_adapter_no_target_no_send]")
    saved = {k: os.environ.pop(k, None) for k in ("BRUNOS_NOTIFY_TARGET", "BRUNOS_ALERT_CHANNEL")}
    try:
        with _patch(na, brain_config=_FakeCfg({"notify.target": None}, na.brain_config)):
            a = na.SlackAdapter()
            check(a.target == "", "no target resolved from config/env")
            check(a.ask("q?", "ref") is False, "ask returns False (nothing sent) without a target")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_delivery_marks_asked_only_on_confirm():
    print("[test_delivery_marks_asked_only_on_confirm]")
    with tempfile.TemporaryDirectory() as td:
        q = Path(td) / "decision_questions.json"
        q.write_text(json.dumps([
            {"id": "d1", "question": "why d1?", "asked": False, "answered": False},
        ]), encoding="utf-8")
        with _patch(md, DECISION_QUESTIONS_PATH=q, _log=lambda *a, **k: None,
                    brain_config=_FakeCfg({}, md.brain_config)):
            # NoneAdapter: ask returns False → not marked asked
            sent = md._deliver_questions(dry_run=False, adapter=na.NoneAdapter())
            check(sent == 0, "NoneAdapter delivers 0")
            check(json.loads(q.read_text())[0]["asked"] is False, "question NOT marked asked")
            # Fake adapter confirms → marked asked
            fa = _FakeAdapter(ok=True)
            sent2 = md._deliver_questions(dry_run=False, adapter=fa)
            check(sent2 == 1 and fa.asked == ["d1"], "confirmed delivery asks once")
            check(json.loads(q.read_text())[0]["asked"] is True, "question marked asked after confirm")


def test_delivery_respects_max_per_day():
    print("[test_delivery_respects_max_per_day]")
    with tempfile.TemporaryDirectory() as td:
        q = Path(td) / "decision_questions.json"
        q.write_text(json.dumps([
            {"id": f"d{i}", "question": f"q{i}", "asked": False, "answered": False}
            for i in range(5)
        ]), encoding="utf-8")
        fa = _FakeAdapter(ok=True)
        with _patch(md, DECISION_QUESTIONS_PATH=q, _log=lambda *a, **k: None,
                    brain_config=_FakeCfg({"dreaming.decision_prompts.max_per_day": 3}, md.brain_config)):
            sent = md._deliver_questions(dry_run=False, adapter=fa)
        check(sent == 3, f"only max_per_day=3 asked ({sent})")
        asked = sum(1 for x in json.loads(q.read_text()) if x["asked"])
        check(asked == 3, "exactly 3 marked asked, 2 still pending")


def test_extract_ref_answers():
    print("[test_extract_ref_answers]")
    pairs = md._extract_ref_answers("It was for latency reasons [ref:my-decision] mostly.")
    check(pairs == [("my-decision", "It was for latency reasons  mostly.")],
          f"ref parsed + token stripped ({pairs})")
    check(md._extract_ref_answers("no ref here") == [], "no ref → empty")


def test_reconcile_patches_entry_and_marks_answered():
    print("[test_reconcile_patches_entry_and_marks_answered]")
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        entry = vault / "Memory" / "playbook" / "my-decision.md"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(
            "---\ntype: reference\ncategory: decision\nname: My decision\n"
            "confidence: low\nsource-refs: [c0.md]\nstatus: active\n---\n"
            "## Decision\nDo X\n\n## Rationale\nguessed why"
            "\n\n_Inferred — low confidence; a rationale question is open "
            "(see decision_questions.json)._\n",
            encoding="utf-8",
        )
        q = Path(td) / "decision_questions.json"
        q.write_text(json.dumps([
            {"id": "my-decision", "question": "why?", "asked": True, "answered": False},
        ]), encoding="utf-8")
        with _patch(md, vault_path=lambda: vault, DECISION_QUESTIONS_PATH=q,
                    _log=lambda *a, **k: None):
            n = md.reconcile_from_text("Because latency [ref:my-decision].")
        check(n == 1, "one answer reconciled")
        body = entry.read_text()
        check("confidence: high" in body, "confidence raised low→high")
        check("**Confirmed" in body and "Because latency" in body, "confirmed rationale folded in")
        check("rationale question is open" not in body, "provisional note removed")
        qrec = json.loads(q.read_text())[0]
        check(qrec["answered"] is True and qrec.get("patched") is True, "question marked answered + patched")


def main():
    test_adapter_selection()
    test_none_adapter_sends_nothing()
    test_slack_adapter_no_target_no_send()
    test_delivery_marks_asked_only_on_confirm()
    test_delivery_respects_max_per_day()
    test_extract_ref_answers()
    test_reconcile_patches_entry_and_marks_answered()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
