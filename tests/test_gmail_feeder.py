#!/usr/bin/env python3
"""Standalone tests for the Gmail comms-capture feeder (no pytest, API stubbed).
Run: uv run python tests/test_gmail_feeder.py

Covers:
  Group A — _extract_body_text unit tests (pure, no I/O)
  Group B — fetch_since tests (_svc() stubbed via _FakeGmailSvc)
  Group C — _gmail_reader tests (gmail.fetch_since patched)
  Group D — channel selection with Gmail keys
  Group E — end-to-end _run with Gmail channel (_read_channel + _distill stubbed)
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".claude" / "scripts"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # needed so @dataclass can resolve cls.__module__
    spec.loader.exec_module(mod)
    return mod


# Load the modules under test
gmail = _load("gmail", ".claude/scripts/integrations/gmail.py")
cc = _load("comms_capture", ".claude/scripts/comms_capture.py")
import shared  # noqa: E402

cc._real_brain_config = cc.brain_config  # snapshot for _FakeCfg fallback

_PASS = _FAIL = 0


def check(c, label):
    global _PASS, _FAIL
    if c:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


# ---------------------------------------------------------------------------
# Patching helpers
# ---------------------------------------------------------------------------

class _patch_cc:
    """Temporarily set attributes on the cc (comms_capture) module."""

    def __init__(self, **kw):
        self.kw = kw
        self.orig = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.orig[k] = getattr(cc, k)
            setattr(cc, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.orig.items():
            setattr(cc, k, v)


class _patch_gmail:
    """Temporarily set attributes on the gmail module."""

    def __init__(self, **kw):
        self.kw = kw
        self.orig = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.orig[k] = getattr(gmail, k)
            setattr(gmail, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.orig.items():
            setattr(gmail, k, v)


class _FakeCfg:
    def __init__(self, overrides):
        self.overrides = overrides

    def get(self, path=None):
        if path in self.overrides:
            return self.overrides[path]
        return cc._real_brain_config.get(path)


def _chan(mode="ingest-and-answer", status="enabled", surface="slack",
          capture="default", name="leadership", **extra):
    cfg = {"surface": surface, "status": status, "ingestion_mode": mode, "name": name}
    if capture == "default":
        capture = {"project": "colinas", "default_export": "linos-protostack"}
    if capture is not None:
        cfg["capture"] = capture
    cfg.update(extra)
    return cfg


DISTILL_MD = "## Decisions\n- Ship the Colinas export on Friday.\n"


# ---------------------------------------------------------------------------
# Fake Gmail service
# ---------------------------------------------------------------------------

class _FakeGmailSvc:
    """Minimal chainable fake for the Google Gmail API service object.

    Usage:
        svc = _FakeGmailSvc()
        svc._list_msgs = [{"id": "M1"}, {"id": "M2"}]
        svc._gets["M1"] = {full message dict}
        svc._raise_ids.add("M1")   # causes .get(id="M1").execute() to raise
    """

    def __init__(self):
        self.last_list_q: str | None = None
        self._list_msgs: list[dict] = []
        self._gets: dict[str, dict] = {}
        self._raise_ids: set[str] = set()
        self._pending: tuple | None = None

    # --- chaining ---

    def users(self): return self
    def messages(self): return self

    def list(self, **kwargs):
        self.last_list_q = kwargs.get("q")
        self._pending = ("list",)
        return self

    def get(self, **kwargs):
        self._pending = ("get", kwargs.get("id"))
        return self

    def execute(self):
        assert self._pending is not None, "_pending not set before execute()"
        kind = self._pending[0]
        if kind == "list":
            return {"messages": self._list_msgs}
        else:  # "get"
            msg_id = self._pending[1]
            if msg_id in self._raise_ids:
                raise Exception(f"simulated get failure for {msg_id}")
            return self._gets.get(msg_id, {})


def _b64(text: str) -> str:
    """base64url-encode a string (no padding), as Gmail API returns."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _make_plain_payload(text: str) -> dict:
    return {"mimeType": "text/plain", "body": {"data": _b64(text)}}


def _make_multipart(mime_type: str, parts: list[dict]) -> dict:
    return {"mimeType": mime_type, "parts": parts}


