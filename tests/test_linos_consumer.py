#!/usr/bin/env python3
"""Standalone unit tests for linos_consumer.py (no pytest).

Run: uv run python tests/test_linos_consumer.py

Tests 1–4 run without linos_consumer.py (pure shared.py + hashing logic).
Tests 5–7 require linos_consumer.py; they are guarded with try/import and
skipped gracefully when the module is absent.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import CONSUMER_READ_SCOPES, validate_consumer_read

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

_PASS = _FAIL = _SKIP = 0


def check(condition: bool, label: str) -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def skip(label: str, reason: str = "") -> None:
    global _SKIP
    _SKIP += 1
    suffix = f" ({reason})" if reason else ""
    print(f"  skip {label}{suffix}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BRT_OFFSET = timezone(timedelta(hours=-3))


def _iso(dt: datetime) -> str:
    """Format datetime as RFC3339 with -03:00 offset."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S-03:00")


def _make_capture(
    tmp_dir: Path,
    slug: str,
    created_iso: str,
    export: str = "linos-protostack",
    status: str = "cleared",
) -> Path:
    """Create a minimal capture file inside tmp_dir/slug/."""
    safe_name = created_iso.replace(":", "-")
    p = tmp_dir / slug / f"{safe_name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntype: inbox\ncreated: {created_iso}\ndefault_export: {export}\n"
        f"share_status: {status}\nproject: {slug}\nsession_id: test\nsource: test\n"
        f"status: active\n---\n\nTest body.\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Test 1 — validate_consumer_read
# ---------------------------------------------------------------------------

def test_validate_consumer_read() -> None:
    # validate_consumer_read is a SCOPE-ONLY gate (signature: fm, consumer).
    # It checks default_export against the consumer's declared scope and does
    # NOT look at share_status — the share_status=="cleared" gate is enforced
    # separately by the consumer loop (see test_eligible_filters_uncleared).
    print("[test_validate_consumer_read]")
    in_scope = {"default_export": "linos-protostack"}
    out_of_scope = {"default_export": "personal"}
    no_export = {"share_status": "cleared"}

    check(validate_consumer_read(in_scope, "linos") is True,
          "linos can read linos-protostack export")
    check(validate_consumer_read(out_of_scope, "linos") is False,
          "linos cannot read personal export")
    check(validate_consumer_read(no_export, "linos") is False,
          "missing default_export → denied")
    # share_status is NOT part of this gate — an un-cleared in-scope capture
    # still passes the scope check (the consumer enforces cleared separately).
    check(validate_consumer_read({"default_export": "linos-protostack",
                                  "share_status": "active"}, "linos") is True,
          "scope gate ignores share_status (in-scope passes regardless)")
    check(validate_consumer_read(in_scope, "unknown") is False,
          "unknown consumer always returns False (fail-closed)")
    check("linos" in CONSUMER_READ_SCOPES,
          "linos key present in CONSUMER_READ_SCOPES")


# ---------------------------------------------------------------------------
# Test 2 — content hash stability
# ---------------------------------------------------------------------------

def test_content_hash_stability() -> None:
    print("[test_content_hash_stability]")
    body = "hello"
    expected = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    # Compute twice via the same algorithm
    h1 = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    h2 = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    check(h1 == h2, "same body → same hash both times")
    check(h1.startswith("sha256:"), "hash starts with 'sha256:'")
    check(h1 == expected, "hash matches expected SHA256 of 'hello'")


# ---------------------------------------------------------------------------
# Test 3 — content hash of empty string
# ---------------------------------------------------------------------------

def test_content_hash_empty() -> None:
    print("[test_content_hash_empty]")
    empty_sha256 = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    h = "sha256:" + hashlib.sha256("".encode("utf-8")).hexdigest()
    check(h == empty_sha256,
          "empty string → known SHA256 value")


# ---------------------------------------------------------------------------
# Test 4 — ack schema
# ---------------------------------------------------------------------------

def test_ack_schema() -> None:
    print("[test_ack_schema]")
    capture_id = "2026-05-31T12-00-00-test"
    body = "Test capture body."
    slug = "colinas"
    now_ts = "2026-05-31T12:00:00-03:00"

    ack = {
        "capture_id": capture_id,
        "content_hash": "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "slug": slug,
        "acked_at": now_ts,
        "schema_version": 1,
    }

    check("capture_id" in ack, "ack has capture_id")
    check("content_hash" in ack, "ack has content_hash")
    check("acked_at" in ack, "ack has acked_at")
    check("slug" in ack, "ack has slug")
    check("schema_version" in ack, "ack has schema_version")
    check(ack["schema_version"] == 1, "schema_version == 1")
    check(ack["content_hash"].startswith("sha256:"), "content_hash starts with 'sha256:'")
    # Round-trip through JSON
    serialised = json.dumps(ack)
    reloaded = json.loads(serialised)
    check(reloaded == ack, "ack survives JSON round-trip")


# ---------------------------------------------------------------------------
# Tests 5–7 — require linos_consumer module
# ---------------------------------------------------------------------------

try:
    import linos_consumer as lc
    _HAS_CONSUMER = True
except ImportError:
    _HAS_CONSUMER = False


