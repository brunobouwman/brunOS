"""Shared Google credentials helper for gmail.py + calendar.py.

Loads the persisted token written by `bootstrap_google_oauth.py`. Auto-refreshes
the access token via the refresh token. Re-persists when refreshed.

Token path: GOOGLE_OAUTH_TOKEN_PATH (env), default `.claude/data/state/google_token.json`.
Relative paths resolve against REPO_ROOT.

Underscore prefix marks this as internal — callers go via gmail.py / calendar.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import atomic_write  # noqa: E402

DEFAULT_TOKEN_REL = ".claude/data/state/google_token.json"

_CREDS = None


def _token_path() -> Path:
    rel = os.environ.get("GOOGLE_OAUTH_TOKEN_PATH", DEFAULT_TOKEN_REL)
    p = Path(rel)
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


def _creds():
    global _CREDS
    if _CREDS is not None and _CREDS.valid:
        return _CREDS
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = _token_path()
    if not path.exists():
        raise RuntimeError(
            f"Google OAuth token missing at {path}. "
            "Run: uv run python .claude/scripts/bootstrap_google_oauth.py"
        )
    creds = Credentials.from_authorized_user_file(str(path))
    if not creds.valid:
        if not creds.refresh_token:
            raise RuntimeError(
                f"Token at {path} has no refresh_token. Re-run bootstrap_google_oauth.py."
            )
        try:
            creds.refresh(Request())
        except Exception as e:
            raise RuntimeError(
                f"Token refresh failed: {type(e).__name__}: {e}. "
                "Re-run bootstrap_google_oauth.py."
            )
        atomic_write(path, creds.to_json(), stamp_updated=False)
    _CREDS = creds
    return _CREDS


def _service(api_name: str, version: str):
    from googleapiclient.discovery import build

    return build(api_name, version, credentials=_creds(), cache_discovery=False)