def _make_full_message(
    msg_id: str,
    thread_id: str = "T1",
    from_addr: str = "Alice <a@example.com>",
    subject: str = "Hello",
    date_iso: str = "Fri, 06 Jun 2026 10:00:00 -0300",
    internal_date_ms: int = 1749204000000,
    body_text: str = "Message body",
    snippet: str = "Message body",
) -> dict:
    return {
        "id": msg_id,
        "threadId": thread_id,
        "internalDate": str(internal_date_ms),
        "snippet": snippet,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": from_addr},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date_iso},
            ],
            "body": {"data": _b64(body_text)},
        },
    }


# ===========================================================================
# Group A — _extract_body_text unit tests
# ===========================================================================

def test_extract_body_text_plain():
    print("[test_extract_body_text_plain]")
    payload = {"mimeType": "text/plain", "body": {"data": _b64("Hello world")}}
    result = gmail._extract_body_text(payload)
    check(result == "Hello world", f"plain text decoded correctly ({result!r})")


def test_extract_body_text_multipart_alternative():
    print("[test_extract_body_text_multipart_alternative]")
    payload = _make_multipart("multipart/alternative", [
        {"mimeType": "text/plain", "body": {"data": _b64("Plain version")}},
        {"mimeType": "text/html", "body": {"data": _b64("<p>HTML version</p>")}},
    ])
    result = gmail._extract_body_text(payload)
    check(result == "Plain version", f"returns text/plain, not text/html ({result!r})")


def test_extract_body_text_nested_multipart():
    print("[test_extract_body_text_nested_multipart]")
    # multipart/mixed → multipart/alternative → text/plain
    payload = _make_multipart("multipart/mixed", [
        _make_multipart("multipart/alternative", [
            {"mimeType": "text/plain", "body": {"data": _b64("Nested plain")}},
        ]),
    ])
    result = gmail._extract_body_text(payload)
    check(result == "Nested plain", f"walks nested multipart tree ({result!r})")


def test_extract_body_text_truncation():
    print("[test_extract_body_text_truncation]")
    long_text = "A" * 5000
    payload = {"mimeType": "text/plain", "body": {"data": _b64(long_text)}}
    result = gmail._extract_body_text(payload, max_chars=4000)
    check(len(result) == 4000, f"truncated to 4000 chars (got {len(result)})")


def test_extract_body_text_empty_payload():
    print("[test_extract_body_text_empty_payload]")
    payload = {"mimeType": "text/plain", "body": {}}
    result = gmail._extract_body_text(payload)
    check(result == "", f"empty data field → '' ({result!r})")


def test_extract_body_text_no_text_plain():
    print("[test_extract_body_text_no_text_plain]")
    payload = _make_multipart("multipart/alternative", [
        {"mimeType": "text/html", "body": {"data": _b64("<p>HTML only</p>")}},
        {"mimeType": "text/html", "body": {"data": _b64("<p>More HTML</p>")}},
    ])
    result = gmail._extract_body_text(payload)
    check(result == "", f"no text/plain found → '' ({result!r})")


# ===========================================================================
# Group B — fetch_since tests (_svc() stubbed)
# ===========================================================================

def test_fetch_since_happy_path():
    print("[test_fetch_since_happy_path]")
    svc = _FakeGmailSvc()
    svc._list_msgs = [{"id": "M1"}, {"id": "M2"}]
    svc._gets["M1"] = _make_full_message("M1", internal_date_ms=1749204010000,
                                         body_text="First msg")
    svc._gets["M2"] = _make_full_message("M2", internal_date_ms=1749204050000,
                                         body_text="Second msg")

    with _patch_gmail(_svc=lambda: svc):
        emails, newest_ms = gmail.fetch_since("in:sent")

    check(len(emails) == 2, f"returns 2 emails ({len(emails)})")
    check(emails[0].internal_date_ms < emails[1].internal_date_ms,
          "ascending by internalDate")
    check(emails[0].body_text == "First msg", f"body decoded ({emails[0].body_text!r})")
    check(newest_ms == 1749204050000, f"newest_ms = max internalDate ({newest_ms})")


def test_fetch_since_query_appends_after():
    print("[test_fetch_since_query_appends_after]")
    svc = _FakeGmailSvc()
    svc._list_msgs = []

    with _patch_gmail(_svc=lambda: svc):
        gmail.fetch_since("in:sent", since_ms=1749204000000)

    check(svc.last_list_q is not None and "after:1749204000" in svc.last_list_q,
          f"query appends after:<epoch_s> ({svc.last_list_q!r})")
    check("in:sent" in svc.last_list_q,
          f"base query preserved ({svc.last_list_q!r})")


