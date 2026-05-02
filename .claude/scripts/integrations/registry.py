"""Central registry of Phase 4 integrations.

`query.py` consults this to know what's wired and what env var gates each
integration. Adding a new integration = appending one entry here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class IntegrationSpec:
    """One row in the integration registry.

    name:    CLI subcommand name (`query.py <name> ...`).
    env_var: env var that must be set (non-empty) for the integration to be usable.
             Used by `enabled()` for early-exit error messages.
             Use empty string to mark "always-on" (e.g. RSS doesn't need a token).
    module:  dotted path to the integration module (importable from .claude/scripts/).
    """

    name: str
    env_var: str
    module: str


INTEGRATIONS: list[IntegrationSpec] = [
    IntegrationSpec("slack", "SLACK_BOT_TOKEN", "integrations.slack"),
    IntegrationSpec("github", "GITHUB_TOKEN", "integrations.github"),
    IntegrationSpec("clickup", "CLICKUP_API_TOKEN", "integrations.clickup"),
    IntegrationSpec("gmail", "GOOGLE_OAUTH_TOKEN_PATH", "integrations.gmail"),
    IntegrationSpec("calendar", "GOOGLE_OAUTH_TOKEN_PATH", "integrations.calendar"),
    IntegrationSpec("rss", "", "integrations.rss"),
]


def enabled(spec: IntegrationSpec) -> bool:
    if not spec.env_var:
        return True
    return bool(os.environ.get(spec.env_var, "").strip())


def find(name: str) -> IntegrationSpec | None:
    for spec in INTEGRATIONS:
        if spec.name == name:
            return spec
    return None
