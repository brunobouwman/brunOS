"""Pluggable "ask the person" surface for BrunOS.

The decision-rationale loop (memory_dream) needs to ask Bruno *why* he made a
low-confidence decision. WHICH channel that question goes out on is per-brain: an
individual uses Slack DM; a company plugs WhatsApp / Telegram / Teams / email.
This module is that seam — the surface is never hardcoded into dream.

    adapter = get_adapter()              # from brain-config notify.adapter
    adapter.ask("why did you ...?", "ref-slug")   # -> bool (delivered?)

Adapters:
  - SlackAdapter (default) — DM via the Phase-4 Slack bot's chat:write.
  - NoneAdapter — no-op (solo brain with no comms wired); ask() returns False.

Stdlib + the existing integrations only; slack_sdk is imported lazily inside the
Slack client so a brain configured with adapter="none" never needs it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

import brain_config  # noqa: E402


class NotifyAdapter:
    """Interface: deliver one question, return whether it was sent."""

    name = "base"

    def ask(self, question: str, ref_id: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class NoneAdapter(NotifyAdapter):
    """No comms surface. ask() always reports "not delivered" and sends nothing."""

    name = "none"

    def ask(self, question: str, ref_id: str) -> bool:
        return False


class SlackAdapter(NotifyAdapter):
    """Ask via a Slack DM, tagging the message with [ref:<id>] so the reply can be
    reconciled back to the decision. Target resolution order:
        brain-config notify.target → $BRUNOS_NOTIFY_TARGET → $BRUNOS_ALERT_CHANNEL
    A channel/user id is required; without one ask() reports not-delivered."""

    name = "slack"

    def __init__(self, target: str | None = None):
        self.target = (
            target
            or brain_config.get("notify.target")
            or os.environ.get("BRUNOS_NOTIFY_TARGET")
            or os.environ.get("BRUNOS_ALERT_CHANNEL")
            or ""
        ).strip()

    def ask(self, question: str, ref_id: str) -> bool:
        if not self.target:
            print("[notify:slack] no target (notify.target / BRUNOS_NOTIFY_TARGET / "
                  "BRUNOS_ALERT_CHANNEL all unset) — not delivered", file=sys.stderr)
            return False
        text = f"{question}\n\n_Reply quoting [ref:{ref_id}] so I can file your answer._"
        try:
            from integrations import slack

            resp = slack.send_message(slack._client(), channel=self.target, text=text)
            return bool(resp.get("ok"))
        except Exception as e:  # noqa: BLE001 — delivery is best-effort
            print(f"[notify:slack] send failed: {type(e).__name__}: {e}", file=sys.stderr)
            return False


_ADAPTERS = {"slack": SlackAdapter, "none": NoneAdapter}


def get_adapter(name: str | None = None) -> NotifyAdapter:
    """Build the adapter named by `name` or brain-config notify.adapter (default
    slack). An unknown name falls back to NoneAdapter (fail-safe: never crash the
    dream run because comms are misconfigured)."""
    chosen = (name or brain_config.get("notify.adapter") or "slack").strip().lower()
    cls = _ADAPTERS.get(chosen)
    if cls is None:
        print(f"[notify] unknown adapter {chosen!r}; falling back to none", file=sys.stderr)
        return NoneAdapter()
    return cls()