def test_fetch_since_strict_watermark_filtering():
    print("[test_fetch_since_strict_watermark_filtering]")
    # A (before watermark), B (equal to watermark), C (after watermark)
    watermark_ms = 1749204000000
    svc = _FakeGmailSvc()
    svc._list_msgs = [{"id": "MA"}, {"id": "MB"}, {"id": "MC"}]
    svc._gets["MA"] = _make_full_message("MA", internal_date_ms=watermark_ms - 1000)
    svc._gets["MB"] = _make_full_message("MB", internal_date_ms=watermark_ms)
    svc._gets["MC"] = _make_full_message("MC", internal_date_ms=watermark_ms + 50000)

    with _patch_gmail(_svc=lambda: svc):
        emails, newest_ms = gmail.fetch_since("in:sent", since_ms=watermark_ms)

    ids = [e.id for e in emails]
    check(ids == ["MC"], f"only messages strictly after watermark returned ({ids})")
    check(newest_ms == watermark_ms + 50000, f"newest_ms is MC's date ({newest_ms})")


def test_fetch_since_empty_result():
    print("[test_fetch_since_empty_result]")
    svc = _FakeGmailSvc()
    svc._list_msgs = []

    with _patch_gmail(_svc=lambda: svc):
        emails, newest_ms = gmail.fetch_since("in:sent")

    check(emails == [], "empty list returned")
    check(newest_ms is None, "newest_ms is None when no messages")


def test_fetch_since_individual_get_failure():
    print("[test_fetch_since_individual_get_failure]")
    svc = _FakeGmailSvc()
    svc._list_msgs = [{"id": "M_FAIL"}, {"id": "M_OK"}]
    svc._raise_ids.add("M_FAIL")
    svc._gets["M_OK"] = _make_full_message("M_OK", internal_date_ms=1749204050000,
                                            body_text="Good message")

    raised = False
    with _patch_gmail(_svc=lambda: svc):
        try:
            emails, newest_ms = gmail.fetch_since("in:sent")
        except Exception:
            raised = True

    check(not raised, "exception from individual get does not propagate")
    check(len(emails) == 1, f"1 message returned despite one get failure ({len(emails)})")
    check(emails[0].id == "M_OK", f"successful message is returned ({emails[0].id})")


# ===========================================================================
# Group C — _gmail_reader tests (gmail.fetch_since patched)
# ===========================================================================

# We need to patch the fetch_since that _gmail_reader sees via
# `from integrations import gmail as gmail_mod`. Since _gmail_reader imports
# at call time, we patch the module-level attribute on the already-loaded
# gmail module.

@dataclass
class _FakeEmailMessage:
    id: str = "M1"
    thread_id: str = "T1"
    from_addr: str = "Bob <b@x.com>"
    subject: str = "Foo"
    date_iso: str = ""
    internal_date_ms: int = 1749204050000
    body_text: str = "See notes"
    snippet: str = ""


def _patch_gmail_fetch_since(stub_fn):
    """Patch gmail.fetch_since AND the module the reader imports at call time."""
    import integrations.gmail as _gmail_integration
    orig = _gmail_integration.fetch_since
    _gmail_integration.fetch_since = stub_fn
    gmail.fetch_since = stub_fn  # also patch the directly-loaded module
    return orig, _gmail_integration


def _restore_gmail_fetch_since(orig, _gmail_integration):
    _gmail_integration.fetch_since = orig
    gmail.fetch_since = orig


def test_gmail_reader_since_none_cold_start():
    print("[test_gmail_reader_since_none_cold_start]")
    captured = {}

    def _fake_fetch(query, since_ms=None, **kw):
        captured["since_ms"] = since_ms
        return [], None

    import integrations.gmail as _gmail_integration
    orig = _gmail_integration.fetch_since
    _gmail_integration.fetch_since = _fake_fetch
    try:
        cc._gmail_reader("in:sent", None)
    finally:
        _gmail_integration.fetch_since = orig

    check(captured.get("since_ms") is None, f"since_ms=None passed to fetch_since ({captured})")


