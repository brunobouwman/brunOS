#!/usr/bin/env python3
"""Standalone tests for the Phase B reflect finalization (no pytest).
Run: uv run python tests/test_reflect_eviction.py

Covers:
  - _evict_to_archive_if_over_cap: an over-cap MEMORY.md sheds its OLDEST dated
    bullet from the LARGEST section into Memory/_archive/MEMORY-archive.md until
    under cap — lossless (the bullet lands in the archive verbatim, nothing is
    deleted), undated context bullets are untouched, and still_over_cap is reported
    when no dated bullet remains to peel.
  - _buffer_personal: personal items are appended to personal_pending.json (the
    hourly inbox pass no longer writes MEMORY.md per batch).
  - _run_memory_curation_stage: drains the buffer into MEMORY.md once + evicts
    once, then clears the buffer.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "memory_reflect", REPO / ".claude" / "scripts" / "memory_reflect.py"
)
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

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
            self.orig[k] = getattr(mr, k)
            setattr(mr, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.orig.items():
            setattr(mr, k, v)


FM = "---\ntype: system\n---\n"


def _stub_embed(texts):
    """Deterministic passage-embed stub: identical text → identical vector (cosine
    1.0); distinct text → centered hash vectors (cosine ≈ 0, well under 0.95). Lets
    the dedup gate be tested without loading FastEmbed."""
    out = []
    for t in texts:
        h = hashlib.sha256(t.strip().lower().encode()).digest()
        out.append(np.frombuffer(h, dtype=np.uint8).astype("float32") - 128.0)
    return out


def _doc(bullets_by_section: dict[str, list[str]]) -> str:
    parts = [FM]
    for section, bullets in bullets_by_section.items():
        parts.append(section)
        parts.append("")
        parts.extend(bullets)
        parts.append("")
    return "\n".join(parts)


def test_under_cap_noop():
    print("[test_under_cap_noop]")
    text = FM + "## A\n\n- **2026-01-01** — small\n"
    out, evicted, over = mr._evict_to_archive_if_over_cap(text, 8192, dry_run=True)
    check(out == text, "text unchanged")
    check(evicted == [], "nothing evicted")
    check(over is False, "still_over_cap False")


def test_evicts_oldest_from_largest_section():
    print("[test_evicts_oldest_from_largest_section]")
    # Section "Big" is far larger; its oldest dated bullet must be the victim.
    big = [f"- **2026-05-{d:02d}** — big bullet number {d} " + ("x" * 200) for d in range(2, 12)]
    big.insert(0, "- **2026-01-01** — OLDEST big bullet " + ("x" * 200))
    small = ["- **2025-01-01** — tiny old bullet", "- not a dated bullet, keep me"]
    text = _doc({"## Big": big, "## Small": small})
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None):
            out, evicted, over = mr._evict_to_archive_if_over_cap(text, 2048, dry_run=False)
        check(len(out.encode()) <= 2048, f"under cap after eviction ({len(out.encode())}B)")
        check(over is False, "still_over_cap False")
        # The oldest dated bullet from the LARGEST section (Big/2026-01-01) goes first.
        check("OLDEST big bullet" not in out, "oldest big bullet evicted from MEMORY")
        # The undated context bullet in Small is never touched.
        check("not a dated bullet, keep me" in out, "undated bullet preserved (lossless ordering)")
        # The tiny old bullet in the SMALL section survives (largest-section rule).
        check("tiny old bullet" in out, "older bullet in small section kept (largest-first)")
        archive = (vault / "Memory" / "_archive" / "MEMORY-archive.md").read_text()
        check("OLDEST big bullet" in archive, "evicted bullet landed in archive verbatim (lossless)")
        check(mr.MEMORY_ARCHIVE_SECTION in archive, "archive has the evicted section header")


def test_still_over_when_no_dated_bullets():
    print("[test_still_over_when_no_dated_bullets]")
    # Over cap but every bullet is undated → cannot evict losslessly → report over.
    text = _doc({"## A": ["- undated " + ("y" * 4000), "- also undated " + ("y" * 4000)]})
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None):
            out, evicted, over = mr._evict_to_archive_if_over_cap(text, 2048, dry_run=False)
        check(evicted == [], "nothing evicted (no dated bullets)")
        check(over is True, "still_over_cap True (signals monitoring)")
        check(out == text, "MEMORY unchanged (nothing dropped)")


def test_dry_run_writes_no_archive():
    print("[test_dry_run_writes_no_archive]")
    big = [f"- **2026-05-{d:02d}** — bullet {d} " + ("x" * 200) for d in range(1, 12)]
    text = _doc({"## Big": big})
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None):
            out, evicted, over = mr._evict_to_archive_if_over_cap(text, 2048, dry_run=True)
        check(len(evicted) > 0, "dry-run computes would-evict list")
        check(not (vault / "Memory" / "_archive" / "MEMORY-archive.md").exists(),
              "dry-run writes no archive file")


def test_buffer_personal_appends():
    print("[test_buffer_personal_appends]")
    with tempfile.TemporaryDirectory() as td:
        buf_path = Path(td) / "personal_pending.json"
        with _patch(PERSONAL_PENDING_PATH=buf_path, _log=lambda *a, **k: None):
            n1 = mr._buffer_personal(
                [{"type": "lesson", "text": "L1", "promote": True},
                 {"type": "fact", "text": "F1", "promote": True},
                 {"type": "soul-suggestion", "text": "ignore me", "promote": True}],
                "vertik",
            )
            n2 = mr._buffer_personal([{"type": "decision", "text": "D1"}], "daily-log")
        check(n1 == 2, f"buffered 2 valid items (soul-suggestion filtered) got {n1}")
        check(n2 == 1, "second call appends")
        buf = json.loads(buf_path.read_text())
        check(len(buf) == 3, f"buffer has 3 items total ({len(buf)})")
        check({b["source"] for b in buf} == {"vertik", "daily-log"}, "source provenance kept")


def test_curation_drains_buffer_and_clears():
    print("[test_curation_drains_buffer_and_clears]")
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        mem = vault / "Memory" / "MEMORY.md"
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text(FM + "## Lessons\n\n- **2026-01-01** — existing\n", encoding="utf-8")
        buf_path = Path(td) / "personal_pending.json"
        buf_path.write_text(json.dumps([
            {"type": "lesson", "text": "buffered lesson", "source": "vertik", "ts": "t"},
        ]), encoding="utf-8")
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None,
                    PERSONAL_PENDING_PATH=buf_path, _embed_texts=_stub_embed):
            rc, fails = mr._run_memory_curation_stage(dry_run=False)
        check(rc == 0 and fails == [], "curation clean")
        body = mem.read_text()
        check("buffered lesson" in body, "buffered item written to MEMORY.md")
        check("existing" in body, "existing item preserved")
        check("<!-- src: vertik -->" in body, "src-slug provenance annotated on bullet")
        check(json.loads(buf_path.read_text()) == [], "buffer cleared after write")


def test_curation_dry_run_keeps_buffer():
    print("[test_curation_dry_run_keeps_buffer]")
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        mem = vault / "Memory" / "MEMORY.md"
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text(FM + "## Lessons\n\n- old\n", encoding="utf-8")
        buf_path = Path(td) / "personal_pending.json"
        buf_path.write_text(json.dumps([
            {"type": "lesson", "text": "should stay buffered", "source": "x", "ts": "t"},
        ]), encoding="utf-8")
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None,
                    PERSONAL_PENDING_PATH=buf_path, _embed_texts=_stub_embed):
            rc, fails = mr._run_memory_curation_stage(dry_run=True)
        check(rc == 0, "dry-run rc 0")
        check("should stay buffered" not in mem.read_text(), "MEMORY.md untouched in dry-run")
        check(len(json.loads(buf_path.read_text())) == 1, "buffer NOT cleared in dry-run")


# --- write-time gates: semantic dedup + src-slug provenance -------------------


def test_append_skips_near_duplicate():
    print("[test_append_skips_near_duplicate]")
    # Existing MEMORY.md already holds "team moved to Floripa"; a buffered item with
    # the same meaning (cosine 1.0 via the stub) must be skipped, the novel one kept.
    mem = FM + "## Lessons\n\n- **2026-01-01** — team moved to Floripa\n"
    items = [
        {"type": "lesson", "text": "team moved to Floripa", "source": "vertik"},
        {"type": "lesson", "text": "new distinct lesson", "source": "vertik"},
    ]
    with _patch(_log=lambda *a, **k: None, _embed_texts=_stub_embed):
        out, skipped = mr._append_promotions(mem, items)
    check(len(skipped) == 1, f"one near-dup skipped (got {len(skipped)})")
    check(skipped[0]["text"] == "team moved to Floripa", "the duplicate is the one skipped")
    check(skipped[0]["similarity"] >= 0.95, "skipped entry carries cosine ≥ 0.95")
    check("new distinct lesson" in out, "novel item appended")
    check(out.count("team moved to Floripa") == 1, "duplicate NOT re-appended")


def test_append_intra_batch_dedup():
    print("[test_append_intra_batch_dedup]")
    # Two identical buffered items, nothing pre-existing: first kept, second skipped
    # (intra-run dedup — the 2nd compares against the just-kept 1st).
    mem = FM + "## Lessons\n\n"
    items = [
        {"type": "lesson", "text": "same exact thing", "source": "a"},
        {"type": "lesson", "text": "same exact thing", "source": "b"},
    ]
    with _patch(_log=lambda *a, **k: None, _embed_texts=_stub_embed):
        out, skipped = mr._append_promotions(mem, items)
    check(len(skipped) == 1, f"second identical item skipped (got {len(skipped)})")
    check(out.count("same exact thing") == 1, "only one copy appended")


def test_append_src_annotation():
    print("[test_append_src_annotation]")
    mem = FM + "## Lessons\n\n"
    items = [{"type": "lesson", "text": "annotate me", "source": "colinas"}]
    with _patch(_log=lambda *a, **k: None, _embed_texts=_stub_embed):
        out, skipped = mr._append_promotions(mem, items)
    check("- **" in out and "annotate me  <!-- src: colinas -->" in out,
          "bullet carries trailing src-slug comment")
    check(skipped == [], "novel item not skipped")


def test_append_dedup_fail_open():
    print("[test_append_dedup_fail_open]")
    # Embedding unavailable → gate is a no-op (all items appended), never a barrier.
    def _boom(_texts):
        raise RuntimeError("model unavailable")

    mem = FM + "## Lessons\n\n- **2026-01-01** — already here\n"
    items = [{"type": "lesson", "text": "already here", "source": "x"}]
    with _patch(_log=lambda *a, **k: None, _embed_texts=_boom):
        out, skipped = mr._append_promotions(mem, items)
    check(skipped == [], "fail-open: nothing skipped when embedding errors")
    check(out.count("already here") == 2, "item appended despite being a dup (fail-open)")


# --- continuity-doc eviction (projects/<slug>.md hard backstop) ---------------

CONT_FM = "---\ntype: project\n---\n"


def _cont_doc(header_bullets: list[str], continuity_bullets: list[str]) -> str:
    """A project doc: frontmatter + hand-written section + continuity section LAST."""
    parts = [CONT_FM, "# vertik\n", "## Hand-written notes\n"]
    parts.extend(header_bullets)
    parts.append("")
    parts.append(mr.CONTINUITY_HEADER)
    parts.extend(continuity_bullets)
    parts.append("")
    return "\n".join(parts)


def test_continuity_evicts_oldest_until_under_cap():
    print("[test_continuity_evicts_oldest_until_under_cap]")
    cont = [f"- **2026-05-{d:02d}** — continuity item {d} " + ("z" * 200) for d in range(2, 14)]
    cont.insert(0, "- **2026-01-01** — OLDEST continuity item " + ("z" * 200))
    text = _cont_doc(["- **2026-06-01** — handwritten keep me " + ("h" * 100)], cont)
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None):
            out, evicted, over = mr._evict_continuity_to_archive_if_over_cap(
                "vertik", text, 2048, dry_run=False
            )
        check(len(out.encode()) <= 2048, f"under cap after eviction ({len(out.encode())}B)")
        check(over is False, "still_over_cap False")
        check("OLDEST continuity item" not in out, "oldest continuity bullet evicted first")
        # Hand-written header bullet (a dated bullet in another section) is untouched.
        check("handwritten keep me" in out, "hand-written section preserved (scoped to continuity)")
        arch = (vault / "Memory" / "projects" / "_archive" / "vertik-continuity.md").read_text()
        check("OLDEST continuity item" in arch, "evicted bullet archived verbatim (lossless)")


def test_continuity_evicts_by_inline_date_protects_undated():
    print("[test_continuity_evicts_by_inline_date_protects_undated]")
    # LLM-compacted shape: thematic ### subsections, bullets carry INLINE dates (or
    # none). Oldest inline-dated bullet is the victim; a truly undated durable bullet
    # is protected even though it sits in the same section.
    cont = [
        "### Recently merged (state baked in)",
        "- **#515 MERGED** (2026-06-04): clarify_drop fix " + ("m" * 220),
        "- **#520 reviewed** (2026-06-06): buffer-then-replay " + ("m" * 220),
        "### Durable schema",
        "- `orcamento_audits.status` enum is keyed on orcamento_id " + ("d" * 220),
    ]
    text = _cont_doc([], cont)
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None):
            out, evicted, over = mr._evict_continuity_to_archive_if_over_cap(
                "vertik", text, 700, dry_run=False
            )
        check("2026-06-04" not in out, "oldest inline-dated bullet (06-04) evicted first")
        check("orcamento_audits.status" in out, "undated durable bullet protected")
        arch = (vault / "Memory" / "projects" / "_archive" / "vertik-continuity.md").read_text()
        check("#515 MERGED" in arch, "evicted inline-dated bullet archived verbatim")


def test_continuity_still_over_when_no_dated():
    print("[test_continuity_still_over_when_no_dated]")
    text = _cont_doc([], ["- undated " + ("y" * 4000), "- also undated " + ("y" * 4000)])
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None):
            out, evicted, over = mr._evict_continuity_to_archive_if_over_cap("vertik", text, 2048)
        check(evicted == [], "nothing evicted (no dated continuity bullets)")
        check(over is True, "still_over_cap True (signals monitoring)")
        check(out == text, "doc unchanged (nothing dropped)")


def test_append_continuity_backstop_evicts():
    print("[test_append_continuity_backstop_evicts]")

    # Simulate the LLM merge-pass failing to get under cap → backstop must evict.
    def _fake_compact(t, cap, instruction=None):
        return t, len(t.encode()) > cap

    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        proj = vault / "Memory" / "projects" / "vertik.md"
        proj.parent.mkdir(parents=True, exist_ok=True)
        existing = _cont_doc(
            [], [f"- **2026-05-{d:02d}** — old item {d} " + ("z" * 300) for d in range(1, 40)]
        )
        proj.write_text(existing, encoding="utf-8")
        with _patch(vault_path=lambda: vault, _log=lambda *a, **k: None,
                    _compact_if_over_cap=_fake_compact):
            over = mr._append_continuity("vertik", ["fresh continuity bullet"])
        body = proj.read_text()
        check(len(body.encode()) <= mr.PROJECT_DOC_CAP_BYTES,
              f"doc under cap after backstop ({len(body.encode())}B)")
        check(over is False, "returns not-over-cap after eviction")
        check("fresh continuity bullet" in body, "new bullet inserted + kept (newest)")
        check((vault / "Memory" / "projects" / "_archive" / "vertik-continuity.md").exists(),
              "archive created by backstop")


def main():
    test_under_cap_noop()
    test_evicts_oldest_from_largest_section()
    test_still_over_when_no_dated_bullets()
    test_dry_run_writes_no_archive()
    test_buffer_personal_appends()
    test_curation_drains_buffer_and_clears()
    test_curation_dry_run_keeps_buffer()
    test_append_skips_near_duplicate()
    test_append_intra_batch_dedup()
    test_append_src_annotation()
    test_append_dedup_fail_open()
    test_continuity_evicts_oldest_until_under_cap()
    test_continuity_evicts_by_inline_date_protects_undated()
    test_continuity_still_over_when_no_dated()
    test_append_continuity_backstop_evicts()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
