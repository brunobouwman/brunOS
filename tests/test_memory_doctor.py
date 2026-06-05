#!/usr/bin/env python3
"""Standalone tests for the memory doctor's pure logic (no pytest).
Run: uv run python tests/test_memory_doctor.py

Covers: index staleness verdicts (missing DB, fresh, stale, empty vault),
newest-md-mtime walk, canary output parsing, and the read-only DB check
against healthy / missing / corrupt sqlite files. No embedding model and no
real vault involved — the end-to-end canary is exercised by the daily timer.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

os.environ["BRUNOS_DISABLE_REPORTING"] = "1"

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from memory_doctor import (  # noqa: E402
    check_db,
    newest_md_mtime,
    parse_canary_output,
    staleness_violation,
)

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="memory-doctor-test-"))

    print("== staleness_violation ==")
    now = time.time()
    check(staleness_violation(None, now, 3.0) is not None, "missing DB → violation")
    check(staleness_violation(now, None, 3.0) is None, "empty vault → no violation")
    check(staleness_violation(now - 600, now, 3.0) is None,
          "vault 10 min newer than index → fresh (under 3h)")
    v = staleness_violation(now - 5 * 3600, now, 3.0)
    check(v is not None and "stale" in v, "vault 5h newer than index → stale")
    check(staleness_violation(now, now - 3600, 3.0) is None,
          "index newer than vault → fresh")

    print("== newest_md_mtime ==")
    mem = tmp / "Memory"
    (mem / "daily").mkdir(parents=True)
    old = mem / "MEMORY.md"
    new = mem / "daily" / "2026-06-03.md"
    old.write_text("old")
    new.write_text("new")
    past = time.time() - 9999
    os.utime(old, (past, past))
    got = newest_md_mtime(mem)
    check(got is not None and abs(got - new.stat().st_mtime) < 1,
          "picks the newest .md recursively")
    check(newest_md_mtime(tmp / "nope") is None, "missing dir → None")
    (mem / "note.txt").write_text("not md")
    check(abs(newest_md_mtime(mem) - new.stat().st_mtime) < 1,
          "non-.md files ignored")

    print("== parse_canary_output ==")
    check(parse_canary_output('[{"id": 1}, {"id": 2}]') == 2, "JSON list → count")
    check(parse_canary_output("[]") == 0, "empty list → 0")
    check(parse_canary_output("not json") is None, "garbage → None")
    check(parse_canary_output('{"a": 1}') is None, "non-list JSON → None")

    print("== check_db ==")
    v, _ = check_db(tmp / "missing.db")
    check(v is not None and "missing" in v, "missing db → violation")

    empty = tmp / "empty.db"
    sqlite3.connect(empty).close()
    v, extra = check_db(empty)
    check(v is not None and "no tables" in v, "zero-table db → violation")

    healthy = tmp / "healthy.db"
    con = sqlite3.connect(healthy)
    con.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, content TEXT)")
    con.execute("INSERT INTO chunks (content) VALUES ('hello')")
    con.commit()
    con.close()
    v, extra = check_db(healthy)
    check(v is None, "healthy db → no violation")
    check(extra.get("db_tables", 0) >= 1, "extra reports table count")

    corrupt = tmp / "corrupt.db"
    corrupt.write_bytes(b"SQLite format 3\x00" + b"\xde\xad\xbe\xef" * 64)
    v, _ = check_db(corrupt)
    check(v is not None, "corrupt file → violation")

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