def test_gmail_reader_since_seconds_to_ms():
    print("[test_gmail_reader_since_seconds_to_ms]")
    captured = {}

    def _fake_fetch(query, since_ms=None, **kw):
        captured["since_ms"] = since_ms
        return [], None

    import integrations.gmail as _gmail_integration
    orig = _gmail_integration.fetch_since
    _gmail_integration.fetch_since = _fake_fetch
    try:
        cc._gmail_reader("in:sent", "1749204000.000000")
    finally:
        _gmail_integration.fetch_since = orig

    check(captured.get("since_ms") == 1749204000000,
          f"epoch seconds float string converted to ms int ({captured.get('since_ms')})")


def test_gmail_reader_entries_format():
    print("[test_gmail_reader_entries_format]")
    msg = _FakeEmailMessage(from_addr="Bob <b@x.com>", subject="Foo",
                            body_text="See notes", snippet="",
                            internal_date_ms=1749204050000)

    def _fake_fetch(query, since_ms=None, **kw):
        return [msg], 1749204050000

    import integrations.gmail as _gmail_integration
    orig = _gmail_integration.fetch_since
    _gmail_integration.fetch_since = _fake_fetch
    try:
        entries, newest = cc._gmail_reader("in:sent", None)
    finally:
        _gmail_integration.fetch_since = orig

    check(len(entries) == 1, f"one entry returned ({len(entries)})")
    who, text, ts = entries[0]
    check(who == "Bob <b@x.com>", f"from_addr as speaker ({who!r})")
    check(text == "Subject: Foo\n\nSee notes", f"subject + body formatted ({text!r})")
    check(ts == "1749204050000", f"ts is string of internal_date_ms ({ts!r})")


def test_gmail_reader_snippet_fallback():
    print("[test_gmail_reader_snippet_fallback]")
    msg = _FakeEmailMessage(from_addr="Alice <a@x.com>", subject="Topic",
                            body_text="", snippet="Quick update",
                            internal_date_ms=1749204050000)

    def _fake_fetch(query, since_ms=None, **kw):
        return [msg], 1749204050000

    import integrations.gmail as _gmail_integration
    orig = _gmail_integration.fetch_since
    _gmail_integration.fetch_since = _fake_fetch
    try:
        entries, _ = cc._gmail_reader("in:sent", None)
    finally:
        _gmail_integration.fetch_since = orig

    _, text, _ = entries[0]
    check(text == "Subject: Topic\n\nQuick update",
          f"snippet used as fallback when body_text empty ({text!r})")


def test_gmail_reader_newest_as_seconds():
    print("[test_gmail_reader_newest_as_seconds]")
    msg = _FakeEmailMessage(internal_date_ms=1749204050000)

    def _fake_fetch(query, since_ms=None, **kw):
        return [msg], 1749204050000

    import integrations.gmail as _gmail_integration
    orig = _gmail_integration.fetch_since
    _gmail_integration.fetch_since = _fake_fetch
    try:
        _, newest = cc._gmail_reader("in:sent", None)
    finally:
        _gmail_integration.fetch_since = orig

    check(newest == "1749204050.000000",
          f"newest_ms converted to epoch seconds float string ({newest!r})")


def test_gmail_reader_newest_when_no_emails():
    print("[test_gmail_reader_newest_when_no_emails]")

    def _fake_fetch(query, since_ms=None, **kw):
        return [], None

    import integrations.gmail as _gmail_integration
    orig = _gmail_integration.fetch_since
    _gmail_integration.fetch_since = _fake_fetch
    try:
        _, newest = cc._gmail_reader("in:sent", "1749204000.000000")
    finally:
        _gmail_integration.fetch_since = orig

    check(newest == "1749204000.000000",
          f"cursor does not regress when no emails found ({newest!r})")


# ===========================================================================
# Group D — channel selection with Gmail
# ===========================================================================

def test_gmail_channel_selected():
    print("[test_gmail_channel_selected]")
    reg = {
        "gmail:in:sent": _chan(surface="gmail"),
    }
    sel = cc._select_channels(reg)
    keys = {k for (k, _, _, _) in sel}
    check("gmail:in:sent" in keys, f"gmail:in:sent is selected ({sorted(keys)})")
    channel_id = {k: cid for (k, cid, _, _) in sel}.get("gmail:in:sent")
    check(channel_id == "in:sent",
          f"channel_id parsed from key (got {channel_id!r})")


