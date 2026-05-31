#!/usr/bin/env python3
"""DEPRECATED compat shim — delegates to .claude/scripts/vault_sync.py.

This used to run simonthum's /usr/local/bin/git-sync. That tool dead-looped on
conflicts and depended on per-clone config that drifted, so the vault sync is
now owned by vault_sync.py (fetch → commit → merge w/ concat-both → push, with
conflict-safety + alerting + a healthchecks.io dead-man's-switch).

Kept only so any launchd plist or muscle-memory still pointing here lands on the
new engine. The current plist calls vault_sync.py directly; prefer that.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

import vault_sync  # noqa: E402

if __name__ == "__main__":
    sys.exit(vault_sync.main())
