#!/usr/bin/env python3
# CI GATE — This file MUST pass before any BaaS pilot deploy.
"""Privacy gate canary test harness. Run: uv run python tests/test_privacy_gate.py
Exits 0 if all assertions pass (or all skips are graceful); exits 1 on any failure.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

_PASS = _FAIL = _SKIP = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def skip(label):
    global _SKIP
    _SKIP += 1
    print(f"  skip {label}")


# ---------------------------------------------------------------------------
# Section 1: scrub_secrets() unit tests
# ---------------------------------------------------------------------------
print("\n[Section 1] scrub_secrets() unit tests")

from sanitize import scrub_secrets  # noqa: E402

# API key redacted
out, n = scrub_secrets("sk-test-CANARY_FAKE_KEY_DO_NOT_USE_XYZ999")
check("sk-test-" not in out, "sk-* API key is redacted")
check(n > 0, "count > 0 for API key")

# PostgreSQL connection string redacted
out, n = scrub_secrets("postgresql://admin:CANARY_FAKE_PASS@192.168.50.100:5432/proddb")
check("CANARY_FAKE_PASS" not in out, "postgres password redacted in conn string")
check("[REDACTED-CONNSTR]" in out, "conn string replaced with [REDACTED-CONNSTR]")

# Email is intentionally PRESERVED by scrub_secrets (not a credential; person-level
# redaction is the excluded-entities layer's job).
out, n = scrub_secrets("contact canary.test.user@example-canary-fake.com for access")
check("canary.test.user@example-canary-fake.com" in out, "email preserved by secret scrub")
check("[REDACTED-EMAIL]" not in out, "no [REDACTED-EMAIL] token emitted")

# CPF (formatted) redacted
out, n = scrub_secrets("123.456.789-09")
check("123.456.789-09" not in out, "formatted CPF is redacted")
check("[REDACTED-CPF]" in out, "CPF replaced with [REDACTED-CPF]")

# CNPJ redacted
out, n = scrub_secrets("12.345.678/0001-90")
check("12.345.678/0001-90" not in out, "CNPJ is redacted")
check("[REDACTED-CNPJ]" in out, "CNPJ replaced with [REDACTED-CNPJ]")

# JWT redacted
out, n = scrub_secrets("eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJDQU5BUlkifQ.FAKESIG123456789")
check("eyJhbGciOiJSUzI1NiJ9" not in out, "JWT start token redacted")
check("[REDACTED-JWT]" in out, "JWT replaced with [REDACTED-JWT]")

# Internal IP redacted
out, n = scrub_secrets("192.168.50.100")
check("192.168.50.100" not in out, "RFC1918 internal IP is redacted")
check("[REDACTED-INTERNAL-IP]" in out, "IP replaced with [REDACTED-INTERNAL-IP]")

# Non-secret content preserved
body = "Decided to rotate all production secrets after the audit"
out, n = scrub_secrets(body)
check("Decided to rotate all production secrets after the audit" in out,
      "non-secret content 'Decided to rotate...' is preserved")

# AWS access key ID redacted
out, n = scrub_secrets("AKIAIOSFODNN7EXAMPLE")
check("AKIAIOSFODNN7EXAMPLE" not in out, "AWS AKIA key is redacted")

# Bearer token redacted
out, n = scrub_secrets("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9LONGTOKEN123")
check("LONGTOKEN123" not in out, "Bearer token is redacted")

# --- Preservation / false-positive guards ---------------------------------
# The federation contract requires work/technical content preserved verbatim.
# These bare digit runs, version strings, and 3-octet patterns must NOT be
# redacted (they previously false-matched the CPF / phone / IP rules).
_PRESERVE_CASES = [
    "order 12345678901 succeeded",          # 11-digit order id (not a CPF)
    "ran 12345678901 iterations",           # 11-digit count
    "processed 98765432100 records",        # 11-digit count
    "timeout bumped to 18002400 seconds",   # 8-digit count
    "released version 1.2.3 today",          # semver
    "see tag 10.20.30 in the repo",          # 3-octet version-ish (not an IP)
    "ClickUp task 86ca1z88k assigned",       # alnum id
]
for case in _PRESERVE_CASES:
    out, n = scrub_secrets(case)
    check(out == case and n == 0, f"preserved verbatim: {case!r}")

# Real phones (with structure) still ARE redacted — precision, not blanket recall.
for phone in ["(11) 98765-4321", "+55 11 98765-4321", "98765-4321"]:
    out, n = scrub_secrets(phone)
    check(n > 0 and "[REDACTED-PHONE]" in out, f"structured phone redacted: {phone!r}")

# Full RFC1918 addresses still ARE redacted, with no octet left dangling.
out, n = scrub_secrets("host at 10.0.0.5 responded")
check("10.0.0.5" not in out and ".5" not in out.replace("[REDACTED-INTERNAL-IP]", ""),
      "10.0.0.0/8 address fully redacted (no dangling octet)")


# ---------------------------------------------------------------------------
# Section 2: scrub_excluded_entities() unit tests (Track C)
# ---------------------------------------------------------------------------
print("\n[Section 2] scrub_excluded_entities() unit tests (Track C)")

try:
    from sanitize import load_excluded_entities, scrub_excluded_entities

    # Test scrub_excluded_entities directly with a frozenset
    entities = frozenset({"CANARY_EXCLUDED_PERSON"})
    body = "CANARY_EXCLUDED_PERSON was also present in the meeting"
    out, n = scrub_excluded_entities(body, entities)
    check("CANARY_EXCLUDED_PERSON" not in out, "excluded person name is redacted")
    check(n > 0, "redaction count > 0 for excluded entity")
    check("[REDACTED-ENTITY]" in out, "replaced with [REDACTED-ENTITY]")

    # Test empty entities → no change
    out, n = scrub_excluded_entities(body, frozenset())
    check(n == 0 and out == body, "empty entities set → no change")

    # Test load_excluded_entities from a temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, dir="/tmp") as f:
        f.write("# Excluded People\n\n## Excluded\n\n- TestPersonAlice\n- TestPersonBob\n")
        tmp_path = Path(f.name)

    # load_excluded_entities needs the Memory/ dir; write temp file as _excluded-people.md
    with tempfile.TemporaryDirectory() as tmpdir:
        excl_path = Path(tmpdir) / "_excluded-people.md"
        excl_path.write_text("# Excluded People\n\n## Excluded\n\n- TestPersonAlice\n- TestPersonBob\n")
        loaded = load_excluded_entities(tmpdir)
        check("TestPersonAlice" in loaded, "load_excluded_entities reads names from temp file")
        check("TestPersonBob" in loaded, "load_excluded_entities reads multiple names")
        check(len(loaded) == 2, "load_excluded_entities returns correct count")

    tmp_path.unlink(missing_ok=True)

except ImportError as e:
    skip(f"Track C symbols not importable ({e}); skipping Section 2")


# ---------------------------------------------------------------------------
# Section 3: fail-closed for unknown share_status
# ---------------------------------------------------------------------------
print("\n[Section 3] fail-closed for unknown share_status")

try:
    from memory_reflect import _STRIP_OPEN_STATUSES

    check("quarantined" not in _STRIP_OPEN_STATUSES,
          "'quarantined' is NOT in _STRIP_OPEN_STATUSES (would be blocked)")
    check("error" not in _STRIP_OPEN_STATUSES,
          "'error' is NOT in _STRIP_OPEN_STATUSES (would be blocked)")
    check("unknown-future-state" not in _STRIP_OPEN_STATUSES,
          "unknown future states are NOT in _STRIP_OPEN_STATUSES (would be blocked)")
    check(None in _STRIP_OPEN_STATUSES,
          "None (absent share_status) IS in _STRIP_OPEN_STATUSES (would proceed)")
    check("" in _STRIP_OPEN_STATUSES,
          "empty string IS in _STRIP_OPEN_STATUSES (would proceed)")
    check("active" in _STRIP_OPEN_STATUSES,
          "'active' IS in _STRIP_OPEN_STATUSES (would proceed)")
    # "cleared" is handled by idempotency guard before reaching this set
    check("cleared" not in _STRIP_OPEN_STATUSES,
          "'cleared' is NOT in _STRIP_OPEN_STATUSES (caught by earlier idempotency guard)")

except ImportError as e:
    skip(f"memory_reflect not importable ({e}); skipping Section 3")


# ---------------------------------------------------------------------------
# Section 4: _parse_flush_output() unit tests (L1)
# ---------------------------------------------------------------------------
print("\n[Section 4] _parse_flush_output() unit tests (L1)")

try:
    from memory_flush import _parse_flush_output

    # JSON input → correct (work, personal) split
    w, p = _parse_flush_output(json.dumps({"work": "- work note", "personal": "- tired"}))
    check(w == "- work note", "JSON: work field parsed correctly")
    check(p == "- tired", "JSON: personal field parsed correctly")

    # FLUSH_OK → ("", "")
    w, p = _parse_flush_output("FLUSH_OK")
    check(w == "" and p == "", "FLUSH_OK returns ('', '')")

    # Non-JSON (bare bullets) → privacy-first fallback: content lands in PERSONAL
    # (daily log only, never the company inbox) by default. Configurable via
    # BRUNOS_FLUSH_PARSE_FALLBACK=work.
    w, p = _parse_flush_output("- plain bullet\n- another bullet")
    check("- plain bullet" in p, "non-JSON fallback: content in personal field (privacy-first)")
    check(w == "", "non-JSON fallback: work field is empty (not routed to inbox)")

    # JSON with empty personal → (work, "")
    w, p = _parse_flush_output(json.dumps({"work": "- work item", "personal": ""}))
    check(w == "- work item", "JSON empty personal: work field correct")
    check(p == "", "JSON empty personal: personal field is empty string")

    # Fenced JSON → parsed correctly
    fenced = '```json\n{"work": "- fenced work", "personal": "- fenced personal"}\n```'
    w, p = _parse_flush_output(fenced)
    check("- fenced work" in w, "fenced JSON: work field parsed correctly")
    check("- fenced personal" in p, "fenced JSON: personal field parsed correctly")

    # JSON with null personal → treated as empty
    w, p = _parse_flush_output(json.dumps({"work": "- w", "personal": None}))
    check(w == "- w", "JSON null personal: work field correct")
    check(p == "", "JSON null personal: personal field is empty string")

    # Empty string → ("", "")
    w, p = _parse_flush_output("")
    check(w == "" and p == "", "empty string → ('', '')")

    # Whitespace only → ("", "")
    w, p = _parse_flush_output("   \n  ")
    check(w == "" and p == "", "whitespace → ('', '')")

except ImportError as e:
    skip(f"memory_flush not importable ({e}); skipping Section 4")


# ---------------------------------------------------------------------------
# Section 5: Full pipeline canary test (integration, no vault writes)
# ---------------------------------------------------------------------------
print("\n[Section 5] Full pipeline canary test")

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
canary_work_path = FIXTURES_DIR / "canary_capture_work.md"

if not canary_work_path.exists():
    skip(f"canary_capture_work.md not found at {canary_work_path}; skipping Section 5")
else:
    # Read the fixture body (strip frontmatter)
    fixture_text = canary_work_path.read_text(encoding="utf-8")
    # Strip frontmatter: find the second ---
    parts = fixture_text.split("---", 2)
    body = parts[2] if len(parts) >= 3 else fixture_text

    # Run scrub_secrets() on the body
    scrubbed, count = scrub_secrets(body)
    check(count > 0, "scrub_secrets() finds at least one secret in canary work capture")

    # Assert canary tokens are gone
    check("sk-test-CANARY_FAKE_KEY_DO_NOT_USE_XYZ999" not in scrubbed,
          "canary API key not present after scrub")
    check("CANARY_FAKE_PASS" not in scrubbed,
          "canary postgres password not present after scrub")
    check("192.168.50.100" not in scrubbed,
          "canary internal IP not present after scrub")
    check("canary.test.user@example-canary-fake.com" in scrubbed,
          "canary email PRESERVED after scrub (handled by excluded-entities, not secret scrub)")
    check("123.456.789-09" not in scrubbed,
          "canary CPF not present after scrub")
    check("12.345.678/0001-90" not in scrubbed,
          "canary CNPJ not present after scrub")
    check("eyJhbGciOiJSUzI1NiJ9" not in scrubbed,
          "canary JWT header not present after scrub")

    # Assert non-secret content is preserved
    check("Decided to rotate all production secrets after the audit" in scrubbed,
          "non-secret content 'Decided to rotate...' preserved after scrub")

    # If Track C available: also run scrub_excluded_entities
    try:
        from sanitize import scrub_excluded_entities
        entities = frozenset({"CANARY_EXCLUDED_PERSON"})
        double_scrubbed, entity_count = scrub_excluded_entities(scrubbed, entities)
        check("CANARY_EXCLUDED_PERSON" not in double_scrubbed,
              "canary excluded person not present after entity scrub")
        check(entity_count > 0, "entity scrub count > 0 for canary person")
    except ImportError as e:
        skip(f"Track C scrub_excluded_entities not importable ({e}); skipping entity scrub in Section 5")


# ---------------------------------------------------------------------------
# Section 6: Consumer boundary assertion (no vault needed)
# ---------------------------------------------------------------------------
print("\n[Section 6] Consumer boundary assertion")

try:
    from shared import validate_consumer_read, CONSUMER_READ_SCOPES

    # Cleared capture with correct default_export
    cleared_fm = {
        "share_status": "cleared",
        "default_export": "linos-protostack",
        "project": "colinas",
    }
    check(validate_consumer_read(cleared_fm, "linos"),
          "validate_consumer_read: cleared linos-protostack capture → True for 'linos'")

    # Wrong default_export
    wrong_export_fm = {"share_status": "cleared", "default_export": "personal"}
    check(not validate_consumer_read(wrong_export_fm, "linos"),
          "validate_consumer_read: 'personal' export → False for 'linos'")

    # Unknown consumer → deny (fail-closed)
    check(not validate_consumer_read(cleared_fm, "unknown-consumer"),
          "validate_consumer_read: unknown consumer → False (fail-closed)")

    # Empty default_export → deny
    empty_fm = {"share_status": "cleared", "default_export": ""}
    check(not validate_consumer_read(empty_fm, "linos"),
          "validate_consumer_read: empty default_export → False")

    # CONSUMER_READ_SCOPES sanity
    check("linos" in CONSUMER_READ_SCOPES, "CONSUMER_READ_SCOPES contains 'linos'")
    check("linos-protostack" in CONSUMER_READ_SCOPES["linos"],
          "linos scope includes 'linos-protostack'")

except ImportError as e:
    skip(f"shared.validate_consumer_read not importable ({e}); skipping Section 6")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{_PASS} passed, {_FAIL} failed, {_SKIP} skipped")
sys.exit(1 if _FAIL else 0)