def test_gmail_channel_missing_capture_fail_closed():
    print("[test_gmail_channel_missing_capture_fail_closed]")
    reg = {
        "gmail:in:sent": _chan(surface="gmail", capture=None),
    }
    sel = cc._select_channels(reg)
    keys = {k for (k, _, _, _) in sel}
    check("gmail:in:sent" not in keys,
          f"channel with no capture block is fail-closed ({sorted(keys)})")


def test_gmail_channel_disabled_skipped():
    print("[test_gmail_channel_disabled_skipped]")
    reg = {
        "gmail:in:sent": _chan(surface="gmail", status="disabled"),
    }
    sel = cc._select_channels(reg)
    keys = {k for (k, _, _, _) in sel}
    check("gmail:in:sent" not in keys,
          f"disabled gmail channel silently skipped ({sorted(keys)})")


def test_gmail_channel_ask_only_skipped():
    print("[test_gmail_channel_ask_only_skipped]")
    reg = {
        "gmail:in:sent": _chan(surface="gmail", mode="ask-only"),
    }
    sel = cc._select_channels(reg)
    keys = {k for (k, _, _, _) in sel}
    check("gmail:in:sent" not in keys,
          f"ask-only gmail channel silently skipped ({sorted(keys)})")


def test_whatsapp_channel_still_silently_skipped():
    print("[test_whatsapp_channel_still_silently_skipped]")
    reg = {
        "whatsapp:W1": _chan(surface="whatsapp"),
    }
    sel = cc._select_channels(reg)
    keys = {k for (k, _, _, _) in sel}
    check("whatsapp:W1" not in keys,
          f"unsupported surface silently skipped ({sorted(keys)})")


# ===========================================================================
# Group E — end-to-end _run with Gmail channel
# ===========================================================================

def _run_ctx_gmail(td, *, overrides, read=None, distill=None, excluded=frozenset()):
    """Build the standard patch set for a _run test against a tmp vault."""
    vault = Path(td) / "vault"
    state = Path(td) / "comms-capture-state.json"
    base = {
        "vault_path": lambda: vault,
        "load_excluded_entities": lambda _p: excluded,
        "COMMS_STATE_PATH": state,
        "brain_config": _FakeCfg(overrides),
        "_log": lambda *a, **k: None,
    }
    if read is not None:
        base["_read_channel"] = read
    if distill is not None:
        base["_distill"] = distill
    return vault, state, base


_GMAIL_DEFAULT_OVERRIDES = {
    "comms_capture.enabled": True,
    "comms_capture.lookback_hours": 24,
    "comms_capture.min_messages": 1,
    "channels": {"gmail:in:sent": _chan(surface="gmail")},
}


def test_gmail_run_writes_capture():
    print("[test_gmail_run_writes_capture]")
    with tempfile.TemporaryDirectory() as td:
        vault, state, base = _run_ctx_gmail(
            td, overrides=dict(_GMAIL_DEFAULT_OVERRIDES),
            read=lambda s, cid, since: (
                [("bob@x.com", "Subject: Ship it\n\nlets go", "1749204050000")],
                "1749204050.000000",
            ),
            distill=lambda t: "## Decisions\n- Ship the Colinas export.\n",
        )
        orig_vp = shared.vault_path
        shared.vault_path = lambda: vault
        try:
            with _patch_cc(**base):
                rc = cc._run(dry_run=False, since_hours=None)["rc"]
        finally:
            shared.vault_path = orig_vp

        check(rc == 0, "run returns 0")
        inbox_dir = vault / "Memory" / "_inbox" / "sessions" / "colinas"
        files = list(inbox_dir.glob("*.md")) if inbox_dir.exists() else []
        check(len(files) == 1, f"one capture written to inbox ({len(files)})")
        body = files[0].read_text() if files else ""
        check("source: comms-gmail:in:sent" in body,
              "source tags the gmail surface + channel")
        check("default_export: linos-protostack" in body,
              "default_export from capture config")
        check("project: colinas" in body, "project from capture config")
        check("Ship the Colinas export" in body, "distilled content present")