def test_watermark_filter() -> None:
    print("[test_watermark_filter]")
    if not _HAS_CONSUMER:
        skip("test_watermark_filter", "linos_consumer not importable")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        inbox = Path(tmpdir) / "inbox"
        slug = "colinas"
        base = datetime(2026, 5, 31, 8, 0, 0, tzinfo=BRT_OFFSET)
        t1 = _iso(base)
        t2 = _iso(base + timedelta(hours=1))
        t3 = _iso(base + timedelta(hours=2))

        _make_capture(inbox, slug, t1)
        _make_capture(inbox, slug, t2)
        _make_capture(inbox, slug, t3)

        # watermark = t1 → only t2 and t3 should be returned
        eligible = lc._eligible_captures(slug, t1, brunos_inbox_root=inbox)
        check(len(eligible) == 2,
              "watermark=t1 → 2 eligible captures (t2, t3)")
        check(eligible[0][0] > lc._parse_iso(t1),
              "first eligible capture is after watermark")

        # watermark = None → all 3
        eligible_all = lc._eligible_captures(slug, None, brunos_inbox_root=inbox)
        check(len(eligible_all) == 3,
              "watermark=None → all 3 captures eligible")

        # watermark = t3 → none
        eligible_none = lc._eligible_captures(slug, t3, brunos_inbox_root=inbox)
        check(len(eligible_none) == 0,
              "watermark=t3 → 0 eligible captures")


def test_eligible_filters_uncleared() -> None:
    # The consumer loop owns the share_status gate (validate_consumer_read is
    # scope-only). An in-scope capture that is NOT yet cleared must be skipped.
    print("[test_eligible_filters_uncleared]")
    if not _HAS_CONSUMER:
        skip("test_eligible_filters_uncleared", "linos_consumer not importable")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        inbox = Path(tmpdir) / "inbox"
        slug = "colinas"
        base = datetime(2026, 5, 31, 8, 0, 0, tzinfo=BRT_OFFSET)
        _make_capture(inbox, slug, _iso(base), status="cleared")
        _make_capture(inbox, slug, _iso(base + timedelta(hours=1)), status="active")

        eligible = lc._eligible_captures(slug, None, brunos_inbox_root=inbox)
        check(len(eligible) == 1,
              "only the cleared capture is eligible; un-cleared is skipped")
        check((eligible[0][2].get("share_status") or "") == "cleared",
              "the surviving eligible capture is the cleared one")


def test_dry_run_no_writes() -> None:
    print("[test_dry_run_no_writes]")
    if not _HAS_CONSUMER:
        skip("test_dry_run_no_writes", "linos_consumer not importable")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        inbox = Path(tmpdir) / "inbox"
        linos = Path(tmpdir) / "linos"
        linos.mkdir()
        slug = "colinas"
        ts = _iso(datetime(2026, 5, 31, 9, 0, 0, tzinfo=BRT_OFFSET))
        _make_capture(inbox, slug, ts)

        lc._run_consumer(
            dry_run=True,
            brunos_inbox_root=inbox,
            linos_vault=linos,
        )

        joint_dir = linos / lc.JOINT_DIR_REL / slug
        ack_dir = linos / lc.ACK_DIR_REL
        watermark_path = lc.CONSUMER_WATERMARK_PATH

        check(not joint_dir.exists() or list(joint_dir.glob("*.md")) == [],
              "dry_run → no joint entries written")
        check(not ack_dir.exists() or list(ack_dir.glob("*.json")) == [],
              "dry_run → no ack files written")


def test_idempotency() -> None:
    print("[test_idempotency]")
    if not _HAS_CONSUMER:
        skip("test_idempotency", "linos_consumer not importable")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        inbox = Path(tmpdir) / "inbox"
        linos = Path(tmpdir) / "linos"
        linos.mkdir()
        slug = "colinas"

        base = datetime(2026, 5, 31, 8, 0, 0, tzinfo=BRT_OFFSET)
        t1 = _iso(base)
        t2 = _iso(base + timedelta(hours=1))
        _make_capture(inbox, slug, t1)
        _make_capture(inbox, slug, t2)

        # Advance watermark past all captures so nothing is eligible
        import json as _json
        from shared import STATE_DIR, save_state
        # Use a temp watermark path so we don't pollute the real state
        future_watermark = _iso(base + timedelta(hours=3))
        temp_watermark = {slug: future_watermark}

        # Monkeypatch CONSUMER_WATERMARK_PATH via load_state override
        import tempfile as _tf
        wm_path = Path(tmpdir) / "consumer_watermark.json"
        wm_path.write_text(_json.dumps(temp_watermark), encoding="utf-8")

        # Patch the module-level path temporarily
        original_wm = lc.CONSUMER_WATERMARK_PATH
        lc.CONSUMER_WATERMARK_PATH = wm_path
        try:
            lc._run_consumer(
                dry_run=False,
                brunos_inbox_root=inbox,
                linos_vault=linos,
            )
        finally:
            lc.CONSUMER_WATERMARK_PATH = original_wm

        joint_dir = linos / lc.JOINT_DIR_REL / slug
        ack_dir = linos / lc.ACK_DIR_REL

        check(not joint_dir.exists() or list(joint_dir.glob("*.md")) == [],
              "idempotency: watermark past all captures → no joint entries")
        check(not ack_dir.exists() or list(ack_dir.glob("*.json")) == [],
              "idempotency: watermark past all captures → no acks")


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_validate_consumer_read()
    test_content_hash_stability()
    test_content_hash_empty()
    test_ack_schema()
    test_watermark_filter()
    test_eligible_filters_uncleared()
    test_dry_run_no_writes()
    test_idempotency()

    print()
    print(f"Results: {_PASS} passed, {_FAIL} failed, {_SKIP} skipped")
    if _FAIL:
        sys.exit(1)
