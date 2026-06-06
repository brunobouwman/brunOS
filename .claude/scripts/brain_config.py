"""Per-brain config store: cadence + behavior toggles, with documented defaults.

Stdlib only (imported by reflect/dream which may run on system python via uv).
The config lives at `.claude/data/state/brain-config.json`; an ABSENT file means
"pure defaults", so a fresh brain works with zero config. Onboarding writes the
file (from `Brain/brain-config.template.json`) and `gen_schedules.py` turns the
cadence strings into timer units. The scripts themselves only read the behavior
toggles at runtime — they never schedule.

    import brain_config as cfg
    cfg.get("dreaming.trigger_min_captures")   # -> 5  (default)
    cfg.get("reflection.federation")           # -> True
    cfg.get()                                   # -> the whole merged dict

`get()` returns DEFAULTS deep-merged with the file (file wins leaf-by-leaf), so a
partial file only overrides the keys it names. The merge is lru_cached for the
process lifetime; tests that swap the file mid-run call `reset_cache()`.
"""

from __future__ import annotations

import copy
import sys
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import STATE_DIR, load_state  # noqa: E402

CONFIG_PATH = STATE_DIR / "brain-config.json"

# The single source of truth for behavior + cadence. Keep in sync with
# Brain/brain-config.template.json (the onboarding copy) and the CLAUDE.md docs.
DEFAULTS: dict = {
    "role": "individual",  # "individual" | "company"
    "reflection": {
        # Frequent federation-fast pass: distil captures, buffer personal items,
        # update continuity, strip+clear in place.
        "inbox_pass": {"enabled": True, "cadence": "hourly", "hours": "08-20"},
        # Daily curation: drain the personal buffer + daily-log promotions into
        # MEMORY.md once, then evict-to-archive once.
        "memory_curation": {"enabled": True, "cadence": "daily@08:00"},
        # strip+clear+forward to the company inbox. False for a solo brain that
        # still wants inbox distillation but no federation.
        "federation": True,
    },
    "dreaming": {
        "enabled": True,
        "cadence": "nightly@03:00",
        # Adaptive trigger: skip the sweep when fewer than this many captures are
        # new since the last dream watermark.
        "trigger_min_captures": 5,
        "extract": ["processes", "decisions"],
        "decision_prompts": {
            "enabled": True,
            "max_per_day": 3,
            "confidence_threshold": 0.6,
        },
    },
    # Pluggable "ask the person" surface. adapter ∈ {slack, none, ...};
    # target=None means the adapter's default destination (Slack DM channel).
    "notify": {"adapter": "slack", "target": None},
    # Comms-capture feeder (BaaS): a cadence-driven pass that reads configured
    # comms channels and distils HIGH-SIGNAL knowledge into the SAME
    # _inbox/sessions/ captures code sessions write — so reflection + dreaming +
    # federation process comms knowledge unchanged. These are the feeder-level
    # knobs only; channel SELECTION reads the shared `channels` registry below.
    "comms_capture": {
        "enabled": True,
        "cadence": "daily@22:00",
        "hours": "08-20",        # only consulted when cadence == "hourly"
        "lookback_hours": 24,    # cold-start window per channel on first run
        "min_messages": 1,       # skip distillation below this many new messages
    },
    # Channel registry — the shared access+routing primitive
    # (projects/Brain/company_brain_channel_registry.md). Keyed "<surface>:<id>"
    # (e.g. "slack:C012345"). The comms-capture feeder reads the ingestion-relevant
    # subset (surface / status / ingestion_mode / redaction + a per-channel
    # capture:{project, default_export} routing block) and FAILS CLOSED on unknown
    # or malformed entries; the company-brain chat skills (ClickUp 86ca5c6nz) read
    # the governance fields (allowed_users / required_tier / personas). Empty by
    # default → the feeder is a clean no-op (no Slack client is even constructed).
    "channels": {},
}


def _deep_merge(base: dict, over: dict | None) -> dict:
    """Return a fresh dict = base overlaid by `over`, recursing into nested dicts."""
    out = copy.deepcopy(base)
    for key, val in (over or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


@lru_cache(maxsize=1)
def _merged() -> dict:
    file_cfg = load_state(CONFIG_PATH, {})
    if not isinstance(file_cfg, dict):
        file_cfg = {}
    return _deep_merge(DEFAULTS, file_cfg)


def reset_cache() -> None:
    """Drop the cached merge (tests that rewrite brain-config.json mid-process)."""
    _merged.cache_clear()


def get(path: str | None = None):
    """Look up a dotted `path` in the merged config; whole dict when path is None.

    Returns None for an unknown leaf (the caller supplies its own fallback, but
    every documented key already has a default so a hit is the normal case).
    """
    cfg = _merged()
    if not path:
        return copy.deepcopy(cfg)
    cur = cfg
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return copy.deepcopy(cur) if isinstance(cur, (dict, list)) else cur


if __name__ == "__main__":
    import json

    print(json.dumps(get(), indent=2, ensure_ascii=False))