def test_gmail_run_cursor_advances_to_seconds_format():
    print("[test_gmail_run_cursor_advances_to_seconds_format]")
    with tempfile.TemporaryDirectory() as td:
        vault, state, base = _run_ctx_gmail(
            td, overrides=dict(_GMAIL_DEFAULT_OVERRIDES),
            read=lambda s, cid, since: (
                [("bob@x.com", "Subject: Foo\n\nbar", "1749204050000")],
                "1749204050.000000",
            ),
            distill=lambda t: DISTILL_MD,
        )
        orig_vp = shared.vault_path
        shared.vault_path = lambda: vault
        try:
            with _patch_cc(**base):
                cc._run(dry_run=False, since_hours=None)
        finally:
            shared.vault_path = orig_vp

        st = json.loads(state.read_text())
        cursor = st["channels"].get("gmail:in:sent")
        check(cursor == "1749204050.000000",
              f"cursor stored as epoch-seconds float string ({cursor!r})")


def test_gmail_run_read_failure_holds_cursor():
    print("[test_gmail_run_read_failure_holds_cursor]")

    def _boom(s, cid, since):
        raise RuntimeError("gmail down")

    with tempfile.TemporaryDirectory() as td:
        vault, state, base = _run_ctx_gmail(
            td, overrides=dict(_GMAIL_DEFAULT_OVERRIDES),
            read=_boom,
        )
        orig_vp = shared.vault_path
        shared.vault_path = lambda: vault
        try:
            with _patch_cc(**base):
                rc = cc._run(dry_run=False, since_hours=None)["rc"]
        finally:
            shared.vault_path = orig_vp

        check(rc == 1, "all channels failed → rc 1")
        st = json.loads(state.read_text()) if state.exists() else {"channels": {}}
        check(st["channels"].get("gmail:in:sent") is None,
              "cursor NOT advanced when read fails (retry next run)")


def test_gmail_run_none_advances_cursor():
    print("[test_gmail_run_none_advances_cursor]")
    with tempfile.TemporaryDirectory() as td:
        vault, state, base = _run_ctx_gmail(
            td, overrides=dict(_GMAIL_DEFAULT_OVERRIDES),
            read=lambda s, cid, since: (
                [("bob@x.com", "Subject: Hi\n\nhey", "1749204060000")],
                "1749204060.000000",
            ),
            distill=lambda t: "NONE",
        )
        orig_vp = shared.vault_path
        shared.vault_path = lambda: vault
        try:
            with _patch_cc(**base):
                rc = cc._run(dry_run=False, since_hours=None)["rc"]
        finally:
            shared.vault_path = orig_vp

        check(rc == 0, "run returns 0")
        inbox_dir = vault / "Memory" / "_inbox"
        check(not inbox_dir.exists(), "no capture written when distill returns NONE")
        st = json.loads(state.read_text())
        cursor = st["channels"].get("gmail:in:sent")
        check(cursor == "1749204060.000000",
              f"cursor advances even when NONE returned ({cursor!r})")


# ===========================================================================
# Runner
# ===========================================================================

def main():
    # Group A
    test_extract_body_text_plain()
    test_extract_body_text_multipart_alternative()
    test_extract_body_text_nested_multipart()
    test_extract_body_text_truncation()
    test_extract_body_text_empty_payload()
    test_extract_body_text_no_text_plain()

    # Group B
    test_fetch_since_happy_path()
    test_fetch_since_query_appends_after()
    test_fetch_since_strict_watermark_filtering()
    test_fetch_since_empty_result()
    test_fetch_since_individual_get_failure()

    # Group C
    test_gmail_reader_since_none_cold_start()
    test_gmail_reader_since_seconds_to_ms()
    test_gmail_reader_entries_format()
    test_gmail_reader_snippet_fallback()
    test_gmail_reader_newest_as_seconds()
    test_gmail_reader_newest_when_no_emails()

    # Group D
    test_gmail_channel_selected()
    test_gmail_channel_missing_capture_fail_closed()
    test_gmail_channel_disabled_skipped()
    test_gmail_channel_ask_only_skipped()
    test_whatsapp_channel_still_silently_skipped()

    # Group E
    test_gmail_run_writes_capture()
    test_gmail_run_cursor_advances_to_seconds_format()
    test_gmail_run_read_failure_holds_cursor()
    test_gmail_run_none_advances_cursor()

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
