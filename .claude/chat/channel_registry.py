"""Deterministic channel access/routing for company-brain chat.

The registry is intentionally checked before any Agent SDK call. For LinOS v1,
an enabled Slack channel must be explicitly present in brain-config AND the
Slack user id must be allowlisted there. Unknown scope fails closed.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import STATE_DIR, vault_path  # noqa: E402

VALID_INGESTION_MODES = {"disabled", "ask-only", "ingest-and-answer", "digest-only"}


@dataclass(frozen=True)
class ChannelDecision:
    allowed: bool
    reason: str
    channel_key: str = ""
    channel_name: str = ""
    persona: str = ""
    ingestion_mode: str = ""
    allowed_sources: tuple[str, ...] = ()
    write_targets: tuple[str, ...] = ()
    external_action: str = "draft-only"

    @property
    def refusal_text(self) -> str:
        if self.reason == "unknown_channel":
            return "_I cannot answer here yet: this channel is not registered for LinOS._"
        if self.reason == "disabled_channel":
            return "_I cannot answer here yet: this LinOS channel is disabled._"
        if self.reason == "unknown_user":
            return "_I cannot answer for this user yet: they are not registered for this LinOS channel._"
        if self.reason == "invalid_channel_config":
            return "_I cannot answer here yet: this LinOS channel config is invalid._"
        return "_I cannot answer here yet: this LinOS channel is not authorized._"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for key, val in over.items():
        cur = out.get(key)
        if isinstance(cur, dict) and isinstance(val, dict):
            out[key] = _deep_merge(cur, val)
        else:
            out[key] = val
    return out


def _config_paths() -> list[Path]:
    paths: list[Path] = []
    try:
        paths.append(vault_path() / "Memory" / "Brain" / "brain-config.json")
    except Exception:
        pass
    override = os.environ.get("CHAT_CHANNEL_REGISTRY_CONFIG")
    if override:
        paths.append(Path(override).expanduser())
    state_override = os.environ.get("CHAT_CHANNEL_REGISTRY_STATE_CONFIG")
    paths.append(Path(state_override).expanduser() if state_override else STATE_DIR / "brain-config.json")
    return paths


def load_config() -> dict[str, Any]:
    """Load vault brain-config plus optional runtime override.

    Later paths win. This lets the vault declare the product policy while a
    deployed node can still carry local-only Slack ids during dogfood.
    """
    cfg: dict[str, Any] = {}
    for path in _config_paths():
        cfg = _deep_merge(cfg, _read_json(path))
    return cfg


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(v).strip() for v in value if str(v).strip())


def _channel_entry(config: dict[str, Any], channel_key: str) -> dict[str, Any] | None:
    channels = config.get("channels")
    if not isinstance(channels, dict):
        return None
    entry = channels.get(channel_key)
    return entry if isinstance(entry, dict) else None


def resolve_slack_event(event: dict[str, Any]) -> ChannelDecision:
    """Resolve one Slack event to an allow/refuse decision."""
    channel_id = str(event.get("channel") or "").strip()
    user_id = str(event.get("user") or "").strip()
    if not channel_id:
        return ChannelDecision(False, "missing_channel")

    channel_key = f"slack:{channel_id}"
    entry = _channel_entry(load_config(), channel_key)
    if entry is None:
        return ChannelDecision(False, "unknown_channel", channel_key=channel_key)

    status = str(entry.get("status") or "disabled").strip().lower()
    if status != "enabled":
        return ChannelDecision(False, "disabled_channel", channel_key=channel_key)

    allowed_users = set(
        _as_str_tuple(entry.get("allowed_users"))
        or _as_str_tuple(entry.get("allowed_user_ids"))
    )
    allow_unknown = entry.get("allow_unknown_users") is True
    if not allow_unknown and (not user_id or user_id not in allowed_users):
        return ChannelDecision(False, "unknown_user", channel_key=channel_key)

    persona = str(entry.get("default_persona") or "company-query").strip()
    allowed_personas = set(_as_str_tuple(entry.get("allowed_personas")))
    if not persona or (allowed_personas and persona not in allowed_personas):
        return ChannelDecision(False, "invalid_channel_config", channel_key=channel_key)

    ingestion_mode = str(entry.get("ingestion_mode") or "ask-only").strip()
    if ingestion_mode not in VALID_INGESTION_MODES or ingestion_mode == "disabled":
        return ChannelDecision(False, "invalid_channel_config", channel_key=channel_key)

    return ChannelDecision(
        True,
        "allowed",
        channel_key=channel_key,
        channel_name=str(entry.get("name") or channel_id).strip(),
        persona=persona,
        ingestion_mode=ingestion_mode,
        allowed_sources=_as_str_tuple(entry.get("allowed_sources")),
        write_targets=_as_str_tuple(entry.get("write_targets")),
        external_action=str(entry.get("external_action") or "draft-only").strip(),
    )


def render_context(decision: ChannelDecision) -> str:
    """Compact internal context injected ahead of the external Slack payload."""
    sources = ",".join(decision.allowed_sources) or "none"
    targets = ",".join(decision.write_targets) or "none"
    return (
        '<channel_registry '
        f'channel="{decision.channel_key}" '
        f'name="{decision.channel_name}" '
        f'persona="{decision.persona}" '
        f'ingestion_mode="{decision.ingestion_mode}" '
        f'allowed_sources="{sources}" '
        f'write_targets="{targets}" '
        f'external_action="{decision.external_action}" '
        "/>\n"
    )
