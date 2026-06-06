#!/usr/bin/env python3
"""Standalone tests for comms_capture (no pytest, Haiku + Slack stubbed).
Run: uv run python tests/test_comms_capture.py

Covers: fail-closed channel selection (mode/status/surface/capture-block gates),
the NONE sentinel, redaction.exclude_people defaulting, transcript build, the run
paths (capture written with right project/export/source + cursor advance; NONE →
cursor advance but no capture; distill failure → cursor HELD; min_messages skip;
excluded-entity + secret scrub), disabled / empty-registry no-ops (no client
built), dry-run writes nothing, and the stateless slack.fetch_channel_history
(filters noise, returns newest-incl-filtered ts, never writes channel cursors).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".claude" / "scripts"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cc = _load("comms_capture", ".claude/scripts/comms_capture.py")
import shared  # noqa: E402
from integrations import slack  # noqa: E402

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


class _patch:
    """Temporarily set module attributes (on cc unless a (mod, name) tuple is used)."""

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


# --------------------------------------------------------------------------- #
# selection (fail-closed)
# --------------------------------------------------------------------------- #
def test_select_channels_fail_closed():
    print("[test_select_channels_fail_closed]")
    reg = {
        "slack:C_OK": _chan(),                                   # selected
        "slack:C_DIGEST": _chan(mode="digest-only"),             # selected
        "slack:C_ASK": _chan(mode="ask-only"),                   # skip: not an ingest mode
        "slack:C_OFF": _chan(status="disabled"),                 # skip: status
        "slack:C_NOCAP": _chan(capture=None),                    # skip: no capture block
        "slack:C_NOEXPORT": _chan(capture={"project": "x"}),     # skip: incomplete capture
        "slack:C_BADEXPORT": _chan(capture={"project": "x", "default_export": "leak"}),  # skip: invalid export
        "gmail:G1": _chan(surface="gmail"),                      # skip: unsupported surface (silent)
        "slack:C_MISMATCH": _chan(surface="gmail"),              # skip: surface field != key prefix
        "badkey": _chan(),                                       # skip: no '<surface>:<id>'
        "slack:": _chan(),                                       # skip: empty id
        "slack:C_STR": "nope",                                   # skip: not an object
    }
    sel = cc._select_channels(reg)
    keys = {k for (k, _cid, _s, _cfg) in sel}
    check(keys == {"slack:C_OK", "slack:C_DIGEST"}, f"only enabled ingest-mode channels ({sorted(keys)})")
    cid = {k: cid for (k, cid, _s, _cfg) in sel}.get("slack:C_OK")
    check(cid == "C_OK", f"channel id parsed from key ({cid})")
    check(cc._select_channels(None) == [], "non-dict registry → []")
    check(cc._select_channels({}) == [], "empty registry → []")


def test_is_none():
    print("[test_is_none]")
    check(cc._is_none("NONE") is True, "bare NONE")
    check(cc._is_none("  none  ") is True, "lowercase + whitespace")
    check(cc._is_none("") is True, "empty")
    check(cc._is_none("```\nNONE\n```") is True, "fenced NONE")
    check(cc._is_none(DISTILL_MD) is False, "real markdown is not NONE")


def test_exclude_people_default():
    print("[test_exclude_people_default]")
    check(cc._exclude_people({}) is True, "missing redaction → True (privacy-safe)")
    check(cc._exclude_people({"redaction": {"exclude_people": False}}) is False, "explicit False honored")
    check(cc._exclude_people({"redaction": {"exclude_people": True}}) is True, "explicit True honored")


def test_build_transcript():
    print("[test_build_transcript]")
    t = cc._build_transcript([("Lisa", "we decided X", "1700000001.0")], "leadership (slack:C1)")
    check("<external_data" in t and "comms-message" in t, "messages wrapped as external_data")
    check("Lisa: we decided X" in t, "speaker + text present")
    check("leadership (slack:C1)" in t, "channel label present")


# --------------------------------------------------------------------------- #
# run paths
# --------------------------------------------------------------------------- #
def _run_ctx(td, *, overrides, read=None, distill=None, excluded=frozenset()):
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


_DEFAULT_OVERRIDES = {
    "comms_capture.enabled": True,
    "comms_capture.lookback_hours": 24,
    "comms_capture.min_messages": 1,
    "channels": {"slack:C_OK": _chan()},
}


def test_run_writes_capture_and_advances_cursor():
    print("[test_run_writes_capture_and_advances_cursor]")
    with tempfile.TemporaryDirectory() as td:
        vault, state, base = _run_ctx(
            td, overrides=dict(_DEFAULT_OVERRIDES),
            read=lambda s, cid, since: ([("Lisa", "ship it", "1700000002.0")], "1700000002.000000"),
            distill=lambda t: DISTILL_MD,
        )
        # write_inbox_capture resolves shared.vault_path() internally → patch it too.
        orig_vp = shared.vault_path
        shared.vault_path = lambda: vault
        try:
            with _patch(**base):
                rc = cc._run(dry_run=False, since_hours=None)
        finally:
            shared.vault_path = orig_vp
        check(rc == 0, "run returns 0")
        files = list((vault / "Memory" / "_inbox" / "sessions" / "colinas").glob("*.md"))
        check(len(files) == 1, f"one capture written to the channel's project inbox ({len(files)})")
        body = files[0].read_text() if files else ""
        check("default_export: linos-protostack" in body, "default_export routed from capture config")
        check("source: comms-slack:C_OK" in body, "source tags the comms surface + channel")
        check("project: colinas" in body, "project routed from capture config")
        check("Ship the Colinas export" in body, "distilled high-signal body present")
        st = json.loads(state.read_text())
        check(st["channels"].get("slack:C_OK") == "1700000002.000000", "cursor advanced to newest ts")


def test_run_none_advances_cursor_no_capture():
    print("[test_run_none_advances_cursor_no_capture]")
    with tempfile.TemporaryDirectory() as td:
        vault, state, base = _run_ctx(
            td, overrides=dict(_DEFAULT_OVERRIDES),
            read=lambda s, cid, since: ([("Lisa", "lol ok", "1700000003.0")], "1700000003.000000"),
            distill=lambda t: "NONE",
        )
        orig_vp = shared.vault_path
        shared.vault_path = lambda: vault
        try:
            with _patch(**base):
                rc = cc._run(dry_run=False, since_hours=None)
        finally:
            shared.vault_path = orig_vp
        check(rc == 0, "run returns 0")
        check(not (vault / "Memory" / "_inbox").exists(), "no capture written for NONE")
        st = json.loads(state.read_text())
        check(st["channels"].get("slack:C_OK") == "1700000003.000000", "cursor still advances past chatter")


def test_run_distill_failure_holds_cursor():
    print("[test_run_distill_failure_holds_cursor]")
    def _boom(_t):
        raise RuntimeError("haiku down")
    with tempfile.TemporaryDirectory() as td:
        vault, state, base = _run_ctx(
            td, overrides=dict(_DEFAULT_OVERRIDES),
            read=lambda s, cid, since: ([("Lisa", "important fact", "1700000004.0")], "1700000004.000000"),
            distill=_boom,
        )
        orig_vp = shared.vault_path
        shared.vault_path = lambda: vault
        try:
            with _patch(**base):
                rc = cc._run(dry_run=False, since_hours=None)
        finally:
            shared.vault_path = orig_vp
        check(rc == 0, "run returns 0 (soft failure)")
        st = json.loads(state.read_text()) if state.exists() else {"channels": {}}
        check(st["channels"].get("slack:C_OK") is None, "cursor HELD on distill failure (retry next run)")


def test_run_min_messages_skip_no_distill():
    print("[test_run_min_messages_skip_no_distill]")
    def _must_not_distill(_t):
        raise AssertionError("distill must not be called below min_messages")
    with tempfile.TemporaryDirectory() as td:
        ov = dict(_DEFAULT_OVERRIDES)
        ov["comms_capture.min_messages"] = 3
        vault, state, base = _run_ctx(
            td, overrides=ov,
            read=lambda s, cid, since: ([("Lisa", "a", "1.0"), ("Bob", "b", "2.000000")], "2.000000"),
            distill=_must_not_distill,
        )
        with _patch(**base):
            rc = cc._run(dry_run=False, since_hours=None)
        check(rc == 0, "run returns 0")
        st = json.loads(state.read_text())
        check(st["channels"].get("slack:C_OK") == "2.000000", "cursor advanced even when below min_messages")


def test_run_scrubs_excluded_and_secrets():
    print("[test_run_scrubs_excluded_and_secrets]")
    leaky = ("## Decisions\n- Use postgres://u:pw@db/x for Colinas; Mallory objected.\n")
    with tempfile.TemporaryDirectory() as td:
        vault, state, base = _run_ctx(
            td, overrides=dict(_DEFAULT_OVERRIDES),
            read=lambda s, cid, since: ([("Lisa", "see notes", "1700000005.0")], "1700000005.000000"),
            distill=lambda t: leaky,
            excluded=frozenset({"Mallory"}),
        )
        orig_vp = shared.vault_path
        shared.vault_path = lambda: vault
        try:
            with _patch(**base):
                cc._run(dry_run=False, since_hours=None)
        finally:
            shared.vault_path = orig_vp
        files = list((vault / "Memory" / "_inbox" / "sessions" / "colinas").glob("*.md"))
        body = files[0].read_text() if files else ""
        check("postgres://" not in body, "connection string scrubbed")
        check("[REDACTED-CONNSTR]" in body, "secret scrub marker present")
        check("Mallory" not in body, "excluded entity scrubbed")
        check("[REDACTED-ENTITY]" in body, "excluded-entity marker present")


def test_disabled_is_noop():
    print("[test_disabled_is_noop]")
    def _must_not_read(*a, **k):
        raise AssertionError("must not read channels when disabled")
    with tempfile.TemporaryDirectory() as td:
        _v, _s, base = _run_ctx(
            td, overrides={"comms_capture.enabled": False, "channels": {"slack:C_OK": _chan()}},
            read=_must_not_read,
        )
        with _patch(**base):
            rc = cc._run(dry_run=False, since_hours=None)
        check(rc == 0, "disabled → returns 0, no reads")


def test_empty_registry_noop_no_client():
    print("[test_empty_registry_noop_no_client]")
    def _must_not_read(*a, **k):
        raise AssertionError("must not read channels with empty registry")
    with tempfile.TemporaryDirectory() as td:
        _v, _s, base = _run_ctx(
            td, overrides={"comms_capture.enabled": True, "channels": {}},
            read=_must_not_read,
        )
        with _patch(**base):
            rc = cc._run(dry_run=False, since_hours=None)
        check(rc == 0, "empty registry → returns 0, no client constructed")


def test_dry_run_writes_nothing():
    print("[test_dry_run_writes_nothing]")
    with tempfile.TemporaryDirectory() as td:
        vault, state, base = _run_ctx(
            td, overrides=dict(_DEFAULT_OVERRIDES),
            read=lambda s, cid, since: ([("Lisa", "ship it", "1700000006.0")], "1700000006.000000"),
            distill=lambda t: DISTILL_MD,
        )
        orig_vp = shared.vault_path
        shared.vault_path = lambda: vault
        try:
            with _patch(**base):
                rc = cc._run(dry_run=True, since_hours=None)
        finally:
            shared.vault_path = orig_vp
        check(rc == 0, "dry-run returns 0")
        check(not (vault / "Memory" / "_inbox").exists(), "dry-run writes no capture")
        check(not state.exists(), "dry-run advances no cursor (no state file)")


# --------------------------------------------------------------------------- #
# the stateless slack helper (no channel-cursor writes)
# --------------------------------------------------------------------------- #
class _FakeSlackClient:
    """Returns two pages, then stops. Mixes a real msg, the bot's own msg, and a
    join-subtype noise msg to exercise filtering."""

    def __init__(self):
        self.pages = [
            {"messages": [
                {"ts": "100.0", "user": "U_LISA", "text": "real one"},
                {"ts": "101.0", "user": "U_BOT", "text": "bot echo"},
            ], "response_metadata": {"next_cursor": "c2"}},
            {"messages": [
                {"ts": "103.0", "user": "U_BOB", "text": "second real", "subtype": "channel_join"},
                {"ts": "104.0", "user": "U_BOB", "text": "third real"},
            ]},
        ]
        self.calls = 0

    def conversations_history(self, **kwargs):
        page = self.pages[self.calls]
        self.calls += 1
        return page


def test_fetch_channel_history_stateless():
    print("[test_fetch_channel_history_stateless]")
    saved = {"n": 0}
    orig_bot, orig_save = slack._bot_user_id, slack._save
    slack._bot_user_id = lambda c, s: "U_BOT"
    slack._save = lambda s: saved.__setitem__("n", saved["n"] + 1)
    try:
        kept, newest = slack.fetch_channel_history(_FakeSlackClient(), "C1", oldest="99.0")
    finally:
        slack._bot_user_id, slack._save = orig_bot, orig_save
    texts = [m.text for m in kept]
    check(texts == ["real one", "third real"], f"bot + subtype filtered, ascending ({texts})")
    check(newest == "104.000000", "newest ts includes filtered messages (no re-scan)")
    check(saved["n"] == 0, "no channel-cursor write to slack-state.json")


def main():
    test_select_channels_fail_closed()
    test_is_none()
    test_exclude_people_default()
    test_build_transcript()
    test_run_writes_capture_and_advances_cursor()
    test_run_none_advances_cursor_no_capture()
    test_run_distill_failure_holds_cursor()
    test_run_min_messages_skip_no_distill()
    test_run_scrubs_excluded_and_secrets()
    test_disabled_is_noop()
    test_empty_registry_noop_no_client()
    test_dry_run_writes_nothing()
    test_fetch_channel_history_stateless()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
